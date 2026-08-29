"""Unit tests for NoticeFetcher CMS host rewrite and retry wrapping."""

from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from core.exceptions import NetworkException
from services.scraper.fetcher import NoticeFetcher


class _AsyncCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _ok_response(text="<html>ok</html>", body=b"file-bytes"):
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(return_value=text)
    response.read = AsyncMock(return_value=body)
    response.raise_for_status = Mock()
    response.headers = {}
    return response


class RecordingSession:
    def __init__(self, response=None, get_error=None):
        self.get_urls = []
        self.head_urls = []
        self.response = response or _ok_response()
        self.get_error = get_error

    def get(self, url, **kwargs):
        self.get_urls.append(url)
        if self.get_error is not None:
            raise self.get_error() if callable(self.get_error) else self.get_error
        return _AsyncCM(self.response)

    def head(self, url, **kwargs):
        self.head_urls.append(url)
        return _AsyncCM(self.response)


@pytest.fixture
def fetcher():
    return NoticeFetcher()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_url, expected_url",
    [
        (
            "https://hcms.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=1",
            "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=1",
        ),
        (
            "https://computer.yu.ac.kr/computer/notice/notice.do?mode=list&articleLimit=10",
            "https://www.yu.ac.kr/computer/notice/notice.do?mode=list&articleLimit=10",
        ),
        (
            "https://swedu.yu.ac.kr/swedu/notice/notice.do?mode=view&articleNo=9",
            "https://www.yu.ac.kr/swedu/notice/notice.do?mode=view&articleNo=9",
        ),
    ],
)
async def test_fetch_url_rewrites_cms_aliases_preserving_path_and_query(
    fetcher, source_url, expected_url
):
    session = RecordingSession()

    html = await fetcher.fetch_url(session, source_url)

    assert html == "<html>ok</html>"
    assert session.get_urls == [expected_url]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://www.yu.ac.kr/cse/community/notice.do?mode=list",
        "https://join.yu.ac.kr/front_new/index.php?g_page=program",
        "https://not-hcms.yu.ac.kr/main/intro/yu-news.do?mode=view",
    ],
)
async def test_fetch_url_leaves_www_and_unrelated_hosts_unchanged(fetcher, url):
    session = RecordingSession()

    await fetcher.fetch_url(session, url)

    assert session.get_urls == [url]


@pytest.mark.asyncio
async def test_head_and_download_use_rewritten_url(fetcher):
    session = RecordingSession()
    source = "https://computer.yu.ac.kr/computer/notice/file.do?attachNo=12"
    expected = "https://www.yu.ac.kr/computer/notice/file.do?attachNo=12"

    head = await fetcher.fetch_file_head(session, source, referer="https://www.yu.ac.kr/")
    body = await fetcher.download_file(session, source, referer="https://www.yu.ac.kr/")

    assert session.head_urls == [expected]
    assert session.get_urls == [expected]
    assert head["status"] == 200
    assert body == b"file-bytes"


@pytest.mark.asyncio
async def test_fetch_url_wraps_exhausted_retries_as_network_exception(fetcher, monkeypatch):
    monkeypatch.setattr("core.utils.asyncio.sleep", AsyncMock())
    session = RecordingSession(
        get_error=lambda: aiohttp.ClientOSError(104, "Connection reset by peer")
    )
    url = "https://www.yu.ac.kr/cse/community/notice.do"

    with pytest.raises(NetworkException) as excinfo:
        await fetcher.fetch_url(session, url)

    assert len(session.get_urls) == 3
    assert session.get_urls == [url, url, url]
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientOSError)
