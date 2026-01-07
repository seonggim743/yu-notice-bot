"""
Core utility functions for the bot.
Provides common functionality used across multiple modules.
"""
import re
import asyncio
import functools
from datetime import datetime, timezone
from typing import Optional, Callable, Any, TypeVar, Tuple
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from core.logger import get_logger

logger = get_logger(__name__)

# Type variable for generic async function return type
T = TypeVar('T')

# Standard timezone constants
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


def get_now() -> datetime:
    """
    Get current datetime in KST (Korea Standard Time).
    
    Always returns a timezone-aware datetime object.
    Use this instead of datetime.now() for consistent timezone handling.
    
    Returns:
        Current datetime with Asia/Seoul timezone
        
    Example:
        >>> from core.utils import get_now
        >>> now = get_now()
        >>> print(now.tzinfo)  # Asia/Seoul
    """
    return datetime.now(KST)


def get_utc_now() -> datetime:
    """
    Get current datetime in UTC.
    
    Always returns a timezone-aware datetime object.
    Use this for Discord/API timestamps that expect UTC.
    
    Returns:
        Current datetime with UTC timezone
    """
    return datetime.now(UTC)


def to_kst(dt: datetime) -> datetime:
    """
    Convert any datetime to KST.
    
    Args:
        dt: Datetime object (aware or naive)
        
    Returns:
        Datetime in KST timezone
    """
    if dt.tzinfo is None:
        # Assume naive datetime is in UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)


def parse_content_disposition(header_value: str, fallback_name: str = "") -> str:
    """
    Parses filename from Content-Disposition header.

    Supports both RFC 5987 (filename*=UTF-8'') and standard (filename=) formats.

    Args:
        header_value: The Content-Disposition header value
        fallback_name: Fallback filename if parsing fails

    Returns:
        Extracted filename or fallback_name
    """
    if not header_value:
        return fallback_name

    # Try filename* (RFC 5987) first - handles UTF-8 encoded filenames
    match_star = re.search(
        r"filename\*=UTF-8''([^;]+)",
        header_value,
        re.IGNORECASE
    )
    if match_star:
        return unquote(match_star.group(1))

    # Try standard filename
    match_std = re.search(
        r'filename=["\']?([^"\';]+)["\']?',
        header_value,
        re.IGNORECASE
    )
    if match_std:
        return unquote(match_std.group(1))

    return fallback_name


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exponential: bool = True,
    retryable_exceptions: Tuple[type, ...] = (Exception,),
    fail_fast_exceptions: Tuple[type, ...] = (),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
):
    """
    Decorator for async functions with retry logic and exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds between retries
        exponential: If True, uses exponential backoff (2^attempt * base_delay)
        retryable_exceptions: Tuple of exception types to retry on
        fail_fast_exceptions: Tuple of exception types to fail immediately on
        on_retry: Optional callback function called on each retry (attempt, exception)

    Returns:
        Decorated async function with retry logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except fail_fast_exceptions as e:
                    # Don't retry on these exceptions
                    raise e
                except retryable_exceptions as e:
                    last_exception = e
                    
                    if attempt >= max_retries:
                        # All retries exhausted
                        raise e
                    
                    # Calculate delay
                    if exponential:
                        delay = base_delay * (2 ** (attempt - 1))
                    else:
                        delay = base_delay
                    
                    # Call retry callback if provided
                    if on_retry:
                        on_retry(attempt, e)
                    
                    logger.warning(
                        f"[RETRY] {func.__name__} failed (Attempt {attempt}/{max_retries}). "
                        f"Retrying in {delay}s... Error: {e}"
                    )
                    
                    await asyncio.sleep(delay)
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Unexpected retry loop exit in {func.__name__}")
        
        return wrapper
    return decorator


def calculate_exponential_backoff(attempt: int, base_delay: float = 1.0) -> float:
    """
    Calculate exponential backoff delay.

    Args:
        attempt: Current attempt number (1-indexed)
        base_delay: Base delay in seconds

    Returns:
        Delay in seconds
    """
    return base_delay * (2 ** (attempt - 1))


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to max length with suffix.

    Args:
        text: Text to truncate
        max_length: Maximum allowed length including suffix
        suffix: Suffix to append when truncating

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def safe_filename(filename: str) -> str:
    """
    Sanitize filename by removing/replacing unsafe characters.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename safe for filesystem
    """
    # Remove or replace problematic characters
    unsafe_chars = '<>:"/\\|?*'
    for char in unsafe_chars:
        filename = filename.replace(char, '_')
    return filename.strip()
