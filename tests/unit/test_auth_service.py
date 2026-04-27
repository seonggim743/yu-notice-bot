"""Tests for AuthService SSO login flow.

The real implementation drives Playwright against portal.yu.ac.kr; here we
mock the Playwright async context-manager stack and exercise the four
outcomes that matter:

- successful redirect to the protected domain with cookies
- redirect failure (still on SSO domain) → returns None
- redirect succeeded but no cookies for target domain → returns None
- page.goto raises (timeout / network) → returns None
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from services.auth_service import AuthService


def _build_playwright_stack(*, page_url: str, cookies):
    """Construct a fully-mocked Playwright stack with given page.url and cookies.

    Returns (async_playwright_factory, page) — pass the factory to
    monkeypatch.setattr and assert on `page` if needed.
    """
    page = MagicMock()
    page.url = page_url
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.reload = AsyncMock()
    # query_selector returns a truthy stand-in for the form element
    page.query_selector = AsyncMock(return_value=MagicMock())
    page.fill = AsyncMock()
    page.press = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.screenshot = AsyncMock()

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    # context.cookies is called twice: once with target URL filter, once for all
    context.cookies = AsyncMock(side_effect=lambda *args, **kwargs: cookies)

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)

    p = MagicMock()
    p.chromium = chromium

    pw_cm = MagicMock()
    pw_cm.__aenter__ = AsyncMock(return_value=p)
    pw_cm.__aexit__ = AsyncMock(return_value=None)

    return (lambda: pw_cm), page


@pytest.fixture
def credentials_set(monkeypatch):
    """Provide non-empty YU_EOULLIM_ID/PW so AuthService doesn't bail early."""
    monkeypatch.setattr(settings, "YU_EOULLIM_ID", "test_user")
    monkeypatch.setattr(settings, "YU_EOULLIM_PW", "test_pw")


@pytest.mark.asyncio
async def test_eoullim_login_success(monkeypatch, credentials_set):
    """Redirect lands on join.yu.ac.kr and cookies are returned."""
    factory, _ = _build_playwright_stack(
        page_url="https://join.yu.ac.kr/main/index.do",
        cookies=[
            {"name": "JSESSIONID", "value": "abc123", "domain": "join.yu.ac.kr"},
            {"name": "SAML2", "value": "xyz", "domain": "join.yu.ac.kr"},
        ],
    )
    monkeypatch.setattr("services.auth_service.async_playwright", factory)

    svc = AuthService()
    cookies = await svc.get_eoullim_cookies()

    assert cookies == {"JSESSIONID": "abc123", "SAML2": "xyz"}


@pytest.mark.asyncio
async def test_eoullim_login_redirect_failure(monkeypatch, credentials_set):
    """Login form was submitted but URL is still on SSO domain → None."""
    factory, _ = _build_playwright_stack(
        page_url="https://portal.yu.ac.kr/sso/login.jsp?error=1",
        cookies=[],
    )
    monkeypatch.setattr("services.auth_service.async_playwright", factory)

    svc = AuthService()
    cookies = await svc.get_eoullim_cookies()

    assert cookies is None


@pytest.mark.asyncio
async def test_eoullim_login_no_cookies(monkeypatch, credentials_set):
    """Reached target domain but no cookies registered → None."""
    factory, _ = _build_playwright_stack(
        page_url="https://join.yu.ac.kr/main",
        cookies=[],
    )
    monkeypatch.setattr("services.auth_service.async_playwright", factory)

    svc = AuthService()
    cookies = await svc.get_eoullim_cookies()

    assert cookies is None


@pytest.mark.asyncio
async def test_eoullim_login_goto_timeout(monkeypatch, credentials_set):
    """page.goto raises (timeout / network) → method returns None, no crash."""
    factory, page = _build_playwright_stack(
        page_url="about:blank",
        cookies=[],
    )
    page.goto = AsyncMock(side_effect=TimeoutError("Navigation timeout"))
    monkeypatch.setattr("services.auth_service.async_playwright", factory)

    svc = AuthService()
    cookies = await svc.get_eoullim_cookies()

    assert cookies is None


@pytest.mark.asyncio
async def test_eoullim_login_skipped_without_credentials(monkeypatch):
    """Empty credentials short-circuit before touching Playwright."""
    monkeypatch.setattr(settings, "YU_EOULLIM_ID", None)
    monkeypatch.setattr(settings, "YU_EOULLIM_PW", None)

    # If Playwright is touched, this would AttributeError — assert it isn't.
    monkeypatch.setattr(
        "services.auth_service.async_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("playwright should not be called")),
    )

    svc = AuthService()
    cookies = await svc.get_eoullim_cookies()

    assert cookies is None


@pytest.mark.asyncio
async def test_yutopia_login_redirect_failure(monkeypatch, credentials_set):
    """Sibling test for the YUtopia branch — same domain-check failure path."""
    factory, _ = _build_playwright_stack(
        page_url="https://portal.yu.ac.kr/sso/login.jsp?error=2",
        cookies=[],
    )
    monkeypatch.setattr("services.auth_service.async_playwright", factory)

    svc = AuthService()
    cookies = await svc.get_yutopia_cookies()

    assert cookies is None
