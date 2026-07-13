"""
NotificationService - Strategy Pattern Implementation

This service orchestrates notifications across multiple channels.
Channels are injected via constructor, enabling OCP compliance.
"""
import aiohttp
from typing import Dict, List, Optional, Any

from core.logger import get_logger
from models.delivery import DeliveryResult
from models.notice import Notice
from services.notification.base import NotificationChannel
from services.notification.telegram import TelegramNotifier
from services.notification.discord import DiscordNotifier

logger = get_logger(__name__)


class NotificationService:
    """
    Unified notification service using Strategy Pattern.
    
    Channels are injected via constructor, enabling:
    - OCP compliance: Add new channels without modifying this class
    - Testability: Inject mock channels for testing
    - Flexibility: Enable/disable channels dynamically
    
    Usage:
        # Default (auto-creates Telegram + Discord)
        service = NotificationService()
        
        # Custom channels (DI)
        channels = [TelegramNotifier(), SlackNotifier()]
        service = NotificationService(channels=channels)
        
        # Select a route and deliver through the unified interface
        channels = service.eligible_channels(notice.site_key)
        result = await service.deliver_notice(channels[0], session, notice, True)
    """
    
    def __init__(
        self,
        channels: Optional[List[NotificationChannel]] = None,
    ):
        """
        Initialize NotificationService with notification channels.
        
        Args:
            channels: List of NotificationChannel implementations.
                     If not provided, creates default Telegram + Discord channels.
        """
        if channels is not None:
            self._channels = channels
        else:
            # Default: Create Telegram and Discord channels
            self._channels = [
                TelegramNotifier(),
                DiscordNotifier(),
            ]
        
        # Log enabled channels
        enabled = [ch.channel_name for ch in self._channels if ch.is_enabled()]
        logger.info(f"[NOTIFICATION] Initialized with channels: {enabled}")
    
    @property
    def channels(self) -> List[NotificationChannel]:
        """Returns all registered channels."""
        return self._channels
    
    def get_channel(self, name: str) -> Optional[NotificationChannel]:
        """
        Get a specific channel by name.
        
        Args:
            name: Channel name (e.g., 'telegram', 'discord')
            
        Returns:
            NotificationChannel if found, None otherwise
        """
        for ch in self._channels:
            if ch.channel_name == name:
                return ch
        return None

    def eligible_channels(self, site_key: str) -> List[str]:
        """Return only channels that have a usable route for this site."""
        return [
            channel.channel_name
            for channel in self._channels
            if channel.can_deliver(site_key)
        ]

    async def deliver_notice(
        self,
        channel: str,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[Any] = None,
        changes: Optional[Dict] = None,
    ) -> DeliveryResult:
        """Deliver through one route and normalize the channel result."""
        notifier = self.get_channel(channel)
        if notifier is None:
            return DeliveryResult(channel, False, error="unknown notification channel")
        if not notifier.can_deliver(notice.site_key):
            return DeliveryResult(channel, False, error="notification route is unavailable")

        try:
            external_id = await notifier.send_notice(
                session=session,
                notice=notice,
                is_new=is_new,
                modified_reason=modified_reason,
                existing_message_id=existing_message_id,
                changes=changes,
            )
        except Exception as exc:
            logger.exception(f"[NOTIFICATION] {channel}: delivery failed")
            return DeliveryResult(channel, False, error=str(exc))

        if external_id is None:
            return DeliveryResult(
                channel,
                False,
                error="primary notification returned no external message ID",
            )
        return DeliveryResult(channel, True, external_id=external_id)
