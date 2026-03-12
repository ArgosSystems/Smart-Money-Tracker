"""
api/routers/portfolio.py
------------------------
REST endpoints for portfolio wallet tracking.

Routes
------
POST   /api/v1/portfolio/wallets                     – add a wallet
GET    /api/v1/portfolio/wallets                     – list wallets (filter by chain)
GET    /api/v1/portfolio/wallets/{id}                – get single wallet
DELETE /api/v1/portfolio/wallets/{id}                – remove a wallet
PATCH  /api/v1/portfolio/wallets/{id}/toggle         – enable / disable snapshots
GET    /api/v1/portfolio/wallets/{id}/balance        – live on-chain balance (+ saves snapshot)
GET    /api/v1/portfolio/wallets/{id}/snapshots      – historical snapshots
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import PortfolioSnapshot, PortfolioWallet, get_db
from api.services.portfolio_tracker import fetch_wallet_balance
from config.chains import CHAINS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/portfolio", tags=["Portfolio"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PortfolioWalletCreate(BaseModel):
    address: str = Field(..., description="Wallet address (0x…)")
    chain: str = Field("ethereum", description="Chain name (ethereum, bsc, polygon, …)")
    label: Optional[str] = Field(None, max_length=100, description="Optional human-readable label")


class PortfolioWalletResponse(BaseModel):
    id: int
    address: str
    chain: str
    label: Optional[str]
    is_active: bool
    added_at: datetime.datetime

    model_config = {"from_attributes": True}

    @field_serializer("added_at")
    def serialize_dt(self, v: datetime.datetime) -> str:
        return v.isoformat()


class PortfolioWalletToggle(BaseModel):
    id: int
    is_active: bool


class PortfolioSnapshotResponse(BaseModel):
    id: int
    wallet_id: int
    chain: str
    native_balance: float
    native_price_usd: float
    total_usd: float
    taken_at: datetime.datetime

    model_config = {"from_attributes": True}

    @field_serializer("taken_at")
    def serialize_dt(self, v: datetime.datetime) -> str:
        return v.isoformat()


class BalanceResponse(BaseModel):
    wallet_id: int
    address: str
    chain: str
    native_symbol: str
    native_balance: float
    native_price_usd: float
    total_usd: float
    fetched_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_chain(chain: str) -> str:
    chain = chain.lower()
    if chain not in CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown chain '{chain}'. Supported: {list(CHAINS.keys())}",
        )
    return chain


async def _get_wallet_or_404(wallet_id: int, db: AsyncSession) -> PortfolioWallet:
    wallet = await db.get(PortfolioWallet, wallet_id)
    if not wallet:
        raise HTTPException(status_code=404, detail=f"Portfolio wallet {wallet_id} not found")
    return wallet


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/wallets",
    response_model=PortfolioWalletResponse,
    status_code=201,
    summary="Add wallet to portfolio",
)
async def add_portfolio_wallet(
    body: PortfolioWalletCreate,
    db: AsyncSession = Depends(get_db),
) -> PortfolioWalletResponse:
    """
    Add a wallet address to portfolio tracking.

    The address will have its native-coin balance snapshotted every 5 minutes,
    and can be queried live at any time via the `/balance` endpoint.
    """
    chain = _validate_chain(body.chain)
    address = body.address.lower()

    # Reject duplicates gracefully
    existing = await db.execute(
        select(PortfolioWallet).where(
            PortfolioWallet.address == address,
            PortfolioWallet.chain == chain,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Wallet {address} on {chain} is already in your portfolio",
        )

    wallet = PortfolioWallet(address=address, chain=chain, label=body.label)
    db.add(wallet)
    await db.commit()
    await db.refresh(wallet)
    return PortfolioWalletResponse.model_validate(wallet)


@router.get(
    "/wallets",
    response_model=list[PortfolioWalletResponse],
    summary="List portfolio wallets",
)
async def list_portfolio_wallets(
    chain: Optional[str] = Query(default=None, description="Filter by chain"),
    active_only: bool = Query(default=False, description="Return only active wallets"),
    db: AsyncSession = Depends(get_db),
) -> list[PortfolioWalletResponse]:
    """Return all portfolio wallets, optionally filtered."""
    q = select(PortfolioWallet)
    if chain:
        q = q.where(PortfolioWallet.chain == _validate_chain(chain))
    if active_only:
        q = q.where(PortfolioWallet.is_active.is_(True))
    result = await db.execute(q)
    return [PortfolioWalletResponse.model_validate(w) for w in result.scalars().all()]


@router.get(
    "/wallets/{wallet_id}",
    response_model=PortfolioWalletResponse,
    summary="Get portfolio wallet",
)
async def get_portfolio_wallet(
    wallet_id: int,
    db: AsyncSession = Depends(get_db),
) -> PortfolioWalletResponse:
    """Return a single portfolio wallet by ID."""
    wallet = await _get_wallet_or_404(wallet_id, db)
    return PortfolioWalletResponse.model_validate(wallet)


@router.delete(
    "/wallets/{wallet_id}",
    response_class=Response,
    status_code=204,
    summary="Remove wallet from portfolio",
)
async def delete_portfolio_wallet(
    wallet_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a portfolio wallet and all its snapshots (cascade)."""
    wallet = await _get_wallet_or_404(wallet_id, db)
    await db.delete(wallet)
    await db.commit()
    return Response(status_code=204)


@router.patch(
    "/wallets/{wallet_id}/toggle",
    response_model=PortfolioWalletToggle,
    summary="Toggle snapshot tracking",
)
async def toggle_portfolio_wallet(
    wallet_id: int,
    db: AsyncSession = Depends(get_db),
) -> PortfolioWalletToggle:
    """Flip `is_active` — pauses / resumes automatic balance snapshots."""
    wallet = await _get_wallet_or_404(wallet_id, db)
    wallet.is_active = not wallet.is_active
    await db.commit()
    await db.refresh(wallet)
    return PortfolioWalletToggle(id=wallet.id, is_active=wallet.is_active)


@router.get(
    "/wallets/{wallet_id}/balance",
    response_model=BalanceResponse,
    summary="Fetch live on-chain balance",
)
async def get_live_balance(
    wallet_id: int,
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """
    Fetch the current native-coin balance for this wallet directly from the chain.

    Also saves a `PortfolioSnapshot` row so the reading appears in history.
    Returns an error if the chain has no configured RPC URL.
    """
    wallet = await _get_wallet_or_404(wallet_id, db)

    try:
        data = await fetch_wallet_balance(wallet.address, wallet.chain)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("Live balance fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"RPC call failed: {exc}")

    # Persist as a snapshot
    snap = PortfolioSnapshot(
        wallet_id=wallet.id,
        chain=wallet.chain,
        native_balance=data["native_balance"],
        native_price_usd=data["native_price_usd"],
        total_usd=data["total_usd"],
        taken_at=datetime.datetime.utcnow(),
    )
    db.add(snap)
    await db.commit()

    return BalanceResponse(
        wallet_id=wallet.id,
        address=data["address"],
        chain=data["chain"],
        native_symbol=data["native_symbol"],
        native_balance=data["native_balance"],
        native_price_usd=data["native_price_usd"],
        total_usd=data["total_usd"],
        fetched_at=snap.taken_at.isoformat(),
    )


@router.get(
    "/wallets/{wallet_id}/snapshots",
    response_model=list[PortfolioSnapshotResponse],
    summary="Get balance history",
)
async def get_snapshots(
    wallet_id: int,
    limit: int = Query(default=50, ge=1, le=500, description="Max snapshots to return"),
    db: AsyncSession = Depends(get_db),
) -> list[PortfolioSnapshotResponse]:
    """
    Return the stored balance snapshots for a wallet, newest first.

    Snapshots are created automatically every 5 minutes and on every
    call to the `/balance` endpoint.
    """
    await _get_wallet_or_404(wallet_id, db)
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.wallet_id == wallet_id)
        .order_by(desc(PortfolioSnapshot.taken_at))
        .limit(limit)
    )
    return [PortfolioSnapshotResponse.model_validate(s) for s in result.scalars().all()]
