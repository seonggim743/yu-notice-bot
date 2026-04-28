"""Repository for Canvas LMS items.

Mirrors the surface area of NoticeRepository: a thin wrapper over Supabase
that translates between dict payloads and the canvas_items table /
upsert_canvas_item RPC.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from supabase import Client

from core.database import Database
from core.logger import get_logger

logger = get_logger(__name__)


class CanvasRepository:
    """CRUD over canvas_items + helpers for reminder/deadline queries."""

    def __init__(self, db: Optional[Client] = None):
        self.db: Client = db or Database.get_client()

    # ---------- Upsert / read ----------

    def upsert_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert via the upsert_canvas_item RPC.

        Args:
            item: payload matching the RPC signature. Required keys:
                  canvas_id, item_type, course_id, title.

        Returns:
            {"id": uuid str, "was_inserted": bool} on success, {} on failure.
        """
        try:
            payload = self._prepare_payload(item)
            response = self.db.rpc("upsert_canvas_item", {"p_item": payload}).execute()
            if response.data:
                row = response.data[0]
                return {
                    "id": row.get("item_id"),
                    "was_inserted": bool(row.get("was_inserted")),
                }
            return {}
        except Exception as e:
            logger.error(
                f"[CANVAS_REPO] Upsert failed for "
                f"{item.get('item_type')}:{item.get('canvas_id')}: {e}"
            )
            return {}

    def get_item(
        self, canvas_id: int, item_type: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single canvas_item by (canvas_id, item_type)."""
        try:
            response = (
                self.db.table("canvas_items")
                .select("*")
                .eq("canvas_id", canvas_id)
                .eq("item_type", item_type)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(
                f"[CANVAS_REPO] get_item failed for "
                f"{item_type}:{canvas_id}: {e}"
            )
            return None

    def update_message_ids(
        self, item_id: str, platform: str, message_id: Any
    ) -> None:
        """Merge a single platform→message_id pair into message_ids JSONB."""
        try:
            current = (
                self.db.table("canvas_items")
                .select("message_ids")
                .eq("id", item_id)
                .limit(1)
                .execute()
            )
            existing = (current.data[0].get("message_ids") if current.data else {}) or {}
            existing[platform] = message_id
            self.db.table("canvas_items").update(
                {"message_ids": existing}
            ).eq("id", item_id).execute()
        except Exception as e:
            logger.error(f"[CANVAS_REPO] update_message_ids failed: {e}")

    def update_discord_thread_id(self, item_id: str, thread_id: str) -> None:
        try:
            self.db.table("canvas_items").update(
                {"discord_thread_id": thread_id}
            ).eq("id", item_id).execute()
        except Exception as e:
            logger.error(f"[CANVAS_REPO] update_discord_thread_id failed: {e}")

    def mark_reminder_sent(self, item_id: str, hours_before: int) -> None:
        """Append `hours_before` to reminders_sent JSONB array (idempotent)."""
        try:
            current = (
                self.db.table("canvas_items")
                .select("reminders_sent")
                .eq("id", item_id)
                .limit(1)
                .execute()
            )
            sent = list((current.data[0].get("reminders_sent") or []) if current.data else [])
            if hours_before not in sent:
                sent.append(hours_before)
            self.db.table("canvas_items").update(
                {"reminders_sent": sent}
            ).eq("id", item_id).execute()
        except Exception as e:
            logger.error(f"[CANVAS_REPO] mark_reminder_sent failed: {e}")

    # ---------- Reminder / deadline queries ----------

    def get_upcoming_deadlines(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Return assignments whose due_at falls within the next `hours` window."""
        try:
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=hours)
            response = (
                self.db.table("canvas_items")
                .select("*")
                .eq("item_type", "assignment")
                .gte("due_at", now.isoformat())
                .lte("due_at", cutoff.isoformat())
                .order("due_at")
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[CANVAS_REPO] get_upcoming_deadlines failed: {e}")
            return []

    def get_unsubmitted_assignments(self) -> List[Dict[str, Any]]:
        """Assignments that are still future and have not been submitted."""
        try:
            now = datetime.now(timezone.utc)
            response = (
                self.db.table("canvas_items")
                .select("*")
                .eq("item_type", "assignment")
                .eq("has_submitted", False)
                .gte("due_at", now.isoformat())
                .order("due_at")
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[CANVAS_REPO] get_unsubmitted_assignments failed: {e}")
            return []

    # ---------- Internal ----------

    @staticmethod
    def _prepare_payload(item: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize types so PostgREST can serialize the RPC payload.

        - JSONB dict/list fields are stringified for fields the RPC re-parses
        - submission_types stays a JSON array for jsonb_array_elements_text()
        - None values are passed through (RPC handles NULL casts)
        """
        payload = dict(item)
        for key in ("message_ids", "reminders_sent"):
            if key in payload and payload[key] is not None and not isinstance(
                payload[key], str
            ):
                payload[key] = json.dumps(payload[key])
        return payload
