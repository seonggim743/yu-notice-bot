"""End-to-end mock test for ScraperService.process_target.

Stubs every collaborator (fetcher, parser, analyzer, repo, notifier,
change_detector, hash_calculator, attachment_processor) so the test can
verify the orchestration sequence without hitting the network or DB.

Two scenarios:
- new notice → upsert + Telegram/Discord notify
- modified notice (hash changed, change_detector confirms) → upsert + notify
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.notice import Notice
from services.scraper_service import ScraperService


def _build_scraper(*, processed_ids, old_notice=None, detect_modifications_result=None):
    """Construct a ScraperService with all collaborators mocked.

    processed_ids: dict returned by repo.get_last_processed_ids
    old_notice: Notice instance returned by repo.get_notice (None for new flow)
    detect_modifications_result: dict returned by change_detector.detect_modifications
    """
    fetcher = MagicMock()
    fetcher.fetch_url = AsyncMock(side_effect=["<list-html/>", "<detail-html/>"])
    fetcher.create_session = AsyncMock()

    list_notice = Notice(
        site_key="yu_news",
        article_id="42",
        title="Scholarship Announcement",
        url="https://www.yu.ac.kr/main/intro/yu-news.do?articleNo=42",
        content="",
    )
    detail_notice = Notice(
        site_key="yu_news",
        article_id="42",
        title="Scholarship Announcement",
        url="https://www.yu.ac.kr/main/intro/yu-news.do?articleNo=42",
        content="장학금 신청 안내. 신청기간: 2026-05-01 ~ 2026-05-31",
    )

    parser = MagicMock()
    parser.parse_list = MagicMock(return_value=[list_notice])
    parser.parse_detail = MagicMock(return_value=detail_notice)

    analyzer = MagicMock()
    # ContentAnalyzer.analyze_notice: returns a Notice with AI metadata applied
    async def _analyze(notice):
        notice.summary = "AI 요약"
        notice.category = "장학"
        notice.tags = ["장학"]
        notice.embedding = [0.0] * 768
        return notice
    analyzer.analyze_notice = AsyncMock(side_effect=_analyze)

    repo = MagicMock()
    repo.get_last_processed_ids = MagicMock(return_value=processed_ids)
    repo.get_notice = MagicMock(return_value=old_notice)
    repo.upsert_notice = MagicMock(return_value="notice-uuid-1")
    repo.get_notice_id = MagicMock(return_value="notice-uuid-1")
    repo.update_message_ids = MagicMock()
    repo.update_discord_thread_id = MagicMock()

    notifier = MagicMock()
    notifier.send_telegram = AsyncMock(return_value=12345)
    notifier.send_discord = AsyncMock(return_value="discord-thread-1")

    change_detector = MagicMock()
    change_detector.should_process_article = AsyncMock(return_value=True)
    change_detector.detect_modifications = AsyncMock(
        return_value=detect_modifications_result
    )

    hash_calculator = MagicMock()
    hash_calculator.calculate_hash = MagicMock(return_value="new-hash")

    attachment_processor = MagicMock()
    attachment_processor.process_attachments = AsyncMock()

    target_manager = MagicMock()
    target_manager.load_targets = MagicMock()
    target_manager.get_targets = MagicMock(return_value=[])

    scraper = ScraperService(
        no_ai_mode=False,
        notifier=notifier,
        repo=repo,
        target_manager=target_manager,
        hash_calculator=hash_calculator,
        change_detector=change_detector,
        attachment_processor=attachment_processor,
        fetcher=fetcher,
        parser=parser,
        analyzer=analyzer,
    )
    return scraper, {
        "fetcher": fetcher,
        "parser": parser,
        "analyzer": analyzer,
        "repo": repo,
        "notifier": notifier,
        "change_detector": change_detector,
        "hash_calculator": hash_calculator,
    }


@pytest.mark.asyncio
async def test_new_notice_full_pipeline():
    """A first-time-seen article goes through fetch → parse → analyze → upsert → notify."""
    scraper, mocks = _build_scraper(processed_ids={})
    session = MagicMock()

    target = {
        "key": "yu_news",
        "url": "https://www.yu.ac.kr/main/intro/yu-news.do",
        "base_url": "https://www.yu.ac.kr",
        "parser": MagicMock(),
    }

    await scraper.process_target(session, target)

    # fetch called twice: list page + detail page
    assert mocks["fetcher"].fetch_url.await_count == 2
    # parser invoked for both list and detail
    mocks["parser"].parse_list.assert_called_once()
    mocks["parser"].parse_detail.assert_called_once()
    # AI analysis ran (no_ai_mode=False, no skip)
    mocks["analyzer"].analyze_notice.assert_awaited_once()
    # upsert and both notifications fired
    mocks["repo"].upsert_notice.assert_called_once()
    mocks["notifier"].send_telegram.assert_awaited_once()
    mocks["notifier"].send_discord.assert_awaited_once()
    # is_new path: did not call get_notice or change_detector
    mocks["repo"].get_notice.assert_not_called()
    mocks["change_detector"].should_process_article.assert_not_called()


@pytest.mark.asyncio
async def test_modified_notice_full_pipeline():
    """An existing article with a changed hash flows through change detection
    into a notification with modified_reason populated from the diff."""
    old_notice = Notice(
        site_key="yu_news",
        article_id="42",
        title="Old Title",
        url="https://www.yu.ac.kr/main/intro/yu-news.do?articleNo=42",
        content="이전 내용",
        content_hash="old-hash",
    )
    scraper, mocks = _build_scraper(
        processed_ids={"42": "old-hash"},
        old_notice=old_notice,
        detect_modifications_result={"title": "'Old Title' -> 'Scholarship Announcement'"},
    )
    session = MagicMock()

    target = {
        "key": "yu_news",
        "url": "https://www.yu.ac.kr/main/intro/yu-news.do",
        "base_url": "https://www.yu.ac.kr",
        "parser": MagicMock(),
    }

    await scraper.process_target(session, target)

    # Existing-record path: change_detector consulted twice
    mocks["change_detector"].should_process_article.assert_awaited_once()
    mocks["change_detector"].detect_modifications.assert_awaited_once()
    # Hash differs ("new-hash" vs "old-hash") so we proceed to AI + upsert
    mocks["analyzer"].analyze_notice.assert_awaited_once()
    mocks["repo"].upsert_notice.assert_called_once()
    # Notifications still go out
    mocks["notifier"].send_telegram.assert_awaited_once()
    mocks["notifier"].send_discord.assert_awaited_once()
    # The is_new flag passed to send_telegram should be False
    _, send_kwargs = mocks["notifier"].send_telegram.call_args
    assert send_kwargs.get("changes") == {
        "title": "'Old Title' -> 'Scholarship Announcement'"
    }
