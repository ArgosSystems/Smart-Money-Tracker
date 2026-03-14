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

from api.events.protocol import AlertDTO, BroadcasterProtocol

logger = logging.getLogger(__name__)


class EventDispatcher:
    """
    Maintains a registry of BroadcasterProtocol plugins and fans out
    every dispatched event to all of them concurrently.

    One plugin's failure never blocks or crashes another.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BroadcasterProtocol] = {}

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

        results = await asyncio.gather(
            *(self._safe_handle(plugin, event) for plugin in self._plugins.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error("EventDispatcher: unexpected gather error: %s", result)

    async def _safe_handle(self, plugin: BroadcasterProtocol, event: AlertDTO) -> None:
        """Call handle_event with exception isolation."""
        try:
            await plugin.handle_event(event)
        except Exception as exc:
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
        await self._broadcaster.publish(event.to_dict())  # type: ignore[attr-defined]
