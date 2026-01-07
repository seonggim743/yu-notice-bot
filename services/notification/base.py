"""
Notification System - Strategy Pattern Implementation

This module defines the abstract interface for notification channels
following the Strategy Pattern for OCP compliance.
"""
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiohttp
from aiohttp import MultipartWriter

from models.notice import Notice
from services.notification.formatters import generate_clean_diff

if TYPE_CHECKING:
    pass


class NotificationChannel(ABC):
    """
    Abstract base class for notification channels (Strategy Pattern).
    
    All notification implementations must implement this interface.
    New channels can be added without modifying NotificationService (OCP).
    
    Usage:
        class SlackChannel(NotificationChannel):
            async def send_notice(self, session, notice, is_new, ...):
                # Slack-specific implementation
                pass
    """
    
    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Returns the name of this notification channel (e.g., 'telegram', 'discord')."""
        pass
    
    @abstractmethod
    async def send_notice(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[Any] = None,
        changes: Optional[Dict] = None,
    ) -> Optional[Any]:
        """
        Send a notice through this channel.
        
        Args:
            session: aiohttp client session
            notice: Notice object to send
            is_new: True if this is a new notice, False if modified
            modified_reason: Description of modifications (for updates)
            existing_message_id: ID of existing message to update (platform-specific)
            changes: Dictionary of detected changes
            
        Returns:
            Platform-specific message ID if successful, None otherwise
        """
        pass
    
    @abstractmethod
    def is_enabled(self) -> bool:
        """
        Check if this channel is enabled (has required configuration).
        
        Returns:
            True if channel can send messages, False otherwise
        """
        pass


class BaseNotifier:
    """
    Base class with common utilities for all notification services.
    Provides helper methods for multipart form building and diff generation.
    """

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
