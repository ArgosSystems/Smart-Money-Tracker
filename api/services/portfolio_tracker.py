"""
api/services/portfolio_tracker.py
-----------------------------------
Background service that periodically snapshots the native-coin balance of every
active PortfolioWallet and stores the result in PortfolioSnapshot.

Design
------
- Runs as an asyncio task launched from api/main.py lifespan.
- Every SNAPSHOT_INTERVAL seconds it:
    1. Loads all active PortfolioWallet rows grouped by chain.
    2. Opens one AsyncWeb3 connection per chain (reusing it across all wallets).
    3. Fetches native balance (wei → decimals) for each address.
    4. Looks up the current native-coin USD price from CoinGecko.
    5. Commits a PortfolioSnapshot row for each wallet.
- Triggered on-demand via fetch_wallet_balance() called by the API router.

Supported native tokens
-----------------------
ETH   – Ethereum, Base, Arbitrum, Optimism
BNB   – BSC
POL   – Polygon
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

import httpx
from sqlalchemy import select
from web3 import AsyncWeb3
from web3.providers.async_rpc import AsyncHTTPProvider

from api.models import AsyncSessionLocal, PortfolioSnapshot, PortfolioWallet
from config.chains import CHAINS
from config.settings import settings

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = 300   # seconds between automatic snapshots

# CoinGecko coin-ID for each native token symbol
_NATIVE_COINGECKO_ID: dict[str, str] = {
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "POL": "matic-network",   # CoinGecko still recognises the old id
}


# ── Price helpers ─────────────────────────────────────────────────────────────

async def _fetch_native_price(symbol: str) -> float:
    """Fetch current USD price for a native token (ETH, BNB, POL) via CoinGecko."""
    coin_id = _NATIVE_COINGECKO_ID.get(symbol.upper())
    if not coin_id:
        logger.warning("No CoinGecko ID for native token %s", symbol)
        return 0.0
    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={coin_id}&vs_currencies=usd"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return float(resp.json().get(coin_id, {}).get("usd", 0.0))
    except Exception as exc:
        logger.warning("Native price fetch failed for %s: %s", symbol, exc)
        return 0.0


# ── Balance fetch ─────────────────────────────────────────────────────────────

async def fetch_wallet_balance(
    address: str,
    chain_name: str,
) -> dict:
    """
    Return the current native-coin balance for *address* on *chain_name*.

    Returns a dict with keys:
        address, chain, native_symbol, native_balance, native_price_usd, total_usd
    Raises ValueError if the chain is unknown or has no configured RPC.
    """
    chain_name = chain_name.lower()
    if chain_name not in CHAINS:
        raise ValueError(f"Unknown chain: {chain_name}")

    chain_cfg = CHAINS[chain_name]
    rpc_url = settings.get_rpc_url(chain_name)
    if not rpc_url:
        raise ValueError(f"No RPC URL configured for chain: {chain_name}")

    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    checksum_addr = AsyncWeb3.to_checksum_address(address)
    balance_wei = await w3.eth.get_balance(checksum_addr)
    native_balance = float(AsyncWeb3.from_wei(balance_wei, "ether"))

    native_price = await _fetch_native_price(chain_cfg.native_token)
    total_usd = native_balance * native_price

    return {
        "address": address.lower(),
        "chain": chain_name,
        "native_symbol": chain_cfg.native_token,
        "native_balance": native_balance,
        "native_price_usd": native_price,
        "total_usd": total_usd,
    }


# ── PortfolioTracker ──────────────────────────────────────────────────────────

class PortfolioTracker:
    """
    Background task that snapshots all active PortfolioWallet balances
    every SNAPSHOT_INTERVAL seconds.
    """

    async def start(self) -> None:
        logger.info("PortfolioTracker started (interval=%ds)", SNAPSHOT_INTERVAL)
        while True:
            try:
                await self._snapshot_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("PortfolioTracker snapshot error: %s", exc, exc_info=True)
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    async def _snapshot_all(self) -> None:
        """Load all active wallets, fetch balances, store snapshots."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PortfolioWallet).where(PortfolioWallet.is_active.is_(True))
            )
            wallets = result.scalars().all()

        if not wallets:
            return

        # Group wallets by chain to share one web3 connection + one price call
        by_chain: dict[str, list[PortfolioWallet]] = {}
        for w in wallets:
            by_chain.setdefault(w.chain, []).append(w)

        for chain_name, chain_wallets in by_chain.items():
            rpc_url = settings.get_rpc_url(chain_name)
            if not rpc_url:
                logger.debug("Skipping %s — no RPC configured", chain_name)
                continue

            chain_cfg = CHAINS.get(chain_name)
            if not chain_cfg:
                continue

            try:
                native_price = await _fetch_native_price(chain_cfg.native_token)
                w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))

                async with AsyncSessionLocal() as db:
                    for wallet in chain_wallets:
                        try:
                            checksum_addr = AsyncWeb3.to_checksum_address(wallet.address)
                            wei = await w3.eth.get_balance(checksum_addr)
                            native_balance = float(AsyncWeb3.from_wei(wei, "ether"))
                            total_usd = native_balance * native_price

                            snap = PortfolioSnapshot(
                                wallet_id=wallet.id,
                                chain=chain_name,
                                native_balance=native_balance,
                                native_price_usd=native_price,
                                total_usd=total_usd,
                                taken_at=datetime.datetime.utcnow(),
                            )
                            db.add(snap)
                        except Exception as exc:
                            logger.warning(
                                "Balance fetch failed for %s on %s: %s",
                                wallet.address, chain_name, exc,
                            )
                    await db.commit()
                    logger.info(
                        "PortfolioTracker: snapshotted %d wallets on %s",
                        len(chain_wallets), chain_name,
                    )
            except Exception as exc:
                logger.error(
                    "PortfolioTracker chain error [%s]: %s", chain_name, exc, exc_info=True
                )
