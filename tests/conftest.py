import pytest
import asyncio
from typing import Dict, Any
from unittest.mock import Mock, AsyncMock
from datetime import datetime
import json

# =============================================================================
# Pytest Configuration
# =============================================================================


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Mock Fixtures - External Services
# =============================================================================


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client for database operations."""
    client = Mock()

    # Mock table operations
    table_mock = Mock()
    table_mock.select.return_value = table_mock
    table_mock.insert.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.upsert.return_value = table_mock
    table_mock.delete.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.neq.return_value = table_mock
    table_mock.execute.return_value = Mock(data=[])

    client.table.return_value = table_mock
    client.rpc.return_value = Mock(execute=Mock(return_value=Mock(data=[])))

    return client


@pytest.fixture
def mock_gemini_response():
    """Mock Gemini API response for AI testing"""
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.text = """
    {
        "summary": "테스트 공지사항 요약",
        "category": "학사",
        "tags": ["테스트", "학사"],
        "importance": "medium",
        "deadline": "2024-12-31",
        "target_dept": "전체",
        "target_grades": [1, 2, 3, 4],
        "eligibility": [],
        "start_date": "2024-12-01",
        "end_date": "2024-12-31"
    }
    """
    mock_response.usage_metadata = Mock()
    mock_response.usage_metadata.prompt_token_count = 100
    mock_response.usage_metadata.candidates_token_count = 50
    return mock_response


@pytest.fixture
def mock_gemini_model():
    """Mock Google Gemini AI model."""
    model = Mock()

    # Mock response
    response = Mock()
    response.text = json.dumps(
        {
            "summary": "테스트 요약입니다.",
            "category": "학사",
            "tags": ["긴급", "장학"],
            "target_grades": [1, 2, 3, 4],
            "target_dept": "전체",
            "start_date": None,
            "end_date": None,
        }
    )
    response.usage_metadata = Mock(
        prompt_token_count=100, candidates_token_count=50, total_token_count=150
    )

    # Make generate_content async
    async def mock_generate_content(*args, **kwargs):
        return response

    model.generate_content_async = mock_generate_content
    model.generate_content = Mock(return_value=response)

    return model


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp ClientSession."""
    session = AsyncMock()

    # Mock response
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(return_value="<html><body>Test HTML</body></html>")
    response.read = AsyncMock(return_value=b"Test content")
    response.headers = {"Content-Type": "text/html"}

    # Mock context manager
    session.get.return_value.__aenter__.return_value = response
    session.post.return_value.__aenter__.return_value = response

    return session


# =============================================================================
# Sample Data Fixtures
# =============================================================================


@pytest.fixture
def sample_notice_data() -> Dict[str, Any]:
    """Sample notice data for testing."""
    return {
        "site_key": "yu_news",
        "article_id": "12345",
        "title": "2024학년도 2학기 장학금 신청 안내",
        "content": "장학금 신청 기간: 2024-12-01 ~ 2024-12-15\n자세한 내용은 학생지원팀으로 문의하시기 바랍니다.",
        "url": "https://www.yu.ac.kr/notice/12345",
        "category": "장학",
        "summary": "2024학년도 2학기 장학금 신청 기간 안내",
        "target_grades": [1, 2, 3, 4],
        "target_dept": "전체",
        "published_at": datetime.now().isoformat(),
        "image_url": None,
        "image_urls": [],
        "attachments": [],
    }


@pytest.fixture
def sample_notice(sample_notice_data):
    """Sample Notice object."""
    from models.notice import Notice

    return Notice(**sample_notice_data)


@pytest.fixture
def sample_html_notice_list() -> str:
    """Sample HTML for notice list page."""
    return """
    <table>
        <tr>
            <td class="td-subject">
                <a href="/notice/12345">2024학년도 2학기 장학금 신청 안내</a>
            </td>
            <td class="td-write">학생지원팀</td>
            <td class="td-date">2024-12-01</td>
        </tr>
        <tr>
            <td class="td-subject">
                <a href="/notice/12346">겨울방학 기숙사 입사 신청</a>
            </td>
            <td class="td-write">생활관</td>
            <td class="td-date">2024-11-30</td>
        </tr>
    </table>
    """


@pytest.fixture
def sample_html_notice_detail() -> str:
    """Sample HTML for notice detail page."""
    return """
    <div class="notice-detail">
        <h1>2024학년도 2학기 장학금 신청 안내</h1>
        <div class="meta">
            <span class="author">학생지원팀</span>
            <span class="date">2024-12-01</span>
        </div>
        <div class="content">
            <p>장학금 신청 기간: 2024-12-01 ~ 2024-12-15</p>
            <p>자세한 내용은 학생지원팀으로 문의하시기 바랍니다.</p>
        </div>
        <div class="attachments">
            <a href="/file/download?id=1">장학금_신청서.pdf</a>
            <a href="/file/download?id=2">지원서류_목록.hwp</a>
        </div>
    </div>
    """


@pytest.fixture
def sample_modified_notice_data(sample_notice_data) -> Dict[str, Any]:
    """Sample modified notice data."""
    modified = sample_notice_data.copy()
    modified["content"] = (
        "장학금 신청 기간: 2024-12-01 ~ 2024-12-20 (연장)\n자세한 내용은 학생지원팀으로 문의하시기 바랍니다."
    )
    modified["summary"] = "2024학년도 2학기 장학금 신청 기간 연장 안내"
    return modified


@pytest.fixture
def sample_menu_image_data() -> Dict[str, Any]:
    """Sample menu OCR data."""
    return {
        "raw_text": "12월 1일 (월)\n아침: 쌀밥, 된장찌개, 김치\n점심: 카레라이스, 샐러드\n저녁: 비빔밥, 미역국",
        "start_date": "2024-12-01",
        "end_date": "2024-12-07",
    }


# =============================================================================
# Test Utilities
# =============================================================================


@pytest.fixture
def freeze_time():
    """Fixture to freeze time for testing."""
    frozen_time = datetime(2024, 12, 1, 12, 0, 0)

    def _freeze():
        return frozen_time

    return _freeze


@pytest.fixture
def temp_db_notice(mock_supabase_client, sample_notice_data):
    """Insert a temporary notice into mock database and return its ID."""
    mock_supabase_client.table("notices").insert(
        {**sample_notice_data, "id": "test-uuid-1234"}
    ).execute()

    return "test-uuid-1234"


# =============================================================================
# Async Test Helpers
# =============================================================================


@pytest.fixture
def async_return():
    """Helper to create async functions that return a value."""

    def _async_return(value):
        async def _inner(*args, **kwargs):
            return value

        return _inner

    return _async_return


@pytest.fixture
def async_raise():
    """Helper to create async functions that raise an exception."""

    def _async_raise(exception):
        async def _inner(*args, **kwargs):
            raise exception

        return _inner

    return _async_raise
