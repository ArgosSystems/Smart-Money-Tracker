"""
api/events
----------
Typed event system for alert broadcasting.

Provides:
- AlertDTO and typed event subclasses (WhaleAlertEvent, PriceTriggerEvent, etc.)
- BroadcasterProtocol interface for pluggable consumers (WebSocket, Twitter, etc.)
- EventDispatcher that fans out events to all registered plugins
"""

from api.events.protocol import AlertDTO, AlertType, BroadcasterProtocol
from api.events.types import PortfolioAlertEvent, PriceTriggerEvent, WhaleAlertEvent
from api.events.dispatcher import event_dispatcher

__all__ = [
    "AlertDTO",
    "AlertType",
    "BroadcasterProtocol",
    "WhaleAlertEvent",
    "PriceTriggerEvent",
    "PortfolioAlertEvent",
    "event_dispatcher",
]
