"""Persistence operations for durable per-channel notification deliveries."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from supabase import Client

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger

logger = get_logger(__name__)


class DeliveryRepository:
    RETRY_MINUTES = (30, 60, 120, 240, 360)

    def __init__(self, db: Optional[Client] = None):
        self.db: Client = db or Database.get_client()

    def get_due_deliveries(
        self,
        *,
        site_key: Optional[str] = None,
        notice_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return pending deliveries whose retry time has arrived."""
        try:
            query = (
                self.db.table("notification_deliveries")
                .select("*")
                .eq("status", "pending")
                .lte("next_attempt_at", datetime.now(timezone.utc).isoformat())
            )
            if site_key:
                query = query.eq("site_key", site_key)
            if notice_id:
                query = query.eq("notice_id", notice_id)
            response = query.order("next_attempt_at").limit(limit).execute()
            return response.data or []
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch pending notification deliveries",
                {"site_key": site_key, "notice_id": notice_id, "error": str(exc)},
            ) from exc

    def complete_delivery(self, delivery_id: str, external_id: Any) -> None:
        """Atomically mark a delivery sent and persist its platform ID."""
        try:
            response = self.db.rpc(
                "complete_notification_delivery",
                {
                    "p_delivery_id": delivery_id,
                    "p_external_message_id": external_id,
                },
            ).execute()
            if response.data is False:
                raise RuntimeError("completion RPC did not update a pending delivery")
        except Exception as exc:
            raise DatabaseException(
                "Failed to complete notification delivery",
                {"delivery_id": delivery_id, "error": str(exc)},
            ) from exc

    def record_failure(
        self, delivery: Dict[str, Any], error: str
    ) -> Tuple[int, bool]:
        """Schedule the next retry and report whether a one-time alert is due."""
        attempt_count = int(delivery.get("attempt_count") or 0) + 1
        delay_index = min(attempt_count - 1, len(self.RETRY_MINUTES) - 1)
        retry_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.RETRY_MINUTES[delay_index]
        )
        try:
            self.db.table("notification_deliveries").update(
                {
                    "attempt_count": attempt_count,
                    "next_attempt_at": retry_at.isoformat(),
                    "last_error": (error or "unknown delivery failure")[:2000],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", delivery["id"]).eq("status", "pending").execute()
        except Exception as exc:
            raise DatabaseException(
                "Failed to record notification delivery failure",
                {"delivery_id": delivery.get("id"), "error": str(exc)},
            ) from exc
        should_alert = attempt_count >= 3 and not delivery.get("alerted_at")
        return attempt_count, should_alert

    def mark_alerted(self, delivery_id: str) -> None:
        try:
            self.db.table("notification_deliveries").update(
                {"alerted_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", delivery_id).execute()
        except Exception as exc:
            raise DatabaseException(
                "Failed to mark notification delivery alert",
                {"delivery_id": delivery_id, "error": str(exc)},
            ) from exc

    def purge_completed(self, retention_days: int = 30) -> None:
        """Delete old terminal rows while retaining pending deliveries forever."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        try:
            (
                self.db.table("notification_deliveries")
                .delete()
                .in_("status", ["sent", "superseded"])
                .lt("updated_at", cutoff.isoformat())
                .execute()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to purge completed notification deliveries",
                {"retention_days": retention_days, "error": str(exc)},
            ) from exc
