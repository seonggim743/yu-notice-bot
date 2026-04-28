"""Canvas LMS orchestrator.

Polls Canvas for new/modified assignments, announcements, and graded
submissions; persists state through CanvasRepository; and emits change
events via `_dispatch_notification`. The dispatch hook is intentionally
thin so that the formatter / channel wiring (added in the next commit)
can be swapped in without touching the change-detection logic here.
"""
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
    CanvasCourse,
    CanvasSubmission,
)
from repositories.canvas_repo import CanvasRepository
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
        client: CanvasClient,
        repo: CanvasRepository,
        notifier=None,
        file_service=None,
        ai_service=None,
    ):
        self.client = client
        self.repo = repo
        self.notifier = notifier
        self.file_service = file_service
        self.ai_service = ai_service

    # ---------- Top-level run ----------

    async def run(self) -> None:
        """One full polling pass."""
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
        try:
            announcements = await self.client.get_announcements(
                list(course_by_id.keys())
            )
        except Exception as e:
            logger.error(f"[CANVAS] get_announcements failed: {e}")
            return

        for ann in announcements:
            course = course_by_id.get(ann.course_id)
            if course is None:
                continue
            ann.course_name = course.name

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

    # ---------- Dispatch hook (commit 5 wires real formatter / sender) ----------

    async def _dispatch(self, event: CanvasEvent) -> None:
        """Emit a Canvas event. Default implementation logs only — the
        notification path is wired in the next commit via the formatter
        module + NotificationService.send_canvas extension."""
        identifier = getattr(event.item, "id", "?")
        title = getattr(event.item, "name", None) or getattr(
            event.item, "title", ""
        )
        logger.info(
            f"[CANVAS] event={event.kind} course={event.course.name!r} "
            f"item_id={identifier} title={title!r} "
            f"changes={list((event.changes or {}).keys())}"
        )

    # ---------- Helpers ----------

    @staticmethod
    def _content_hash(*parts: Optional[str]) -> str:
        h = hashlib.sha256()
        for part in parts:
            h.update((part or "").encode("utf-8", errors="replace"))
            h.update(b"\x00")
        return h.hexdigest()
