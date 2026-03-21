import asyncio
import logging
import uuid
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import WalletFundingEvent
from config.chains import CHAINS
from config.settings import settings

logger = logging.getLogger(__name__)

async def backfill_wallet_funding(db: AsyncSession, wallet_address: str, chain: str) -> int:
    """
    Backfill historical native transfers for a newly tracked wallet.
    Uses Alchemy's alchemy_getAssetTransfers for EVM chains.
    """
    wallet_address = wallet_address.lower()
    
    if chain not in CHAINS:
        return 0
    config = CHAINS[chain]
    
    # We only support EVM backfill via Alchemy for now
    if config.chain_type != "evm":
        logger.info("[backfiller] Skipping non-EVM chain %s", chain)
        return 0

    rpc_url = settings.get_rpc_url(chain)
    if not rpc_url or "alchemy" not in rpc_url:
        logger.warning("[backfiller] Requires Alchemy RPC URL for %s", chain)
        return 0

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": "0x0",
            "toBlock": "latest",
            "toAddress": wallet_address,
            "category": ["external"],  # native ETH transfers
            "withMetadata": False,
            "excludeZeroValue": True,
            "maxCount": "0x3e8" # 1000 txs max backfill
        }]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            if "error" in data:
                logger.error("[backfiller] Alchemy API error: %s", data["error"])
                return 0
                
            transfers = data.get("result", {}).get("transfers", [])
            
            count = 0
            for tx in transfers:
                tx_hash = tx.get("hash")
                from_addr = (tx.get("from") or "").lower()
                amount_hex = tx.get("value")
                
                if not tx_hash or not from_addr or amount_hex in [None, "0x0", 0]:
                    continue
                
                # Handling amounts based on Alchemy response type
                try:
                    amount = float(amount_hex) if not isinstance(amount_hex, str) else float(int(amount_hex, 16) / 10**18) if amount_hex.startswith('0x') else float(amount_hex)
                except Exception:
                    continue

                exists = await db.scalar(
                    select(WalletFundingEvent.id)
                    .where(WalletFundingEvent.tx_hash == tx_hash, WalletFundingEvent.chain == chain)
                    .limit(1)
                )
                if not exists:
                    event = WalletFundingEvent(
                        id=str(uuid.uuid4()),
                        chain=chain,
                        from_address=from_addr,
                        to_address=wallet_address,
                        token_address=None,
                        amount=amount,
                        tx_hash=tx_hash
                    )
                    db.add(event)
                    count += 1
            
            if count > 0:
                await db.commit()
                logger.info("[backfiller] Inserted %d funding events for %s on %s", count, wallet_address, chain)
            return count

    except Exception as exc:
        logger.error("[backfiller] Error fetching transfers via alchemy for %s: %s", wallet_address, exc)
        return 0
