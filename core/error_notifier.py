import asyncio
import aiohttp
import html
import traceback
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from enum import Enum
from collections import defaultdict
from core.config import settings
from core.logger import get_logger
from core.utils import get_now

logger = get_logger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels"""

    CRITICAL = "🔴 CRITICAL"
    ERROR = "🟠 ERROR"
    WARNING = "🟡 WARNING"
    HIGH = "🟠 HIGH"
    MEDIUM = "🟡 MEDIUM"
    LOW = "🟢 LOW"


class ErrorNotifier:
    """Centralized error notification system with rate limiting"""

    def __init__(self):
        self.error_history: Dict[str, List[datetime]] = defaultdict(list)
        self.max_errors_per_hour = 5
        self.notification_cooldown = timedelta(hours=1)

    def _should_notify(self, error_key: str) -> bool:
        """
        Check if notification should be sent based on rate limiting.

        Args:
            error_key: Unique identifier for the error type

        Returns:
            True if notification should be sent, False otherwise
        """
        now = get_now()

        # Clean old errors (older than 1 hour)
        self.error_history[error_key] = [
            timestamp
            for timestamp in self.error_history[error_key]
            if now - timestamp < self.notification_cooldown
        ]

        # Check if limit exceeded
        if len(self.error_history[error_key]) >= self.max_errors_per_hour:
            logger.warning(
                f"Error notification rate limit exceeded for '{error_key}' "
                f"({len(self.error_history[error_key])}/{self.max_errors_per_hour})"
            )
            return False

        # Record this notification
        self.error_history[error_key].append(now)
        return True

    async def send_critical_error(
        self,
        error_message: str,
        exception: Optional[Exception] = None,
        context: Optional[Dict] = None,
        severity: ErrorSeverity = ErrorSeverity.CRITICAL,
    ) -> bool:
        """
        Send critical error notification to Discord and Telegram.

        Args:
            error_message: Human-readable error description
            exception: Optional exception object
            context: Optional context dictionary (e.g., {"notice_id": 123})
            severity: Error severity level

        Returns:
            True if notification was sent successfully
        """
        error_key = f"{severity.name}:{error_message[:50]}"

        # Rate limiting check
        if not self._should_notify(error_key):
            return False

        logger.error(
            f"Sending error notification: {error_message}",
            context=context or {},
            exc_info=exception is not None,
        )

        # Prepare error details
        error_details = {
            "message": error_message,
            "severity": severity.value,
            "timestamp": get_now().isoformat(),
            "context": context or {},
        }

        if exception:
            error_details["exception_type"] = type(exception).__name__
            error_details["exception_message"] = str(exception)
            error_details["traceback"] = traceback.format_exc()

        # Send notifications in parallel
        async with aiohttp.ClientSession() as session:
            tasks = []

            if settings.DISCORD_BOT_TOKEN:
                tasks.append(self._send_discord_error(session, error_details))

            if settings.TELEGRAM_TOKEN:
                tasks.append(self._send_telegram_error(session, error_details))

            if not tasks:
                logger.warning("No notification channels configured for error alerts")
                return False

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check if at least one succeeded
            success = any(r is True for r in results)

            if not success:
                logger.error(f"All error notifications failed: {results}")

            return success

    async def _send_discord_error(
        self, session: aiohttp.ClientSession, error_details: Dict
    ) -> bool:
        """Send error notification to Discord via Bot API (Threaded)"""
        try:
            if not settings.DISCORD_BOT_TOKEN:
                return False

            # Determine Channel ID
            channel_id = settings.DISCORD_ERROR_CHANNEL_ID
            if not channel_id:
                channel_id = settings.DISCORD_CHANNEL_MAP.get("dev")
            
            if not channel_id:
                logger.warning("No Discord Error Channel ID configured")
                return False

            severity_colors = {
                "🔴 CRITICAL": 0xFF0000,
                "🟠 HIGH": 0xFF8C00,
                "🟡 MEDIUM": 0xFFD700,
                "🟢 LOW": 0x00FF00,
            }
            color = severity_colors.get(error_details["severity"], 0xFF0000)

            # 1. Prepare Initial Embed (Summary)
            # Truncate description to avoid limit
            description = error_details["message"]
            if len(description) > 2000:
                description = description[:2000] + "\n... (truncated)"

            embed = {
                "title": f"{error_details['severity']} System Error",
                "description": description,
                "color": color,
                "timestamp": error_details["timestamp"],
                "fields": [],
            }

            if error_details["context"]:
                context_str = "\n".join(f"**{k}**: {v}" for k, v in error_details["context"].items())
                embed["fields"].append({"name": "📋 Context", "value": context_str, "inline": False})

            if "exception_type" in error_details:
                embed["fields"].append({
                    "name": "⚠️ Exception", 
                    "value": f"`{error_details['exception_type']}: {error_details['exception_message']}`", 
                    "inline": False
                })

            # 2. Prepare Traceback Chunks
            traceback_chunks = []
            if "traceback" in error_details:
                tb_content = error_details["traceback"]
                # Split into chunks of 1900 to allow for code blocks and pagination info
                chunk_size = 1900
                traceback_chunks = [tb_content[i:i+chunk_size] for i in range(0, len(tb_content), chunk_size)]

            # 3. Create Thread (or Message + Thread)
            # Try creating a Forum Thread first (requires 'message' field)
            thread_name = f"[{error_details['severity']}] {error_details['message'][:50]}"
            
            url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
            headers = {
                "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            }
            
            # Payload for Forum Thread
            payload = {
                "name": thread_name,
                "auto_archive_duration": 1440,
                "message": {"embeds": [embed]}
            }

            thread_id = None
            
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status in [200, 201]:
                    data = await resp.json()
                    thread_id = data.get("id")
                    logger.info(f"Created Discord thread: {thread_id}")
                elif resp.status == 400:
                    # Likely not a Forum Channel. Try sending a message to Text Channel then create thread.
                    # Fallback: Send Message to Channel
                    msg_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    async with session.post(msg_url, headers=headers, json={"embeds": [embed]}) as msg_resp:
                        if msg_resp.status in [200, 201]:
                            msg_data = await msg_resp.json()
                            msg_id = msg_data.get("id")
                            
                            # Create Thread on that message
                            thread_url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg_id}/threads"
                            async with session.post(thread_url, headers=headers, json={"name": thread_name, "auto_archive_duration": 1440}) as th_resp:
                                if th_resp.status in [200, 201]:
                                    th_data = await th_resp.json()
                                    thread_id = th_data.get("id")
                        else:
                            logger.error(f"Discord fallback message failed: {await msg_resp.text()}")
                            return False
                else:
                    logger.error(f"Discord thread creation failed: {await resp.text()}")
                    return False

            # 4. Send Traceback Chunks to Thread
            if thread_id and traceback_chunks:
                thread_msg_url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
                total_chunks = len(traceback_chunks)
                
                for i, chunk in enumerate(traceback_chunks):
                    pagination = f"**Traceback ({i+1}/{total_chunks})**"
                    content = f"{pagination}\n```python\n{chunk}\n```"
                    
                    async with session.post(thread_msg_url, headers=headers, json={"content": content}) as resp:
                        if resp.status not in [200, 201]:
                            logger.error(f"Failed to send Discord traceback chunk {i+1}: {await resp.text()}")
                        await asyncio.sleep(0.5)

            return True

        except Exception as e:
            logger.error(f"Failed to send Discord error notification: {e}")
            return False

    async def _send_telegram_error(
        self, session: aiohttp.ClientSession, error_details: Dict
    ) -> bool:
        """Send error notification to Telegram with threading and pagination"""
        try:
            # 1. Build Base Message (Header + Context + Message + Exception)
            header = f"<b>{error_details['severity']} System Error</b>\n"
            
            context_part = ""
            if error_details["context"]:
                context_part += "\n📋 <b>Context:</b>\n"
                for k, v in error_details["context"].items():
                    context_part += f"  • <b>{k}:</b> {v}\n"

            message_part = f"\n📝 <b>Message:</b>\n{error_details['message']}\n"

            exception_part = ""
            if "exception_type" in error_details:
                exception_part += "\n⚠️ <b>Exception:</b>\n"
                exception_part += f"<code>{error_details['exception_type']}: {error_details['exception_message']}</code>\n"

            base_message = header + context_part + message_part + exception_part
            
            # 2. Prepare Traceback Chunks
            traceback_chunks = []
            if "traceback" in error_details:
                tb_content = error_details["traceback"]
                # Telegram limit 4096. Base message takes some space.
                # Strategy: Send Base Message first. Then Traceback chunks.
                
                # Chunk size for traceback: 4000 (safe margin)
                chunk_size = 4000
                traceback_chunks = [tb_content[i:i+chunk_size] for i in range(0, len(tb_content), chunk_size)]

            # 3. Send Messages
            url = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage"
            topic_id = settings.TELEGRAM_ERROR_TOPIC_ID
            
            # Send First Message (Base)
            # Add (1/N) if there are traceback chunks
            total_pages = 1 + len(traceback_chunks)
            pagination_header = f"(1/{total_pages}) " if total_pages > 1 else ""
            
            first_payload = {
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": pagination_header + base_message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if topic_id:
                first_payload["message_thread_id"] = topic_id

            first_msg_id = None
            async with session.post(url, json=first_payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    first_msg_id = data.get("result", {}).get("message_id")
                else:
                    logger.error(f"Telegram base message failed: {await resp.text()}")
                    return False

            # Send Traceback Chunks (Reply to First Message)
            if first_msg_id and traceback_chunks:
                for i, chunk in enumerate(traceback_chunks):
                    page_num = i + 2
                    pagination = f"({page_num}/{total_pages}) 🔍 <b>Traceback:</b>\n"
                    msg_content = f"{pagination}<pre>{html.escape(chunk)}</pre>"
                    
                    # If it's the last chunk, add timestamp footer
                    if i == len(traceback_chunks) - 1:
                        msg_content += f"\n🕐 {error_details['timestamp']}"

                    payload = {
                        "chat_id": settings.TELEGRAM_CHAT_ID,
                        "text": msg_content,
                        "parse_mode": "HTML",
                        "reply_to_message_id": first_msg_id, # Threading!
                    }
                    if topic_id:
                        payload["message_thread_id"] = topic_id

                    async with session.post(url, json=payload) as resp:
                        if resp.status != 200:
                            logger.error(f"Telegram chunk {page_num} failed: {await resp.text()}")
                        await asyncio.sleep(0.2)

            logger.info("Error notification sent to Telegram")
            return True

        except Exception as e:
            logger.error(f"Failed to send Telegram error notification: {e}")
            return False


# =============================================================================
# Dependency Injection Support
# =============================================================================

# Global instance with thread-safe initialization
_error_notifier = None
_error_notifier_lock = __import__("threading").Lock()


def set_error_notifier(notifier: ErrorNotifier) -> None:
    """
    Set the global error notifier instance (Composition Root pattern).
    
    Call this from main.py to inject a configured ErrorNotifier instance.
    This enables proper Dependency Injection while maintaining backward
    compatibility with code that uses get_error_notifier().
    
    Args:
        notifier: Configured ErrorNotifier instance
        
    Example:
        # In main.py (Composition Root)
        error_notifier = ErrorNotifier()
        set_error_notifier(error_notifier)
        
        # Pass to services that need it
        bot = Bot(error_notifier=error_notifier)
    """
    global _error_notifier
    with _error_notifier_lock:
        _error_notifier = notifier
    logger.debug("[ERROR_NOTIFIER] Global instance set via DI")


def get_error_notifier() -> ErrorNotifier:
    """
    Get error notifier instance.
    
    If set_error_notifier() was called, returns that instance.
    Otherwise, creates a new instance (backward compatibility).
    
    For new code, prefer receiving ErrorNotifier via constructor injection.
    
    Returns:
        ErrorNotifier instance
    """
    global _error_notifier
    
    # Fast path: already initialized
    if _error_notifier is not None:
        return _error_notifier
    
    # Slow path: need to initialize with lock
    with _error_notifier_lock:
        # Double-check after acquiring lock
        if _error_notifier is None:
            _error_notifier = ErrorNotifier()
    
    return _error_notifier


def _reset_error_notifier_for_testing() -> None:
    """Reset global state for testing purposes only."""
    global _error_notifier
    with _error_notifier_lock:
        _error_notifier = None
