"""
api/routers/whales.py
----------------------
Wallet management + chain info endpoints.

Routes
------
POST   /api/v1/wallets/track         – add wallet to tracking list
DELETE /api/v1/wallets/{address}     – deactivate tracking (per chain)
GET    /api/v1/wallets               – list tracked wallets (optionally filter by chain)
GET    /api/v1/tokens/trending       – tokens whales are buying most
GET    /api/v1/chains                – list configured chains + status
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import TrackedWallet, get_db
from api.services.price_alerts import get_trending_tokens
from config.chains import CHAIN_NAMES, CHAINS, active_chains
from config.settings import settings

router = APIRouter(prefix="/api/v1", tags=["Wallets"])

ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# base58 alphabet excludes 0, O, I, l to avoid visual ambiguity
_SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ── Schemas ───────────────────────────────────────────────────────────────────

class TrackWalletRequest(BaseModel):
    address: str
    chain: str = "ethereum"
    label: Optional[str] = None

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        v = v.lower()
        if v not in CHAIN_NAMES:
            raise ValueError(f"Unknown chain '{v}'. Supported: {', '.join(CHAIN_NAMES)}")
        return v

    @model_validator(mode="after")
    def validate_address_for_chain(self) -> "TrackWalletRequest":
        """Validate address format based on chain type. Solana addresses are case-sensitive."""
        if self.chain == "solana":
            if not _SOL_ADDRESS_RE.match(self.address):
                raise ValueError(
                    "Invalid Solana address (must be base58, 32–44 chars, "
                    "no 0/O/I/l characters)"
                )
            # Solana addresses are case-sensitive — do NOT lowercase
        else:
            if not ETH_ADDRESS_RE.match(self.address):
                raise ValueError("Invalid Ethereum address (must be 0x + 40 hex chars)")
            self.address = self.address.lower()
        return self


class WalletResponse(BaseModel):
    id: int
    address: str
    chain: str
    label: Optional[str]
    is_active: bool
    last_checked_block: Optional[int]

    model_config = {"from_attributes": True}


class TokenActivityResponse(BaseModel):
    chain: str
    token_address: str
    token_symbol: str
    buy_count: int
    sell_count: int
    total_volume_usd: float

    model_config = {"from_attributes": True}


class ChainStatusResponse(BaseModel):
    name: str
    chain_id: int
    emoji: str
    explorer: str
    native_token: str
    block_time: float
    poll_interval: int
    configured: bool   # True if RPC URL is set


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/wallets/track",
    response_model=WalletResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start tracking a wallet on a specific chain",
)
async def track_wallet(
    payload: TrackWalletRequest,
    db: AsyncSession = Depends(get_db),
) -> WalletResponse:
    """
    Add a wallet to the monitoring list for the specified chain.
    The same address can be tracked independently on different chains.
    Idempotent — returns the existing record if already tracked.
    """
    existing = await db.scalar(
        select(TrackedWallet).where(
            and_(
                TrackedWallet.address == payload.address,
                TrackedWallet.chain   == payload.chain,
            )
        )
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.label = payload.label or existing.label
            await db.commit()
            await db.refresh(existing)
        return WalletResponse.model_validate(existing)

    wallet = TrackedWallet(
        address=payload.address,
        chain=payload.chain,
        label=payload.label,
        is_active=True,
    )
    db.add(wallet)
    await db.commit()
    await db.refresh(wallet)
    return WalletResponse.model_validate(wallet)


@router.delete(
    "/wallets/{address}",
    status_code=status.HTTP_200_OK,
    summary="Stop tracking a wallet",
)
async def untrack_wallet(
    address: str,
    chain: str = Query(default="ethereum"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Soft-delete: mark the wallet as inactive without removing its alert history.
    Pass `?chain=base` to remove from a specific chain (default: ethereum).
    """
    # Solana addresses are case-sensitive; EVM addresses are normalised to lowercase
    if chain != "solana":
        address = address.lower()
    wallet = await db.scalar(
        select(TrackedWallet).where(
            and_(TrackedWallet.address == address, TrackedWallet.chain == chain)
        )
    )
    if not wallet:
        raise HTTPException(status_code=404, detail=f"Wallet {address} not found on {chain}.")

    wallet.is_active = False
    await db.commit()
    return {"message": f"No longer tracking {address} on {chain}."}


@router.get(
    "/wallets",
    response_model=list[WalletResponse],
    summary="List tracked wallets",
)
async def list_wallets(
    chain: Optional[str] = Query(default=None, description="Filter by chain name"),
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[WalletResponse]:
    query = select(TrackedWallet)
    if active_only:
        query = query.where(TrackedWallet.is_active == True)  # noqa: E712
    if chain:
        query = query.where(TrackedWallet.chain == chain.lower())
    result = await db.execute(query)
    return [WalletResponse.model_validate(w) for w in result.scalars().all()]


@router.get(
    "/tokens/trending",
    response_model=list[TokenActivityResponse],
    summary="Tokens whales are buying most",
)
async def trending_tokens(
    chain: Optional[str] = Query(default=None, description="Filter by chain"),
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> list[TokenActivityResponse]:
    """Top tokens by whale buy count. Optionally filter to a single chain."""
    tokens = await get_trending_tokens(db, chain=chain, limit=limit)
    return [TokenActivityResponse.model_validate(t) for t in tokens]


@router.get(
    "/chains",
    response_model=list[ChainStatusResponse],
    summary="List all supported chains",
)
async def list_chains() -> list[ChainStatusResponse]:
    """
    Returns every chain known to the system with its configuration and
    whether an RPC URL has been set (i.e. whether it is actively scanned).
    """
    configured = set(active_chains())
    return [
        ChainStatusResponse(
            name=name,
            chain_id=cfg.chain_id,
            emoji=cfg.emoji,
            explorer=cfg.explorer,
            native_token=cfg.native_token,
            block_time=cfg.block_time,
            poll_interval=cfg.poll_interval,
            configured=(name in configured),
        )
        for name, cfg in CHAINS.items()
    ]
