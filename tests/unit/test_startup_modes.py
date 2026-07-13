from unittest.mock import MagicMock, patch

import pytest

from core.config import Settings
from main import Bot
from services.scraper.analyzer import ContentAnalyzer


def _settings(**overrides):
    values = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key",
        "GEMINI_API_KEY": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_no_ai_mode_does_not_require_gemini_key():
    messages = _settings().validate_all(no_ai_mode=True)
    assert not any("GEMINI_API_KEY" in message for message in messages)


def test_normal_mode_requires_gemini_key():
    messages = _settings().validate_all()
    assert any(message.startswith("ERROR:") and "GEMINI_API_KEY" in message for message in messages)


def test_no_ai_mode_does_not_construct_ai_service():
    with patch(
        "services.scraper.analyzer.AIService",
        side_effect=AssertionError("Gemini service must not initialize"),
    ):
        analyzer = ContentAnalyzer(no_ai_mode=True)
    assert analyzer.ai is None


def _scraper(routes):
    scraper = MagicMock()
    scraper.targets = [
        {
            "key": "yu_news",
            "base_url": "https://www.yu.ac.kr",
            "url": "https://www.yu.ac.kr/notices",
        }
    ]
    scraper.notifier.eligible_channels.side_effect = lambda key: routes
    return scraper


@pytest.mark.asyncio
async def test_init_mode_skips_notification_route_and_ai_validation():
    bot = Bot(
        init_mode=True,
        scraper=_scraper([]),
        error_notifier=MagicMock(),
    )
    with patch("main.Database.get_client"), patch(
        "main.Database.health_check", return_value=True
    ), patch("main.settings.GEMINI_API_KEY", None):
        assert await bot.validate_startup() is True


@pytest.mark.asyncio
async def test_no_ai_discord_only_route_passes_startup():
    bot = Bot(
        no_ai_mode=True,
        scraper=_scraper(["discord"]),
        error_notifier=MagicMock(),
    )
    with patch("main.Database.get_client"), patch(
        "main.Database.health_check", return_value=True
    ), patch("main.settings.GEMINI_API_KEY", None):
        assert await bot.validate_startup() is True


@pytest.mark.asyncio
async def test_missing_route_fails_non_init_startup():
    bot = Bot(
        no_ai_mode=True,
        scraper=_scraper([]),
        error_notifier=MagicMock(),
    )
    with patch("main.Database.get_client"), patch(
        "main.Database.health_check", return_value=True
    ), patch("main.settings.GEMINI_API_KEY", None):
        assert await bot.validate_startup() is False
