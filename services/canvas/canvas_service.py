"""Canvas LMS orchestrator.

Polls Canvas for new/modified assignments, announcements, and graded
submissions; persists state through CanvasRepository; emits change
events via `_dispatch`; and runs deadline reminders at the tiers
configured in core.constants.CANVAS_REMINDER_HOURS.
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from core import constants
from core.error_notifier import ErrorNotifier
from core.logger import get_logger
from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
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

            changes = self._diff_assignment(existing, assignment)
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

    def _diff_assignment(
        self, existing: Dict[str, Any], item: CanvasAssignment
    ) -> Dict[str, Any]:
        """Return per-field {old, new} for fields that materially changed."""
        changes: Dict[str, Any] = {}

        # canvas_updated_at: if Canvas's own timestamp moved, something changed.
        old_updated = existing.get("canvas_updated_at")
        if old_updated and item.updated_at and old_updated != item.updated_at:
            # We still need to identify *what* changed
            if (existing.get("title") or "") != item.name:
                changes["title"] = {"old": existing.get("title"), "new": item.name}

            new_hash = self._content_hash(item.name, item.description)
            if (existing.get("content_hash") or "") != new_hash:
                changes["body"] = {
                    "old": existing.get("content_hash"),
                    "new": new_hash,
                }

        # Due date changes are a separate event class — always check explicitly.
        old_due = existing.get("due_at")
        if old_due != item.due_at and (old_due or item.due_at):
            changes["due_at"] = {"old": old_due, "new": item.due_at}

        return changes

    def _upsert_assignment(self, item: CanvasAssignment) -> Dict[str, Any]:
        payload = {
            "canvas_id": item.id,
            "item_type": "assignment",
            "course_id": item.course_id,
            "course_name": item.course_name,
            "title": item.name,
            "body": item.description,
            "content_hash": self._content_hash(item.name, item.description),
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
        """Format the event and broadcast via NotificationService."""
        text = self._format_event(event)
        identifier = getattr(event.item, "id", "?")
        logger.info(
            f"[CANVAS] event={event.kind} course={event.course.name!r} "
            f"item_id={identifier} changes={list((event.changes or {}).keys())}"
        )
        if not text or self.notifier is None:
            return
        try:
            await self.notifier.send_canvas_message(
                self.client.session,
                text,
                event_kind=event.kind,
            )
        except Exception as e:
            logger.error(f"[CANVAS] notification send failed: {e}")

    def _format_event(self, event: CanvasEvent) -> str:
        """Map a CanvasEvent to its notification text via canvas_formatter."""
        if event.kind == KIND_NEW_ASSIGNMENT:
            return canvas_formatter.format_new_assignment(event.item)
        if event.kind in (KIND_ASSIGNMENT_MODIFIED, KIND_DUE_DATE_CHANGED):
            return canvas_formatter.format_modified_assignment(
                event.item, event.changes or {}
            )
        if event.kind == KIND_NEW_ANNOUNCEMENT:
            return canvas_formatter.format_new_announcement(event.item)
        if event.kind == KIND_GRADE_REGISTERED:
            return canvas_formatter.format_grade_notification(
                event.item, course_name=event.course.name
            )
        return ""

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

        text = canvas_formatter.format_deadline_reminder(item, hours_left=tier_hours)
        logger.info(
            f"[CANVAS] reminder tier={tier_hours}h item_id={item.id} "
            f"title={item.name!r}"
        )
        if self.notifier is None or not text:
            return
        try:
            await self.notifier.send_canvas_message(
                self.client.session,
                text,
                event_kind="deadline_reminder",
            )
        except Exception as e:
            logger.error(f"[CANVAS] reminder send failed: {e}")

    # ---------- Helpers ----------

    @staticmethod
    def _content_hash(*parts: Optional[str]) -> str:
        h = hashlib.sha256()
        for part in parts:
            h.update((part or "").encode("utf-8", errors="replace"))
            h.update(b"\x00")
        return h.hexdigest()
