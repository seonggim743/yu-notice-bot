import logging
import sys
import re
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
import pytz
from core.config import settings

# KST Timezone
KST = pytz.timezone("Asia/Seoul")


class SensitiveDataFilter(logging.Filter):
    """Filter to mask sensitive data in logs"""

    PATTERNS = [
        (r"(TELEGRAM_TOKEN=|bot)[0-9]{8,}:[A-Za-z0-9_-]{35}", r"\1***MASKED***"),
        (r"(GEMINI_API_KEY=|AIza)[A-Za-z0-9_-]{35,}", r"\1***MASKED***"),
        (r"(SUPABASE_KEY=|eyJ)[A-Za-z0-9_.-]{100,}", r"\1***MASKED***"),
        (r"(OPENAI_API_KEY=|sk-)[A-Za-z0-9]{40,}", r"\1***MASKED***"),
        (
            r"(DISCORD_WEBHOOK_URL=|https://discord\.com/api/webhooks/)[0-9]+/[A-Za-z0-9_-]+",
            r"\1***MASKED***",
        ),
        (r"(CANVAS_TOKEN=)[A-Za-z0-9]{50,}", r"\1***MASKED***"),
        (r"https://[a-z0-9-]+\.supabase\.co", r"***SUPABASE_URL***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._mask_sensitive(record.msg)

        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self._mask_sensitive(arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)
        return True

    def _mask_sensitive(self, text: str) -> str:
        for pattern, replacement in self.PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text


class KSTFormatter(logging.Formatter):
    """Formatter that uses KST timezone with structured context support"""

    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp)
        return dt.astimezone(KST)

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds")

    def format(self, record):
        """Override to add structured context"""
        base_msg = super().format(record)

        # Add structured context if present
        if hasattr(record, "context") and record.context:
            context_str = " | ".join(f"{k}={v}" for k, v in record.context.items())
            return f"{base_msg} | {context_str}"

        return base_msg


class PerformanceFormatter(KSTFormatter):
    """Specialized formatter for performance logs"""

    def format(self, record):
        base_msg = super().format(record)

        # Add timing information if present
        if hasattr(record, "duration_ms"):
            return f"{base_msg} | ⏱️ {record.duration_ms:.2f}ms"
        elif hasattr(record, "duration"):
            return f"{base_msg} | ⏱️ {record.duration:.2f}s"

        return base_msg


class StructuredLoggerAdapter(logging.LoggerAdapter):
    """Adapter to add structured context to log messages"""

    def process(self, msg, kwargs):
        # Extract context from kwargs
        context = kwargs.pop("context", {})

        # Add to extra for formatter to access
        if "extra" not in kwargs:
            kwargs["extra"] = {}
        kwargs["extra"]["context"] = context

        # Add duration if present
        if "duration" in kwargs:
            kwargs["extra"]["duration"] = kwargs.pop("duration")
        if "duration_ms" in kwargs:
            kwargs["extra"]["duration_ms"] = kwargs.pop("duration_ms")

        return msg, kwargs


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def format(self, record):
        log_record = {
            "timestamp": datetime.fromtimestamp(record.created)
            .astimezone(KST)
            .isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }

        # Add context if present
        if hasattr(record, "context") and record.context:
            log_record["context"] = record.context

        # Add timing if present
        if hasattr(record, "duration"):
            log_record["duration_seconds"] = record.duration
        if hasattr(record, "duration_ms"):
            log_record["duration_ms"] = record.duration_ms

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            log_record["traceback"] = self.formatException(record.exc_info)

        return json.dumps(log_record, ensure_ascii=False)


import requests
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

class DiscordLogHandler(logging.Handler):
    """
    Custom handler to send WARNING/ERROR logs to Discord.
    Uses ThreadPoolExecutor to avoid blocking the main thread.
    Implements throttling to prevent spam.
    """
    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.last_errors = {}  # {hash_key: last_time}
        self.webhook_url = settings.DISCORD_ERROR_CHANNEL_ID # Using Channel ID as Webhook URL for simplicity or need to adapt if it's just ID.
        # Wait, config says DISCORD_ERROR_CHANNEL_ID is a channel ID, not webhook.
        # If we use bot token, we need to post to channel.
        self.bot_token = settings.DISCORD_BOT_TOKEN
        self.channel_id = settings.DISCORD_ERROR_CHANNEL_ID

    def _send_to_discord(self, record: logging.LogRecord):
        try:
            if not self.bot_token or not self.channel_id:
                return

            # Format message
            color = 0xFF0000 if record.levelno >= logging.ERROR else 0xFFA500 # Red or Orange
            
            # Create Embed
            embed = {
                "title": f"[{record.levelname}] {record.name}",
                "description": record.getMessage(),
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": f"Module: {record.module}:{record.lineno}"}
            }
            
            # Add traceback if available
            if record.exc_info:
                exc_text = self.formatException(record.exc_info)
                # Truncate if too long (Discord limit 1024)
                if len(exc_text) > 1000:
                    exc_text = exc_text[:1000] + "..."
                embed["fields"] = [{"name": "Traceback", "value": f"```python\n{exc_text}\n```"}]

            payload = {
                "embeds": [embed]
            }

            # Send request (Synchronous inside ThreadPool)
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json"
            }
            url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            
            requests.post(url, headers=headers, json=payload, timeout=2.0)
            
        except Exception as e:
            # Fallback to stderr if Discord fails
            sys.stderr.write(f"Failed to send log to Discord: {e}\n")

    def emit(self, record: logging.LogRecord):
        try:
            # 1. Throttling Check
            # Create hash key from static parts (pathname, lineno, msg template)
            # We use record.msg (template) instead of record.getMessage() (formatted) to group similar errors
            msg_key = hashlib.md5(f"{record.pathname}:{record.lineno}:{str(record.msg)}".encode()).hexdigest()
            current_time = time.time()
            
            if msg_key in self.last_errors:
                if current_time - self.last_errors[msg_key] < 60:
                    return # Ignore duplicate within 60s
            
            self.last_errors[msg_key] = current_time

            # 2. Async Dispatch
            self.executor.submit(self._send_to_discord, record)
            
        except Exception:
            self.handleError(record)

    def close(self):
        """Graceful shutdown"""
        self.executor.shutdown(wait=False)
        super().close()


def get_logger(
    name: str,
    log_level: Optional[str] = None,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Get a configured logger instance with console, file, and Discord handlers.
    """
    # Get or create logger
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger

    # Set log level
    if log_level is None:
        log_level = settings.LOG_LEVEL

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console Formatter (Human-readable with KST)
    console_formatter = PerformanceFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File Formatter
    if settings.LOG_FORMAT.lower() == "json":
        file_formatter = JSONFormatter()
    else:
        file_formatter = PerformanceFormatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(SensitiveDataFilter())

    # File Handler (with rotation)
    if log_file is None:
        log_file = settings.LOG_FILE

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # File gets all levels
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(SensitiveDataFilter())

        logger.addHandler(file_handler)

    # Discord Handler (WARNING+)
    if settings.DISCORD_BOT_TOKEN and settings.DISCORD_ERROR_CHANNEL_ID:
        discord_handler = DiscordLogHandler()
        discord_handler.setLevel(logging.WARNING)
        discord_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(discord_handler)

    # Add console handler
    logger.addHandler(console_handler)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    # Return wrapped logger with structured context support
    return StructuredLoggerAdapter(logger, {})


# Convenience function for quick setup
def setup_logging(log_level: str = "INFO", log_file: str = "bot.log") -> None:
    """
    Setup root logger configuration.

    Args:
        log_level: Log level for root logger
        log_file: Path to log file
    """
    root_logger = logging.getLogger()

    # Clear existing handlers
    root_logger.handlers.clear()

    # Configure using get_logger
    get_logger("root", log_level, log_file)
