import aiohttp
import asyncio
from typing import Optional, Dict, Any
from core.config import settings
from core.logger import get_logger
from core.exceptions import NetworkException, ScraperException

logger = get_logger(__name__)

class NoticeFetcher:
    """
    Handles network operations for fetching notices and files.
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

    async def fetch_url(self, session: aiohttp.ClientSession, url: str) -> str:
        """
        Fetches URL content with error handling.
        """
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                return await resp.text()
        except asyncio.TimeoutError:
            raise NetworkException(f"Timeout fetching {url}", {"url": url})
        except aiohttp.ClientError as e:
            raise NetworkException(f"HTTP error fetching {url}", {"url": url, "error": str(e)})
        except Exception as e:
            raise ScraperException(f"Unexpected error fetching {url}", {"url": url, "error": str(e)})

    async def fetch_file_head(self, session: aiohttp.ClientSession, url: str, referer: str) -> Dict[str, Any]:
        """
        Performs a HEAD request to get file metadata.
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

    async def download_file(self, session: aiohttp.ClientSession, url: str, referer: str) -> Optional[bytes]:
        """
        Downloads a file fully.
        """
        headers = {
            "Referer": referer,
            "User-Agent": settings.USER_AGENT,
        }
        try:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.read()
        except Exception as e:
            logger.warning(f"Download failed for {url}: {e}")
            return None
