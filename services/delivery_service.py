"""Durable notification outbox orchestration."""

from typing import Any, Dict, List, Optional

import aiohttp

from core.error_notifier import ErrorNotifier, ErrorSeverity, get_error_notifier
from core.logger import get_logger
from models.notice import Notice
from repositories.delivery_repo import DeliveryRepository

logger = get_logger(__name__)


class DeliveryService:
    def __init__(
        self,
        notifier,
        repo: Optional[DeliveryRepository] = None,
        error_notifier: Optional[ErrorNotifier] = None,
    ):
        self.notifier = notifier
        self.repo = repo or DeliveryRepository()
        self.error_notifier = error_notifier or get_error_notifier()

    def prepare_deliveries(
        self,
        notice: Notice,
        old_notice: Optional[Notice],
        *,
        event_type: str,
        modified_reason: str = "",
        changes: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build one JSON-safe delivery payload per currently routable channel."""
        snapshot = notice.model_dump(mode="json", exclude={"embedding"})
        deliveries = []
        for channel in self.notifier.eligible_channels(notice.site_key):
            existing_id = self._existing_id(channel, old_notice)
            deliveries.append(
                {
                    "channel": channel,
                    "event_type": event_type,
                    "payload": {
                        "notice": snapshot,
                        "is_new": existing_id is None,
                        "modified_reason": modified_reason,
                        "changes": changes,
                        "existing_message_id": existing_id,
                    },
                }
            )
        return deliveries

    async def dispatch_due(
        self,
        session: aiohttp.ClientSession,
        *,
        site_key: Optional[str] = None,
        notice_id: Optional[str] = None,
        notice_override: Optional[Notice] = None,
    ) -> int:
        """Attempt all due deliveries in scope and return the success count."""
        rows = self.repo.get_due_deliveries(
            site_key=site_key,
            notice_id=notice_id,
        )
        delivered = 0
        for row in rows:
            payload = row.get("payload") or {}
            channel = row["channel"]
            if channel not in self.notifier.eligible_channels(row["site_key"]):
                logger.warning(
                    f"[DELIVERY] Channel {channel} is not currently routable for "
                    f"{row['site_key']}; leaving delivery pending."
                )
                continue

            notice = notice_override or Notice(**payload["notice"])
            result = await self.notifier.deliver_notice(
                channel=channel,
                session=session,
                notice=notice,
                is_new=bool(payload.get("is_new")),
                modified_reason=payload.get("modified_reason") or "",
                existing_message_id=payload.get("existing_message_id"),
                changes=payload.get("changes"),
            )
            if result.success:
                self.repo.complete_delivery(row["id"], result.external_id)
                delivered += 1
                continue

            attempt_count, should_alert = self.repo.record_failure(
                row, result.error or "channel returned no message ID"
            )
            logger.warning(
                f"[DELIVERY] {channel} delivery failed for {row['site_key']} "
                f"(attempt {attempt_count}); queued for retry."
            )
            if should_alert:
                try:
                    await self.error_notifier.send_critical_error(
                        "Notification delivery failed three or more times",
                        context={
                            "delivery_id": row["id"],
                            "site_key": row["site_key"],
                            "channel": channel,
                            "attempt_count": attempt_count,
                            "last_error": result.error,
                        },
                        severity=ErrorSeverity.WARNING,
                    )
                except Exception as exc:
                    # Alert transport is not part of notice delivery success.
                    logger.error(
                        f"[DELIVERY] Failed to send developer warning: {exc}"
                    )
                self.repo.mark_alerted(row["id"])
        return delivered

    def purge_completed(self, retention_days: int = 30) -> None:
        self.repo.purge_completed(retention_days=retention_days)

    @staticmethod
    def _existing_id(channel: str, old_notice: Optional[Notice]) -> Optional[Any]:
        if old_notice is None:
            return None
        if channel == "telegram":
            return (old_notice.message_ids or {}).get("telegram")
        if channel == "discord":
            return old_notice.discord_thread_id
        return (old_notice.message_ids or {}).get(channel)
