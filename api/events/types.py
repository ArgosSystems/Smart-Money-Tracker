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
    from_smart_label_name: str | None
    from_smart_label_tier: str | None
    to_smart_label_name  : str | None
    to_smart_label_tier  : str | None

    Cluster enrichment (set when the whale wallet belongs to a detected cluster):
    cluster_id         : int | None    — FK to wallet_clusters.id
    cluster_label      : str | None    — human-readable label, e.g. "Whale X's Cluster"
    cluster_confidence : float | None  — 0.0-1.0 detection confidence
    cluster_size       : int | None    — number of wallets in the cluster
    cluster_methods    : str | None    — CSV of detection methods, e.g. "funding,timing"
    cluster_volume_usd : float | None  — combined USD volume across all cluster members
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


@dataclass(frozen=True, slots=True)
class AccumulationAlertEvent(AlertDTO):
    """
    Fired when a wallet accumulates the same token >= 3 times in 24 h
    and total buy volume exceeds $50 K.

    Expected metadata keys
    ----------------------
    wallet_address   : str
    token_symbol     : str
    token_address    : str | None
    buy_count        : int     — number of buys in the window
    total_usd        : float   — total USD volume in the window
    window_hours     : int     — look-back window (always 24)
    avg_per_tx_usd   : float   — total_usd / buy_count
    accumulation_id  : int     — FK to accumulation_events.id
    wallet_label     : str | None
    """

    alert_type: AlertType = field(default=AlertType.ACCUMULATION, init=False)


@dataclass(frozen=True, slots=True)
class ExchangeFlowAlertEvent(AlertDTO):
    """
    Fired when a tracked whale wallet moves tokens to/from a known exchange.

    OUTFLOW = whale → exchange  (sell signal 🔴)
    INFLOW  = exchange → whale  (accumulation signal 🟢)

    Uses SmartLabel.entity_type == 'exchange' to identify exchange addresses.
    Cooldown: 1 h per (wallet_address, exchange_address, token).

    Expected metadata keys
    ----------------------
    exchange_flow_id  : int     — FK to exchange_flow_events.id
    wallet_address    : str
    wallet_label      : str | None
    exchange_name     : str     — from SmartLabel.name
    exchange_address  : str
    flow_direction    : "OUTFLOW" | "INFLOW"
    token_symbol      : str | None
    token_address     : str | None
    amount_usd        : float
    amount_token      : float
    tx_hash           : str
    """

    alert_type: AlertType = field(default=AlertType.EXCHANGE_FLOW, init=False)
