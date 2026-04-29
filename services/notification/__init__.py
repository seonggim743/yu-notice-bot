"""
Notification service initialization.
Exports all notification-related classes for easy importing.
"""

import importlib

__all__ = ["BaseNotifier", "TelegramNotifier", "DiscordNotifier", "formatters"]


def __getattr__(name):
    if name == "BaseNotifier":
        from services.notification.base import BaseNotifier

        return BaseNotifier
    if name == "TelegramNotifier":
        from services.notification.telegram import TelegramNotifier

        return TelegramNotifier
    if name == "DiscordNotifier":
        from services.notification.discord import DiscordNotifier

        return DiscordNotifier
    if name == "formatters":
        return importlib.import_module("services.notification.formatters")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
