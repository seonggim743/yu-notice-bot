import json

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


@pytest.mark.asyncio
async def test_failure_notification_truncates_discord_logs_keeps_telegram_original(
    monkeypatch,
):
    long_log = "x" * 2000
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("DISCORD_CHANNEL_MAP", json.dumps({"dev": "channel-1"}))
    monkeypatch.setenv("TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CHAT_ID", "chat-1")
    monkeypatch.setenv("TELEGRAM_TOPIC_MAP", "{}")
    monkeypatch.setenv("LOG_SNIPPET", long_log)
    monkeypatch.delenv("TELEGRAM_DEV_TOPIC_ID", raising=False)
    monkeypatch.delenv("DISCORD_DEV_CHANNEL_ID", raising=False)

    discord_embeds = []
    telegram_messages = []

    async def fake_send_discord(session, token, channel_id, embed):
        discord_embeds.append((channel_id, embed))

    async def fake_send_telegram(session, token, chat_id, topic_id, message):
        telegram_messages.append(message)

    monkeypatch.setattr(notify_failure, "send_discord", fake_send_discord)
    monkeypatch.setattr(notify_failure, "send_telegram", fake_send_telegram)
    monkeypatch.setattr(
        notify_failure.aiohttp, "ClientSession", lambda: FakeClientSession()
    )

    await notify_failure.main()

    assert len(discord_embeds) == 1
    assert discord_embeds[0][0] == "channel-1"
    logs_field = discord_embeds[0][1]["fields"][2]["value"]
    assert logs_field.startswith("```log\n")
    assert logs_field.endswith("\n```")
    inner = logs_field[len("```log\n") : -len("\n```")]
    assert len(logs_field) <= 1024
    assert len(inner) == 1013
    assert inner == long_log[:1013]
    assert len(telegram_messages) == 1
    assert long_log in telegram_messages[0]
