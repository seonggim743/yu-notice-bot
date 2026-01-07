"""
Logging configuration with async-safe handlers.
Provides structured logging with Discord integration.
"""
import logging
import sys
import re
import json
import atexit
import hashlib
import time
import threading
from queue import Queue, Empty
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import pytz
import requests

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


class DiscordLogHandler(logging.Handler):
    """
    Async-safe Discord log handler using Queue + Background Worker Thread.
    
    Features:
    - Non-blocking: Uses queue to avoid blocking the event loop
    - Throttling: Prevents spam by deduplicating similar errors
    - Graceful shutdown: Flushes queue on exit via atexit
    - Memory-safe: Limits error cache and queue size
    """
    
    # Max number of error hashes to track (prevents memory leak)
    MAX_ERROR_CACHE = 100
    # Throttle window in seconds
    THROTTLE_SECONDS = 60
    # Max queue size to prevent memory issues
    MAX_QUEUE_SIZE = 100
    # Timeout for queue operations
    QUEUE_TIMEOUT = 0.1

    def __init__(self):
        super().__init__()
        self.bot_token = settings.DISCORD_BOT_TOKEN
        self.channel_id = settings.DISCORD_ERROR_CHANNEL_ID
        
        # Queue for async-safe logging
        self._queue: Queue = Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._last_errors: dict = {}  # {hash_key: timestamp}
        self._lock = threading.Lock()
        
        # Background worker thread
        self._shutdown = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        
        # Register atexit handler for graceful shutdown
        atexit.register(self._graceful_shutdown)

    def _worker_loop(self):
        """Background worker that processes log queue."""
        while not self._shutdown.is_set():
            try:
                # Wait for items with timeout (allows checking shutdown flag)
                record = self._queue.get(timeout=self.QUEUE_TIMEOUT)
                self._send_to_discord(record)
                self._queue.task_done()
            except Empty:
                continue
            except Exception as e:
                # Never crash the worker thread
                sys.stderr.write(f"[DiscordLogHandler] Worker error: {e}\n")

    def _send_to_discord(self, record: logging.LogRecord):
        """Send log record to Discord (runs in worker thread)."""
        try:
            if not self.bot_token or not self.channel_id:
                return

            # Format message
            color = 0xFF0000 if record.levelno >= logging.ERROR else 0xFFA500  # Red or Orange
            
            # Create Embed
            embed = {
                "title": f"[{record.levelname}] {record.name}",
                "description": record.getMessage()[:4000],  # Discord description limit
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": f"Module: {record.module}:{record.lineno}"}
            }
            
            # Add traceback if available
            if record.exc_info:
                exc_text = self.formatException(record.exc_info)
                # Truncate if too long (Discord field limit 1024)
                if len(exc_text) > 1000:
                    exc_text = exc_text[:1000] + "..."
                embed["fields"] = [{"name": "Traceback", "value": f"```python\n{exc_text}\n```"}]

            payload = {"embeds": [embed]}

            # Send request
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json"
            }
            url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            
            response = requests.post(url, headers=headers, json=payload, timeout=5.0)
            
            # Log failures but don't raise
            if response.status_code not in [200, 201, 204]:
                sys.stderr.write(f"Discord log failed: {response.status_code} - {response.text[:200]}\n")
                
        except requests.exceptions.Timeout:
            sys.stderr.write("Discord log timeout\n")
        except requests.exceptions.RequestException as e:
            sys.stderr.write(f"Discord log request error: {e}\n")
        except Exception as e:
            # Fallback to stderr if Discord fails
            sys.stderr.write(f"Failed to send log to Discord: {e}\n")

    def _cleanup_old_errors(self, current_time: float):
        """Remove old error hashes to prevent memory leak."""
        if len(self._last_errors) > self.MAX_ERROR_CACHE:
            # Remove entries older than throttle window
            expired_keys = [
                k for k, v in self._last_errors.items()
                if current_time - v > self.THROTTLE_SECONDS
            ]
            for k in expired_keys:
                del self._last_errors[k]

    def emit(self, record: logging.LogRecord):
        """Emit a log record (non-blocking)."""
        try:
            # 1. Throttling Check
            msg_key = hashlib.md5(
                f"{record.pathname}:{record.lineno}:{str(record.msg)}".encode()
            ).hexdigest()
            current_time = time.time()
            
            with self._lock:
                if msg_key in self._last_errors:
                    if current_time - self._last_errors[msg_key] < self.THROTTLE_SECONDS:
                        return  # Ignore duplicate within throttle window
                
                self._last_errors[msg_key] = current_time
                self._cleanup_old_errors(current_time)

            # 2. Queue the record (non-blocking)
            try:
                self._queue.put_nowait(record)
            except Exception:
                # Queue full, drop the log (better than blocking)
                sys.stderr.write("[DiscordLogHandler] Queue full, dropping log\n")
            
        except Exception:
            self.handleError(record)

    def _graceful_shutdown(self):
        """Graceful shutdown - flush queue before exit."""
        # Signal worker to stop
        self._shutdown.set()
        
        # Wait for worker to finish (max 5 seconds)
        self._worker.join(timeout=5.0)
        
        # Drain remaining queue items
        remaining = 0
        while not self._queue.empty():
            try:
                record = self._queue.get_nowait()
                self._send_to_discord(record)
                remaining += 1
            except Empty:
                break
        
        if remaining > 0:
            sys.stderr.write(f"[DiscordLogHandler] Flushed {remaining} logs on shutdown\n")

    def close(self):
        """Close the handler."""
        self._graceful_shutdown()
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
