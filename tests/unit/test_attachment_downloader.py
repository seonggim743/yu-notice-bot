import asyncio
import importlib
import sys
import types
from unittest.mock import Mock

import pytest


class _FakeResponse:
    def __init__(self, status: int, data: bytes = b"", headers=None):
        self.status = status
        self._data = data
        self.headers = headers or {}

    async def read(self):
        return self._data


class _FakeContext:
    def __init__(self, response: _FakeResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture
def attachment_downloader_cls(monkeypatch):
    fake_config = types.ModuleType("core.config")
    fake_config.settings = types.SimpleNamespace(USER_AGENT="test-agent")
    monkeypatch.setitem(sys.modules, "core.config", fake_config)

    fake_logger = types.ModuleType("core.logger")
    fake_logger.get_logger = lambda _name: types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "core.logger", fake_logger)

    fake_utils = types.ModuleType("core.utils")
    fake_utils.parse_content_disposition = lambda _value, fallback_name: fallback_name
    monkeypatch.setitem(sys.modules, "core.utils", fake_utils)

    fake_notice = types.ModuleType("models.notice")
    fake_notice.Attachment = object
    monkeypatch.setitem(sys.modules, "models.notice", fake_notice)

    module_name = "services.file.attachment_downloader"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)

    yield module.AttachmentDownloader

    sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_content_image_retries_transient_timeout(attachment_downloader_cls):
    session = Mock()
    session.get = Mock(
        side_effect=[
            asyncio.TimeoutError(),
            _FakeContext(_FakeResponse(200, b"image-data")),
        ]
    )
    downloader = attachment_downloader_cls(max_retries=2, retry_delay=0)

    result = await downloader.download_content_images(
        session,
        ["https://example.com/image.jpg"],
        referer="https://example.com/notice",
    )

    assert result == [(0, b"image-data")]
    assert session.get.call_count == 2

    _, kwargs = session.get.call_args
    assert kwargs["headers"]["Referer"] == "https://example.com/notice"
    assert kwargs["headers"]["Accept"].startswith("image/")
    assert kwargs["timeout"].total == 30


@pytest.mark.asyncio
async def test_content_image_respects_file_size_limit(attachment_downloader_cls):
    session = Mock()
    session.get = Mock(
        return_value=_FakeContext(_FakeResponse(200, b"too-large"))
    )
    downloader = attachment_downloader_cls(max_retries=2, retry_delay=0)

    result = await downloader.download_content_images(
        session,
        ["https://example.com/image.jpg"],
        file_size_limit=3,
    )

    assert result == []
    assert session.get.call_count == 1
