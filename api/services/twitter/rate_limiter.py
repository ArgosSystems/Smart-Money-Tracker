"""
api/services/twitter/rate_limiter.py
-------------------------------------
Token bucket rate limiter + per-entity cooldown tracker.

Per-alert-type budget allocation
---------------------------------
The daily budget is split into five pools:

  whale         — reserved for whale alerts with score > 90 (20% default)
  exchange_flow — reserved for all exchange flow alerts    (20% default)
  accumulation  — reserved for all accumulation alerts     (20% default)
  price         — reserved for all price alerts            (10% default)
  shared        — remaining budget, available to ALL types (30% default)

Rules:
  1. When acquire() is called, it tries the alert type's dedicated reserve first.
  2. If the reserve is full, it falls through to the shared pool.
  3. Reserved slots can NEVER be consumed by other alert types.
  4. The hourly cap is a hard global limit — no exceptions.

Whale notes:
  Whale alerts only get the "whale" reserve when score > 90.
  Lower-score whale alerts go directly to the shared pool.

Entity cooldown:
  Separate from budget — prevents spamming about the same wallet/token:
    4h between posts about the same wallet address
    2h between posts about the same token symbol

Configuration:
  Pool capacities come from explicit constructor parameters (absolute counts).
  Broadcasters compute them from config.budget_reserve_* fields.
  The shared pool capacity is automatically: daily_budget - sum(all reserves).
"""

from __future__ import annotations

import time
from collections import deque


# ── Reserve pool helper ───────────────────────────────────────────────────────

def get_reserve_type(alert_type_value: str, score: float) -> str:
    """
    Map an alert_type string (.value of AlertType enum) + score to a reserve pool name.

    Called by all three broadcasters inside _process_alert() to decide which
    pool to draw from before calling TokenBucketRateLimiter.acquire().

    Returns one of: "whale", "exchange_flow", "accumulation", "price", "shared".
    """
    if alert_type_value == "whale" and score > 90:
        return "whale"
    if alert_type_value == "exchange_flow":
        return "exchange_flow"
    if alert_type_value == "accumulation":
        return "accumulation"
    if alert_type_value == "price":
        return "price"
    return "shared"


# ── Token bucket with per-type reserves ───────────────────────────────────────

class TokenBucketRateLimiter:
    """
    Enforces posting budget with per-alert-type reserves and a shared pool.

    Each reserve is a separate rolling-24h deque.  The hourly cap is a single
    global deque covering all pools combined.

    Parameters
    ----------
    daily_budget:
        Total posts allowed per 24 hours across all pools.
    hourly_cap:
        Hard limit on posts per hour (no exceptions, applies across all pools).
    reserve_whale:
        Slots reserved exclusively for whale alerts with score > 90.
    reserve_exchange_flow:
        Slots reserved exclusively for exchange flow alerts.
    reserve_accumulation:
        Slots reserved exclusively for accumulation alerts.
    reserve_price:
        Slots reserved exclusively for price alerts.

    The shared pool capacity is computed automatically:
        shared_cap = daily_budget - (reserve_whale + reserve_exchange_flow
                                     + reserve_accumulation + reserve_price)
    """

    _POOL_NAMES = ("whale", "exchange_flow", "accumulation", "price")

    def __init__(
        self,
        daily_budget: int = 50,
        hourly_cap: int = 17,
        reserve_whale: int = 10,
        reserve_exchange_flow: int = 10,
        reserve_accumulation: int = 10,
        reserve_price: int = 5,
        # Kept for backward compatibility — ignored when individual reserves are set.
        critical_reserve_pct: float = 0.10,
    ) -> None:
        self._daily_budget = daily_budget
        self._hourly_cap = hourly_cap

        # Per-type reserve capacities (absolute counts)
        self._reserve_caps: dict[str, int] = {
            "whale":         reserve_whale,
            "exchange_flow": reserve_exchange_flow,
            "accumulation":  reserve_accumulation,
            "price":         reserve_price,
        }

        # Shared pool: whatever budget remains after all reserves
        reserved_total = sum(self._reserve_caps.values())
        self._shared_cap = max(0, daily_budget - reserved_total)

        # Rolling windows — separate deque per reserve pool + shared + hourly
        self._reserve_posts: dict[str, deque[float]] = {
            name: deque() for name in self._pool_names()
        }
        self._shared_posts: deque[float] = deque()
        self._hourly_posts: deque[float] = deque()

    def _pool_names(self) -> tuple[str, ...]:
        return self._POOL_NAMES

    def _prune(self) -> None:
        """Remove expired timestamps from all windows."""
        now = time.monotonic()
        day_cutoff  = now - 86400
        hour_cutoff = now - 3600

        for dq in self._reserve_posts.values():
            while dq and dq[0] < day_cutoff:
                dq.popleft()

        while self._shared_posts and self._shared_posts[0] < day_cutoff:
            self._shared_posts.popleft()

        while self._hourly_posts and self._hourly_posts[0] < hour_cutoff:
            self._hourly_posts.popleft()

    def acquire(self, reserve_type: str = "shared") -> bool:
        """
        Try to consume a budget token for the given reserve pool.

        Returns True if posting is allowed, False if the budget is exhausted.

        Logic:
          1. Hourly cap check (hard global limit).
          2. If reserve_type has a dedicated pool and it has capacity → draw from it.
          3. Otherwise → try the shared pool.
          4. If shared pool is also full → deny.
        """
        self._prune()

        # Gate 1: Hourly hard cap — no exceptions for any pool
        if len(self._hourly_posts) >= self._hourly_cap:
            return False

        now = time.monotonic()

        # Gate 2: Try the type's dedicated reserve (if it has one)
        if reserve_type in self._reserve_posts:
            dq  = self._reserve_posts[reserve_type]
            cap = self._reserve_caps[reserve_type]
            if len(dq) < cap:
                dq.append(now)
                self._hourly_posts.append(now)
                return True
            # Reserve full → fall through to shared pool

        # Gate 3: Shared pool (available to all types)
        if len(self._shared_posts) < self._shared_cap:
            self._shared_posts.append(now)
            self._hourly_posts.append(now)
            return True

        return False

    def reset(self) -> None:
        """Clear all budget windows (called by reset_budget in each broadcaster)."""
        for dq in self._reserve_posts.values():
            dq.clear()
        self._shared_posts.clear()
        self._hourly_posts.clear()

    @property
    def remaining_today(self) -> int:
        self._prune()
        used = sum(len(dq) for dq in self._reserve_posts.values()) + len(self._shared_posts)
        return max(0, self._daily_budget - used)

    @property
    def remaining_this_hour(self) -> int:
        self._prune()
        return max(0, self._hourly_cap - len(self._hourly_posts))

    @property
    def info(self) -> dict:
        """Return budget info for observability (/twitter_status, /bluesky_status, etc.)."""
        self._prune()
        reserves_info = {
            name: {
                "used":     len(self._reserve_posts[name]),
                "capacity": self._reserve_caps[name],
                "remaining": max(0, self._reserve_caps[name] - len(self._reserve_posts[name])),
            }
            for name in self._pool_names()
        }
        return {
            "remaining_today":     self.remaining_today,
            "remaining_this_hour": self.remaining_this_hour,
            "daily_budget":        self._daily_budget,
            "hourly_cap":          self._hourly_cap,
            "reserves":            reserves_info,
            "shared_pool": {
                "used":      len(self._shared_posts),
                "capacity":  self._shared_cap,
                "remaining": max(0, self._shared_cap - len(self._shared_posts)),
            },
        }


# ── Entity cooldown tracker ───────────────────────────────────────────────────

class EntityCooldownTracker:
    """
    Per-entity deduplication to prevent post spam about the same wallet/token.

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
        """Return True if enough time has passed since the last post about this entity."""
        last = self._last_seen.get(entity_key)
        if last is None:
            return True
        return (time.monotonic() - last) >= self._ttl_for(entity_key)

    def record(self, entity_key: str) -> None:
        """Mark this entity as just posted about."""
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
