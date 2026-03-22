"""
api/models.py
-------------
SQLAlchemy ORM models + async database engine setup.

Tables
------
tracked_wallets   – addresses we are actively monitoring (per chain)
whale_alerts      – every transaction that crossed the USD threshold
token_activity    – aggregated buy/sell counts per token per chain (trending)

Schema version
--------------
v2: added `chain` column to all three tables.
    tracked_wallets unique constraint changed from (address) → (address, chain)
    so the same address can be tracked independently on Ethereum, Base, Arbitrum.

Migration is handled in migrate_db() — safe to call on existing v1 databases.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config.settings import settings

logger = logging.getLogger(__name__)

# ── Engine & session factory ──────────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Base class ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class TrackedWallet(Base):
    """
    An address we are monitoring on a specific chain.

    The same address can appear multiple times with different chains —
    the compound unique constraint (address, chain) enforces this.
    """

    __tablename__ = "tracked_wallets"
    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_wallet_address_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_checked_block: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    alerts: Mapped[list["WhaleAlert"]] = relationship(
        "WhaleAlert", back_populates="wallet", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<TrackedWallet {self.chain}:{self.address[:10]}… label={self.label}>"


class WhaleAlert(Base):
    """A single whale transaction that crossed the USD threshold."""

    __tablename__ = "whale_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tracked_wallets.id"), nullable=False, index=True
    )
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)
    to_address: Mapped[str] = mapped_column(String(42), nullable=False)
    token_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True, index=True)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    amount_token: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL | SEND
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    wallet: Mapped["TrackedWallet"] = relationship("TrackedWallet", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<WhaleAlert {self.chain} {self.token_symbol} ${self.amount_usd:,.0f} {self.direction}>"


class TokenActivity(Base):
    """Aggregated token-level stats used by the trending endpoint."""

    __tablename__ = "token_activity"
    __table_args__ = (
        UniqueConstraint("token_address", "chain", name="uq_token_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    token_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    token_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    buy_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_count: Mapped[int] = mapped_column(Integer, default=0)
    total_volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    last_activity: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<TokenActivity {self.chain}:{self.token_symbol} buys={self.buy_count}>"


class PortfolioWallet(Base):
    """
    A wallet address added to portfolio tracking.

    Unlike TrackedWallet (which watches whale activity), PortfolioWallet
    is for monitoring the native-coin balance of addresses you own.
    Periodic snapshots are stored in PortfolioSnapshot.
    """

    __tablename__ = "portfolio_wallets"
    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_portfolio_address_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(
        "PortfolioSnapshot", back_populates="wallet", lazy="select",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PortfolioWallet {self.chain}:{self.address[:10]}… label={self.label}>"


class PortfolioSnapshot(Base):
    """
    A point-in-time snapshot of a portfolio wallet's native-coin balance.

    Taken every SNAPSHOT_INTERVAL seconds by PortfolioTracker.
    Also created on-demand when the `/balance` endpoint is called.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolio_wallets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chain: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    native_balance: Mapped[float] = mapped_column(Float, nullable=False)   # e.g. 1.234 ETH
    native_price_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    taken_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    wallet: Mapped["PortfolioWallet"] = relationship("PortfolioWallet", back_populates="snapshots")

    def __repr__(self) -> str:
        return (
            f"<PortfolioSnapshot wallet={self.wallet_id} "
            f"{self.native_balance:.4f} native @ ${self.native_price_usd:,.2f} "
            f"= ${self.total_usd:,.2f}>"
        )


class PriceAlertRule(Base):
    """
    A user-defined price alert rule for a specific token.

    When the token price crosses `target_price_usd` in the direction
    specified by `condition` ('above' or 'below'), an alert is fired
    and broadcast to all WebSocket subscribers.

    Cooldown of 1 hour is applied between repeated triggers to avoid spam.
    """

    __tablename__ = "price_alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    token_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    token_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    condition: Mapped[str] = mapped_column(String(5), nullable=False)   # 'above' | 'below'
    target_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_triggered_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PriceAlertRule {self.chain}:{self.token_symbol} "
            f"{self.condition} ${self.target_price_usd}>"
        )


class TwitterPost(Base):
    """
    Record of every tweet posted (or dry-run logged) by the TwitterBroadcaster.

    tweet_id is NULL when TWITTER_DRY_RUN=true — the content was formatted
    and stored for review but never sent to the Twitter API.
    """

    __tablename__ = "twitter_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tweet_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    thread_parent_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    posted_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    engagement_metrics: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    def __repr__(self) -> str:
        status = self.tweet_id or "dry-run"
        return f"<TwitterPost {self.alert_type} alert={self.alert_id} tweet={status}>"


class TelegramChannelPost(Base):
    """
    Record of every message posted (or dry-run logged) by TelegramChannelBroadcaster.

    message_id is NULL when TELEGRAM_CHANNEL_DRY_RUN=true — the content was
    formatted and stored for review but never sent to the Telegram Bot API.
    """

    __tablename__ = "telegram_channel_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    posted_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        status = self.message_id or "dry-run"
        return f"<TelegramChannelPost {self.alert_type} alert={self.alert_id} msg={status}>"


class BlueSkyPost(Base):
    """
    Record of every post created (or dry-run logged) by BlueSkyBroadcaster.

    post_uri / post_cid are NULL when BLUESKY_DRY_RUN=true.
    post_uri is the AT URI (at://did:plc:.../app.bsky.feed.post/...).
    post_cid is the content identifier (CID) of the post.
    """

    __tablename__ = "bluesky_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    post_uri: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    post_cid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    posted_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        status = self.post_uri or "dry-run"
        return f"<BlueSkyPost {self.alert_type} alert={self.alert_id} uri={status}>"


class BroadcasterMetric(Base):
    """
    Operational metrics for broadcaster plugins (queue depth, posts/day, etc.).
    Used for observability and analytics.
    """

    __tablename__ = "broadcaster_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<BroadcasterMetric {self.plugin_name}:{self.metric_name}={self.metric_value}>"


class SmartLabel(Base):
    """
    A known entity address for smart labeling (Exchanges, VCs, Smart Money, etc.).
    """

    __tablename__ = "smart_labels"
    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_smart_label_address_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="ethereum", index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tier: Mapped[str] = mapped_column(String(10), nullable=False, default="free") # 'free' or 'pro'
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<SmartLabel {self.chain}:{self.address[:8]} {self.name} tier={self.tier}>"


class GuildSubscription(Base):
    """
    Discord server/Guild subscription details to manage Free vs Pro features.
    """

    __tablename__ = "guild_subscriptions"

    guild_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    tier: Mapped[str] = mapped_column(String(10), nullable=False, default="free") # 'free' or 'pro'
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<GuildSubscription guild={self.guild_id} tier={self.tier}>"


class AlertChannel(Base):
    """
    A Discord channel configured to receive real-time alert push notifications.

    Each guild can have multiple alert channels with different filters
    (e.g., one for critical-only whale alerts, one for all chains).
    """

    __tablename__ = "alert_channels"
    __table_args__ = (
        UniqueConstraint("guild_id", "channel_id", name="uq_alert_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    min_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    chains: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )  # comma-separated chain names, NULL = all chains
    alert_types: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # comma-separated: "whale,price,portfolio", NULL = all
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<AlertChannel guild={self.guild_id} channel={self.channel_id} min_score={self.min_score}>"

    @property
    def chain_list(self) -> list[str] | None:
        """Parse chains CSV into a list, or None for 'all chains'."""
        if not self.chains:
            return None
        return [c.strip().lower() for c in self.chains.split(",") if c.strip()]

    @property
    def alert_type_list(self) -> list[str] | None:
        """Parse alert_types CSV into a list, or None for 'all types'."""
        if not self.alert_types:
            return None
        return [t.strip().lower() for t in self.alert_types.split(",") if t.strip()]


class AlertDelivery(Base):
    """
    Tracks delivery of each alert to each output channel.

    Used for observability: latency tracking, delivery confirmation,
    and debugging missed alerts.
    """

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    plugin_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="delivered"
    )  # delivered | dropped | failed
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    delivered_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<AlertDelivery {self.plugin_name} alert={self.alert_id} "
            f"status={self.status} latency={self.latency_ms:.1f}ms>"
        )


class WalletPosition(Base):
    """
    Running P&L tracker for a tracked wallet + token combination.

    Uses a weighted-average cost basis (WACB) approach:
    - BUY  → update avg_cost_usd and total_bought_usd / total_bought_token
    - SELL → realize P&L against current avg_cost, update total_sold_*

    token_key sentinel: token_address.lower() for ERC-20 tokens, or
    "NATIVE:{SYMBOL}" (e.g. "NATIVE:ETH") for native-coin transfers
    so the unique constraint never contains NULL.
    """

    __tablename__ = "wallet_positions"
    __table_args__ = (
        UniqueConstraint("wallet_address", "chain", "token_key", name="uq_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    token_key: Mapped[str] = mapped_column(String(50), nullable=False)   # sentinel key
    token_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    token_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)

    # Cost basis
    avg_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_bought_token: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_bought_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Realized P&L
    total_sold_token: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_sold_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    tx_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<WalletPosition {self.chain}:{self.wallet_address[:8]} "
            f"{self.token_key} pnl={self.realized_pnl_usd:,.2f}>"
        )


class AccumulationEvent(Base):
    """
    Fired when a tracked wallet buys the same token >= 3 times
    in a 24-hour window with total volume >= $50 K.

    A 6-hour cooldown prevents the same wallet+token pair from
    re-firing until the pattern resets.
    """

    __tablename__ = "accumulation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    token_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True, index=True)
    buy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_usd: Mapped[float] = mapped_column(Float, nullable=False)
    avg_per_tx_usd: Mapped[float] = mapped_column(Float, nullable=False)
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    fired_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return (
            f"<AccumulationEvent {self.chain}:{self.wallet_address[:8]} "
            f"{self.token_symbol} buys={self.buy_count} ${self.total_usd:,.0f}>"
        )


class ExchangeFlowEvent(Base):
    """
    Fired when a tracked whale wallet moves tokens to/from a known exchange.

    OUTFLOW = whale → exchange  (sell signal 🔴)
    INFLOW  = exchange → whale  (accumulation signal 🟢)

    Cooldown: 1 h per (wallet_address, exchange_address, token_address/symbol)
    to prevent spam when a whale makes multiple rapid transfers to the same exchange.
    """

    __tablename__ = "exchange_flow_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", "fired_at", name="pk_exchange_flow_events"),
    )

    id: Mapped[int] = mapped_column(Integer, autoincrement=True)
    whale_alert_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("whale_alerts.id"), nullable=False, index=True
    )
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    exchange_name: Mapped[str] = mapped_column(String(100), nullable=False)
    exchange_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    flow_direction: Mapped[str] = mapped_column(String(10), nullable=False)  # OUTFLOW | INFLOW
    token_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    token_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True, index=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    amount_token: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    fired_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return (
            f"<ExchangeFlowEvent {self.chain}:{self.wallet_address[:8]} "
            f"{self.flow_direction} → {self.exchange_name} "
            f"{self.token_symbol} ${self.amount_usd:,.0f}>"
        )





class SeenTransaction(Base):
    """
    Lightweight deduplication table — stores tx_hash+chain of every transaction
    already processed, preventing the same whale alert from being inserted twice
    if a scanner restarts mid-block.
    """

    __tablename__ = "seen_transactions"

    tx_hash: Mapped[str] = mapped_column(String(66), primary_key=True)
    chain:   Mapped[str] = mapped_column(String(20), primary_key=True)
    seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<SeenTransaction {self.chain}:{self.tx_hash[:12]}…>"





# ── DB lifecycle helpers ──────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Enable TimescaleDB extension, create all tables, and set up hypertables.
    Safe to call on a fresh DB or after a restart — all operations are idempotent.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    for table, time_col in [
        ("whale_alerts",        "detected_at"),
        ("portfolio_snapshots", "taken_at"),
        ("twitter_posts",       "posted_at"),
        ("alert_deliveries",    "delivered_at"),
        ("accumulation_events", "fired_at"),
        ("exchange_flow_events","fired_at"),
    ]:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    f"SELECT create_hypertable('{table}', '{time_col}', "
                    f"if_not_exists => TRUE, migrate_data => TRUE)"
                ))
            logger.info("TimescaleDB hypertable ready: %s", table)
        except Exception as exc:
            logger.warning(
                "Could not create hypertable for %s (non-fatal, data still works): %s",
                table, exc
            )


async def migrate_db() -> None:
    """
    Idempotent schema migration — upgrades pre-multi-chain PostgreSQL databases.
    Uses information_schema to detect missing columns; safe to run on every startup.
    """
    async with engine.begin() as conn:

        # 1. tracked_wallets — add chain column
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='tracked_wallets' AND column_name='chain'"
        ))
        if result.fetchone() is None:
            logger.info("Migration: adding chain to tracked_wallets…")
            await conn.execute(text(
                "ALTER TABLE tracked_wallets "
                "ADD COLUMN chain VARCHAR(20) NOT NULL DEFAULT 'ethereum'"
            ))
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_wallet_address_chain'
                    ) THEN
                        ALTER TABLE tracked_wallets
                        ADD CONSTRAINT uq_wallet_address_chain UNIQUE (address, chain);
                    END IF;
                END $$;
            """))
            logger.info("Migration: tracked_wallets done.")

        # 2. whale_alerts — add chain column
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='whale_alerts' AND column_name='chain'"
        ))
        if result.fetchone() is None:
            logger.info("Migration: adding chain to whale_alerts…")
            await conn.execute(text(
                "ALTER TABLE whale_alerts "
                "ADD COLUMN chain VARCHAR(20) NOT NULL DEFAULT 'ethereum'"
            ))
            logger.info("Migration: whale_alerts done.")

        # 3. token_activity — add chain column
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='token_activity' AND column_name='chain'"
        ))
        if result.fetchone() is None:
            logger.info("Migration: adding chain to token_activity…")
            await conn.execute(text(
                "ALTER TABLE token_activity "
                "ADD COLUMN chain VARCHAR(20) NOT NULL DEFAULT 'ethereum'"
            ))
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_token_chain'
                    ) THEN
                        ALTER TABLE token_activity
                        ADD CONSTRAINT uq_token_chain UNIQUE (token_address, chain);
                    END IF;
                END $$;
            """))
            logger.info("Migration: token_activity done.")

        # 4. smart_labels and guild_subscriptions tables will be created automatically by create_all 
        # in init_db(). No altering is needed for new tables unless columns need to be added to existing ones.


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
