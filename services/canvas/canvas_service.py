"""Canvas LMS orchestrator.

Polls Canvas for new/modified assignments, announcements, and graded
submissions; persists state through CanvasRepository; emits change
events via `_dispatch`; and runs deadline reminders at the tiers
configured in core.constants.CANVAS_REMINDER_HOURS.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from core import constants
from core.config import settings
from core.error_notifier import ErrorNotifier
from core.logger import get_logger
from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
    CanvasAttachment,
    CanvasCourse,
    CanvasSubmission,
)
from repositories.canvas_repo import CanvasRepository
from services.canvas import canvas_formatter
from services.canvas.canvas_client import CanvasClient

logger = get_logger(__name__)


# Item kinds emitted to the dispatch hook.
KIND_NEW_ASSIGNMENT = "new_assignment"
KIND_ASSIGNMENT_MODIFIED = "assignment_modified"
KIND_DUE_DATE_CHANGED = "due_date_changed"
KIND_NEW_ANNOUNCEMENT = "new_announcement"
KIND_GRADE_REGISTERED = "grade_registered"
KIND_UNSUBMITTED_WARNING = "unsubmitted_warning"


@dataclass
class CanvasEvent:
    """Aggregates everything a downstream notifier needs.

    `changes` is a dict of field → (old, new) pairs for modification
    events; empty for new-item events.
    """

    kind: str
    course: CanvasCourse
    item: Any  # CanvasAssignment | CanvasAnnouncement | CanvasSubmission
    db_record: Optional[Dict[str, Any]] = None
    changes: Optional[Dict[str, Any]] = None


class CanvasService:
    def __init__(
        self,
        repo: CanvasRepository,
        api_url: str = "",
        api_token: str = "",
        notifier=None,
        file_service=None,
        ai_service=None,
        error_notifier: Optional[ErrorNotifier] = None,
        client: Optional[CanvasClient] = None,
    ):
        """Initialize CanvasService.

        Either pass `client` (tests inject a mock with a pre-built session)
        or pass `api_url` + `api_token` (production: a fresh aiohttp session
        is created per `run()` call and torn down on exit).
        """
        self.repo = repo
        self.api_url = api_url
        self.api_token = api_token
        self.notifier = notifier
        self.file_service = file_service
        self.ai_service = ai_service
        self.error_notifier = error_notifier
        self.client: Optional[CanvasClient] = client

    # ---------- Top-level run ----------

    async def run(self) -> None:
        """One full polling pass.

        Manages an aiohttp session if no client was injected at init.
        """
        if self.client is not None:
            await self._poll()
            return

        async with aiohttp.ClientSession() as session:
            self.client = CanvasClient(
                self.api_url,
                self.api_token,
                session,
                error_notifier=self.error_notifier,
            )
            try:
                await self._poll()
            finally:
                self.client = None

    async def run_reminders(self) -> None:
        """Send deadline reminders at the configured hour tiers.

        Tier ordering: longest first (e.g. 72h before D-day, then 24h, then
        3h). Each item tracks which tiers have already fired in its
        reminders_sent JSONB column to avoid double-sending.
        """
        if self.client is not None:
            await self._poll_reminders()
            await self.check_unsubmitted()
            return

        async with aiohttp.ClientSession() as session:
            # Reminders don't actually call Canvas, but having a session
            # ensures _send_reminder can use it for notifications uniformly.
            self.client = CanvasClient(
                self.api_url,
                self.api_token,
                session,
                error_notifier=self.error_notifier,
            )
            try:
                await self._poll_reminders()
                await self.check_unsubmitted()
            finally:
                self.client = None

    async def _poll(self) -> None:
        try:
            courses = await self.client.get_active_courses()
        except Exception as e:
            logger.error(f"[CANVAS] Failed to list active courses: {e}")
            return

        if not courses:
            logger.info("[CANVAS] No active courses found.")
            return

        course_by_id = {c.id: c for c in courses}
        logger.info(f"[CANVAS] Polling {len(courses)} active courses.")

        for course in courses:
            await self._process_assignments(course)
            await self._process_submissions(course)

        await self._process_announcements(course_by_id)

    # ---------- Assignments ----------

    async def _process_assignments(self, course: CanvasCourse) -> None:
        try:
            assignments = await self.client.get_assignments(course.id)
        except Exception as e:
            logger.error(
                f"[CANVAS] get_assignments failed for course {course.id}: {e}"
            )
            return

        for assignment in assignments:
            assignment.course_name = course.name
            existing = self.repo.get_item(assignment.id, "assignment")

            if existing is None:
                self._upsert_assignment(assignment)
                await self._dispatch(
                    CanvasEvent(KIND_NEW_ASSIGNMENT, course, assignment)
                )
                continue

            changes = await self._diff_assignment(existing, assignment)
            if not changes:
                # Touch DB to refresh updated_at / has_submitted etc., but
                # do not fire a notification.
                self._upsert_assignment(assignment)
                continue

            self._upsert_assignment(assignment)
            if "due_at" in changes:
                await self._dispatch(
                    CanvasEvent(
                        KIND_DUE_DATE_CHANGED,
                        course,
                        assignment,
                        db_record=existing,
                        changes=changes,
                    )
                )
            else:
                await self._dispatch(
                    CanvasEvent(
                        KIND_ASSIGNMENT_MODIFIED,
                        course,
                        assignment,
                        db_record=existing,
                        changes=changes,
                    )
                )

    async def _diff_assignment(
        self, existing: Dict[str, Any], item: CanvasAssignment
    ) -> Dict[str, Any]:
        """Return per-field {old, new} for fields that materially changed.

        Short-circuits when Canvas's own canvas_updated_at hasn't moved —
        Canvas bumps that timestamp on every meaningful edit, so when it
        is unchanged we know nothing material changed regardless of what
        the volatile body URLs / due-at string formatting say.
        """
        changes: Dict[str, Any] = {}

        if self._canvas_updated_unchanged(
            existing.get("canvas_updated_at"), item.updated_at
        ):
            return changes

        if (existing.get("title") or "") != (item.name or ""):
            changes["title"] = {"old": existing.get("title"), "new": item.name}

        old_body_norm = self._normalize_body(existing.get("body"))
        new_body_norm = self._normalize_body(item.description)
        if old_body_norm != new_body_norm:
            changes["body"] = {
                "old": existing.get("body") or "",
                "new": item.description or "",
                "summary": await self._summarize_body_diff(
                    existing.get("body") or "", item.description or ""
                ),
            }

        if not self._datetimes_equal(existing.get("due_at"), item.due_at):
            changes["due_at"] = {"old": existing.get("due_at"), "new": item.due_at}

        old_points = self._number_value(existing.get("points_possible"))
        new_points = self._number_value(item.points_possible)
        if old_points != new_points:
            changes["points_possible"] = {
                "old": existing.get("points_possible"),
                "new": item.points_possible,
            }

        old_submission_types = self._normalize_submission_types(
            existing.get("submission_types")
        )
        new_submission_types = self._normalize_submission_types(item.submission_types)
        if old_submission_types != new_submission_types:
            changes["submission_types"] = {
                "old": old_submission_types,
                "new": new_submission_types,
            }

        return changes

    async def _summarize_body_diff(self, old_body: str, new_body: str) -> str:
        if self.ai_service is None or not hasattr(self.ai_service, "get_diff_summary"):
            return "본문이 수정되었습니다."
        try:
            summary = await self.ai_service.get_diff_summary(old_body, new_body)
        except Exception as e:
            logger.error(f"[CANVAS] assignment body diff summary failed: {e}")
            return "본문이 수정되었습니다."
        if not summary or summary in {"NO_CHANGE", "변동사항 없음"}:
            return "본문이 수정되었습니다."
        return summary

    # ---------- Normalization helpers (used by _diff_assignment) ----------

    # Canvas embeds verifier tokens / instfs ids in inline file URLs that
    # change every fetch; strip them before hashing/comparing bodies so
    # we don't see a "modification" on every poll.
    _VOLATILE_QUERY_KEYS = re.compile(
        r"[?&](?:verifier|download_frd|wrap|instfs_id|sf_verifier)=[^&\"'\s]+",
        re.IGNORECASE,
    )

    @classmethod
    def _normalize_body(cls, value: Optional[str]) -> str:
        """Strip volatile per-fetch query tokens and collapse whitespace."""
        if not value:
            return ""
        cleaned = cls._VOLATILE_QUERY_KEYS.sub("", value)
        # Squash runs of whitespace so trivial reformatting is invisible
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _canvas_updated_unchanged(old: Any, new: Any) -> bool:
        """True iff Canvas's own canvas_updated_at is present on both sides
        and equal after normalization."""
        if not old or not new:
            return False
        return CanvasService._datetimes_equal(old, new)

    @staticmethod
    def _datetimes_equal(a: Any, b: Any) -> bool:
        """Compare two datetime-ish values (ISO string, datetime, None).

        Both None → equal. One None and the other empty string → equal.
        Otherwise parse to UTC and compare instants.
        """
        a_norm = CanvasService._to_utc(a)
        b_norm = CanvasService._to_utc(b)
        return a_norm == b_norm

    @staticmethod
    def _to_utc(value: Any) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _number_value(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_submission_types(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = [v.strip() for v in value.split(",") if v.strip()]
            value = parsed
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value]
        return [str(value)]

    def _upsert_assignment(self, item: CanvasAssignment) -> Dict[str, Any]:
        payload = {
            "canvas_id": item.id,
            "item_type": "assignment",
            "course_id": item.course_id,
            "course_name": item.course_name,
            "title": item.name,
            "body": item.description,
            "content_hash": self._content_hash(
                item.name, self._normalize_body(item.description)
            ),
            "due_at": item.due_at,
            "points_possible": item.points_possible,
            "submission_types": item.submission_types,
            "has_submitted": item.has_submitted_submissions,
            "html_url": item.html_url,
            "canvas_created_at": item.created_at,
            "canvas_updated_at": item.updated_at,
        }
        return self.repo.upsert_item(payload)

    # ---------- Announcements ----------

    async def _process_announcements(
        self, course_by_id: Dict[int, CanvasCourse]
    ) -> None:
        if not course_by_id:
            return
        for course in course_by_id.values():
            try:
                announcements = await self.client.get_announcements([course.id])
            except Exception as e:
                logger.error(
                    f"[CANVAS] get_announcements failed for course {course.id}: {e}"
                )
                continue

            for ann in announcements:
                ann.course_name = course.name
                if not ann.course_id:
                    ann.course_id = course.id

                existing = self.repo.get_item(ann.id, "announcement")
                if existing is not None:
                    # Refresh DB but do not re-notify on subsequent passes
                    self._upsert_announcement(ann)
                    continue

                self._upsert_announcement(ann)
                await self._dispatch(
                    CanvasEvent(KIND_NEW_ANNOUNCEMENT, course, ann)
                )

    def _upsert_announcement(self, item: CanvasAnnouncement) -> Dict[str, Any]:
        payload = {
            "canvas_id": item.id,
            "item_type": "announcement",
            "course_id": item.course_id,
            "course_name": item.course_name,
            "title": item.title,
            "body": item.message,
            "content_hash": self._content_hash(item.title, item.message),
            "html_url": item.html_url,
            "canvas_created_at": item.created_at,
        }
        return self.repo.upsert_item(payload)

    # ---------- Submissions / Grades ----------

    async def _process_submissions(self, course: CanvasCourse) -> None:
        try:
            submissions = await self.client.get_submissions(course.id)
        except Exception as e:
            logger.error(
                f"[CANVAS] get_submissions failed for course {course.id}: {e}"
            )
            return

        for sub in submissions:
            sub.course_id = course.id
            existing = self.repo.get_item(sub.id, "submission")
            new_score = sub.score

            self._upsert_submission(sub)

            is_newly_graded = (
                sub.workflow_state == "graded"
                and new_score is not None
                and (existing is None or existing.get("score") is None)
            )
            if is_newly_graded:
                await self._dispatch(
                    CanvasEvent(
                        KIND_GRADE_REGISTERED, course, sub, db_record=existing
                    )
                )

    def _upsert_submission(self, item: CanvasSubmission) -> Dict[str, Any]:
        payload = {
            "canvas_id": item.id,
            "item_type": "submission",
            "course_id": item.course_id,
            "course_name": "",
            "title": f"submission:{item.assignment_id}",
            "body": "",
            "content_hash": "",
            "assignment_canvas_id": item.assignment_id,
            "score": item.score,
            "grade": item.grade,
            "workflow_state": item.workflow_state,
            "canvas_updated_at": item.graded_at,
        }
        return self.repo.upsert_item(payload)

    # ---------- Dispatch ----------

    async def _dispatch(self, event: CanvasEvent) -> None:
        """Format the event (plain + html) and broadcast via NotificationService."""
        text_plain = self._format_event(event, html=False)
        text_html = self._format_event(event, html=True)
        identifier = getattr(event.item, "id", "?")
        logger.info(
            f"[CANVAS] event={event.kind} course={event.course.name!r} "
            f"item_id={identifier} changes={list((event.changes or {}).keys())}"
        )
        if not text_plain or self.notifier is None:
            return
        try:
            attachment_payloads = await self._build_attachment_payloads(event.item)
            await self.notifier.send_canvas_message(
                self.client.session,
                text_plain,
                text_html=text_html,
                event_kind=event.kind,
                attachment_payloads=attachment_payloads,
            )
        except Exception as e:
            logger.error(f"[CANVAS] notification send failed: {e}")

    def _format_event(self, event: CanvasEvent, html: bool = False) -> str:
        """Map a CanvasEvent to its notification text via canvas_formatter."""
        if event.kind == KIND_NEW_ASSIGNMENT:
            return canvas_formatter.format_new_assignment(event.item, html=html)
        if event.kind in (KIND_ASSIGNMENT_MODIFIED, KIND_DUE_DATE_CHANGED):
            return canvas_formatter.format_modified_assignment(
                event.item, event.changes or {}, html=html
            )
        if event.kind == KIND_NEW_ANNOUNCEMENT:
            return canvas_formatter.format_new_announcement(event.item, html=html)
        if event.kind == KIND_GRADE_REGISTERED:
            return canvas_formatter.format_grade_notification(
                event.item, course_name=event.course.name, html=html
            )
        if event.kind == KIND_UNSUBMITTED_WARNING:
            return canvas_formatter.format_unsubmitted_warning(
                event.item, html=html
            )
        return ""

    # ---------- Attachments ----------

    async def _build_attachment_payloads(
        self, item: Any
    ) -> List[Dict[str, Any]]:
        """Download Canvas attachments and build per-file payloads.

        Each payload entry:
            {
                "source_filename": str,   # human-readable name shown in captions
                "source_size": int,       # bytes; used for "(150KB)" labels
                "original_data": bytes,   # raw file for the [원본] reply
                "preview_images": [
                    {"filename": str, "data": bytes}, ...
                ],
            }

        The grouping (one entry per source file) lets the notifier add a
        "📑 [미리보기] {name} (N/M)" caption per chunk and a separate
        "📎 [원본] {name} (size)" reply for the original file.
        """
        attachments = getattr(item, "attachments", None) or []
        if not attachments or self.file_service is None or self.client is None:
            return []

        payloads: List[Dict[str, Any]] = []
        total_previews = 0
        max_previews = max(1, settings.MAX_PREVIEWS)

        for att in attachments:
            if total_previews >= max_previews:
                break
            file_data = await self._download_canvas_attachment(att)
            if not file_data:
                continue

            filename = self._canvas_attachment_filename(att)
            content_type = (att.content_type or "").lower()

            preview_images: List[Dict[str, Any]] = []
            if content_type.startswith("image/") or self.file_service.is_image(
                filename
            ):
                preview_images.append(
                    {
                        "filename": self._image_preview_filename(filename),
                        "data": self.file_service.image_handler.optimize_for_telegram(
                            file_data
                        ),
                    }
                )
            else:
                generated = self.file_service.generate_preview_images(
                    file_data,
                    filename,
                    max_pages=max_previews - total_previews,
                )
                stem = os.path.splitext(filename)[0] or "canvas_preview"
                for idx, image_data in enumerate(generated):
                    preview_images.append(
                        {
                            "filename": f"{stem}_preview_{idx + 1}.jpg",
                            "data": image_data,
                        }
                    )
                    if total_previews + len(preview_images) >= max_previews:
                        break

            total_previews += len(preview_images)
            payloads.append(
                {
                    "source_filename": filename,
                    "source_size": len(file_data),
                    "original_data": file_data,
                    "preview_images": preview_images,
                }
            )

        return payloads

    async def _download_canvas_attachment(
        self, attachment: CanvasAttachment
    ) -> Optional[bytes]:
        """Download a Canvas attachment with Bearer auth."""
        if not attachment.url:
            return None
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "*/*",
        }
        try:
            data = await self.file_service.download_file(
                self.client.session,
                attachment.url,
                headers=headers,
            )
            if data is None:
                logger.warning(
                    f"[CANVAS] Failed to download attachment {attachment.display_name!r}"
                )
            return data
        except Exception as e:
            logger.error(
                f"[CANVAS] Attachment download failed for {attachment.display_name!r}: {e}"
            )
            return None

    def _canvas_attachment_filename(self, attachment: CanvasAttachment) -> str:
        """Choose a filename that FileService can route by extension."""
        name = attachment.display_name or self.file_service.extract_filename(
            attachment.url
        )
        name = self.file_service.sanitize_filename(name or "canvas_attachment")
        if "." in name:
            return name

        ext = self._extension_from_content_type(attachment.content_type)
        return f"{name}.{ext}" if ext else name

    @staticmethod
    def _extension_from_content_type(content_type: str) -> str:
        content_type = (content_type or "").split(";", 1)[0].lower()
        mapping = {
            "application/pdf": "pdf",
            "application/vnd.ms-excel": "xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/msword": "doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.ms-powerpoint": "ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
        }
        return mapping.get(content_type, "")

    @staticmethod
    def _image_preview_filename(filename: str) -> str:
        stem, ext = os.path.splitext(filename)
        return f"{stem or 'canvas_image'}{ext or '.jpg'}"

    # ---------- Deadline reminders ----------

    async def _poll_reminders(self) -> None:
        """For each upcoming assignment, send the largest applicable
        reminder tier that hasn't fired yet."""
        tiers = sorted(constants.CANVAS_REMINDER_HOURS, reverse=True)
        max_window = max(tiers) if tiers else 0
        if max_window <= 0:
            return

        rows = self.repo.get_upcoming_deadlines(hours=max_window)
        now = datetime.now(timezone.utc)

        for row in rows:
            if row.get("has_submitted"):
                continue
            due_at_str = row.get("due_at")
            if not due_at_str:
                continue
            try:
                due_at = datetime.fromisoformat(due_at_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            hours_left = (due_at - now).total_seconds() / 3600.0
            if hours_left <= 0:
                continue

            tier = self._pick_reminder_tier(hours_left, tiers, row.get("reminders_sent") or [])
            if tier is None:
                continue

            await self._send_reminder(row, tier)
            self.repo.mark_reminder_sent(row["id"], tier)

    @staticmethod
    def _pick_reminder_tier(
        hours_left: float, tiers: List[int], already_sent: List[int]
    ) -> Optional[int]:
        """Smallest tier >= hours_left that hasn't been sent yet."""
        for tier in tiers:
            if hours_left <= tier and tier not in already_sent:
                return tier
        return None

    async def _send_reminder(self, row: Dict[str, Any], tier_hours: int) -> None:
        """Build a CanvasAssignment from a DB row and dispatch the reminder."""
        try:
            item = CanvasAssignment(
                id=row["canvas_id"],
                course_id=row["course_id"],
                course_name=row.get("course_name") or "",
                name=row.get("title") or "",
                description=row.get("body") or "",
                due_at=row.get("due_at"),
                points_possible=row.get("points_possible"),
                has_submitted_submissions=bool(row.get("has_submitted")),
                html_url=row.get("html_url") or "",
            )
        except Exception as e:
            logger.error(f"[CANVAS] reminder build failed for row {row.get('id')}: {e}")
            return

        text_plain = canvas_formatter.format_deadline_reminder(
            item, hours_left=tier_hours, html=False
        )
        text_html = canvas_formatter.format_deadline_reminder(
            item, hours_left=tier_hours, html=True
        )
        logger.info(
            f"[CANVAS] reminder tier={tier_hours}h item_id={item.id} "
            f"title={item.name!r}"
        )
        if self.notifier is None or not text_plain:
            return
        try:
            await self.notifier.send_canvas_message(
                self.client.session,
                text_plain,
                text_html=text_html,
                event_kind="deadline_reminder",
            )
        except Exception as e:
            logger.error(f"[CANVAS] reminder send failed: {e}")

    async def check_unsubmitted(self) -> None:
        """Warn once when an assignment is still unsubmitted just after due."""
        rows = self.repo.get_recent_overdue_unsubmitted_assignments(hours_after_due=1)
        for row in rows:
            if row.get("has_submitted"):
                continue
            try:
                item = CanvasAssignment(
                    id=row["canvas_id"],
                    course_id=row["course_id"],
                    course_name=row.get("course_name") or "",
                    name=row.get("title") or "",
                    description=row.get("body") or "",
                    due_at=row.get("due_at"),
                    points_possible=row.get("points_possible"),
                    has_submitted_submissions=False,
                    html_url=row.get("html_url") or "",
                )
            except Exception as e:
                logger.error(
                    f"[CANVAS] unsubmitted warning build failed for row {row.get('id')}: {e}"
                )
                continue

            text_plain = canvas_formatter.format_unsubmitted_warning(item, html=False)
            text_html = canvas_formatter.format_unsubmitted_warning(item, html=True)
            logger.info(
                f"[CANVAS] unsubmitted warning item_id={item.id} title={item.name!r}"
            )
            if self.notifier is None or not text_plain:
                continue
            try:
                await self.notifier.send_canvas_message(
                    self.client.session,
                    text_plain,
                    text_html=text_html,
                    event_kind=KIND_UNSUBMITTED_WARNING,
                )
                self.repo.mark_unsubmitted_alerted(row["id"])
            except Exception as e:
                logger.error(f"[CANVAS] unsubmitted warning send failed: {e}")

    # ---------- Helpers ----------

    @staticmethod
    def _content_hash(*parts: Optional[str]) -> str:
        h = hashlib.sha256()
        for part in parts:
            h.update((part or "").encode("utf-8", errors="replace"))
            h.update(b"\x00")
        return h.hexdigest()
