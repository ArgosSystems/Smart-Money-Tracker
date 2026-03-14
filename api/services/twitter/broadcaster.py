"""
api/services/twitter/broadcaster.py
-------------------------------------
TwitterBroadcaster — implements BroadcasterProtocol.

Receives AlertDTO events from the EventDispatcher, scores them, enqueues
into a priority queue, and posts tweets via a background posting loop.

Lifecycle:
  start()        → spawn _posting_loop as asyncio.Task
  handle_event() → score → enqueue (non-blocking)
  stop()         → cancel posting loop, drain queue

The posting loop:
  1. Awaits queue.get()
  2. Checks circuit breaker state
  3. Checks rate limiter (token bucket)
  4. Checks entity cooldown
  5. Renders tweet template
  6. Posts via TwitterClient (or logs if dry_run)
  7. Persists to twitter_posts table
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from api.events.protocol import AlertDTO, AlertType
from api.services.twitter.circuit_breaker import CircuitBreaker
from api.services.twitter.client import TwitterClient, TwitterClientError
from api.services.twitter.rate_limiter import EntityCooldownTracker, TokenBucketRateLimiter
from api.services.twitter.scoring import AlertScorer, ScoredAlert
from api.services.twitter.templates import ThreadComposer, TweetRenderer

logger = logging.getLogger(__name__)


class TwitterBroadcaster:
    """
    Production-grade Twitter broadcasting plugin.

    Implements BroadcasterProtocol — registered with the EventDispatcher
    during FastAPI lifespan startup.
    """

    def __init__(
        self,
        config: object,   # TwitterConfig from settings
        session_factory: async_sessionmaker,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._queue: asyncio.PriorityQueue[ScoredAlert] = asyncio.PriorityQueue(
            maxsize=config.max_queue_size  # type: ignore[attr-defined]
        )

        # Sub-components
        self._scorer = AlertScorer(weights=config.scoring_weights)  # type: ignore[attr-defined]
        self._rate_limiter = TokenBucketRateLimiter(
            daily_budget=config.daily_budget,  # type: ignore[attr-defined]
            hourly_cap=config.hourly_cap,  # type: ignore[attr-defined]
            critical_reserve_pct=config.critical_reserve_pct,  # type: ignore[attr-defined]
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
        self._renderer = TweetRenderer()
        self._thread_composer = ThreadComposer()

        # Twitter API client (only initialized if not dry_run)
        self._client: TwitterClient | None = None
        if not config.dry_run:  # type: ignore[attr-defined]
            self._client = TwitterClient(
                api_key=config.api_key,  # type: ignore[attr-defined]
                api_secret=config.api_secret,  # type: ignore[attr-defined]
                access_token=config.access_token,  # type: ignore[attr-defined]
                access_token_secret=config.access_token_secret,  # type: ignore[attr-defined]
                bearer_token=config.bearer_token,  # type: ignore[attr-defined]
            )

        self._task: asyncio.Task | None = None
        self._running = False

    # ── BroadcasterProtocol interface ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return "twitter"

    @property
    def is_healthy(self) -> bool:
        return self._running and self._circuit.state != CircuitBreaker.STATE_OPEN

    async def start(self) -> None:
        """Spawn the posting loop as an asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._posting_loop(), name="twitter_broadcaster")
        mode = "DRY-RUN" if self._config.dry_run else "LIVE"  # type: ignore[attr-defined]
        logger.info("TwitterBroadcaster started (%s mode)", mode)

    async def stop(self) -> None:
        """Cancel the posting loop and drain the queue."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TwitterBroadcaster stopped (queue had %d items)", self._queue.qsize())

    async def handle_event(self, event: AlertDTO) -> None:
        """
        Score and enqueue an alert event.  Non-blocking — never raises.

        Filtering:
          - Whale alerts: check enable_whale_tweets flag
          - Price alerts: check enable_price_tweets flag
          - Portfolio alerts: check enable_portfolio_tweets flag AND is_public
        """
        try:
            # Feature flag check
            if not self._should_accept(event):
                return

            score = self._scorer.score(event)
            if score <= 0:
                return  # Suppressed (e.g., private portfolio)

            scored = ScoredAlert(event=event, score=score, queued_at=datetime.utcnow())

            # Handle queue overflow: drop lowest-priority item
            if self._queue.full():
                self._handle_overflow(scored)
            else:
                self._queue.put_nowait(scored)

            logger.debug(
                "Twitter queue: enqueued %s alert #%d (score=%.1f, depth=%d)",
                event.alert_type.value, event.alert_id, score, self._queue.qsize(),
            )
        except Exception as exc:
            logger.error("TwitterBroadcaster.handle_event error: %s", exc)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _should_accept(self, event: AlertDTO) -> bool:
        """Check feature flags for this alert type."""
        cfg = self._config
        if event.alert_type == AlertType.WHALE:
            return cfg.enable_whale_tweets  # type: ignore[attr-defined]
        elif event.alert_type == AlertType.PRICE:
            return cfg.enable_price_tweets  # type: ignore[attr-defined]
        elif event.alert_type == AlertType.PORTFOLIO:
            if not cfg.enable_portfolio_tweets:  # type: ignore[attr-defined]
                return False
            return event.metadata.get("is_public", False)
        return False

    def _handle_overflow(self, new_item: ScoredAlert) -> None:
        """
        Queue is full.  Replace the lowest-priority item if the new one
        scores higher.  Never drop critical (score > 90) alerts.
        """
        # Drain, sort, drop lowest, re-insert
        items: list[ScoredAlert] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        items.append(new_item)
        items.sort()  # Higher score first (ScoredAlert.__lt__ is inverted)

        # Drop the last item (lowest score), unless it's critical
        dropped = items.pop()
        if dropped.score > 90 and new_item.score <= 90:
            # Don't drop a critical alert in favor of a non-critical one
            items.append(dropped)
            dropped = new_item
            items.sort()
            items.pop()

        logger.warning(
            "Twitter queue overflow: dropped alert #%d (score=%.1f)",
            dropped.event.alert_id, dropped.score,
        )

        for item in items:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                break

    async def _posting_loop(self) -> None:
        """
        Main posting loop — runs as an asyncio task.

        Pulls from the priority queue, checks all gates (circuit breaker,
        rate limiter, cooldown), renders, and posts.
        """
        logger.info("Twitter posting loop started")

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
                logger.error("Twitter posting loop error: %s", exc)

            # Small delay between posts to be respectful to the API
            await asyncio.sleep(2.0)

    async def _process_alert(self, scored: ScoredAlert) -> None:
        """Process a single scored alert through all gates and post."""
        event = scored.event
        score = scored.score

        # Gate 1: Circuit breaker
        if not self._circuit.can_execute():
            # Re-queue if circuit is open (it'll be retried after recovery)
            logger.debug("Circuit breaker OPEN — re-queuing alert #%d", event.alert_id)
            try:
                self._queue.put_nowait(scored)
            except asyncio.QueueFull:
                logger.warning("Re-queue failed (full) — dropping alert #%d", event.alert_id)
            return

        # Gate 2: Rate limiter
        is_critical = score > 90
        if not self._rate_limiter.acquire(is_critical=is_critical):
            logger.debug(
                "Rate limit hit — dropping alert #%d (remaining today=%d, hour=%d)",
                event.alert_id, self._rate_limiter.remaining_today,
                self._rate_limiter.remaining_this_hour,
            )
            return

        # Gate 3: Entity cooldown
        entity_key = self._entity_key(event)
        if entity_key and not self._cooldown.is_cooled_down(entity_key):
            logger.debug("Cooldown active for %s — skipping alert #%d", entity_key, event.alert_id)
            return

        # Gate 4: Thread composition check
        if entity_key:
            self._thread_composer.add_to_buffer(entity_key, event)
            if self._thread_composer.should_thread(entity_key):
                events = self._thread_composer.flush_thread(entity_key)
                await self._post_thread(events, score)
                if entity_key:
                    self._cooldown.record(entity_key)
                return

        # Render and post single tweet
        content = self._renderer.render(event, score)
        if not content:
            return

        tweet_id = await self._post_tweet(content)
        await self._persist(event, content, score, tweet_id)

        if entity_key:
            self._cooldown.record(entity_key)

    async def _post_thread(self, events: list[AlertDTO], score: float) -> None:
        """Post a thread of related alerts."""
        tweets = self._renderer.render_thread(events, score)
        if not tweets:
            return

        parent_id: str | None = None
        for i, content in enumerate(tweets):
            tweet_id = await self._post_tweet(content, reply_to=parent_id)
            if i == 0:
                parent_id = tweet_id

            # Persist the first event as representative
            if i < len(events):
                await self._persist(
                    events[i] if i < len(events) else events[0],
                    content, score, tweet_id,
                    thread_parent_id=parent_id if i > 0 else None,
                )

    async def _post_tweet(self, content: str, reply_to: str | None = None) -> str | None:
        """
        Post a single tweet.  Returns tweet_id or None.

        In dry_run mode, logs the tweet and returns None.
        """
        if self._config.dry_run:  # type: ignore[attr-defined]
            logger.info("🐦 [DRY-RUN] Tweet:\n%s", content)
            return None

        if not self._client:
            logger.error("Twitter client not initialized — check credentials")
            return None

        try:
            tweet_id = await self._client.post_tweet(content, reply_to=reply_to)
            self._circuit.record_success()
            return tweet_id
        except TwitterClientError as exc:
            self._circuit.record_failure(exc.status_code)
            logger.error("Tweet failed (status=%d): %s", exc.status_code, exc)
            return None

    async def _persist(
        self,
        event: AlertDTO,
        content: str,
        score: float,
        tweet_id: str | None,
        thread_parent_id: str | None = None,
    ) -> None:
        """Save tweet record to the database."""
        try:
            from api.models import TwitterPost  # noqa: PLC0415

            async with self._session_factory() as db:
                post = TwitterPost(
                    alert_type=event.alert_type.value,
                    alert_id=event.alert_id,
                    tweet_id=tweet_id,
                    thread_parent_id=thread_parent_id,
                    content=content,
                    priority_score=score,
                    posted_at=datetime.utcnow(),
                )
                db.add(post)
                await db.commit()
        except Exception as exc:
            logger.error("Failed to persist twitter_post: %s", exc)

    def _entity_key(self, event: AlertDTO) -> str | None:
        """Extract cooldown entity key from an alert."""
        meta = event.metadata
        if event.alert_type == AlertType.WHALE:
            # Use from_address or to_address as entity
            addr = meta.get("from_address") or meta.get("to_address")
            if addr:
                return f"wallet:{addr.lower()}"
        elif event.alert_type == AlertType.PRICE:
            symbol = meta.get("token_symbol")
            if symbol:
                return f"token:{symbol.upper()}"
        return None

    # ── Observability ──────────────────────────────────────────────────────────

    @property
    def status(self) -> dict:
        """Full status snapshot for /twitter_status command."""
        return {
            "mode": "dry-run" if self._config.dry_run else "live",  # type: ignore[attr-defined]
            "running": self._running,
            "queue_depth": self._queue.qsize(),
            "rate_limiter": self._rate_limiter.info,
            "circuit_breaker": self._circuit.info,
            "features": {
                "whale_tweets": self._config.enable_whale_tweets,  # type: ignore[attr-defined]
                "price_tweets": self._config.enable_price_tweets,  # type: ignore[attr-defined]
                "portfolio_tweets": self._config.enable_portfolio_tweets,  # type: ignore[attr-defined]
            },
        }
