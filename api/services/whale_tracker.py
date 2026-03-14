"""
api/services/whale_tracker.py
------------------------------
Multi-chain whale tracking engine.

Classes
-------
BaseChainScanner    – abstract interface that all chain scanners implement
EvmChainScanner     – EVM chains (Ethereum, Base, Arbitrum, BSC, Polygon, Optimism)
MultiChainTracker   – orchestrates N scanners concurrently (one asyncio task per chain)

The abstraction layer
---------------------
BaseChainScanner defines three abstract methods:
    is_healthy()          → bool
    get_latest_block()    → int   (returns slot number for Solana)
    scan_block(n)         → list[WhaleAlert]

And one concrete default:
    scan_range(from, to)  → calls scan_block() in MAX_CONCURRENT_SCANS batches

SolanaScanner (api/services/solana_scanner.py) overrides scan_range() with
getSignaturesForAddress per wallet, which is far more efficient than scanning
individual Solana slots.

MultiChainTracker._build_scanners() dispatches on chain_type:
    "evm"    → EvmChainScanner
    "solana" → SolanaScanner
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional, cast

import httpx
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3
from web3 import AsyncHTTPProvider
from web3.types import FilterParams

from api.models import AsyncSessionLocal, TokenActivity, TrackedWallet, WhaleAlert
from api.events.dispatcher import event_dispatcher
from api.events.types import WhaleAlertEvent
from config.chains import CHAINS, ChainConfig, active_chains
from config.settings import settings

logger = logging.getLogger(__name__)

# ── ERC-20 ABI fragments ──────────────────────────────────────────────────────

ERC20_META_ABI = [
    {"constant": True, "inputs": [], "name": "symbol",   "outputs": [{"name": "", "type": "string"}],  "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",  "outputs": [{"name": "", "type": "uint8"}],   "type": "function"},
]

# keccak256("Transfer(address,address,uint256)") — same on every EVM chain
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# ── Price cache with TTL ──────────────────────────────────────────────────────

class _PriceCache:
    TTL = 60.0  # seconds

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, float]] = {}   # key → (price, expires_at)

    def get(self, key: str) -> Optional[float]:
        entry = self._data.get(key)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        return None

    def set(self, key: str, price: float) -> None:
        self._data[key] = (price, time.monotonic() + self.TTL)


# Module-level ETH price cache — shared across ALL EvmChainScanner instances so
# concurrent chains don't each make their own CoinGecko call every cycle.
_SHARED_ETH_PRICE_CACHE = _PriceCache()


# ── Abstract base scanner ─────────────────────────────────────────────────────

class BaseChainScanner(ABC):
    """
    Abstract interface for all chain scanners.

    Each concrete implementation (EvmChainScanner, SolanaScanner) handles
    one chain's RPC protocol.  MultiChainTracker talks only to this interface.

    scan_range() default: calls scan_block() for each block/slot in batches of
    MAX_CONCURRENT_SCANS.  Solana overrides this with a single per-wallet RPC
    covering the entire slot range, which is far cheaper.
    """

    MAX_CONCURRENT_SCANS = 5

    def __init__(self, chain_name: str, config: ChainConfig, rpc_url: str) -> None:
        self.chain_name = chain_name
        self.config     = config
        self._rpc_url   = rpc_url

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Ping the RPC; return True if reachable within 5 s."""

    @abstractmethod
    async def get_latest_block(self) -> int:
        """Return the latest confirmed block number (or slot for Solana)."""

    @abstractmethod
    async def scan_block(self, block_number: int) -> list[WhaleAlert]:
        """Scan a single block/slot and return new WhaleAlerts."""

    async def scan_range(self, from_block: int, to_block: int) -> list[WhaleAlert]:
        """
        Scan all blocks in [from_block, to_block].

        Default: calls scan_block() for each in batches of MAX_CONCURRENT_SCANS.
        Override for chains (e.g. Solana) that support range-based queries.
        """
        blocks = list(range(from_block, to_block + 1))
        results: list[WhaleAlert] = []
        for i in range(0, len(blocks), self.MAX_CONCURRENT_SCANS):
            batch = blocks[i : i + self.MAX_CONCURRENT_SCANS]
            batch_results = await asyncio.gather(
                *[self.scan_block(b) for b in batch],
                return_exceptions=True,
            )
            for r in batch_results:
                if isinstance(r, list):
                    results.extend(r)
        return results

    # ── Shared DB helpers (available to all subclasses) ───────────────────────

    async def _load_wallets(self, db: AsyncSession) -> list[TrackedWallet]:
        result = await db.execute(
            select(TrackedWallet).where(
                and_(TrackedWallet.chain == self.chain_name, TrackedWallet.is_active == True)  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def _alert_exists(self, db: AsyncSession, tx_hash: str) -> bool:
        return bool(await db.scalar(
            select(WhaleAlert).where(
                and_(WhaleAlert.tx_hash == tx_hash, WhaleAlert.chain == self.chain_name)
            )
        ))

    async def _upsert_token_activity(
        self, db: AsyncSession, token_address: str, symbol: str, direction: str, usd_value: float
    ) -> None:
        row = await db.scalar(
            select(TokenActivity).where(
                and_(TokenActivity.token_address == token_address, TokenActivity.chain == self.chain_name)
            )
        )
        if row is None:
            row = TokenActivity(
                chain=self.chain_name,
                token_address=token_address,
                token_symbol=symbol,
            )
            db.add(row)

        if direction == "BUY":
            row.buy_count += 1
        else:
            row.sell_count += 1
        row.total_volume_usd += usd_value


# ── EvmChainScanner ───────────────────────────────────────────────────────────

class EvmChainScanner(BaseChainScanner):
    """
    Scans a single EVM chain for whale transactions.

    Public API
    ----------
    is_healthy()           → bool
    get_latest_block()     → int
    scan_block(n)          → list[WhaleAlert]   (creates its own DB session)
    scan_range(from, to)   → uses default BaseChainScanner batched implementation
    """

    def __init__(self, chain_name: str, config: ChainConfig, rpc_url: str) -> None:
        super().__init__(chain_name, config, rpc_url)
        self._w3: Optional[AsyncWeb3] = None
        self._token_meta_cache: dict[str, dict] = {}
        self._price_cache = _PriceCache()
        # ETH price is the same for every chain — use the shared module-level cache
        self._eth_price_cache = _SHARED_ETH_PRICE_CACHE

    # ── Web3 client (lazy) ────────────────────────────────────────────────────

    @property
    def w3(self) -> AsyncWeb3:
        if self._w3 is None:
            self._w3 = AsyncWeb3(AsyncHTTPProvider(self._rpc_url))
        return self._w3

    # ── Public interface ──────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Ping the RPC; return True if we get a block number within 5 s."""
        try:
            await asyncio.wait_for(self.w3.eth.block_number, timeout=5.0)
            return True
        except Exception:
            return False

    async def get_latest_block(self) -> int:
        return await self.w3.eth.block_number

    async def scan_block(self, block_number: int) -> list[WhaleAlert]:
        """
        Scan one block for whale activity across all tracked wallets on this chain.

        Steps
        -----
        1. Load active wallets for this chain from DB.
        2. Fetch all ERC-20 Transfer events in the block (one getLogs call).
        3. Filter events to those involving tracked addresses.
        4. Batch-fetch USD prices via CoinGecko for the token set.
        5. Persist alerts above WHALE_THRESHOLD_USD.
        6. Also scan native-token (ETH) transfers from the full block.
        """
        new_alerts: list[WhaleAlert] = []

        async with AsyncSessionLocal() as db:
            wallets = await self._load_wallets(db)
            if not wallets:
                return []

            wallet_set = {w.address.lower() for w in wallets}
            wallet_map = {w.address.lower(): w for w in wallets}
            eth_price  = await self._get_eth_price()

            # ── ERC-20 transfers ──────────────────────────────────────────────
            try:
                logs = await self.w3.eth.get_logs(cast(FilterParams, {
                    "fromBlock": hex(block_number),
                    "toBlock":   hex(block_number),
                    "topics":    [TRANSFER_TOPIC],
                }))
            except Exception as exc:
                logger.warning("[%s] get_logs block %d failed: %s", self.chain_name, block_number, exc)
                logs = []

            relevant_logs = [
                log for log in logs
                if len(log["topics"]) >= 3
                and (
                    ("0x" + log["topics"][1].hex()[-40:]).lower() in wallet_set
                    or ("0x" + log["topics"][2].hex()[-40:]).lower() in wallet_set
                )
            ]

            if relevant_logs:
                token_addrs = list({log["address"].lower() for log in relevant_logs})
                prices = await self._get_token_prices(token_addrs)

                for log in relevant_logs:
                    alert = await self._process_erc20_log(
                        log, wallet_set, wallet_map, prices, block_number, db
                    )
                    if alert:
                        new_alerts.append(alert)

            # ── Native ETH transfers ──────────────────────────────────────────
            if eth_price > 0:
                try:
                    block = await self.w3.eth.get_block(block_number, full_transactions=True)
                    for tx in block["transactions"]:
                        alert = await self._process_native_tx(
                            tx, wallet_set, wallet_map, eth_price, block_number, db
                        )
                        if alert:
                            new_alerts.append(alert)
                except Exception as exc:
                    logger.warning("[%s] get_block %d failed: %s", self.chain_name, block_number, exc)

            if new_alerts:
                await db.commit()
                logger.info(
                    "[%s] block %d → %d new whale alert(s)",
                    self.chain_name, block_number, len(new_alerts)
                )
                # Broadcast to all registered plugins (WebSocket, Twitter, etc.)
                for alert in new_alerts:
                    wallet = wallet_map.get(alert.from_address) or wallet_map.get(alert.to_address)
                    await event_dispatcher.dispatch(WhaleAlertEvent(
                        alert_id=alert.id,
                        chain=alert.chain,
                        timestamp=alert.detected_at or datetime.datetime.utcnow(),
                        metadata={
                            "id":             alert.id,
                            "tx_hash":        alert.tx_hash,
                            "from_address":   alert.from_address,
                            "to_address":     alert.to_address,
                            "from_label":     wallet.label if wallet and alert.from_address == wallet.address.lower() else None,
                            "to_label":       wallet.label if wallet and alert.to_address == wallet.address.lower() else None,
                            "token_symbol":   alert.token_symbol,
                            "token_address":  alert.token_address,
                            "amount_token":   alert.amount_token,
                            "amount_usd":     alert.amount_usd,
                            "direction":      alert.direction,
                            "block_number":   alert.block_number,
                            "detected_at":    alert.detected_at.isoformat() if alert.detected_at else None,
                            "smart_money_score": None,
                            "entity_type":    "unknown",
                        },
                    ))

        return new_alerts

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _process_erc20_log(
        self,
        log: Any,
        wallet_set: set[str],
        wallet_map: dict[str, TrackedWallet],
        prices: dict[str, float],
        block_number: int,
        db: AsyncSession,
    ) -> Optional[WhaleAlert]:
        token_addr = log["address"].lower()
        price = prices.get(token_addr, 0.0)
        if price == 0.0:
            return None

        meta = await self._get_token_meta(token_addr)

        try:
            raw_data = log["data"]
            raw_value = int(raw_data.hex(), 16) if hasattr(raw_data, "hex") else int(raw_data, 16)
        except (ValueError, AttributeError):
            return None

        if raw_value == 0:
            return None

        token_amount = raw_value / (10 ** meta["decimals"])
        usd_value    = token_amount * price

        if usd_value < settings.whale_threshold_usd:
            return None

        from_addr = ("0x" + log["topics"][1].hex()[-40:]).lower()
        to_addr   = ("0x" + log["topics"][2].hex()[-40:]).lower()
        direction = "SELL" if from_addr in wallet_set else "BUY"
        wallet    = wallet_map.get(from_addr) or wallet_map.get(to_addr)
        if not wallet:
            return None

        tx_hash = log["transactionHash"].hex()
        if await self._alert_exists(db, tx_hash):
            return None

        alert = WhaleAlert(
            wallet_id=wallet.id,
            chain=self.chain_name,
            tx_hash=tx_hash,
            from_address=from_addr,
            to_address=to_addr,
            token_address=token_addr,
            token_symbol=meta["symbol"],
            amount_token=token_amount,
            amount_usd=usd_value,
            direction=direction,
            block_number=block_number,
            raw_data=json.dumps({"log_index": log.get("logIndex")}),
        )
        db.add(alert)
        await self._upsert_token_activity(db, token_addr, meta["symbol"], direction, usd_value)

        logger.info(
            "[%s] %s %s %.4f ($%.0f) tx=%s",
            self.chain_name, direction, meta["symbol"], token_amount, usd_value, tx_hash[:12]
        )
        return alert

    async def _process_native_tx(
        self,
        tx,
        wallet_set: set[str],
        wallet_map: dict[str, TrackedWallet],
        eth_price: float,
        block_number: int,
        db: AsyncSession,
    ) -> Optional[WhaleAlert]:
        from_addr = tx["from"].lower()
        to_addr   = (tx.get("to") or "").lower()

        if from_addr not in wallet_set and to_addr not in wallet_set:
            return None

        eth_value = float(self.w3.from_wei(tx["value"], "ether"))
        usd_value = eth_value * eth_price

        if usd_value < settings.whale_threshold_usd:
            return None

        direction = "SEND" if from_addr in wallet_set else "BUY"
        wallet    = wallet_map.get(from_addr) or wallet_map.get(to_addr)
        if not wallet:
            return None

        tx_hash = tx["hash"].hex()
        if await self._alert_exists(db, tx_hash):
            return None

        alert = WhaleAlert(
            wallet_id=wallet.id,
            chain=self.chain_name,
            tx_hash=tx_hash,
            from_address=from_addr,
            to_address=to_addr,
            token_address=None,
            token_symbol=self.config.native_token,
            amount_token=eth_value,
            amount_usd=usd_value,
            direction=direction,
            block_number=block_number,
            raw_data=json.dumps({"gas": tx.get("gas")}),
        )
        db.add(alert)

        logger.info(
            "[%s] %s %s %.4f ETH ($%.0f) tx=%s",
            self.chain_name, direction, self.config.native_token,
            eth_value, usd_value, tx_hash[:12]
        )
        return alert

    async def _get_token_meta(self, token_address: str) -> dict:
        if token_address in self._token_meta_cache:
            return self._token_meta_cache[token_address]

        checksum = AsyncWeb3.to_checksum_address(token_address)
        contract = self.w3.eth.contract(address=checksum, abi=ERC20_META_ABI)
        try:
            symbol   = await contract.functions.symbol().call()
            decimals = await contract.functions.decimals().call()
        except Exception:
            symbol, decimals = "???", 18

        meta = {"symbol": symbol, "decimals": decimals}
        self._token_meta_cache[token_address] = meta
        return meta

    async def _get_token_prices(self, token_addresses: list[str]) -> dict[str, float]:
        """Batch-fetch USD prices from CoinGecko for a list of token addresses."""
        if not token_addresses:
            return {}

        # Use cached values where available
        result: dict[str, float] = {}
        missing: list[str] = []
        for addr in token_addresses:
            cached = self._price_cache.get(addr)
            if cached is not None:
                result[addr] = cached
            else:
                missing.append(addr)

        if not missing:
            return result

        platform = self.config.coingecko_platform
        joined   = ",".join(missing)
        url      = (
            f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
            f"?contract_addresses={joined}&vs_currencies=usd"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            for addr in missing:
                price = data.get(addr.lower(), {}).get("usd", 0.0)
                self._price_cache.set(addr, price)
                result[addr] = price
        except Exception as exc:
            logger.warning("[%s] CoinGecko price fetch failed: %s", self.chain_name, exc)
            for addr in missing:
                result[addr] = 0.0

        return result

    async def _get_eth_price(self) -> float:
        cached = self._eth_price_cache.get("eth")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
                )
                resp.raise_for_status()
                price = resp.json()["ethereum"]["usd"]
            self._eth_price_cache.set("eth", price)
            return price
        except Exception:
            return 0.0


# Backwards-compatible alias (existing code that imports ChainScanner still works)
ChainScanner = EvmChainScanner


# ── MultiChainTracker ─────────────────────────────────────────────────────────

class MultiChainTracker:
    """
    Orchestrates one scanner per configured chain.

    Each chain runs in its own asyncio Task at its own poll interval.
    Chains whose RPC URL env var is empty are skipped with a log warning.

    Scanner dispatch
    ----------------
    chain_type == "evm"    → EvmChainScanner  (web3.py, eth_getLogs)
    chain_type == "solana" → SolanaScanner    (httpx JSON-RPC, getSignaturesForAddress)

    Usage
    -----
        tracker = MultiChainTracker()
        await tracker.start()   # runs forever; call from lifespan task
    """

    def __init__(self) -> None:
        self.scanners: dict[str, BaseChainScanner] = {}

    def _build_scanners(self) -> None:
        # Import SolanaScanner here to avoid circular imports and keep the
        # web3 import in whale_tracker.py isolated from solana_scanner.py
        from api.services.solana_scanner import SolanaScanner  # noqa: PLC0415

        for chain_name, config in CHAINS.items():
            rpc_url = settings.get_rpc_url(chain_name)
            if not rpc_url:
                logger.warning(
                    "Chain '%s' skipped — set %s in .env to enable it.",
                    chain_name, config.rpc_url_env
                )
                continue

            if config.chain_type == "solana":
                scanner: BaseChainScanner = SolanaScanner(chain_name, config, rpc_url)
            else:
                scanner = EvmChainScanner(chain_name, config, rpc_url)

            self.scanners[chain_name] = scanner
            logger.info(
                "Chain '%s' registered (%s scanner, poll_interval=%ds)",
                chain_name, config.chain_type, config.poll_interval
            )

    async def _health_check(self) -> None:
        """Log RPC health for every configured scanner at startup."""
        checks = {
            name: asyncio.create_task(scanner.is_healthy())
            for name, scanner in self.scanners.items()
        }
        for name, task in checks.items():
            healthy = await task
            status = "OK" if healthy else "UNREACHABLE"
            logger.info("  [%s] RPC health: %s", name, status)

    async def start(self) -> None:
        """Build scanners, verify health, then launch per-chain loops."""
        self._build_scanners()

        if not self.scanners:
            logger.error("No chains configured — set at least one RPC URL in .env.")
            return

        logger.info("Running RPC health checks…")
        await self._health_check()

        # Launch one independent loop per chain
        tasks = [
            asyncio.create_task(
                self._chain_loop(chain_name),
                name=f"scanner_{chain_name}"
            )
            for chain_name in self.scanners
        ]
        await asyncio.gather(*tasks)

    async def _chain_loop(self, chain_name: str) -> None:
        """
        Infinite loop for one chain.

        On each tick:
        - ask the RPC for the latest block/slot
        - compute new blocks since last tick (capped at MAX_BACKFILL)
        - call scanner.scan_range(from, latest) — each scanner type handles
          the range in the most efficient way for its protocol
        - sleep for poll_interval

        Solana uses a larger MAX_BACKFILL (150 slots ≈ 60s worth of slots at
        2.5 slots/s) vs EVM chains (20 blocks).
        """
        scanner = self.scanners[chain_name]
        config  = CHAINS[chain_name]

        last_block: Optional[int] = None
        backoff = config.poll_interval

        # Solana produces ~2.5 slots/s vs ~0.08 blocks/s for Ethereum
        # Cap backfill to ~60s worth to avoid huge catch-up scans
        MAX_BACKFILL = 150 if config.chain_type == "solana" else 20
        MAX_BACKOFF  = 300  # never wait more than 5 minutes after errors

        logger.info("[%s] polling loop started (interval=%ds)", chain_name, config.poll_interval)

        while True:
            try:
                latest = await scanner.get_latest_block()

                if last_block is None:
                    last_block = latest
                    logger.info("[%s] starting from block %d", chain_name, latest)
                elif latest > last_block:
                    from_block = max(last_block + 1, latest - MAX_BACKFILL)
                    missed     = latest - last_block - 1
                    if missed > MAX_BACKFILL:
                        logger.warning(
                            "[%s] missed %d blocks; backfilling last %d only",
                            chain_name, missed, MAX_BACKFILL
                        )

                    await scanner.scan_range(from_block, latest)
                    last_block = latest

                # Successful tick — reset backoff to normal
                backoff = config.poll_interval

            except asyncio.CancelledError:
                logger.info("[%s] loop cancelled.", chain_name)
                return

            except Exception as exc:
                err = str(exc)

                if "403" in err or "Forbidden" in err:
                    logger.error(
                        "[%s] 403 Forbidden — RPC plan doesn't support this chain.\n"
                        "  Fix options:\n"
                        "    1. Check your provider dashboard (alchemy.com / helius.dev)\n"
                        "    2. Use a public RPC: set %s=<full_rpc_url> in .env\n"
                        "    3. Comment out the chain in config/chains.py to disable it\n"
                        "  Scanner stopped for this session.",
                        chain_name, config.rpc_url_env
                    )
                    return  # stop this loop; other chains keep running

                elif "429" in err or "Too Many Requests" in err:
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    logger.warning(
                        "[%s] Rate limited (429) — backing off to %ds.",
                        chain_name, backoff
                    )

                else:
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    logger.error("[%s] loop error (backing off to %ds): %s", chain_name, backoff, exc)

            await asyncio.sleep(backoff)
