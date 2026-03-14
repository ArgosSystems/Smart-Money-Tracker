"""
data/seed_smart_labels.py
--------------------------
Seeds the smart_labels table with 80 well-known free-tier addresses.

Free tier (80 labels): major exchanges, founders, foundations, DAOs,
well-known VCs, bridges, and MEV bots — all publicly documented.

Pro labels are added separately via:
    python admin/add_pro_label.py <address> <chain> <name> <entity_type>

Usage:
    python data/seed_smart_labels.py
"""

import asyncio
import logging

from sqlalchemy import select

from api.models import AsyncSessionLocal, SmartLabel, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_smart_labels")

FREE_LABELS = [
    # ── Binance ─────────────────────────────────────────────────────────────
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "chain": "ethereum", "name": "Binance 14",        "entity_type": "exchange"},
    {"address": "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "chain": "ethereum", "name": "Binance 15",        "entity_type": "exchange"},
    {"address": "0x5a52e96bacbae9fa86d52ee95c16c943715e215a", "chain": "ethereum", "name": "Binance 16",        "entity_type": "exchange"},
    {"address": "0x56eddb7aa87536c09ccc2793473599fd21a8b17f", "chain": "ethereum", "name": "Binance 17",        "entity_type": "exchange"},
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "chain": "ethereum", "name": "Binance 8",         "entity_type": "exchange"},
    {"address": "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be", "chain": "ethereum", "name": "Binance 1",         "entity_type": "exchange"},
    {"address": "0xd551234ae421e3bcba99a0da6d736074f22192ff", "chain": "ethereum", "name": "Binance 2",         "entity_type": "exchange"},
    {"address": "0x564286362092d8e7936f0549571a803b203aaced", "chain": "ethereum", "name": "Binance 3",         "entity_type": "exchange"},
    {"address": "0x0681d8db095565fe8a346fa0277bffde9c0edbbf", "chain": "ethereum", "name": "Binance 4",         "entity_type": "exchange"},
    {"address": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "chain": "bsc",      "name": "Binance Hot Wallet", "entity_type": "exchange"},

    # ── Coinbase ─────────────────────────────────────────────────────────────
    {"address": "0x71660c4005ba85c37ccec55d0c4493e66fe775d3", "chain": "ethereum", "name": "Coinbase 1",        "entity_type": "exchange"},
    {"address": "0x503828976d22510aad0201ac7ec88293211d23da", "chain": "ethereum", "name": "Coinbase 2",        "entity_type": "exchange"},
    {"address": "0xddf1c516cf87126c6c610b52fd8d609e67fb60aa", "chain": "ethereum", "name": "Coinbase 3",        "entity_type": "exchange"},
    {"address": "0x02466e547bfdab679fc49e96bbfc62b9747d997c", "chain": "ethereum", "name": "Coinbase 4",        "entity_type": "exchange"},
    {"address": "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43", "chain": "ethereum", "name": "Coinbase Prime",     "entity_type": "exchange"},

    # ── Kraken ───────────────────────────────────────────────────────────────
    {"address": "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0", "chain": "ethereum", "name": "Kraken 1",          "entity_type": "exchange"},
    {"address": "0x0a869d79a7052c7f1b55a8ebabeea323f4af28b6", "chain": "ethereum", "name": "Kraken 2",          "entity_type": "exchange"},
    {"address": "0xe853c56864a2ebe4576a807d26fdc4a0ada51919", "chain": "ethereum", "name": "Kraken 3",          "entity_type": "exchange"},

    # ── OKX ──────────────────────────────────────────────────────────────────
    {"address": "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b", "chain": "ethereum", "name": "OKX 1",             "entity_type": "exchange"},
    {"address": "0x5041ed759cb4bcc074e948408b17fa80fa61eeaf", "chain": "ethereum", "name": "OKX 2",             "entity_type": "exchange"},
    {"address": "0x98ec059dc3adfbdd63429454aeb0c990fba4a128", "chain": "ethereum", "name": "OKX 3",             "entity_type": "exchange"},

    # ── Bybit ─────────────────────────────────────────────────────────────────
    {"address": "0x1d9cebebde87cd9e698bf4ff435bea8baeea5b3e", "chain": "ethereum", "name": "Bybit 1",           "entity_type": "exchange"},
    {"address": "0xf89d7b9c864f589bbf53a82105107622b35eaa40", "chain": "ethereum", "name": "Bybit 2",           "entity_type": "exchange"},

    # ── Bitfinex ──────────────────────────────────────────────────────────────
    {"address": "0x77134cbc06cb00b66f4c7e623d5fdbf6777635ec", "chain": "ethereum", "name": "Bitfinex 1",        "entity_type": "exchange"},
    {"address": "0x742d35cc6634c0532925a3b844bc454e4438f44e", "chain": "ethereum", "name": "Bitfinex 2",        "entity_type": "exchange"},

    # ── Huobi / HTX ──────────────────────────────────────────────────────────
    {"address": "0xab5c66752a9e8167967685f1450532fb96d5d24f", "chain": "ethereum", "name": "HTX 1",             "entity_type": "exchange"},
    {"address": "0x6748f50f686bfbca6fe8ad62b22228b87f31ff2b", "chain": "ethereum", "name": "HTX 2",             "entity_type": "exchange"},

    # ── Founders & Public Figures ─────────────────────────────────────────────
    {"address": "0xd8da6bf26964af9d7eed9e03e53415d37aa96045", "chain": "ethereum", "name": "Vitalik Buterin",   "entity_type": "founder"},
    {"address": "0x220866b1a2219f40e72f5c628b65d54268ca3a9d", "chain": "ethereum", "name": "Justin Sun",        "entity_type": "founder"},

    # ── Foundations ───────────────────────────────────────────────────────────
    {"address": "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae", "chain": "ethereum", "name": "Ethereum Foundation 1", "entity_type": "foundation"},
    {"address": "0x8e1df0e48ee79c26fc4a73262fbe01211ebbddea", "chain": "ethereum", "name": "Ethereum Foundation 2", "entity_type": "foundation"},
    {"address": "0xb8901acb165ed027e32754e0ffe830802919727f", "chain": "ethereum", "name": "Ethereum Foundation 3", "entity_type": "foundation"},

    # ── DAOs / Treasuries ─────────────────────────────────────────────────────
    {"address": "0x1a9c8182c09f50c8318d769245bea52c32be35bc", "chain": "ethereum", "name": "Uniswap Treasury",  "entity_type": "dao"},
    {"address": "0x464c71f6c2f760dda6093dcb91c24c39e5d6e18c", "chain": "ethereum", "name": "Aave Treasury",     "entity_type": "dao"},
    {"address": "0x3e40d73eb977dc6a537af587d48316fee66e9c8c", "chain": "ethereum", "name": "Lido Treasury",     "entity_type": "dao"},
    {"address": "0xbe8e3e3618f7474c8ce9d1a488eecdf8c218bfcc", "chain": "ethereum", "name": "MakerDAO Treasury", "entity_type": "dao"},
    {"address": "0x0bb23ac8d60c5e6f0ef3d9b8d9b63a9f48c6e3ca", "chain": "ethereum", "name": "Compound Treasury", "entity_type": "dao"},
    {"address": "0x4b5ab61593a2401b1075b90c04cbcdd3f87ce011", "chain": "ethereum", "name": "Curve DAO Treasury", "entity_type": "dao"},

    # ── VCs / Trading Firms ───────────────────────────────────────────────────
    {"address": "0xf584f8728b874a6a5c7a8d4d387c9aae9172d621", "chain": "ethereum", "name": "Jump Trading",      "entity_type": "vc"},
    {"address": "0xbf2722b5133af0e8ceec60bd7fb14fcb7b7fccaa", "chain": "ethereum", "name": "Jump Trading 2",    "entity_type": "vc"},
    {"address": "0x00000000ae347930bd1e7b0f35588b92280f9e75", "chain": "ethereum", "name": "Wintermute",        "entity_type": "vc"},
    {"address": "0xdbf5e9c5206d0db70a90108bf836bc7084d0bfdd", "chain": "ethereum", "name": "Wintermute 2",      "entity_type": "vc"},
    {"address": "0xe92d1a43df510f82c66382592a047d288f85226f", "chain": "ethereum", "name": "Cumberland DRW",    "entity_type": "vc"},
    {"address": "0x2b5ad5c4795c026514f8317c7a215e218dccd6cf", "chain": "ethereum", "name": "Paradigm",          "entity_type": "vc"},
    {"address": "0xab7c8803962c0f2f5bbbe3fa8bf41cd82aa1923c", "chain": "ethereum", "name": "Alameda Research",  "entity_type": "vc"},

    # ── Bridges ───────────────────────────────────────────────────────────────
    {"address": "0x4ce162066d1ddc6239bc7a6ac4af7afcf5b0c7be", "chain": "ethereum", "name": "Arbitrum Bridge",  "entity_type": "bridge"},
    {"address": "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1", "chain": "ethereum", "name": "Optimism Bridge",  "entity_type": "bridge"},
    {"address": "0x3154cf16ccdb4c6d922629664174b904d80f2c35", "chain": "ethereum", "name": "Base Bridge",       "entity_type": "bridge"},
    {"address": "0x40ec5b33f54e0e8a33a975908c5ba1c14e5bbbdf", "chain": "ethereum", "name": "Polygon Bridge",   "entity_type": "bridge"},
    {"address": "0x737901bea3eeb88459df9ef1be8ff3ae1b42a2ba", "chain": "ethereum", "name": "Aztec Connect",    "entity_type": "bridge"},

    # ── Staking / Liquid Staking ──────────────────────────────────────────────
    {"address": "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "chain": "ethereum", "name": "Lido: stETH",      "entity_type": "staking"},
    {"address": "0x00000000219ab540356cbb839cbe05303d7705fa", "chain": "ethereum", "name": "ETH2 Deposit",     "entity_type": "staking"},
    {"address": "0xa2f987a546d4cd1c607ee8141276876c26b72bdf", "chain": "ethereum", "name": "Anchor Protocol",  "entity_type": "staking"},

    # ── DeFi Protocols ────────────────────────────────────────────────────────
    {"address": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "chain": "ethereum", "name": "Uniswap V2 Router", "entity_type": "defi"},
    {"address": "0xe592427a0aece92de3edee1f18e0157c05861564", "chain": "ethereum", "name": "Uniswap V3 Router", "entity_type": "defi"},
    {"address": "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f", "chain": "ethereum", "name": "SushiSwap Router",  "entity_type": "defi"},
    {"address": "0x881d40237659c251811cec9c364ef91dc08d300c", "chain": "ethereum", "name": "MetaMask Swap",     "entity_type": "defi"},
    {"address": "0x1111111254fb6c44bac0bed2854e76f90643097d", "chain": "ethereum", "name": "1inch V4",         "entity_type": "defi"},

    # ── Lending ───────────────────────────────────────────────────────────────
    {"address": "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b", "chain": "ethereum", "name": "Compound Comptroller", "entity_type": "lending"},
    {"address": "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9", "chain": "ethereum", "name": "Aave V2 Market",   "entity_type": "lending"},
    {"address": "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2", "chain": "ethereum", "name": "Aave V3 Market",   "entity_type": "lending"},

    # ── MEV Bots ──────────────────────────────────────────────────────────────
    {"address": "0x000000000000084e91743124a982076c59f10084", "chain": "ethereum", "name": "MEV Bot (Jaredfromsubway)", "entity_type": "mev"},
    {"address": "0x0000000000007f150bd6f54c40a34d7c3d5e9f56", "chain": "ethereum", "name": "MEV Bot",           "entity_type": "mev"},
    {"address": "0xba12222222228d8ba445958a75a0704d566bf2c8", "chain": "ethereum", "name": "MEV Bot (Flashbots 1)", "entity_type": "mev"},
    {"address": "0x000000000000006f6502b7f2bbac8c30a3f67e9a", "chain": "ethereum", "name": "MEV Bot (Flashbots 2)", "entity_type": "mev"},
    {"address": "0x000000000000057ae4ad9d6575d5e5b6026a4b16", "chain": "ethereum", "name": "MEV Bot (Flashbots 3)", "entity_type": "mev"},
    {"address": "0xedc7ea0089e0ee25fde6c1417088bcebae28e67a", "chain": "ethereum", "name": "MEV Bot (Builder)", "entity_type": "mev"},

    # ── Custodians / Prime Brokers ────────────────────────────────────────────
    {"address": "0xc6cde7c39eb2f0f0095f41570af89efc2c1ea828", "chain": "ethereum", "name": "BitGo",            "entity_type": "custodian"},
    {"address": "0x2faf487a4414fe77e2327f0bf4ae2a264a776ad2", "chain": "ethereum", "name": "FTX (Defunct)",    "entity_type": "exchange"},

    # ── BSC ───────────────────────────────────────────────────────────────────
    {"address": "0x10ed43c718714eb63d5aa57b78b54704e256024e", "chain": "bsc",      "name": "PancakeSwap Router V2", "entity_type": "defi"},
    {"address": "0x05ff2b0db69458a0750badebc4f9e13add608c7f", "chain": "bsc",      "name": "PancakeSwap Router V1", "entity_type": "defi"},
    {"address": "0x0000000000000000000000000000000000001004", "chain": "bsc",      "name": "BSC Validator Set",     "entity_type": "staking"},

    # ── Polygon ───────────────────────────────────────────────────────────────
    {"address": "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff", "chain": "polygon",  "name": "QuickSwap Router",      "entity_type": "defi"},
    {"address": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063", "chain": "polygon",  "name": "DAI on Polygon",        "entity_type": "defi"},

    # ── Arbitrum ─────────────────────────────────────────────────────────────
    {"address": "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506", "chain": "arbitrum", "name": "SushiSwap ARB Router",  "entity_type": "defi"},
    {"address": "0xe592427a0aece92de3edee1f18e0157c05861564", "chain": "arbitrum", "name": "Uniswap V3 ARB Router", "entity_type": "defi"},

    # ── Binance (extra) ───────────────────────────────────────────────────────
    {"address": "0x4976a4a02f38326660d17bf34b431dc6e2eb2327", "chain": "ethereum", "name": "Binance 9",             "entity_type": "exchange"},
    {"address": "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "chain": "ethereum", "name": "Binance 10 (Cold)",     "entity_type": "exchange"},
    {"address": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "chain": "ethereum", "name": "Binance BSC (ETH side)", "entity_type": "exchange"},
    {"address": "0xe0f0cfde7ee664943906f17f7f14342a76a9e42c", "chain": "ethereum", "name": "Gate.io",               "entity_type": "exchange"},
]

# Exactly 80 entries above — verified
assert len(FREE_LABELS) == 80, f"Expected 80 free labels, got {len(FREE_LABELS)}"


async def seed() -> None:
    await init_db()
    inserted = 0
    skipped = 0

    async with AsyncSessionLocal() as db:
        for lbl in FREE_LABELS:
            addr = lbl["address"].lower()
            chain = lbl["chain"]

            existing = await db.scalar(
                select(SmartLabel).where(
                    SmartLabel.address == addr,
                    SmartLabel.chain == chain,
                )
            )

            if existing:
                skipped += 1
            else:
                db.add(SmartLabel(
                    address=addr,
                    chain=chain,
                    name=lbl["name"],
                    entity_type=lbl["entity_type"],
                    tier="free",
                ))
                inserted += 1

        if inserted > 0:
            await db.commit()

    logger.info("Seeded %d new free-tier labels (%d already existed).", inserted, skipped)


if __name__ == "__main__":
    asyncio.run(seed())
