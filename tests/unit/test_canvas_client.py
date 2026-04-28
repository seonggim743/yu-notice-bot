import asyncio
from unittest.mock import AsyncMock

import pytest

from core.exceptions import (
    CanvasAuthException,
    CanvasRateLimitException,
    NetworkException,
)
from services.canvas.canvas_client import CanvasClient


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return self._text


class RaisingContext:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            return RaisingContext(item)
        return item


@pytest.mark.asyncio
async def test_get_active_courses_paginates():
    session = FakeSession(
        [
            FakeResponse(
                body=[{"id": 1, "name": "Logic"}],
                headers={
                    "Content-Type": "application/json",
                    "Link": '<https://canvas.test/api/v1/courses?page=2>; rel="next"',
                },
            ),
            FakeResponse(body=[{"id": 2, "name": "Math"}]),
        ]
    )
    client = CanvasClient("https://canvas.test", "token", session)

    courses = await client.get_active_courses()

    assert [c.id for c in courses] == [1, 2]
    assert session.calls[0]["params"]["enrollment_state"] == "active"
    assert session.calls[1]["params"] is None


@pytest.mark.asyncio
async def test_endpoint_models_are_mapped():
    session = FakeSession(
        [
            FakeResponse(body=[{"id": 10, "name": "HW", "html_url": "u"}]),
            FakeResponse(
                body=[
                    {
                        "id": 20,
                        "title": "Notice",
                        "message": "Body",
                        "context_code": "course_7",
                    }
                ]
            ),
            FakeResponse(body=[{"id": 30, "assignment_id": 10, "score": 9}]),
        ]
    )
    client = CanvasClient("https://canvas.test", "token", session)

    assignments = await client.get_assignments(7)
    announcements = await client.get_announcements([7])
    submissions = await client.get_submissions(7)

    assert assignments[0].course_id == 7
    assert announcements[0].course_id == 7
    assert submissions[0].course_id == 7


@pytest.mark.asyncio
async def test_429_raises_rate_limit_and_honors_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    session = FakeSession(
        [FakeResponse(status=429, headers={"Retry-After": "3", "Content-Type": "application/json"})]
    )
    client = CanvasClient("https://canvas.test", "token", session)

    with pytest.raises(CanvasRateLimitException):
        await client.get_active_courses()

    sleep.assert_awaited_once_with(3.0)


@pytest.mark.asyncio
async def test_auth_and_server_errors():
    notifier = AsyncMock()
    session = FakeSession(
        [
            FakeResponse(status=401),
            FakeResponse(status=403),
            FakeResponse(status=500, text="server down"),
        ]
    )
    client = CanvasClient(
        "https://canvas.test",
        "token",
        session,
        error_notifier=notifier,
    )

    with pytest.raises(CanvasAuthException):
        await client.get_active_courses()
    notifier.send_critical_error.assert_awaited_once()

    with pytest.raises(CanvasAuthException):
        await client.get_active_courses()

    with pytest.raises(NetworkException):
        await client.get_active_courses()


@pytest.mark.asyncio
async def test_timeout_retries_then_succeeds(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    session = FakeSession(
        [
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            FakeResponse(body=[]),
        ]
    )
    client = CanvasClient(
        "https://canvas.test",
        "token",
        session,
        max_timeout_retries=2,
    )

    assert await client.get_active_courses() == []
    assert len(session.calls) == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_non_json_response_is_network_error():
    session = FakeSession(
        [
            FakeResponse(
                body="<html>maintenance</html>",
                headers={"Content-Type": "text/html"},
                text="<html>maintenance</html>",
            )
        ]
    )
    client = CanvasClient("https://canvas.test", "token", session)

    with pytest.raises(NetworkException, match="non-JSON"):
        await client.get_active_courses()
