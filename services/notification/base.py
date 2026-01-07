"""
Base notification utilities shared between Telegram and Discord.
"""
import urllib.parse
from typing import Any

from aiohttp import MultipartWriter

from services.notification.formatters import generate_clean_diff


class BaseNotifier:
    """Base class with common utilities for all notification services."""

    def _add_text_part(self, writer: MultipartWriter, name: str, value: Any) -> None:
        """Adds a text field to MultipartWriter."""
        part = writer.append(str(value))
        part.set_content_disposition("form-data", name=name)

    def _add_file_part(
        self,
        writer: MultipartWriter,
        field_name: str,
        file_data: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        """
        Adds a file to MultipartWriter with manual Content-Disposition header.
        Supports both raw UTF-8 (Discord/Legacy) and RFC 5987 (Telegram/Standard).
        """
        # 1. Append payload
        part = writer.append(file_data, {"Content-Type": content_type})

        # 2. Prepare filenames
        # RFC 5987: Percent-encoded
        filename_star = urllib.parse.quote(filename)
        # Legacy: Raw UTF-8 (escape quotes)
        filename_legacy = filename.replace('"', '\\"')

        # 3. Construct new header value
        new_header_value = (
            f'form-data; name="{field_name}"; '
            f'filename="{filename_legacy}"; '
            f"filename*=UTF-8''{filename_star}"
        )

        # 4. Set Header
        part.headers["Content-Disposition"] = new_header_value

    def generate_clean_diff(self, old_text: str, new_text: str) -> str:
        """
        Generates a clean, line-by-line diff showing only changes.
        Delegates to formatters module.
        """
        return generate_clean_diff(old_text, new_text)
