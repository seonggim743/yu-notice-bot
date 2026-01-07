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

    def set_cookies(self, session: aiohttp.ClientSession, cookies: Dict[str, str]):
        """Injects authentication cookies into the session."""
        session.cookie_jar.update_cookies(cookies)
        logger.info(f"[FETCHER] Injected {len(cookies)} cookies into session.")


    async def fetch_url(self, session: aiohttp.ClientSession, url: str) -> str:
        """
        Fetches URL content with error handling and retry logic.
        """
        max_retries = 3
        attempt = 0
        
        while attempt < max_retries:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    return await resp.text()
            except (asyncio.TimeoutError, aiohttp.ServerDisconnectedError, aiohttp.ClientConnectionError) as e:
                # Transient network errors
                attempt += 1
                if attempt >= max_retries:
                    raise NetworkException(f"Timeout/Connection error fetching {url} after {max_retries} retries", {"url": url})
                
                wait_time = 2 ** (attempt - 1)
                logger.warning(f"[FETCHER] Network error fetching {url} (Attempt {attempt}/{max_retries}). Retrying in {wait_time}s... Error: {e}")
                await asyncio.sleep(wait_time)
                
            except aiohttp.ClientResponseError as e:
                # HTTP Status errors
                # Fail Fast on 404/403
                if e.status in [403, 404]:
                    raise NetworkException(f"HTTP {e.status} error fetching {url}", {"url": url, "error": str(e)})
                
                # Retry on 5xx or 429
                if 500 <= e.status < 600 or e.status == 429:
                    attempt += 1
                    if attempt >= max_retries:
                        raise NetworkException(f"HTTP {e.status} error fetching {url} after {max_retries} retries", {"url": url, "error": str(e)})
                    
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(f"[FETCHER] HTTP {e.status} error fetching {url} (Attempt {attempt}/{max_retries}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    # Other 4xx errors - Fail Fast
                    raise NetworkException(f"HTTP {e.status} error fetching {url}", {"url": url, "error": str(e)})
                    
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
        Downloads a file fully with retry logic.
        """
        headers = {
            "Referer": referer,
            "User-Agent": settings.USER_AGENT,
        }
        
        max_retries = 3
        attempt = 0
        
        while attempt < max_retries:
            try:
                async with session.get(url, headers=headers) as resp:
                    resp.raise_for_status()
                    return await resp.read()
            except (asyncio.TimeoutError, aiohttp.ServerDisconnectedError, aiohttp.ClientConnectionError) as e:
                attempt += 1
                if attempt >= max_retries:
                    logger.warning(f"Download failed for {url} after {max_retries} retries: {e}")
                    return None
                
                wait_time = 2 ** (attempt - 1)
                logger.warning(f"[FETCHER] Download error for {url} (Attempt {attempt}/{max_retries}). Retrying in {wait_time}s... Error: {e}")
                await asyncio.sleep(wait_time)
                
            except aiohttp.ClientResponseError as e:
                # Fail Fast on 404/403
                if e.status in [403, 404]:
                    logger.warning(f"Download failed for {url}: HTTP {e.status}")
                    return None
                
                # Retry on 5xx or 429
                if 500 <= e.status < 600 or e.status == 429:
                    attempt += 1
                    if attempt >= max_retries:
                        logger.warning(f"Download failed for {url} after {max_retries} retries: HTTP {e.status}")
                        return None
                    
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(f"[FETCHER] Download HTTP {e.status} for {url} (Attempt {attempt}/{max_retries}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.warning(f"Download failed for {url}: HTTP {e.status}")
                    return None
                    
            except Exception as e:
                logger.warning(f"Download failed for {url}: {e}")
                return None
        
        return None
