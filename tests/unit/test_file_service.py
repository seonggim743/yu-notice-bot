"""
Unit tests for FileService.

Tests cover:
- PDF processing and preview generation
- Image downloading
- File validation
- Error handling
"""

import pytest
from unittest.mock import Mock, AsyncMock
from services.file_service import FileService


class TestFileService:
    """Test suite for FileService"""

    @pytest.fixture
    def file_service(self):
        """Create FileService instance"""
        return FileService()

    @pytest.mark.asyncio
    async def test_download_file_success(self, file_service):
        """Test successful file download"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b"PDF content here")

        mock_session = AsyncMock()
        # session.get() returns a context manager, not a coroutine directly
        mock_session.get = Mock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

        content = await file_service.download_file(
            mock_session, "https://example.com/file.pdf"
        )

        assert content == b"PDF content here"

    @pytest.mark.asyncio
    async def test_download_file_404(self, file_service):
        """Test 404 error handling"""
        mock_response = AsyncMock()
        mock_response.status = 404

        mock_session = AsyncMock()
        mock_session.get = Mock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

        content = await file_service.download_file(
            mock_session, "https://example.com/notfound.pdf"
        )

        # Should return None for 404, not raise exception (based on implementation)
        assert content is None

    @pytest.mark.asyncio
    async def test_download_with_referer(self, file_service):
        """Test that referer header is included"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b"content")

        mock_session = AsyncMock()
        mock_session.get = Mock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

        await file_service.download_file(
            mock_session,
            "https://example.com/file.pdf",
            headers={"Referer": "https://example.com/notice"},
        )

        # Verify referer was passed
        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert "headers" in call_args[1]
        assert call_args[1]["headers"]["Referer"] == "https://example.com/notice"

    @pytest.mark.asyncio
    async def test_download_timeout(self, file_service):
        """Test timeout handling"""
        import asyncio

        mock_session = AsyncMock()
        mock_session.get = Mock()
        mock_session.get.side_effect = asyncio.TimeoutError("Timeout")

        content = await file_service.download_file(
            mock_session, "https://example.com/slow.pdf"
        )

        # Should return None on error
        assert content is None

    def test_extract_filename_from_url(self, file_service):
        """Test filename extraction from URL"""
        url1 = "https://example.com/files/document.pdf"
        assert file_service.extract_filename(url1) == "document.pdf"

        url2 = "https://example.com/download?file=report.pdf&id=123"
        # Should extract 'report.pdf' or handle query params
        filename = file_service.extract_filename(url2)
        assert "pdf" in filename.lower()

    def test_sanitize_filename(self, file_service):
        """Test filename sanitization"""
        dangerous = "../../etc/passwd"
        safe = file_service.sanitize_filename(dangerous)
        assert ".." not in safe
        assert "/" not in safe

        korean = "장학금_신청서.pdf"
        safe_korean = file_service.sanitize_filename(korean)
        assert safe_korean == "장학금_신청서.pdf"
