"""
tests/test_broadcaster.py
--------------------------
Unit tests for AlertBroadcaster pub/sub logic.

Pure Python — no DB, no HTTP, no network.
"""

from __future__ import annotations

import asyncio

from api.services.broadcaster import MAX_QUEUE_SIZE, AlertBroadcaster


# ── subscribe / unsubscribe ────────────────────────────────────────────────────

def test_subscribe_returns_async_queue():
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()
    assert isinstance(q, asyncio.Queue)


def test_subscriber_count_starts_at_zero():
    broadcaster = AlertBroadcaster()
    assert broadcaster.subscriber_count == 0


def test_subscriber_count_increments_on_subscribe():
    broadcaster = AlertBroadcaster()
    broadcaster.subscribe()
    broadcaster.subscribe()
    assert broadcaster.subscriber_count == 2


def test_subscriber_count_decrements_on_unsubscribe():
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()
    broadcaster.unsubscribe(q)
    assert broadcaster.subscriber_count == 0


def test_unsubscribe_unknown_queue_does_not_raise():
    """Removing a queue that was never subscribed must not raise."""
    broadcaster = AlertBroadcaster()
    broadcaster.unsubscribe(asyncio.Queue())


# ── publish ───────────────────────────────────────────────────────────────────

async def test_publish_delivers_message_to_subscriber():
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()

    await broadcaster.publish({"type": "whale_alert", "amount_usd": 100_000})

    assert not q.empty()
    assert q.get_nowait()["amount_usd"] == 100_000


async def test_publish_delivers_to_all_subscribers():
    broadcaster = AlertBroadcaster()
    q1 = broadcaster.subscribe()
    q2 = broadcaster.subscribe()
    payload = {"type": "whale_alert"}

    await broadcaster.publish(payload)

    assert q1.get_nowait() == payload
    assert q2.get_nowait() == payload


async def test_publish_with_no_subscribers_does_not_raise():
    broadcaster = AlertBroadcaster()
    await broadcaster.publish({"data": "test"})  # must not raise


async def test_unsubscribed_queue_does_not_receive_messages():
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()
    broadcaster.unsubscribe(q)

    await broadcaster.publish({"data": "test"})

    assert q.empty()


async def test_publish_multiple_messages_preserves_order():
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()

    for i in range(5):
        await broadcaster.publish({"seq": i})

    for i in range(5):
        assert q.get_nowait()["seq"] == i


async def test_publish_drops_message_for_full_queue_without_raising():
    """A full subscriber queue causes a message to be silently dropped."""
    broadcaster = AlertBroadcaster()
    q = broadcaster.subscribe()

    # Fill the queue to capacity
    for i in range(MAX_QUEUE_SIZE):
        q.put_nowait({"i": i})

    # Publish to an already-full queue — must not raise
    await broadcaster.publish({"overflow": True})

    # Queue size unchanged; the overflow message was dropped
    assert q.qsize() == MAX_QUEUE_SIZE


async def test_subscriber_still_receives_after_another_unsubscribes():
    """Unsubscribing one client must not affect other active clients."""
    broadcaster = AlertBroadcaster()
    q_keep = broadcaster.subscribe()
    q_gone = broadcaster.subscribe()
    broadcaster.unsubscribe(q_gone)

    await broadcaster.publish({"hello": True})

    assert q_keep.get_nowait()["hello"] is True
    assert q_gone.empty()
