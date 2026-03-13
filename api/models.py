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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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

    async with engine.begin() as conn:
        for table, time_col in [
            ("whale_alerts",        "detected_at"),
            ("portfolio_snapshots", "taken_at"),
        ]:
            await conn.execute(text(
                f"SELECT create_hypertable('{table}', '{time_col}', "
                f"if_not_exists => TRUE, migrate_data => TRUE)"
            ))
        logger.info("TimescaleDB hypertables ready.")


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


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
