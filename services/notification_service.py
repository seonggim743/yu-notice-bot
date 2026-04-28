"""
NotificationService - Strategy Pattern Implementation

This service orchestrates notifications across multiple channels.
Channels are injected via constructor, enabling OCP compliance.
"""
import aiohttp
from typing import Dict, List, Optional, Any, Tuple

from core.config import settings
from core.logger import get_logger
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
        
        # Send to all enabled channels
        results = await service.send_all(session, notice, is_new=True)
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
    
    @property
    def enabled_channels(self) -> List[NotificationChannel]:
        """Returns only enabled channels."""
        return [ch for ch in self._channels if ch.is_enabled()]
    
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
    
    # =========================================================================
    # Strategy Pattern - Unified Send Methods
    # =========================================================================
    
    async def send_all(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_ids: Optional[Dict[str, Any]] = None,
        changes: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Send notice to all enabled channels.
        
        Args:
            session: aiohttp client session
            notice: Notice to send
            is_new: True if new notice, False if modified
            modified_reason: Description of modifications
            existing_message_ids: Dict mapping channel_name -> message_id for updates
            changes: Dictionary of detected changes
            
        Returns:
            Dict mapping channel_name -> message_id (or None if failed)
        """
        existing_message_ids = existing_message_ids or {}
        results = {}
        
        for channel in self.enabled_channels:
            try:
                existing_id = existing_message_ids.get(channel.channel_name)
                result = await channel.send_notice(
                    session=session,
                    notice=notice,
                    is_new=is_new,
                    modified_reason=modified_reason,
                    existing_message_id=existing_id,
                    changes=changes,
                )
                results[channel.channel_name] = result
                
                if result:
                    logger.info(
                        f"[NOTIFICATION] {channel.channel_name}: Sent successfully (ID: {result})"
                    )
                else:
                    logger.warning(
                        f"[NOTIFICATION] {channel.channel_name}: Send returned None"
                    )
                    
            except Exception as e:
                logger.error(
                    f"[NOTIFICATION] {channel.channel_name}: Send failed - {e}"
                )
                results[channel.channel_name] = None
        
        return results
    
    async def send_canvas_message(
        self,
        session: aiohttp.ClientSession,
        text: str,
        text_html: Optional[str] = None,
        routing_key: str = "canvas",
        event_kind: Optional[str] = None,
        attachment_payloads: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Broadcast a Canvas notification to all enabled channels.

        - `text` is the plain-text rendering used by Discord embed bodies
          and as the Telegram fallback if `text_html` isn't provided.
        - `text_html` is the Telegram parse_mode="HTML" rendering with
          <b>/<blockquote> markup; pass it whenever the channel can render
          rich text.
        - `attachment_payloads` is the per-file payload list produced by
          CanvasService._build_attachment_payloads. Each notifier handles
          its own preview-batch + original-file reply chain.

        Routing:
        - Telegram: settings.TELEGRAM_TOPIC_MAP[routing_key] for topic id
        - Discord:  settings.DISCORD_CHANNEL_MAP[routing_key] for channel id
        """
        results: Dict[str, Any] = {}

        telegram = self.telegram
        if telegram and telegram.is_enabled():
            topic_id = settings.TELEGRAM_TOPIC_MAP.get(routing_key)
            try:
                results["telegram"] = await telegram.send_canvas_message(
                    session,
                    text_html or text,
                    topic_id=topic_id,
                    attachment_payloads=attachment_payloads,
                    use_html=bool(text_html),
                )
            except Exception as e:
                logger.error(f"[NOTIFICATION] Telegram canvas send failed: {e}")
                results["telegram"] = None

        discord = self.discord
        if discord and discord.is_enabled():
            channel_id = settings.DISCORD_CHANNEL_MAP.get(routing_key)
            if not channel_id:
                logger.warning(
                    f"[NOTIFICATION] DISCORD_CHANNEL_MAP has no '{routing_key}' "
                    "key; skipping Discord canvas message."
                )
                results["discord"] = None
            else:
                try:
                    results["discord"] = await discord.send_canvas_message(
                        session,
                        text,
                        channel_id=channel_id,
                        event_kind=event_kind,
                        attachment_payloads=attachment_payloads,
                    )
                except Exception as e:
                    logger.error(f"[NOTIFICATION] Discord canvas send failed: {e}")
                    results["discord"] = None

        return results

    # =========================================================================
    # Backward Compatibility - Legacy Methods
    # These delegate to the new Strategy-based implementation
    # =========================================================================
    
    @property
    def telegram(self) -> Optional[TelegramNotifier]:
        """Legacy accessor for Telegram notifier."""
        ch = self.get_channel("telegram")
        return ch if isinstance(ch, TelegramNotifier) else None
    
    @property
    def discord(self) -> Optional[DiscordNotifier]:
        """Legacy accessor for Discord notifier."""
        ch = self.get_channel("discord")
        return ch if isinstance(ch, DiscordNotifier) else None
    
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
        Legacy method - Sends a notice to Telegram.
        Delegates to TelegramNotifier.
        """
        telegram = self.telegram
        if telegram and telegram.is_enabled():
            return await telegram.send_telegram(
                session, notice, is_new, modified_reason, existing_message_id, changes
            )
        return None
    
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
        Legacy method - Sends a notice to Discord.
        Delegates to DiscordNotifier.
        """
        discord = self.discord
        if discord and discord.is_enabled():
            return await discord.send_discord(
                session, notice, is_new, modified_reason, existing_thread_id, changes
            )
        return None
    
    async def send_menu_notification(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        menu_data: Dict[str, Any],
    ):
        """
        Legacy method - Sends menu notification to Telegram.
        Delegates to TelegramNotifier.
        """
        telegram = self.telegram
        if telegram and telegram.is_enabled():
            return await telegram.send_menu_notification(session, notice, menu_data)
        return None
    
    def generate_clean_diff(self, old_text: str, new_text: str) -> str:
        """
        Legacy method - Generates a clean diff.
        Delegates to first available channel (all use same base method).
        """
        if self._channels:
            return self._channels[0].generate_clean_diff(old_text, new_text)
        
        # Fallback
        from services.notification.formatters import generate_clean_diff
        return generate_clean_diff(old_text, new_text)
