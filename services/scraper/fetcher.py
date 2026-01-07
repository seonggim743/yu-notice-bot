"""
Network operations for fetching notices and files.
Refactored to use async_retry decorator for clean retry logic.
"""
import aiohttp
import asyncio
from typing import Optional, Dict, Any

from core.config import settings
from core.logger import get_logger
from core.exceptions import NetworkException, ScraperException
from core.utils import async_retry

logger = get_logger(__name__)


# Define retryable network exceptions
TRANSIENT_EXCEPTIONS = (
    asyncio.TimeoutError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientConnectionError,
)


class NoticeFetcher:
    """
    Handles network operations for fetching notices and files.
    Uses async_retry decorator for clean retry logic with exponential backoff.
    """
    
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        self.headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

    async def create_session(self) -> aiohttp.ClientSession:
        """Creates and returns a new aiohttp session."""
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        return aiohttp.ClientSession(
            timeout=self.timeout,
            connector=connector,
            headers=self.headers
        )

    def set_cookies(self, session: aiohttp.ClientSession, cookies: Dict[str, str]):
        """Injects authentication cookies into the session."""
        session.cookie_jar.update_cookies(cookies)
        logger.info(f"[FETCHER] Injected {len(cookies)} cookies into session.")

    async def fetch_url(self, session: aiohttp.ClientSession, url: str) -> str:
        """
        Fetches URL content with error handling and retry logic.
        
        Args:
            session: aiohttp session
            url: URL to fetch
            
        Returns:
            Response text content
            
        Raises:
            NetworkException: On network errors after retries exhausted
            ScraperException: On unexpected errors
        """
        return await self._fetch_url_with_retry(session, url)
    
    @async_retry(
        max_retries=3,
        base_delay=1.0,
        retryable_exceptions=TRANSIENT_EXCEPTIONS,
    )
    async def _fetch_url_with_retry(self, session: aiohttp.ClientSession, url: str) -> str:
        """Internal method with retry decorator applied."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                # Handle HTTP errors
                if resp.status in [403, 404]:
                    raise NetworkException(
                        f"HTTP {resp.status} error fetching {url}",
                        {"url": url, "status": resp.status}
                    )
                
                if 500 <= resp.status < 600 or resp.status == 429:
                    # Raise to trigger retry
                    raise aiohttp.ServerDisconnectedError(
                        f"Server error {resp.status}"
                    )
                
                resp.raise_for_status()
                return await resp.text()
                
        except TRANSIENT_EXCEPTIONS:
            # Re-raise for retry decorator to handle
            raise
        except aiohttp.ClientResponseError as e:
            if e.status in [403, 404]:
                raise NetworkException(
                    f"HTTP {e.status} error fetching {url}",
                    {"url": url, "error": str(e)}
                )
            raise NetworkException(
                f"HTTP error fetching {url}: {e}",
                {"url": url, "error": str(e)}
            )
        except NetworkException:
            raise
        except Exception as e:
            raise ScraperException(
                f"Unexpected error fetching {url}",
                {"url": url, "error": str(e)}
            )

    async def fetch_file_head(
        self,
        session: aiohttp.ClientSession,
        url: str,
        referer: str
    ) -> Dict[str, Any]:
        """
        Performs a HEAD request to get file metadata.
        
        Args:
            session: aiohttp session
            url: File URL
            referer: Referer header value
            
        Returns:
            Dict with status, content_length, and etag
        """
        headers = {
            "Referer": referer,
            "User-Agent": settings.USER_AGENT,
        }
        try:
            async with session.head(url, headers=headers, timeout=5) as resp:
                return {
                    "status": resp.status,
                    "content_length": int(resp.headers.get("Content-Length", 0)),
                    "etag": resp.headers.get("ETag"),
                }
        except Exception as e:
            logger.warning(f"HEAD request failed for {url}: {e}")
            return {"status": 0, "content_length": 0, "etag": None}

    async def download_file(
        self,
        session: aiohttp.ClientSession,
        url: str,
        referer: str
    ) -> Optional[bytes]:
        """
        Downloads a file with retry logic.
        
        Args:
            session: aiohttp session
            url: File URL
            referer: Referer header value
            
        Returns:
            File bytes or None on failure
        """
        try:
            return await self._download_file_with_retry(session, url, referer)
        except Exception as e:
            logger.warning(f"Download failed for {url}: {e}")
            return None
    
    @async_retry(
        max_retries=3,
        base_delay=1.0,
        retryable_exceptions=TRANSIENT_EXCEPTIONS,
    )
    async def _download_file_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        referer: str
    ) -> bytes:
        """Internal download method with retry decorator."""
        headers = {
            "Referer": referer,
            "User-Agent": settings.USER_AGENT,
        }
        
        async with session.get(url, headers=headers) as resp:
            # Fail fast on 403/404
            if resp.status in [403, 404]:
                raise NetworkException(
                    f"HTTP {resp.status} downloading {url}",
                    {"url": url, "status": resp.status}
                )
            
            # Trigger retry on server errors
            if 500 <= resp.status < 600 or resp.status == 429:
                raise aiohttp.ServerDisconnectedError(f"Server error {resp.status}")
            
            resp.raise_for_status()
            return await resp.read()
