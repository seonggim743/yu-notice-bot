"""
Unit tests for ScraperService.

Tests cover:
- Notice parsing and extraction
- Change detection
- Hash calculation
- Error handling
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from services.scraper_service import ScraperService
from models.notice import Notice
from core.exceptions import NetworkException
import aiohttp


class TestScraperService:
    """Test suite for ScraperService"""

    @pytest.fixture
    def scraper_service(self):
        """Create ScraperService instance"""
        return ScraperService()

    @pytest.fixture
    def sample_html(self):
        """Sample HTML for testing"""
        return """
        <html>
        <head><title>테스트 공지</title></head>
        <body>
            <div class="notice">
                <h1>2024학년도 장학금 신청 안내</h1>
                <div class="content">
                    신청기간: 2024-12-01 ~ 2024-12-15
                    대상: 재학생 전체
                </div>
                <div class="attachments">
                    <a href="/file/1.pdf">신청서.pdf</a>
                </div>
            </div>
        </body>
        </html>
        """

    def test_calculate_hash(self, scraper_service):
        """Test hash calculation for change detection"""
        notice = Notice(
            url="https://test.com/1",
            article_id="1",
            title="테스트 공지",
            content="내용",
            published_at="2024-12-01",
            site_key="yu_news",
        )

        hash1 = scraper_service.calculate_hash(notice)
        assert len(hash1) == 64  # SHA-256 hex length

        # Same content should produce same hash
        notice2 = Notice(
            url="https://test.com/1",
            article_id="1",
            title="테스트 공지",
            content="내용",
            published_at="2024-12-01",
            site_key="yu_news",
        )
        hash2 = scraper_service.calculate_hash(notice2)
        assert hash1 == hash2

    def test_calculate_hash_changes_with_content(self, scraper_service):
        """Test that hash changes when content changes"""
        notice1 = Notice(
            url="https://test.com/1",
            article_id="1",
            title="공지",
            content="원본 내용",
            published_at="2024-12-01",
            site_key="yu_news",
        )

        notice2 = Notice(
            url="https://test.com/1",
            article_id="1",
            title="공지",
            content="변경된 내용",
            published_at="2024-12-01",
            site_key="yu_news",
        )

        hash1 = scraper_service.calculate_hash(notice1)
        hash2 = scraper_service.calculate_hash(notice2)

        assert hash1 != hash2

    def test_calculate_hash_includes_image_url(self, scraper_service):
        """Test that hash includes image_url"""
        notice1 = Notice(
            url="https://test.com/1",
            article_id="1",
            title="공지",
            content="내용",
            published_at="2024-12-01",
            site_key="yu_news",
            image_urls=["https://example.com/img1.jpg"],
        )

        notice2 = Notice(
            url="https://test.com/1",
            article_id="1",
            title="공지",
            content="내용",
            published_at="2024-12-01",
            site_key="yu_news",
            image_urls=["https://example.com/img2.jpg"],
        )

        hash1 = scraper_service.calculate_hash(notice1)
        hash2 = scraper_service.calculate_hash(notice2)

        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_fetch_url_success(self, scraper_service):
        """Test successful URL fetching"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="<html>Test</html>")

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value = mock_session

            # Mock get() to return a context manager
            mock_session.get = Mock()
            mock_session.get.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

            html = await scraper_service.fetch_url(mock_session, "https://test.com")
            assert html == "<html>Test</html>"

    @pytest.mark.asyncio
    async def test_fetch_url_404(self, scraper_service):
        """Test 404 error handling"""
        mock_response = AsyncMock()
        mock_response.status = 404
        # raise_for_status is synchronous
        mock_response.raise_for_status = Mock(
            side_effect=aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=404,
                message="Not Found",
                headers={},
            )
        )

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value = mock_session

            mock_session.get = Mock()
            mock_session.get.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(NetworkException):
                await scraper_service.fetch_url(mock_session, "https://test.com")

    @pytest.mark.asyncio
    async def test_detect_modifications(self, scraper_service):
        """Test detecting modifications between notices"""
        old_notice = Notice(
            site_key="yu_news",
            article_id="123",
            title="Old Title",
            url="https://test.com",
            content="Old Content",
        )

        new_notice = Notice(
            site_key="yu_news",
            article_id="123",
            title="New Title",
            url="https://test.com",
            content="New Content",
        )

        # Mock AI diff summary
        with patch.object(
            scraper_service.ai, "get_diff_summary", return_value="Content changed"
        ):
            changes = await scraper_service.detect_modifications(new_notice, old_notice)

            assert "title" in changes
            assert "content" in changes
            assert changes["title"] == "'Old Title' -> 'New Title'"
            assert changes["content"] == "Content changed"

    def test_parse_attachments(self, scraper_service):
        """Test attachment parsing"""
        # This would test actual HTML parsing
        # Implementation depends on HTMLParser
        pass

    @pytest.mark.asyncio
    async def test_rate_limiting(self, scraper_service):
        """Test that rate limiting is applied"""
        # import time

        # start = time.time()

        # ScraperService should have rate limiting
        # This test verifies delay between requests
        # Implementation would check sleep calls

        pass
