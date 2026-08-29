from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.delivery import DeliveryResult
from models.notice import Notice
from services.notification.discord import DiscordNotifier
from services.notification_service import NotificationService


def test_discord_requires_a_site_specific_mapping():
    with patch("services.notification.discord.settings") as configured:
        configured.DISCORD_BOT_TOKEN = "token"
        configured.DISCORD_CHANNEL_MAP = {"mapped": "channel-1"}
        notifier = DiscordNotifier()

        assert notifier.can_deliver("mapped") is True
        assert notifier.can_deliver("unmapped") is False


@pytest.mark.asyncio
async def test_unified_delivery_returns_normalized_failure():
    channel = MagicMock()
    channel.channel_name = "telegram"
    channel.can_deliver.return_value = True
    channel.is_enabled.return_value = True
    channel.send_notice = AsyncMock(return_value=None)
    service = NotificationService(channels=[channel])
    notice = Notice(
        site_key="yu_news",
        article_id="1",
        title="Notice",
        content="Long enough notice content",
        url="https://example.com/1",
    )

    result = await service.deliver_notice("telegram", MagicMock(), notice, True)

    assert result == DeliveryResult(
        "telegram",
        False,
        error="primary notification returned no external message ID",
    )
