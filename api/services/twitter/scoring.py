"""
api/services/twitter/scoring.py
--------------------------------
Priority scoring engine for alert→tweet conversion.

Each alert is scored 0–100.  Higher score = higher priority in the
posting queue.  Scores determine which alerts get posted when budget
is limited and which get dropped during queue overflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from api.events.protocol import AlertDTO, AlertType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScoredAlert:
    """Wrapper around AlertDTO with computed priority score."""

    event: AlertDTO
    score: float
    queued_at: datetime

    def __lt__(self, other: ScoredAlert) -> bool:
        """PriorityQueue ordering: higher score = higher priority (inverted for min-heap)."""
        return self.score > other.score


class AlertScorer:
    """
    Computes priority score for an alert based on type, value,
    entity classification, and configurable weights.

    Scoring matrix (defaults):
      Whale — Exchange transfer >$500k → 90
      Whale — VC wallet                → 80
      Whale — Smart money >$100k       → 70
      Whale — Other                    → 40
      Price — ATH                      → 75
      Price — Target hit               → 30
      Portfolio — Public opt-in only    → max 50
    """

    def __init__(self, weights: dict | None = None) -> None:
        self._weights = weights or {
            "whale_exchange_500k": 90,
            "whale_vc": 80,
            "whale_smart_money_100k": 70,
            "price_ath": 75,
            "price_target_hit": 30,
            "portfolio_public": 50,
        }

    def score(self, event: AlertDTO) -> float:
        if event.alert_type == AlertType.WHALE:
            return self._score_whale(event)
        elif event.alert_type == AlertType.PRICE:
            return self._score_price(event)
        elif event.alert_type == AlertType.PORTFOLIO:
            return self._score_portfolio(event)
        return 0.0

    def _score_whale(self, event: AlertDTO) -> float:
        meta = event.metadata
        amount_usd = meta.get("amount_usd", 0.0)
        entity_type = meta.get("entity_type", "unknown")

        if entity_type == "exchange" and amount_usd >= 500_000:
            return float(self._weights.get("whale_exchange_500k", 90))
        elif entity_type == "vc":
            return float(self._weights.get("whale_vc", 80))
        elif entity_type == "smart_money" and amount_usd >= 100_000:
            return float(self._weights.get("whale_smart_money_100k", 70))

        # Fallback: scale linearly from 20-60 based on USD value
        # $10K → 20, $100K → 40, $500K+ → 60
        capped = min(amount_usd, 500_000)
        return 20 + (capped / 500_000) * 40

    def _score_price(self, event: AlertDTO) -> float:
        meta = event.metadata
        pct_change = abs(meta.get("pct_change_24h", 0.0) or 0.0)

        # Boost for extreme moves
        if pct_change >= 100:
            return float(self._weights.get("price_ath", 75))
        return float(self._weights.get("price_target_hit", 30))

    def _score_portfolio(self, event: AlertDTO) -> float:
        meta = event.metadata
        if not meta.get("is_public", False):
            return 0.0  # Private wallets get zero score → suppressed
        return float(self._weights.get("portfolio_public", 50))
