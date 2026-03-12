"""
api/services/broadcaster.py
----------------------------
In-process pub/sub bus for real-time whale alert broadcasting.

Usage
-----
Publishing (from whale_tracker.py after a new alert is saved):

    from api.services.broadcaster import alert_broadcaster
    await alert_broadcaster.publish(alert_dict)

Subscribing (inside a WebSocket handler):

    queue = alert_broadcaster.subscribe()
    try:
        while True:
            data = await queue.get()
            await websocket.send_json(data)
    finally:
        alert_broadcaster.unsubscribe(queue)

Design notes
------------
- One asyncio.Queue per connected WebSocket client.
- publish() puts a dict into every active queue (non-blocking; drops messages
  for slow clients if their queue exceeds MAX_QUEUE_SIZE).
- Thread-safe for use from concurrent asyncio tasks (all ops are in the same
  event loop).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 100   # per-client buffer; older items dropped when full


class AlertBroadcaster:
    """
    Manages a set of subscriber queues and broadcasts alert dicts to all of them.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    # ── Subscription management ───────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber and return their private queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.add(q)
        logger.debug("WS subscriber added (%d total)", len(self._subscribers))
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue (call in WebSocket disconnect handler)."""
        self._subscribers.discard(queue)
        logger.debug("WS subscriber removed (%d remaining)", len(self._subscribers))

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def publish(self, alert_data: dict) -> None:
        """
        Push alert_data to every active subscriber.

        If a subscriber's queue is full (slow client), the message is dropped
        for that client only — others are unaffected.
        """
        if not self._subscribers:
            return

        dead: list[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(alert_data)
            except asyncio.QueueFull:
                logger.warning("WS client queue full — dropping message for one subscriber")
            except Exception as exc:
                logger.warning("WS publish error: %s — removing subscriber", exc)
                dead.append(q)

        for q in dead:
            self._subscribers.discard(q)

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this everywhere:  from api.services.broadcaster import alert_broadcaster

alert_broadcaster = AlertBroadcaster()
