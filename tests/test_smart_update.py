import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.scraper_service import ScraperService
from models.notice import Notice, Attachment
from aiohttp import ClientSession

@pytest.fixture
def scraper():
    return ScraperService(no_ai_mode=True)

@pytest.fixture
def old_notice():
    return Notice(
        site_key="test",
        article_id="1",
        title="Old Title",
        content="Old Content",
        url="http://test.com/1",
        attachments=[
            Attachment(name="file1.pdf", url="http://test.com/file1.pdf", file_size=100, etag="etag1")
        ]
    )

@pytest.fixture
def new_notice():
    return Notice(
        site_key="test",
        article_id="1",
        title="Old Title",
        content="Old Content",
        url="http://test.com/1",
        attachments=[
            Attachment(name="file1.pdf", url="http://test.com/file1.pdf")
        ]
    )

@pytest.mark.asyncio
async def test_metadata_change_title(scraper, old_notice, new_notice):
    new_notice.title = "New Title"
    session = AsyncMock(spec=ClientSession)
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_metadata_change_content(scraper, old_notice, new_notice):
    new_notice.content = "New Content"
    session = AsyncMock(spec=ClientSession)
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_metadata_change_att_count(scraper, old_notice, new_notice):
    new_notice.attachments.append(Attachment(name="file2.pdf", url="http://test.com/file2.pdf"))
    session = AsyncMock(spec=ClientSession)
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_metadata_change_att_url(scraper, old_notice, new_notice):
    new_notice.attachments[0].url = "http://test.com/file1_v2.pdf"
    session = AsyncMock(spec=ClientSession)
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_no_change_etag_match(scraper, old_notice, new_notice):
    session = AsyncMock(spec=ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.headers = {"ETag": "etag1", "Content-Length": "100"}
    session.head.return_value.__aenter__.return_value = mock_resp
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is False

@pytest.mark.asyncio
async def test_change_etag_mismatch(scraper, old_notice, new_notice):
    session = AsyncMock(spec=ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.headers = {"ETag": "etag2", "Content-Length": "100"}
    session.head.return_value.__aenter__.return_value = mock_resp
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_no_change_size_match(scraper, old_notice, new_notice):
    # Remove ETag from old notice to force size check
    old_notice.attachments[0].etag = None
    
    session = AsyncMock(spec=ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Length": "100"} # No ETag in response
    session.head.return_value.__aenter__.return_value = mock_resp
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is False

@pytest.mark.asyncio
async def test_change_size_mismatch(scraper, old_notice, new_notice):
    # Remove ETag from old notice to force size check
    old_notice.attachments[0].etag = None
    
    session = AsyncMock(spec=ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Length": "200"} # Size changed
    session.head.return_value.__aenter__.return_value = mock_resp
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_head_fail(scraper, old_notice, new_notice):
    session = AsyncMock(spec=ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 404 # Not 200
    session.head.return_value.__aenter__.return_value = mock_resp
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True

@pytest.mark.asyncio
async def test_head_exception(scraper, old_notice, new_notice):
    session = AsyncMock(spec=ClientSession)
    session.head.side_effect = Exception("Network Error")
    
    should_process = await scraper.should_process_article(session, new_notice, old_notice)
    assert should_process is True
