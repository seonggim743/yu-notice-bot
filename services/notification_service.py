"""
Notification service - delegates to Telegram and Discord notifiers.
This file maintains backward compatibility with existing code that imports NotificationService.
"""
import aiohttp
from typing import Dict, Optional, Any

from models.notice import Notice
from services.notification.telegram import TelegramNotifier
from services.notification.discord import DiscordNotifier


class NotificationService:
    """
    Unified notification service that delegates to platform-specific notifiers.
    Maintains the same interface as the original NotificationService for backward compatibility.
    """

    def __init__(self):
        self.telegram = TelegramNotifier()
        self.discord = DiscordNotifier()

    async def send_telegram(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[int] = None,
        changes: Optional[Dict] = None,
    ) -> Optional[int]:
        """
        Sends a notice to Telegram with enhanced formatting. Returns the Message ID.
        Delegates to TelegramNotifier.
        """
        return await self.telegram.send_telegram(
            session, notice, is_new, modified_reason, existing_message_id, changes
        )

    async def send_discord(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_thread_id: str = None,
        changes: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Sends a notice to Discord (Forum Channel preferred).
        Returns the Thread ID (or Message ID) if successful, None otherwise.
        Delegates to DiscordNotifier.
        """
        return await self.discord.send_discord(
            session, notice, is_new, modified_reason, existing_thread_id, changes
        )

    async def send_menu_notification(
        self, session: aiohttp.ClientSession, notice: Notice, menu_data: Dict[str, Any]
    ):
        """
        Sends extracted menu text to Telegram and Pins it.
        Delegates to TelegramNotifier.
        """
        return await self.telegram.send_menu_notification(session, notice, menu_data)

    def generate_clean_diff(self, old_text: str, new_text: str) -> str:
        """
        Generates a clean, line-by-line diff showing only changes.
        Delegates to TelegramNotifier (both use the same base method).
        """
        return self.telegram.generate_clean_diff(old_text, new_text)
