"""
Notification service initialization.
Currently exports formatters module only.
Full NotificationService will be available after telegram/discord modules are created.
"""

from services.notification import formatters

__all__ = ["formatters"]
