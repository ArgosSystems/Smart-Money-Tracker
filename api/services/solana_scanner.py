"""
api/services/solana_scanner.py
--------------------------------
Solana whale tracking scanner.

Architecture overview
---------------------
Solana is fundamentally different from EVM chains:

  EVM                              Solana
  ───────────────────────────────  ────────────────────────────────────────
  Block-based polling              Slot-based (400ms slots, 172k/day)
  eth_getLogs covers whole block   No equivalent — must query per wallet
  ERC-20 Transfer events (logs)    SPL token balance diffs (pre/post meta)
  0x-prefixed hex addresses        base58 public keys (32-44 chars)
  Single token per tx (usually)    Multi-instruction: N transfers per tx

Scanning strategy
-----------------
scan_range(from_slot, to_slot) overrides the default BaseChainScanner
implementation.  Instead of scanning individual slots (which would be
2.5 calls/s per wallet at free-tier), it calls:

    getSignaturesForAddress(wallet, {minContextSlot: from_slot, limit: 100})

once per tracked wallet per poll cycle (~4s), returning all signatures in
the slot range.  Each signature is then fetched with getTransaction() using
jsonParsed encoding.

Token transfer detection
------------------------
Rather than parsing raw instruction data, we diff preTokenBalances vs
postTokenBalances from transaction meta.  This is:
  - Chain-agnostic to SPL vs Token-2022
  - Handles multi-hop swaps (A→B→C emits one diff per owner)
  - Gives us the wallet owner directly (not the ATA address)

Direction classification
------------------------
  delta > 0  (balance increased)  → BUY / RECEIVE
  delta < 0  (balance decreased)  → SELL / SEND

For native SOL we diff preBalances vs postBalances (in lamports, ÷1e9 for SOL),
adjusting for the transaction fee on the fee payer (index 0).

RPC cost at steady state
------------------------
Per poll cycle (4s):  1 getSignaturesForAddress + N getTransaction calls
  where N = number of signatures returned (typically 0-5 for a tracked wallet)
At 10 tracked wallets: ~10 + 0-50 calls per 4s = 2.5-15 req/s
Helius free tier: 100 req/s — comfortable headroom.

Fallback on rate-limit
-----------------------
If getTransaction returns a 429, the outer _chain_loop backs off exponentially
(up to 5 min) — the same circuit-breaker used for EVM chains.

Whitelist approach for token prices
------------------------------------
CoinGecko is queried per mint address.  Tokens not listed on CoinGecko return
price=0.0 and are silently skipped (no alert generated).  This naturally
filters low-liquidity memecoins — only tokens with CoinGecko price data that
push $10k+ in a single tx will generate alerts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

import httpx

from api.models import AsyncSessionLocal, TokenActivity, TrackedWallet, WhaleAlert
from api.services.broadcaster import alert_broadcaster
from api.services.whale_tracker import BaseChainScanner, _PriceCache
from config.chains import ChainConfig
from config.settings import settings

logger = logging.getLogger(__name__)


# ── Shared SOL price cache (module-level, like _SHARED_ETH_PRICE_CACHE) ───────
_SHARED_SOL_PRICE_CACHE = _PriceCache()

# ── Known SPL mint → symbol mapping (avoids CoinGecko calls for majors) ───────
_KNOWN_MINTS: dict[str, str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "So11111111111111111111111111111111111111112":    "wSOL",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN":  "JUP",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So":  "mSOL",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",   # Wormhole ETH
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",   # Wormhole BTC
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",  # Wormhole WBTC
}


# ── SolanaScanner ─────────────────────────────────────────────────────────────

class SolanaScanner(BaseChainScanner):
    """
    Scans Solana mainnet for SPL token and native SOL whale transfers.

    Requires a Helius RPC URL (or any Solana JSON-RPC 2.0 compatible endpoint).
    Set HELIUS_RPC_URL or HELIUS_API_KEY in .env.
    """

    SPL_TOKEN_PROGRAM   = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    TOKEN_2022_PROGRAM  = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

    def __init__(self, chain_name: str, config: ChainConfig, rpc_url: str) -> None:
        super().__init__(chain_name, config, rpc_url)
        self._token_meta_cache: dict[str, str] = {}   # mint → symbol
        self._price_cache = _PriceCache()              # per-mint USD price
        self._sol_price_cache = _SHARED_SOL_PRICE_CACHE

    # ── JSON-RPC 2.0 transport ────────────────────────────────────────────────

    async def _rpc(self, method: str, params: list) -> Any:
        """Send a single JSON-RPC 2.0 request to the Solana RPC endpoint."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self._rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            code = data["error"].get("code", "?")
            msg  = data["error"].get("message", str(data["error"]))
            raise RuntimeError(f"Solana RPC [{method}] error {code}: {msg}")
        return data.get("result")

    # ── Public interface ──────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        try:
            result = await asyncio.wait_for(self._rpc("getHealth", []), timeout=5.0)
            return result == "ok"
        except Exception:
            return False

    async def get_latest_block(self) -> int:
        """Return the current confirmed slot number."""
        return await self._rpc("getSlot", [{"commitment": "confirmed"}])

    async def scan_block(self, block_number: int) -> list[WhaleAlert]:
        """Single-slot scan — delegates to scan_range for efficiency."""
        return await self.scan_range(block_number, block_number)

    async def scan_range(self, from_slot: int, to_slot: int) -> list[WhaleAlert]:
        """
        Scan all Solana slots in [from_slot, to_slot] for all tracked wallets.

        Uses getSignaturesForAddress once per wallet (covers the entire range),
        then fetches full transaction details only for signatures in range.
        Concurrency is capped at 5 wallets simultaneously to stay within
        Helius free-tier rate limits.
        """
        async with AsyncSessionLocal() as db:
            wallets = await self._load_wallets(db)
            if not wallets:
                return []

            sol_price = await self._get_sol_price()

            # Process up to 5 wallets concurrently
            sem = asyncio.Semaphore(5)

            async def _scan(w: TrackedWallet) -> list[WhaleAlert]:
                async with sem:
                    return await self._scan_wallet(w, from_slot, to_slot, sol_price, db)

            results = await asyncio.gather(
                *[_scan(w) for w in wallets],
                return_exceptions=True,
            )

            new_alerts: list[WhaleAlert] = []
            for r in results:
                if isinstance(r, list):
                    new_alerts.extend(r)

            if new_alerts:
                await db.commit()
                logger.info(
                    "[solana] slots %d–%d → %d new whale alert(s)",
                    from_slot, to_slot, len(new_alerts)
                )
                # Broadcast to all connected WebSocket clients
                for alert in new_alerts:
                    await alert_broadcaster.publish({
                        "id":            alert.id,
                        "chain":         alert.chain,
                        "tx_hash":       alert.tx_hash,
                        "from_address":  alert.from_address,
                        "to_address":    alert.to_address,
                        "token_symbol":  alert.token_symbol,
                        "token_address": alert.token_address,
                        "amount_token":  alert.amount_token,
                        "amount_usd":    alert.amount_usd,
                        "direction":     alert.direction,
                        "block_number":  alert.block_number,
                        "detected_at":   alert.detected_at.isoformat() if alert.detected_at else None,
                    })

        return new_alerts

    # ── Per-wallet scanning ───────────────────────────────────────────────────

    async def _scan_wallet(
        self,
        wallet: TrackedWallet,
        from_slot: int,
        to_slot: int,
        sol_price: float,
        db,
    ) -> list[WhaleAlert]:
        """Fetch all signatures for a wallet in the slot range and parse each tx."""
        try:
            sigs_result = await self._rpc("getSignaturesForAddress", [
                wallet.address,
                {
                    "minContextSlot": from_slot,
                    "limit": 100,
                    "commitment": "confirmed",
                },
            ])
        except Exception as exc:
            logger.warning(
                "[solana] getSignaturesForAddress %s…: %s",
                wallet.address[:8], exc
            )
            return []

        if not sigs_result:
            return []

        # Keep only signatures whose slot is within our range
        sigs_in_range = [
            s for s in sigs_result
            if s.get("slot", 0) <= to_slot and not s.get("err")
        ]

        alerts: list[WhaleAlert] = []
        for sig_info in sigs_in_range:
            sig  = sig_info["signature"]
            slot = sig_info.get("slot", to_slot)

            try:
                tx = await self._rpc("getTransaction", [
                    sig,
                    {
                        "encoding":                      "jsonParsed",
                        "commitment":                    "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    },
                ])
            except Exception as exc:
                logger.debug("[solana] getTransaction %s…: %s", sig[:12], exc)
                continue

            if not tx:
                continue  # slot was skipped (leader failure) — not an error

            tx_alerts = await self._parse_tx(tx, wallet, sig, slot, sol_price, db)
            alerts.extend(tx_alerts)

        return alerts

    # ── Transaction parsing ───────────────────────────────────────────────────

    async def _parse_tx(
        self,
        tx: dict,
        wallet: TrackedWallet,
        sig: str,
        slot: int,
        sol_price: float,
        db,
    ) -> list[WhaleAlert]:
        """
        Extract whale alerts from a parsed Solana transaction.

        SPL token transfers: diff preTokenBalances vs postTokenBalances.
        Native SOL transfers: diff preBalances vs postBalances (lamports).

        Using balance diffs instead of instruction parsing:
        - Works transparently for SPL Token and Token-2022 programs
        - Resolves ATA → owner automatically (owner field in balance entry)
        - Correctly handles multi-hop swaps (one diff per owner per mint)
        """
        alerts: list[WhaleAlert] = []
        meta = tx.get("meta") or {}

        # Build account key list (jsonParsed may use dicts with pubkey field)
        msg_data  = tx.get("transaction", {}).get("message", {})
        raw_keys  = msg_data.get("accountKeys", [])
        acct_keys = [
            (k if isinstance(k, str) else k.get("pubkey", ""))
            for k in raw_keys
        ]

        # ── SPL token balance diffs ───────────────────────────────────────────
        pre_tok  = {
            (b["owner"], b["mint"]): b.get("uiTokenAmount", {})
            for b in meta.get("preTokenBalances",  [])
            if "owner" in b
        }
        post_tok = {
            (b["owner"], b["mint"]): b.get("uiTokenAmount", {})
            for b in meta.get("postTokenBalances", [])
            if "owner" in b
        }
        all_keys = set(pre_tok) | set(post_tok)

        for (owner, mint) in all_keys:
            if owner != wallet.address:
                continue

            pre_amt  = float(pre_tok.get((owner, mint),  {}).get("uiAmount") or 0)
            post_amt = float(post_tok.get((owner, mint), {}).get("uiAmount") or 0)
            delta    = post_amt - pre_amt   # positive = received, negative = sent

            if delta == 0:
                continue

            amount = abs(delta)
            price  = await self._get_token_price(mint)
            if price == 0.0:
                continue

            usd_value = amount * price
            if usd_value < settings.whale_threshold_usd:
                continue

            # Duplicate check: key on sig + mint to allow multiple tokens per tx
            alert_key = f"{sig}:{mint}"
            if await self._alert_exists(db, alert_key):
                continue

            symbol    = await self._get_token_symbol(mint)
            direction = "BUY" if delta > 0 else "SELL"
            from_addr, to_addr = _extract_parties(acct_keys, owner, delta > 0)

            alert = WhaleAlert(
                wallet_id    = wallet.id,
                chain        = self.chain_name,
                tx_hash      = alert_key,          # sig:mint — unique per token per tx
                from_address = from_addr[:44],
                to_address   = to_addr[:44],
                token_address= mint,
                token_symbol = symbol,
                amount_token = amount,
                amount_usd   = usd_value,
                direction    = direction,
                block_number = slot,
                raw_data     = json.dumps({"sig": sig, "slot": slot}),
            )
            db.add(alert)
            await self._upsert_token_activity(db, mint, symbol, direction, usd_value)

            logger.info(
                "[solana] %s %s %.4f ($%.0f) sig=%s…",
                direction, symbol, amount, usd_value, sig[:12]
            )
            alerts.append(alert)

        # ── Native SOL balance diffs ──────────────────────────────────────────
        if sol_price > 0:
            pre_sol  = meta.get("preBalances",  [])
            post_sol = meta.get("postBalances", [])
            fee      = meta.get("fee", 0)

            for idx, key in enumerate(acct_keys):
                if key != wallet.address:
                    continue
                if idx >= len(pre_sol) or idx >= len(post_sol):
                    continue

                delta_lam = post_sol[idx] - pre_sol[idx]
                # The fee payer (index 0) pays the tx fee; add it back so we
                # measure the "economic" transfer, not the fee-adjusted change.
                if idx == 0:
                    delta_lam += fee

                delta_sol = delta_lam / 1e9  # lamports → SOL

                # Skip dust / wSOL account rent deposits (< 0.01 SOL)
                if abs(delta_sol) < 0.01:
                    continue

                usd_value = abs(delta_sol) * sol_price
                if usd_value < settings.whale_threshold_usd:
                    continue

                sol_key = f"{sig}:SOL"
                if await self._alert_exists(db, sol_key):
                    continue

                direction = "BUY" if delta_sol > 0 else "SEND"
                if delta_sol < 0:
                    from_addr, to_addr = wallet.address, (acct_keys[1] if len(acct_keys) > 1 else wallet.address)
                else:
                    from_addr, to_addr = (acct_keys[0] if acct_keys else wallet.address), wallet.address

                alert = WhaleAlert(
                    wallet_id    = wallet.id,
                    chain        = self.chain_name,
                    tx_hash      = sol_key,
                    from_address = from_addr[:44],
                    to_address   = to_addr[:44],
                    token_address= None,
                    token_symbol = "SOL",
                    amount_token = abs(delta_sol),
                    amount_usd   = usd_value,
                    direction    = direction,
                    block_number = slot,
                    raw_data     = json.dumps({"sig": sig, "slot": slot, "type": "native_sol"}),
                )
                db.add(alert)

                logger.info(
                    "[solana] %s SOL %.4f ($%.0f) sig=%s…",
                    direction, abs(delta_sol), usd_value, sig[:12]
                )
                alerts.append(alert)

        return alerts

    # ── Price & metadata helpers ──────────────────────────────────────────────

    async def _get_sol_price(self) -> float:
        cached = self._sol_price_cache.get("sol")
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
                )
                resp.raise_for_status()
                price = float(resp.json()["solana"]["usd"])
            self._sol_price_cache.set("sol", price)
            return price
        except Exception as exc:
            logger.warning("[solana] SOL price fetch failed: %s", exc)
            return 0.0

    async def _get_token_price(self, mint_address: str) -> float:
        """Fetch USD price for an SPL token mint via CoinGecko."""
        cached = self._price_cache.get(mint_address)
        if cached is not None:
            return cached

        # wSOL tracks SOL price
        if mint_address == self.config.weth_address:
            return await self._get_sol_price()

        url = (
            f"https://api.coingecko.com/api/v3/simple/token_price/solana"
            f"?contract_addresses={mint_address}&vs_currencies=usd"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            price = float(data.get(mint_address, {}).get("usd", 0.0))
            self._price_cache.set(mint_address, price)
            return price
        except Exception as exc:
            logger.debug("[solana] price fetch failed for %s…: %s", mint_address[:8], exc)
            self._price_cache.set(mint_address, 0.0)
            return 0.0

    async def _get_token_symbol(self, mint_address: str) -> str:
        """Return symbol for a mint address. Falls back to first 8 chars of mint."""
        if mint_address in self._token_meta_cache:
            return self._token_meta_cache[mint_address]

        if mint_address in _KNOWN_MINTS:
            sym = _KNOWN_MINTS[mint_address]
            self._token_meta_cache[mint_address] = sym
            return sym

        # Attempt Helius DAS getAsset for on-chain metadata (best-effort)
        try:
            result = await self._rpc("getAsset", [mint_address])
            if result:
                sym = (
                    result.get("content", {})
                          .get("metadata", {})
                          .get("symbol", "")
                    or result.get("token_info", {}).get("symbol", "")
                )
                if sym:
                    self._token_meta_cache[mint_address] = sym.upper()
                    return sym.upper()
        except Exception:
            pass  # getAsset only works on Helius; gracefully degrade

        # Fallback: abbreviated mint address
        sym = mint_address[:8]
        self._token_meta_cache[mint_address] = sym
        return sym


# ── Module-level helper ───────────────────────────────────────────────────────

def _extract_parties(
    acct_keys: list[str],
    owner: str,
    is_receive: bool,
) -> tuple[str, str]:
    """
    Heuristically determine from/to addresses for a Solana transfer.

    For receives (delta > 0): the sender is the first non-owner, non-program key.
    For sends   (delta < 0): the recipient is the first non-owner, non-program key.
    Falls back to the owner address if no counterparty can be identified.
    """
    _programs = {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "11111111111111111111111111111111",
        "ComputeBudget111111111111111111111111111111",
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bRS",
    }
    counterparty = next(
        (k for k in acct_keys if k and k != owner and k not in _programs),
        owner,
    )
    if is_receive:
        return counterparty, owner
    return owner, counterparty
