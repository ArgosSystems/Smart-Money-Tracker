"""
api/services/twitter/circuit_breaker.py
----------------------------------------
Circuit breaker for Twitter API resilience.

States: CLOSED (normal) → OPEN (paused) → HALF_OPEN (testing)

Triggers:
  - N consecutive failures (429 or 5xx) → OPEN
  - OPEN: pause for configured duration, then HALF_OPEN
  - HALF_OPEN: one test tweet.  Success → CLOSED.  Failure → OPEN (double pause, capped)
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Prevents cascading failures when the Twitter API is unavailable.

    When OPEN, the posting loop skips API calls and queues accumulate.
    Discord/WebSocket alerts are never affected.
    """

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 3,
        pause_seconds: int = 1800,
        max_pause_seconds: int = 7200,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._base_pause = pause_seconds
        self._max_pause = max_pause_seconds

        self._state = self.STATE_CLOSED
        self._consecutive_failures = 0
        self._current_pause = pause_seconds
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        if self._state == self.STATE_OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._current_pause:
                self._state = self.STATE_HALF_OPEN
                logger.info("CircuitBreaker → HALF_OPEN (testing one request)")
        return self._state

    def can_execute(self) -> bool:
        """Return True if a tweet can be attempted right now."""
        s = self.state
        return s in (self.STATE_CLOSED, self.STATE_HALF_OPEN)

    def record_success(self) -> None:
        """Called after a successful tweet post."""
        if self._state != self.STATE_CLOSED:
            logger.info("CircuitBreaker → CLOSED (success)")
        self._state = self.STATE_CLOSED
        self._consecutive_failures = 0
        self._current_pause = self._base_pause

    def record_failure(self, status_code: int = 0) -> None:
        """Called after a failed tweet post (429 or 5xx)."""
        self._consecutive_failures += 1
        logger.warning(
            "CircuitBreaker: failure #%d (status=%d)",
            self._consecutive_failures, status_code,
        )

        if self._consecutive_failures >= self._failure_threshold:
            self._state = self.STATE_OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "CircuitBreaker → OPEN (pausing %ds after %d failures)",
                self._current_pause, self._consecutive_failures,
            )
            # Exponential backoff for next open
            self._current_pause = min(self._current_pause * 2, self._max_pause)

    @property
    def info(self) -> dict:
        """Return state info for observability (/health, /twitter_status)."""
        return {
            "state": self.state,
            "consecutive_failures": self._consecutive_failures,
            "current_pause_seconds": self._current_pause,
        }
