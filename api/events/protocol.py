"""
api/events/protocol.py
-----------------------
Core abstractions: BroadcasterProtocol + AlertDTO base.

Extraction boundary
-------------------
Moving any broadcaster to a separate service only requires changing the import:
    from api.events.protocol import BroadcasterProtocol, AlertDTO
to:
    from argos_api_client import BroadcasterProtocol, AlertDTO
Zero business logic changes — only the adapter layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


class AlertType(str, Enum):
    WHALE = "whale"
    PRICE = "price"
    PORTFOLIO = "portfolio"


@dataclass(frozen=True, slots=True)
class AlertDTO:
    """
    Immutable, serializable alert data transfer object.

    All broadcaster plugins receive the same DTO — each decides
    independently how to render / filter / enqueue it.
    """

    alert_type: AlertType
    alert_id: int               # FK to source table (whale_alerts.id, etc.)
    chain: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict (for WebSocket JSON, logging, etc.)."""
        return {
            "alert_type": self.alert_type.value,
            "alert_id": self.alert_id,
            "chain": self.chain,
            "timestamp": self.timestamp.isoformat(),
            **self.metadata,
        }


@runtime_checkable
class BroadcasterProtocol(Protocol):
    """
    Contract for any alert consumer that plugs into the EventDispatcher.
    """

    async def start(self) -> None:
        """Initialize resources (API clients, queues). Called during lifespan startup."""
        ...

    async def stop(self) -> None:
        """Graceful shutdown. Drain queue, flush pending, close connections."""
        ...

    async def handle_event(self, event: AlertDTO) -> None:
        """
        Receive a single typed alert event from the dispatcher.

        Must not raise — errors are logged internally, never propagate
        to the dispatcher or other plugins.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable identifier for logging and health checks."""
        ...

    @property
    def is_healthy(self) -> bool:
        """True if this broadcaster is operational. Exposed via /health."""
        ...
