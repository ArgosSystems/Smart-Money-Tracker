"""
api/services/bluesky/broadcaster.py
--------------------------------------
BlueSkyBroadcaster — implements BroadcasterProtocol.

Follows the exact same pattern as TwitterBroadcaster:
  - Priority queue + AlertScorer
  - TokenBucketRateLimiter (shared from twitter module)
  - EntityCooldownTracker (shared from twitter module)
  - CircuitBreaker (shared from twitter module)
  - Dry-run mode: logs instead of posting

Lifecycle:
  start()        → spawn _posting_loop as asyncio.Task
  handle_event() → score → enqueue (non-blocking)
  stop()         → cancel posting loop, drain queue
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from api.events.protocol import AlertDTO, AlertType
from api.services.bluesky.client import BlueSkyClient, BlueSkyClientError
from api.services.bluesky.templates import BlueSkyPostRenderer
from api.services.twitter.circuit_breaker import CircuitBreaker
from api.services.twitter.rate_limiter import EntityCooldownTracker, TokenBucketRateLimiter, get_reserve_type
from api.services.twitter.scoring import AlertScorer, ScoredAlert

logger = logging.getLogger(__name__)


class BlueSkyBroadcaster:
    """
    Production-grade Bluesky broadcasting plugin.

    Implements BroadcasterProtocol — registered with the EventDispatcher
    during FastAPI lifespan startup.
    """

    def __init__(
        self,
        config: object,          # BlueSkyConfig from settings
        session_factory: async_sessionmaker,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._queue: asyncio.PriorityQueue[ScoredAlert] = asyncio.PriorityQueue(
            maxsize=config.max_queue_size  # type: ignore[attr-defined]
        )

        # Sub-components — shared generics from twitter module
        self._scorer = AlertScorer(weights=config.scoring_weights)  # type: ignore[attr-defined]
        self._rate_limiter = TokenBucketRateLimiter(
            daily_budget=config.daily_budget,  # type: ignore[attr-defined]
            hourly_cap=config.hourly_cap,  # type: ignore[attr-defined]
            reserve_whale=config.budget_reserve_whale,  # type: ignore[attr-defined]
            reserve_exchange_flow=config.budget_reserve_exchange_flow,  # type: ignore[attr-defined]
            reserve_accumulation=config.budget_reserve_accumulation,  # type: ignore[attr-defined]
            reserve_price=config.budget_reserve_price,  # type: ignore[attr-defined]
        )
        self._cooldown = EntityCooldownTracker(
            wallet_cooldown_hours=config.cooldown_wallet_hours,  # type: ignore[attr-defined]
            token_cooldown_hours=config.cooldown_token_hours,  # type: ignore[attr-defined]
        )
        self._circuit = CircuitBreaker(
            failure_threshold=config.circuit_failure_threshold,  # type: ignore[attr-defined]
            pause_seconds=config.circuit_pause_seconds,  # type: ignore[attr-defined]
            max_pause_seconds=config.circuit_max_pause_seconds,  # type: ignore[attr-defined]
        )
        self._renderer = BlueSkyPostRenderer()

        # Bluesky client (only if not dry_run)
        self._client: BlueSkyClient | None = None
        if not config.dry_run:  # type: ignore[attr-defined]
            self._client = BlueSkyClient(
                handle=config.handle,  # type: ignore[attr-defined]
                password=config.password,  # type: ignore[attr-defined]
            )

        self._task: asyncio.Task | None = None
        self._running = False

    # ── BroadcasterProtocol interface ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return "bluesky"

    @property
    def is_healthy(self) -> bool:
        return self._running and self._circuit.state != CircuitBreaker.STATE_OPEN

    async def start(self) -> None:
        """Spawn the posting loop as an asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._posting_loop(), name="bluesky_broadcaster")
        mode = "DRY-RUN" if self._config.dry_run else "LIVE"  # type: ignore[attr-defined]
        logger.info("BlueSkyBroadcaster started (%s mode)", mode)

    async def stop(self) -> None:
        """Cancel the posting loop and drain the queue."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "BlueSkyBroadcaster stopped (queue had %d items)", self._queue.qsize()
        )

    async def handle_event(self, event: AlertDTO) -> None:
        """
        Score and enqueue an alert event.  Non-blocking — never raises.
        """
        try:
            if not self._should_accept(event):
                return

            score = self._scorer.score(event)
            min_score = getattr(self._config, "min_score", 0.0)
            if score <= 0 or score < min_score:
                logger.debug(
                    "Bluesky: skipping alert #%d — score %.1f below min %.1f",
                    event.alert_id, score, min_score,
                )
                return

            scored = ScoredAlert(event=event, score=score, queued_at=datetime.utcnow())

            if self._queue.full():
                self._handle_overflow(scored)
            else:
                self._queue.put_nowait(scored)

            logger.debug(
                "Bluesky queue: enqueued %s alert #%d (score=%.1f, depth=%d)",
                event.alert_type.value, event.alert_id, score, self._queue.qsize(),
            )
        except Exception as exc:
            logger.error("BlueSkyBroadcaster.handle_event error: %s", exc)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _should_accept(self, event: AlertDTO) -> bool:
        """Check feature flags for this alert type."""
        cfg = self._config
        if event.alert_type == AlertType.WHALE:
            return cfg.enable_whale_posts  # type: ignore[attr-defined]
        elif event.alert_type == AlertType.PRICE:
            return cfg.enable_price_posts  # type: ignore[attr-defined]
        elif event.alert_type == AlertType.PORTFOLIO:
            if not cfg.enable_portfolio_posts:  # type: ignore[attr-defined]
                return False
            return event.metadata.get("is_public", False)
        elif event.alert_type == AlertType.ACCUMULATION:
            return cfg.enable_accumulation_posts  # type: ignore[attr-defined]
        elif event.alert_type == AlertType.EXCHANGE_FLOW:
            return cfg.enable_exchange_flow_posts  # type: ignore[attr-defined]
        return False

    def _handle_overflow(self, new_item: ScoredAlert) -> None:
        """Queue is full. Replace the lowest-priority item if new one scores higher."""
        items: list[ScoredAlert] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        items.append(new_item)
        items.sort()

        dropped = items.pop()
        if dropped.score > 90 and new_item.score <= 90:
            items.append(dropped)
            dropped = new_item
            items.sort()
            items.pop()

        logger.warning(
            "Bluesky queue overflow: dropped alert #%d (score=%.1f)",
            dropped.event.alert_id, dropped.score,
        )
        for item in items:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                break

    async def _posting_loop(self) -> None:
        """Main posting loop — pulls from queue, checks gates, renders, posts."""
        logger.info("Bluesky posting loop started")

        while self._running:
            try:
                scored = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            try:
                await self._process_alert(scored)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("Bluesky posting loop error: %s", exc)

            # Respect Bluesky's API — 2s between posts
            await asyncio.sleep(2.0)

    async def _process_alert(self, scored: ScoredAlert) -> None:
        """Process a single scored alert through all gates and post."""
        event = scored.event
        score = scored.score

        # Gate 1: Circuit breaker
        if not self._circuit.can_execute():
            logger.debug("Circuit breaker OPEN — re-queuing alert #%d", event.alert_id)
            try:
                self._queue.put_nowait(scored)
            except asyncio.QueueFull:
                logger.warning("Re-queue failed (full) — dropping alert #%d", event.alert_id)
            return

        # Gate 2: Rate limiter
        reserve_type = get_reserve_type(event.alert_type.value, score)
        if not self._rate_limiter.acquire(reserve_type=reserve_type):
            logger.warning(
                "Bluesky rate limit hit — dropping alert #%d score=%.1f pool=%s "
                "(remaining today=%d, hour=%d)",
                event.alert_id, score, reserve_type,
                self._rate_limiter.remaining_today,
                self._rate_limiter.remaining_this_hour,
            )
            return

        # Gate 3: Entity cooldown
        entity_key = self._entity_key(event)
        if entity_key and not self._cooldown.is_cooled_down(entity_key):
            logger.info(
                "Bluesky cooldown active for %s — skipping alert #%d (score=%.1f)",
                entity_key, event.alert_id, score,
            )
            return

        # Render and post
        content = self._renderer.render(event, score)
        if not content:
            return

        post_uri, post_cid = await self._post(content)
        await self._persist(event, content, score, post_uri, post_cid)

        if entity_key:
            self._cooldown.record(entity_key)

    async def _post(self, content: str) -> tuple[str | None, str | None]:
        """Post to Bluesky. Returns (uri, cid) or (None, None). Logs on dry_run."""
        if self._config.dry_run:  # type: ignore[attr-defined]
            logger.info("🦋 [DRY-RUN] Bluesky:\n%s", content)
            return None, None

        if not self._client:
            logger.error("Bluesky client not initialized — check credentials")
            return None, None

        try:
            uri, cid = await self._client.post(content)
            self._circuit.record_success()
            return uri, cid
        except BlueSkyClientError as exc:
            self._circuit.record_failure(exc.status_code)
            logger.error("Bluesky post failed (status=%d): %s", exc.status_code, exc)
            return None, None

    async def _persist(
        self,
        event: AlertDTO,
        content: str,
        score: float,
        post_uri: str | None,
        post_cid: str | None,
    ) -> None:
        """Save post record to the database."""
        try:
            from api.models import BlueSkyPost  # noqa: PLC0415

            async with self._session_factory() as db:
                post = BlueSkyPost(
                    alert_type=event.alert_type.value,
                    alert_id=event.alert_id,
                    post_uri=post_uri,
                    post_cid=post_cid,
                    content=content,
                    priority_score=score,
                    posted_at=datetime.utcnow(),
                )
                db.add(post)
                await db.commit()
        except Exception as exc:
            logger.error("Failed to persist bluesky_post: %s", exc)

    def _entity_key(self, event: AlertDTO) -> str | None:
        """Extract cooldown entity key from an alert."""
        meta = event.metadata
        if event.alert_type == AlertType.WHALE:
            addr = meta.get("from_address") or meta.get("to_address")
            if addr:
                return f"wallet:{addr.lower()}"
        elif event.alert_type == AlertType.PRICE:
            symbol = meta.get("token_symbol")
            if symbol:
                return f"token:{symbol.upper()}"
        elif event.alert_type == AlertType.ACCUMULATION:
            addr = meta.get("wallet_address")
            symbol = meta.get("token_symbol")
            if addr and symbol:
                return f"accum:{addr.lower()}:{symbol.upper()}"
        elif event.alert_type == AlertType.EXCHANGE_FLOW:
            addr = meta.get("wallet_address")
            exchange = meta.get("exchange_address")
            if addr and exchange:
                return f"exflow:{addr.lower()}:{exchange.lower()}"
        return None

    # ── Budget management ──────────────────────────────────────────────────────

    def reset_budget(self) -> dict:
        """Clear all rate-limiter windows. Returns the new budget snapshot."""
        self._rate_limiter.reset()
        logger.warning("Bluesky rate-limiter budget manually reset")
        return self._rate_limiter.info

    # ── Observability ──────────────────────────────────────────────────────────

    @property
    def status(self) -> dict:
        """Full status snapshot for /bluesky_status command."""
        return {
            "mode": "dry-run" if self._config.dry_run else "live",  # type: ignore[attr-defined]
            "running": self._running,
            "queue_depth": self._queue.qsize(),
            "handle": self._config.handle,  # type: ignore[attr-defined]
            "min_score": getattr(self._config, "min_score", 0.0),
            "critical_score": getattr(self._config, "critical_score", 80.0),
            "rate_limiter": self._rate_limiter.info,
            "circuit_breaker": self._circuit.info,
            "features": {
                "whale_posts": self._config.enable_whale_posts,  # type: ignore[attr-defined]
                "price_posts": self._config.enable_price_posts,  # type: ignore[attr-defined]
                "portfolio_posts": self._config.enable_portfolio_posts,  # type: ignore[attr-defined]
                "accumulation_posts": self._config.enable_accumulation_posts,  # type: ignore[attr-defined]
                "exchange_flow_posts": self._config.enable_exchange_flow_posts,  # type: ignore[attr-defined]
            },
        }
