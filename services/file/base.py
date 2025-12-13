"""
Base file handling utilities shared across file service modules.
"""
import aiohttp
import logging
import os
import re
import shutil
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)


class BaseFileHandler:
    """Base class with common utilities for all file handlers."""

    async def download_file(
        self, session: aiohttp.ClientSession, url: str, headers: dict = None
    ) -> Optional[bytes]:
        """Downloads a file into memory."""
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.warning(f"[FILE] Download failed: {resp.status} for {url}")
                    return None
        except Exception as e:
            logger.error(f"[FILE] Download error: {e}")
            return None

    def get_soffice_command(self) -> Optional[str]:
        """
        Tries to find the LibreOffice 'soffice' command.
        Checks PATH first, then common Windows paths.
        """
        # 1. Check PATH
        if shutil.which("soffice"):
            return "soffice"

        # 2. Check Common Windows Paths
        windows_paths = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for path in windows_paths:
            if os.path.exists(path):
                return path

        return None

    def is_pdf(self, filename: str) -> bool:
        return filename.lower().endswith(".pdf")

    def is_image(self, filename: str) -> bool:
        return filename.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        )

    def is_hwp(self, filename: str) -> bool:
        return filename.lower().endswith((".hwp", ".hwpx"))

    def is_office(self, filename: str) -> bool:
        return filename.lower().endswith(
            (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt")
        )

    def get_extension(self, filename: str) -> str:
        """Returns the file extension in lowercase."""
        return filename.split(".")[-1].lower() if "." in filename else ""

    def validate_file_size(self, file_data: bytes, max_mb: int) -> bool:
        return len(file_data) <= max_mb * 1024 * 1024

    def extract_filename(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        filename = path.split("/")[-1]

        # If filename is empty or generic, try query params
        if not filename or "." not in filename:
            qs = urllib.parse.parse_qs(parsed.query)
            if "file" in qs:
                filename = qs["file"][0]
            elif "filename" in qs:
                filename = qs["filename"][0]

        return urllib.parse.unquote(filename)

    def sanitize_filename(self, filename: str) -> str:
        # Remove directory traversal
        filename = re.sub(r"[/\\]", "", filename)
        # Remove ..
        filename = filename.replace("..", "")
        # Remove control characters
        filename = re.sub(r"[\x00-\x1f]", "", filename)
        return filename
