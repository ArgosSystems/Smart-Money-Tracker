"""
api/events/dispatcher.py
-------------------------
Central event bus that fans out AlertDTOs to all registered BroadcasterProtocol
plugins concurrently.

Replaces the direct alert_broadcaster.publish() calls.  The existing WebSocket
fan-out becomes one plugin among many (see WebSocketBroadcasterPlugin below).

Usage
-----
    from api.events.dispatcher import event_dispatcher
    from api.events.types import WhaleAlertEvent

    # Register plugins at startup
    event_dispatcher.register(some_plugin)

    # Dispatch from scanning services
    await event_dispatcher.dispatch(WhaleAlertEvent(...))
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from api.events.protocol import AlertDTO, BroadcasterProtocol
from api.services.twitter.scoring import AlertScorer

logger = logging.getLogger(__name__)


class _PluginMetrics:
    """In-memory delivery metrics for a single plugin."""

    __slots__ = ("delivered", "failed", "dropped", "total_latency_ms")

    def __init__(self) -> None:
        self.delivered: int = 0
        self.failed: int = 0
        self.dropped: int = 0
        self.total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        total = self.delivered + self.failed
        return (self.total_latency_ms / total) if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "delivered": self.delivered,
            "failed": self.failed,
            "dropped": self.dropped,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


class EventDispatcher:
    """
    Maintains a registry of BroadcasterProtocol plugins and fans out
    every dispatched event to all of them concurrently.

    One plugin's failure never blocks or crashes another.
    Tracks delivery metrics per plugin for observability.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BroadcasterProtocol] = {}
        self._metrics: dict[str, _PluginMetrics] = defaultdict(_PluginMetrics)
        self._total_dispatched: int = 0

    # ── Plugin management ──────────────────────────────────────────────────────

    def register(self, plugin: BroadcasterProtocol) -> None:
        """Add a broadcaster plugin. Called during lifespan startup."""
        self._plugins[plugin.name] = plugin
        logger.info("EventDispatcher: registered plugin '%s'", plugin.name)

    def unregister(self, name: str) -> None:
        """Remove a plugin by name. For hot-reload or graceful degradation."""
        if name in self._plugins:
            del self._plugins[name]
            logger.info("EventDispatcher: unregistered plugin '%s'", name)

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def dispatch(self, event: AlertDTO) -> None:
        """
        Fan out event to all registered plugins concurrently.
        Each plugin.handle_event() is wrapped so failures are logged
        but never propagate.
        """
        if not self._plugins:
            return

        self._total_dispatched += 1

        results = await asyncio.gather(
            *(self._safe_handle(plugin, event) for plugin in self._plugins.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error("EventDispatcher: unexpected gather error: %s", result)

    async def _safe_handle(self, plugin: BroadcasterProtocol, event: AlertDTO) -> None:
        """Call handle_event with exception isolation and latency tracking."""
        metrics = self._metrics[plugin.name]
        t0 = time.monotonic()
        try:
            await plugin.handle_event(event)
            elapsed_ms = (time.monotonic() - t0) * 1000
            metrics.delivered += 1
            metrics.total_latency_ms += elapsed_ms
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            metrics.failed += 1
            metrics.total_latency_ms += elapsed_ms
            logger.error(
                "EventDispatcher: plugin '%s' failed on event %s (alert_id=%d): %s",
                plugin.name, event.alert_type.value, event.alert_id, exc,
            )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start_all(self) -> None:
        """Call start() on all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                await plugin.start()
                logger.info("EventDispatcher: started plugin '%s'", name)
            except Exception as exc:
                logger.error("EventDispatcher: failed to start plugin '%s': %s", name, exc)

    async def stop_all(self) -> None:
        """Call stop() on all plugins."""
        for name, plugin in self._plugins.items():
            try:
                await plugin.stop()
                logger.info("EventDispatcher: stopped plugin '%s'", name)
            except Exception as exc:
                logger.error("EventDispatcher: failed to stop plugin '%s': %s", name, exc)

    # ── Observability ──────────────────────────────────────────────────────────

    @property
    def plugin_status(self) -> dict[str, bool]:
        """Map of plugin_name → is_healthy.  Exposed via /health endpoint."""
        return {name: plugin.is_healthy for name, plugin in self._plugins.items()}

    @property
    def dispatch_metrics(self) -> dict:
        """Full metrics snapshot for the /api/v1/metrics/events endpoint."""
        return {
            "total_dispatched": self._total_dispatched,
            "plugins": {
                name: {
                    "healthy": self._plugins[name].is_healthy if name in self._plugins else False,
                    **self._metrics[name].to_dict(),
                }
                for name in self._metrics
            },
        }


# ── Module-level singleton ────────────────────────────────────────────────────

event_dispatcher = EventDispatcher()


# ── WebSocket adapter ─────────────────────────────────────────────────────────

class WebSocketBroadcasterPlugin:
    """
    Wraps the existing AlertBroadcaster as a BroadcasterProtocol plugin.
    Converts typed AlertDTO back to dict for backward-compatible WebSocket JSON.
    """

    def __init__(self, broadcaster: object) -> None:
        # Accept the AlertBroadcaster instance (avoids circular import of the type)
        self._broadcaster = broadcaster
        self._scorer = AlertScorer()

    @property
    def name(self) -> str:
        return "websocket"

    @property
    def is_healthy(self) -> bool:
        return True

    async def start(self) -> None:
        pass  # WebSocket broadcaster has no startup logic

    async def stop(self) -> None:
        pass  # WebSocket broadcaster has no shutdown logic

    async def handle_event(self, event: AlertDTO) -> None:
        """Convert AlertDTO to dict and publish via the existing broadcaster."""
        data = event.to_dict()
        data["priority_score"] = self._scorer.score(event)
        await self._broadcaster.publish(data)  # type: ignore[attr-defined]
