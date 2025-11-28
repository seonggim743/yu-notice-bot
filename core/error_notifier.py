import asyncio
import aiohttp
import traceback
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from enum import Enum
from collections import defaultdict
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels"""
    CRITICAL = "🔴 CRITICAL"
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
        now = datetime.now()
        
        # Clean old errors (older than 1 hour)
        self.error_history[error_key] = [
            timestamp for timestamp in self.error_history[error_key]
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
        severity: ErrorSeverity = ErrorSeverity.CRITICAL
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
            exc_info=exception is not None
        )
        
        # Prepare error details
        error_details = {
            "message": error_message,
            "severity": severity.value,
            "timestamp": datetime.now().isoformat(),
            "context": context or {}
        }
        
        if exception:
            error_details["exception_type"] = type(exception).__name__
            error_details["exception_message"] = str(exception)
            error_details["traceback"] = traceback.format_exc()
        
        # Send notifications in parallel
        async with aiohttp.ClientSession() as session:
            tasks = []
            
            if settings.DISCORD_WEBHOOK_URL:
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
        self,
        session: aiohttp.ClientSession,
        error_details: Dict
    ) -> bool:
        """Send error notification to Discord"""
        try:
            severity_colors = {
                "🔴 CRITICAL": 0xFF0000,  # Red
                "🟠 HIGH": 0xFF8C00,       # Orange
                "🟡 MEDIUM": 0xFFD700,     # Gold
                "🟢 LOW": 0x00FF00         # Green
            }
            
            color = severity_colors.get(error_details["severity"], 0xFF0000)
            
            embed = {
                "title": f"{error_details['severity']} System Error",
                "description": error_details["message"],
                "color": color,
                "timestamp": error_details["timestamp"],
                "fields": []
            }
            
            # Add context if present
            if error_details["context"]:
                context_str = "\n".join(
                    f"**{k}**: {v}" for k, v in error_details["context"].items()
                )
                embed["fields"].append({
                    "name": "📋 Context",
                    "value": context_str,
                    "inline": False
                })
            
            # Add exception details
            if "exception_type" in error_details:
                embed["fields"].append({
                    "name": "⚠️ Exception",
                    "value": f"`{error_details['exception_type']}: {error_details['exception_message']}`",
                    "inline": False
                })
            
            # Add truncated traceback
            if "traceback" in error_details:
                traceback_preview = error_details["traceback"][:500]
                if len(error_details["traceback"]) > 500:
                    traceback_preview += "\n... (truncated)"
                
                embed["fields"].append({
                    "name": "🔍 Traceback",
                    "value": f"```python\n{traceback_preview}\n```",
                    "inline": False
                })
            
            payload = {"embeds": [embed]}
            
            async with session.post(
                settings.DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status in [200, 204]:
                    logger.info("Error notification sent to Discord")
                    return True
                else:
                    error_text = await resp.text()
                    logger.error(f"Discord error notification failed: {resp.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send Discord error notification: {e}")
            return False
    
    async def _send_telegram_error(
        self,
        session: aiohttp.ClientSession,
        error_details: Dict
    ) -> bool:
        """Send error notification to Telegram"""
        try:
            # Build message
            msg_parts = [
                f"<b>{error_details['severity']} System Error</b>",
                "",
                f"📝 <b>Message:</b>",
                error_details["message"],
            ]
            
            # Add context
            if error_details["context"]:
                msg_parts.append("")
                msg_parts.append("📋 <b>Context:</b>")
                for k, v in error_details["context"].items():
                    msg_parts.append(f"  • <b>{k}:</b> {v}")
            
            # Add exception
            if "exception_type" in error_details:
                msg_parts.append("")
                msg_parts.append("⚠️ <b>Exception:</b>")
                msg_parts.append(f"<code>{error_details['exception_type']}: {error_details['exception_message']}</code>")
            
            # Add truncated traceback
            if "traceback" in error_details:
                msg_parts.append("")
                msg_parts.append("🔍 <b>Traceback:</b>")
                traceback_preview = error_details["traceback"][:300]
                if len(error_details["traceback"]) > 300:
                    traceback_preview += "\n... (truncated)"
                msg_parts.append(f"<pre>{traceback_preview}</pre>")
            
            msg_parts.append("")
            msg_parts.append(f"🕐 {error_details['timestamp']}")
            
            message = "\n".join(msg_parts)
            
            payload = {
                'chat_id': settings.TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            url = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage"
            
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info("Error notification sent to Telegram")
                    return True
                else:
                    error_text = await resp.text()
                    logger.error(f"Telegram error notification failed: {resp.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send Telegram error notification: {e}")
            return False


# Global instance
_error_notifier = None

def get_error_notifier() -> ErrorNotifier:
    """Get singleton error notifier instance"""
    global _error_notifier
    if _error_notifier is None:
        _error_notifier = ErrorNotifier()
    return _error_notifier
