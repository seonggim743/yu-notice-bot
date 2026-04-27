"""
Shared attachment and inline-image download helpers.

Telegram and Discord notifiers both fetch attachment files and inline
content images with the same retry / Content-Disposition logic. This
module centralizes that flow so each notifier only deals in
(filename, bytes) tuples and applies its own platform-specific wrapping
(image optimization, MultipartWriter shape, etc.) on top.
"""
from typing import List, Tuple

import aiohttp
import asyncio

from core.config import settings
from core.logger import get_logger
from core.utils import parse_content_disposition
from models.notice import Attachment

logger = get_logger(__name__)


class AttachmentDownloader:
    """Downloads attachments and content images with retry handling.

    Designed to be stateless beyond config; one instance can be reused
    across notifications and notifiers.
    """

    def __init__(self, max_retries: int = 2, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def download_attachments(
        self,
        session: aiohttp.ClientSession,
        attachments: List[Attachment],
        file_size_limit: int,
        max_count: int = 10,
        referer: str = "",
    ) -> List[Tuple[str, bytes]]:
        """Download up to max_count attachments below file_size_limit.

        Returns a list of (actual_filename, data) tuples in input order,
        skipping files that exceed the size limit, return 4xx, or fail
        all retries.

        actual_filename is parsed from the Content-Disposition response
        header when available, falling back to Attachment.name.
        """
        if not attachments:
            return []

        results: List[Tuple[str, bytes]] = []
        download_headers = {
            "Referer": referer,
            "User-Agent": settings.USER_AGENT,
            "Accept": "*/*",
            "Connection": "keep-alive",
        }

        for att in attachments[:max_count]:
            data = await self._fetch_with_retry(
                session,
                att.url,
                download_headers,
                file_size_limit,
                label=att.name,
            )
            if data is None:
                continue
            file_data, content_disposition = data
            actual_filename = parse_content_disposition(
                content_disposition, fallback_name=att.name
            )
            results.append((actual_filename, file_data))
            logger.info(
                f"[DOWNLOADER] Got attachment '{actual_filename}' ({len(file_data)} bytes)"
            )

        return results

    async def download_content_images(
        self,
        session: aiohttp.ClientSession,
        image_urls: List[str],
        referer: str = "",
        max_count: int = 10,
        timeout_seconds: int = 10,
    ) -> List[Tuple[int, bytes]]:
        """Download up to max_count content images.

        Returns a list of (original_index, data) tuples. Failed downloads
        are skipped silently after a logged error. No retries — content
        images are best-effort and a missing one should not block the
        notification.
        """
        if not image_urls:
            return []

        results: List[Tuple[int, bytes]] = []
        headers = {"Referer": referer, "User-Agent": settings.USER_AGENT}

        for idx, image_url in enumerate(image_urls[:max_count]):
            try:
                async with session.get(
                    image_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        results.append((idx, data))
                        logger.info(
                            f"[DOWNLOADER] Got content image {idx + 1}/{len(image_urls)}"
                        )
                    else:
                        logger.warning(
                            f"[DOWNLOADER] Image {idx} returned status {resp.status}"
                        )
            except Exception as e:
                logger.error(f"[DOWNLOADER] Image {idx} failed: {e}")

        return results

    async def _fetch_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict,
        file_size_limit: int,
        label: str,
    ):
        """Fetch a single URL with retry/abort policy.

        Returns (data, content_disposition_header) on success, None on
        permanent failure or oversized file.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as file_resp:
                    if file_resp.status == 200:
                        file_data = await file_resp.read()
                        if len(file_data) > file_size_limit:
                            logger.warning(
                                f"[DOWNLOADER] {label} exceeds size limit "
                                f"({len(file_data)} > {file_size_limit}), skipping"
                            )
                            return None
                        return file_data, file_resp.headers.get(
                            "Content-Disposition", ""
                        )
                    if file_resp.status in (404, 403):
                        logger.warning(
                            f"[DOWNLOADER] {label} status {file_resp.status}, no retry"
                        )
                        return None
                    if attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay)
            except Exception as e:
                logger.error(f"[DOWNLOADER] Error downloading {label}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
        return None
