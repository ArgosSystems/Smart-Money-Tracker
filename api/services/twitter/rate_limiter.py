"""
api/services/twitter/rate_limiter.py
-------------------------------------
Token bucket rate limiter + per-entity cooldown tracker.

Token bucket enforces global posting budget:
  - N tweets/day (rolling 24h window)
  - M tweets/hour (rolling 1h window)
  - Reserve % for critical alerts (score > 90)

Entity cooldown prevents spamming about the same wallet/token:
  - 4h between tweets about the same wallet address
  - 2h between tweets about the same token
"""

from __future__ import annotations

import time
from collections import deque


class TokenBucketRateLimiter:
    """
    Enforces posting budget with rolling time windows.

    Uses deques of posting timestamps — no external dependencies.
    Resets naturally on restart (acceptable for a 50/day budget).
    """

    def __init__(
        self,
        daily_budget: int = 50,
        hourly_cap: int = 17,
        critical_reserve_pct: float = 0.20,
    ) -> None:
        self._daily_budget = daily_budget
        self._hourly_cap = hourly_cap
        self._critical_reserve = int(daily_budget * critical_reserve_pct)

        # Rolling windows — store timestamps of each post
        self._daily_posts: deque[float] = deque()
        self._hourly_posts: deque[float] = deque()

    def _prune(self) -> None:
        """Remove expired timestamps from both windows."""
        now = time.monotonic()
        day_cutoff = now - 86400
        hour_cutoff = now - 3600

        while self._daily_posts and self._daily_posts[0] < day_cutoff:
            self._daily_posts.popleft()
        while self._hourly_posts and self._hourly_posts[0] < hour_cutoff:
            self._hourly_posts.popleft()

    def acquire(self, is_critical: bool = False) -> bool:
        """
        Try to consume a token.  Returns True if posting is allowed.

        If is_critical=True, uses the reserved pool (only denied when
        truly at the hard cap).
        """
        self._prune()

        # Hourly hard cap — no exceptions
        if len(self._hourly_posts) >= self._hourly_cap:
            return False

        # Daily budget — critical alerts get the reserved portion
        daily_used = len(self._daily_posts)
        if is_critical:
            if daily_used >= self._daily_budget:
                return False
        else:
            # Non-critical: leave reserve for critical
            available = self._daily_budget - self._critical_reserve
            if daily_used >= available:
                return False

        # Token granted
        now = time.monotonic()
        self._daily_posts.append(now)
        self._hourly_posts.append(now)
        return True

    @property
    def remaining_today(self) -> int:
        self._prune()
        return max(0, self._daily_budget - len(self._daily_posts))

    @property
    def remaining_this_hour(self) -> int:
        self._prune()
        return max(0, self._hourly_cap - len(self._hourly_posts))

    @property
    def info(self) -> dict:
        """Return budget info for observability."""
        return {
            "remaining_today": self.remaining_today,
            "remaining_this_hour": self.remaining_this_hour,
            "daily_budget": self._daily_budget,
            "hourly_cap": self._hourly_cap,
        }


class EntityCooldownTracker:
    """
    Per-entity deduplication to prevent tweet spam about the same wallet/token.

    Keys use the format "wallet:{address}" or "token:{symbol}".
    TTL-based: entries expire after the configured cooldown period.
    """

    def __init__(
        self,
        wallet_cooldown_hours: float = 4.0,
        token_cooldown_hours: float = 2.0,
    ) -> None:
        self._wallet_cooldown = wallet_cooldown_hours * 3600
        self._token_cooldown = token_cooldown_hours * 3600
        self._last_seen: dict[str, float] = {}

    def _ttl_for(self, entity_key: str) -> float:
        if entity_key.startswith("wallet:"):
            return self._wallet_cooldown
        return self._token_cooldown

    def is_cooled_down(self, entity_key: str) -> bool:
        """Return True if enough time has passed since the last tweet about this entity."""
        last = self._last_seen.get(entity_key)
        if last is None:
            return True
        return (time.monotonic() - last) >= self._ttl_for(entity_key)

    def record(self, entity_key: str) -> None:
        """Mark this entity as just tweeted about."""
        self._last_seen[entity_key] = time.monotonic()
        self._evict_expired()

    def _evict_expired(self) -> None:
        """Clean up expired entries to prevent unbounded memory growth."""
        now = time.monotonic()
        expired = [
            k for k, t in self._last_seen.items()
            if (now - t) >= self._ttl_for(k)
        ]
        for k in expired:
            del self._last_seen[k]
