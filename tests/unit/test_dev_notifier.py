from unittest.mock import MagicMock

import pytest

from core.config import settings
from services.notification.dev_notifier import DevNotifier


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakeSession:
    def __init__(self):
        self.post = MagicMock(return_value=FakeResponse())


@pytest.mark.asyncio
async def test_discord_dev_alert_uses_configured_bot_token(monkeypatch):
    monkeypatch.setattr(settings, "DEV_PLATFORM", "discord")
    monkeypatch.setattr(settings, "DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setattr(settings, "DISCORD_CHANNEL_MAP", {"dev": "channel-1"})
    notifier = DevNotifier()
    session = FakeSession()

    await notifier._send_discord(session, "failure")

    _, kwargs = session.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bot discord-token"


@pytest.mark.asyncio
async def test_discord_dev_alert_is_skipped_without_token(monkeypatch):
    monkeypatch.setattr(settings, "DEV_PLATFORM", "discord")
    monkeypatch.setattr(settings, "DISCORD_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "DISCORD_CHANNEL_MAP", {"dev": "channel-1"})
    notifier = DevNotifier()
    session = FakeSession()

    await notifier._send_discord(session, "failure")

    session.post.assert_not_called()
