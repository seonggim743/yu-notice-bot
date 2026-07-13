from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.exceptions import DatabaseException
from models.notice import Notice
from repositories.delivery_repo import DeliveryRepository
from repositories.notice_repo import NoticeRepository


class Query:
    def __init__(self, data=None, error=None):
        self.data = [] if data is None else data
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class FakeDB:
    def __init__(self, query=None, rpc_data="notice-1"):
        self.query = query or Query()
        self.rpc_data = rpc_data
        self.rpc_calls = []

    def table(self, name):
        self.query.calls.append(("table", (name,), {}))
        return self.query

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return Query(data=self.rpc_data)


@pytest.mark.parametrize(
    ("prior_attempts", "expected_minutes"),
    [(0, 30), (1, 60), (2, 120), (3, 240), (4, 360), (8, 360)],
)
def test_retry_backoff_is_capped(prior_attempts, expected_minutes):
    query = Query()
    repo = DeliveryRepository(db=FakeDB(query=query))
    before = datetime.now(timezone.utc)

    attempt_count, _ = repo.record_failure(
        {"id": "d1", "attempt_count": prior_attempts}, "failed"
    )

    update_payload = next(args[0] for name, args, _ in query.calls if name == "update")
    retry_at = datetime.fromisoformat(update_payload["next_attempt_at"])
    delay = (retry_at - before).total_seconds() / 60
    assert attempt_count == prior_attempts + 1
    assert expected_minutes <= delay < expected_minutes + 0.1


def test_completed_cleanup_keeps_pending_rows_out_of_delete_filter():
    query = Query()
    DeliveryRepository(db=FakeDB(query=query)).purge_completed(retention_days=30)

    in_call = next(call for call in query.calls if call[0] == "in_")
    assert in_call[1] == ("status", ["sent", "superseded"])
    assert any(call[0] == "lt" and call[1][0] == "updated_at" for call in query.calls)


def test_completion_uses_atomic_rpc_with_external_message_id():
    db = FakeDB(rpc_data=True)
    DeliveryRepository(db=db).complete_delivery("delivery-1", "thread-123")

    assert db.rpc_calls == [
        (
            "complete_notification_delivery",
            {
                "p_delivery_id": "delivery-1",
                "p_external_message_id": "thread-123",
            },
        )
    ]


def test_notice_lookup_failure_is_not_treated_as_missing_data():
    repo = NoticeRepository(db=FakeDB(query=Query(error=RuntimeError("db down"))))

    with pytest.raises(DatabaseException, match="Failed to fetch notice"):
        repo.get_notice("yu_news", "42")


def test_empty_notice_lookup_is_clean_absence():
    repo = NoticeRepository(db=FakeDB(query=Query(data=[])))

    assert repo.get_notice("yu_news", "42") is None


def test_notice_and_deliveries_use_the_atomic_rpc():
    db = FakeDB(rpc_data="notice-uuid")
    repo = NoticeRepository(db=db)
    notice = Notice(
        site_key="yu_news",
        article_id="42",
        title="Notice",
        content="Long enough content",
        url="https://example.com/42",
        content_hash="hash-v2",
    )
    deliveries = [{"channel": "telegram", "event_type": "new", "payload": {}}]

    assert repo.persist_notice_with_deliveries(notice, deliveries) == "notice-uuid"
    rpc_name, params = db.rpc_calls[0]
    assert rpc_name == "persist_notice_with_deliveries"
    assert params["p_notice"]["content_hash"] == "hash-v2"
    assert params["p_deliveries"] == deliveries


def test_migration_supersedes_older_pending_versions_and_completes_ids_atomically():
    sql = (
        Path(__file__).parents[2]
        / "database"
        / "migrations"
        / "006_notification_deliveries.sql"
    ).read_text(encoding="utf-8")

    assert "status = 'superseded'" in sql
    assert "content_hash <> v_content_hash" in sql
    assert "CREATE OR REPLACE FUNCTION complete_notification_delivery" in sql
    assert "message_ids = jsonb_set" in sql
    assert "discord_thread_id = p_external_message_id" in sql
    assert "ALTER TABLE notification_deliveries ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE notification_deliveries FROM anon, authenticated" in sql
    assert sql.count("FROM PUBLIC, anon, authenticated") == 2
    assert sql.count("TO service_role") == 3
