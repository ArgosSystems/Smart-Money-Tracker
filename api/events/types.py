"""
api/events/types.py
--------------------
Typed event subclasses for each alert source.

Each event enriches the base AlertDTO with domain-specific metadata keys.
The metadata dict is used (rather than dedicated fields) to keep the DTO
flat and JSON-serializable without needing per-type serialization logic.

Metadata keys documented per class below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from api.events.protocol import AlertDTO, AlertType


@dataclass(frozen=True, slots=True)
class WhaleAlertEvent(AlertDTO):
    """
    Enriched whale transaction alert.

    Expected metadata keys
    ----------------------
    tx_hash          : str
    from_address     : str
    to_address       : str
    from_label       : str | None   — resolved entity name ("Binance Hot Wallet")
    to_label         : str | None
    token_symbol     : str | None
    token_address    : str | None
    amount_token     : float
    amount_usd       : float
    direction        : "BUY" | "SELL" | "SEND"
    block_number     : int
    smart_money_score: float | None  — 0.0-1.0 confidence
    entity_type      : "exchange" | "vc" | "smart_money" | "unknown"
    """

    alert_type: AlertType = field(default=AlertType.WHALE, init=False)


@dataclass(frozen=True, slots=True)
class PriceTriggerEvent(AlertDTO):
    """
    Price rule hit event.

    Expected metadata keys
    ----------------------
    rule_id          : int
    token_symbol     : str
    token_address    : str
    condition        : "above" | "below"
    target_price_usd : float
    current_price_usd: float
    label            : str | None
    pct_change_24h   : float | None
    """

    alert_type: AlertType = field(default=AlertType.PRICE, init=False)


@dataclass(frozen=True, slots=True)
class PortfolioAlertEvent(AlertDTO):
    """
    Portfolio balance change alert.

    Expected metadata keys
    ----------------------
    wallet_id         : int
    is_public         : bool    — CRITICAL: if false, Twitter MUST suppress
    balance_change_usd: float
    balance_change_pct: float
    current_total_usd : float
    native_symbol     : str
    """

    alert_type: AlertType = field(default=AlertType.PORTFOLIO, init=False)
