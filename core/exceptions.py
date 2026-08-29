"""
Custom exception hierarchy for yu-notice-bot-v2.
Provides specific exceptions for better error handling and debugging.
"""


class BotException(Exception):
    """Base exception for all bot-related errors."""

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self):
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


# =============================================================================
# Scraper Exceptions
# =============================================================================


class ScraperException(BotException):
    """Base exception for scraping-related errors."""

    pass


class NetworkException(ScraperException):
    """Exception for network/HTTP errors."""

    pass


class ParsingException(ScraperException):
    """Exception for HTML parsing errors."""

    pass


class ContentNotFoundException(ScraperException):
    """Exception when expected content is not found."""

    pass


# =============================================================================
# Notification Exceptions
# =============================================================================


class NotificationException(BotException):
    """Base exception for notification delivery errors."""

    pass


class TelegramAPIException(NotificationException):
    """Exception for Telegram API errors."""

    pass


class DiscordAPIException(NotificationException):
    """Exception for Discord API errors."""

    pass


class WebhookException(NotificationException):
    """Exception for webhook-related errors."""

    pass


# =============================================================================
# AI Service Exceptions
# =============================================================================


class AIServiceException(BotException):
    """Base exception for AI service errors."""

    pass


class APIQuotaExceededException(AIServiceException):
    """Exception when API quota is exceeded."""

    pass


class InvalidResponseException(AIServiceException):
    """Exception when AI returns invalid response."""

    pass


# =============================================================================
# Database Exceptions
# =============================================================================


class DatabaseException(BotException):
    """Base exception for database errors."""

    pass


class ConnectionException(DatabaseException):
    """Exception for database connection errors."""

    pass


class QueryException(DatabaseException):
    """Exception for database query errors."""

    pass


# =============================================================================
# File Service Exceptions
# =============================================================================


class FileServiceException(BotException):
    """Base exception for file service errors."""

    pass


class FileDownloadException(FileServiceException):
    """Exception for file download errors."""

    pass


class PDFProcessingException(FileServiceException):
    """Exception for PDF processing errors."""

    pass


class ImageProcessingException(FileServiceException):
    """Exception for image processing errors."""

    pass


# =============================================================================
# Configuration Exceptions
# =============================================================================


class ConfigurationException(BotException):
    """Exception for configuration errors."""

    pass


class MissingConfigException(ConfigurationException):
    """Exception when required configuration is missing."""

    pass
