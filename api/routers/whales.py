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

import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import TrackedWallet, WalletPosition, get_db
from api.services.price_alerts import fetch_token_price, get_trending_tokens
from config.chains import CHAIN_NAMES, CHAINS, active_chains
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Wallets"])

ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# DeFiLlama coin keys for native (non-contract) tokens
_DEFILLAMA_URL = "https://coins.llama.fi/prices/current"
_NATIVE_COIN_KEYS: dict[str, str] = {
    "ETH": "coingecko:ethereum",
    "BNB": "coingecko:binancecoin",
    "POL": "coingecko:matic-network",
    "SOL": "coingecko:solana",
}


async def _fetch_native_price(coin_key: str) -> float:
    """Fetch native-token USD price from DeFiLlama using a coingecko: coin key."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_DEFILLAMA_URL}/{coin_key}")
            resp.raise_for_status()
            return resp.json().get("coins", {}).get(coin_key, {}).get("price", 0.0)
    except Exception as exc:
        logger.warning("Native price fetch failed for %s: %s", coin_key, exc)
        return 0.0
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
    label_updated: bool = False

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
        updated = False
        label_updated = False
        if not existing.is_active:
            existing.is_active = True
            updated = True
        if payload.label and payload.label != existing.label:
            existing.label = payload.label
            updated = True
            label_updated = True
        if updated:
            await db.commit()
            await db.refresh(existing)
        result = WalletResponse.model_validate(existing)
        result.label_updated = label_updated
        return result

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


# ── Wallet P&L ────────────────────────────────────────────────────────────────

class WalletPositionResponse(BaseModel):
    id: int
    wallet_address: str
    chain: str
    token_key: str
    token_symbol: Optional[str]
    token_address: Optional[str]
    avg_cost_usd: float
    total_bought_token: float
    total_bought_usd: float
    total_sold_token: float
    total_sold_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float = 0.0  # computed at request time — not stored in DB
    tx_count: int

    model_config = {"from_attributes": True}


@router.get(
    "/wallets/{address}/pnl",
    response_model=list[WalletPositionResponse],
    summary="Wallet P&L — realized + unrealized profit/loss per token",
)
async def wallet_pnl(
    address: str,
    chain: Optional[str] = Query(default=None, description="Filter by chain"),
    db: AsyncSession = Depends(get_db),
) -> list[WalletPositionResponse]:
    """
    Return all tracked token positions with realized and unrealized P&L.

    Unrealized P&L = (current_price − avg_cost) × qty_held, fetched live
    from DeFiLlama.  Positions are created automatically whenever a whale
    alert for this address is committed by the scanner.
    """
    query = select(WalletPosition).where(
        WalletPosition.wallet_address == address.lower()
    ).order_by(WalletPosition.realized_pnl_usd.desc())

    if chain:
        query = query.where(WalletPosition.chain == chain.lower())

    result = await db.execute(query)
    positions = list(result.scalars().all())

    responses: list[WalletPositionResponse] = []
    for pos in positions:
        qty_held = max(0.0, pos.total_bought_token - pos.total_sold_token)
        unrealized = 0.0

        if qty_held > 0:
            if pos.token_address:
                current_price = await fetch_token_price(pos.token_address, pos.chain)
            else:
                # Native token (e.g. NATIVE:ETH) — use coingecko coin key
                symbol = pos.token_key.split(":")[-1] if ":" in pos.token_key else ""
                coin_key = _NATIVE_COIN_KEYS.get(symbol)
                current_price = await _fetch_native_price(coin_key) if coin_key else 0.0

            unrealized = (current_price - pos.avg_cost_usd) * qty_held

        resp = WalletPositionResponse.model_validate(pos).model_copy(
            update={"unrealized_pnl_usd": unrealized}
        )
        responses.append(resp)

    return responses
