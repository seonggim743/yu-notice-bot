"""Notification delivery state shared by the outbox and notifier layers."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class DeliveryResult:
    """Normalized result for one channel's primary notification."""

    channel: str
    success: bool
    external_id: Optional[Any] = None
    error: Optional[str] = None
