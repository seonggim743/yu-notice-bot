from unittest.mock import AsyncMock, MagicMock

import pytest

from models.delivery import DeliveryResult
from models.notice import Notice
from services.delivery_service import DeliveryService


def _notice(**overrides):
    values = {
        "site_key": "yu_news",
        "article_id": "42",
        "title": "Notice",
        "content": "A sufficiently long notice body",
        "url": "https://example.com/42",
        "content_hash": "hash-v2",
    }
    values.update(overrides)
    return Notice(**values)


def test_prepare_deliveries_snapshots_only_eligible_channels_and_existing_ids():
    notifier = MagicMock()
    notifier.eligible_channels.return_value = ["telegram", "discord"]
    service = DeliveryService(
        notifier,
        repo=MagicMock(),
        error_notifier=MagicMock(),
    )
    old_notice = _notice(
        content_hash="hash-v1",
        message_ids={"telegram": 123},
        discord_thread_id="thread-9",
    )

    rows = service.prepare_deliveries(
        _notice(),
        old_notice,
        event_type="modified",
        modified_reason="content changed",
        changes={"content": "summary"},
    )

    assert [row["channel"] for row in rows] == ["telegram", "discord"]
    assert rows[0]["payload"]["existing_message_id"] == 123
    assert rows[1]["payload"]["existing_message_id"] == "thread-9"
    assert all(row["payload"]["is_new"] is False for row in rows)
    assert rows[0]["payload"]["notice"]["content_hash"] == "hash-v2"
    assert "embedding" not in rows[0]["payload"]["notice"]


@pytest.mark.asyncio
async def test_partial_success_completes_only_the_successful_channel():
    notifier = MagicMock()
    notifier.eligible_channels.return_value = ["telegram", "discord"]
    notifier.deliver_notice = AsyncMock(
        side_effect=[
            DeliveryResult("telegram", True, external_id=777),
            DeliveryResult("discord", False, error="timeout"),
        ]
    )
    payload = {
        "notice": _notice().model_dump(mode="json"),
        "is_new": True,
        "modified_reason": "",
        "changes": None,
        "existing_message_id": None,
    }
    repo = MagicMock()
    repo.get_due_deliveries.return_value = [
        {"id": "tg", "site_key": "yu_news", "channel": "telegram", "payload": payload},
        {"id": "dc", "site_key": "yu_news", "channel": "discord", "payload": payload},
    ]
    repo.record_failure.return_value = (1, False)
    service = DeliveryService(notifier, repo=repo, error_notifier=MagicMock())

    delivered = await service.dispatch_due(MagicMock())

    assert delivered == 1
    repo.complete_delivery.assert_called_once_with("tg", 777)
    repo.record_failure.assert_called_once_with(repo.get_due_deliveries.return_value[1], "timeout")


@pytest.mark.asyncio
async def test_three_failures_emit_one_warning_and_retries_continue():
    row = {
        "id": "delivery-1",
        "site_key": "yu_news",
        "channel": "telegram",
        "attempt_count": 0,
        "alerted_at": None,
        "payload": {
            "notice": _notice().model_dump(mode="json"),
            "is_new": True,
        },
    }

    class StatefulRepo:
        def get_due_deliveries(self, **kwargs):
            return [row]

        def record_failure(self, delivery, error):
            row["attempt_count"] += 1
            return row["attempt_count"], row["attempt_count"] >= 3 and not row["alerted_at"]

        def mark_alerted(self, delivery_id):
            row["alerted_at"] = "now"

        def complete_delivery(self, delivery_id, external_id):
            raise AssertionError("failed deliveries must not complete")

    notifier = MagicMock()
    notifier.eligible_channels.return_value = ["telegram"]
    notifier.deliver_notice = AsyncMock(
        return_value=DeliveryResult("telegram", False, error="unavailable")
    )
    error_notifier = MagicMock()
    error_notifier.send_critical_error = AsyncMock(return_value=True)
    service = DeliveryService(
        notifier,
        repo=StatefulRepo(),
        error_notifier=error_notifier,
    )

    for _ in range(5):
        assert await service.dispatch_due(MagicMock()) == 0

    assert notifier.deliver_notice.await_count == 5
    error_notifier.send_critical_error.assert_awaited_once()
