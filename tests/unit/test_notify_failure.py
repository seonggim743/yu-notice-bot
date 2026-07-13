import pytest

from scripts import notify_failure


class FakeClientSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_failure_notification_uses_telegram_default_chat_without_topic(
    monkeypatch,
):
    monkeypatch.setenv("TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CHAT_ID", "chat-1")
    monkeypatch.setenv("TELEGRAM_TOPIC_MAP", "{}")
    monkeypatch.delenv("TELEGRAM_DEV_TOPIC_ID", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_DEV_CHANNEL_ID", raising=False)
    sent = []

    async def fake_send_telegram(session, token, chat_id, topic_id, message):
        sent.append((token, chat_id, topic_id, message))

    monkeypatch.setattr(notify_failure, "send_telegram", fake_send_telegram)
    monkeypatch.setattr(
        notify_failure.aiohttp, "ClientSession", lambda: FakeClientSession()
    )

    await notify_failure.main()

    assert len(sent) == 1
    assert sent[0][:3] == ("telegram-token", "chat-1", None)
