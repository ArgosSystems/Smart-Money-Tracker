# Architecture Overview

This document describes the architecture of Smart Money Tracker, including the design decisions, components, and data flow.

---

## 🏗️ High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Interface                                  │
├─────────────────────────────────┬───────────────────────────────────────────┤
│         Discord Bot             │            Telegram Bot                    │
│      (discord.py 2.3.2)         │      (python-telegram-bot 21.0)           │
│  • Chain-specific commands      │  • Multi-chain support                    │
│  • Chain emojis & indicators    │  • Chain filtering                        │
└─────────────────┬───────────────┴───────────────────┬───────────────────────┘
                  │                               │
                  │         HTTP REST API         │
                  └───────────────┬───────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                          FastAPI Backend (Port 8000)                        │
│  • Multi-chain Wallet Management  • Alert History  • Token Activity        │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
    ┌─────────▼────────┐  ┌───────▼───────┐  ┌───────▼────────┐
    │ MultiChainTracker│  │    SQLite     │  │   CoinGecko    │
    │  • ChainScanner  │  │     DB        │  │   Price API    │
    │    per chain     │  │  (per-chain   │  │  (with TTL     │
    │  • Concurrent    │  │   storage)    │  │   cache)       │
    │    polling       │  └───────────────┘  └────────────────┘
    └─────────┬────────┘
              │
    ┌─────────┴────────────────────────────────────────────┐
    │                   Multi-Chain RPC                     │
    │                                                      │
┌───▼────────────┐  ┌───────────────┐  ┌──────────────────▼───┐
│  ⬛ Ethereum   │  │  🔵 Base      │  │  🔶 Arbitrum         │
│  Chain ID: 1   │  │  Chain ID: 8453│  │  Chain ID: 42161    │
│  (12s blocks)  │  │  (2s blocks)  │  │  (0.25s blocks)      │
│  Alchemy RPC   │  │  Alchemy RPC  │  │  Alchemy RPC         │
└────────────────┘  └───────────────┘  └──────────────────────┘
```

---

## 🧩 Core Components

### 1. API Layer (`api/`)

The FastAPI backend serves as the central hub for all operations.

#### Routers (`api/routers/`)

| Router | Purpose | Endpoints |
|--------|---------|-----------|
| `whales.py` | Wallet management | `POST /wallets/track`, `DELETE /wallets/{addr}`, `GET /wallets`, `GET /tokens/trending` |
| `alerts.py` | Alert retrieval | `GET /alerts`, `GET /alerts/token/{token}` |

#### Services (`api/services/`)

| Service | Purpose |
|---------|---------|
| `whale_tracker.py` | Multi-chain blockchain scanning (ChainScanner + MultiChainTracker) |
| `price_alerts.py` | Token price fetching via CoinGecko with TTL cache |

### 2. Multi-Chain Tracker (`api/services/whale_tracker.py`)

The core scanning engine has been refactored into a modular, multi-chain architecture:

#### `_PriceCache`
- TTL-based price cache (60 seconds)
- Reduces CoinGecko API calls
- Thread-safe price lookups

#### `ChainScanner`
- One Web3 connection per chain
- `__init__(chain_config)` — Initialize with chain-specific config
- `scan_block(block_number)` — Fetch all ERC-20 transfers in one `eth_getLogs` call
- `get_latest_block()` — Get current block number
- Client-side filtering for efficiency

#### `MultiChainTracker`
- Initializes scanners for all configured chains
- Runs them concurrently using `asyncio.gather`
- Different polling intervals per chain based on block time
- Detects missed blocks and batches them (cap: 20, concurrent: 5)

### 3. Bot Layer (`bots/`)

The bot implementations communicate exclusively with the API.

#### Discord Bot (`bots/discord_bot/`)

- Uses `discord.py` 2.3.2 with slash commands
- Chain parameter with autocomplete for all wallet commands
- Chain emojis for visual identification (⬛ 🔵 🔶)
- Embeds with clickable explorer links

#### Telegram Bot (`bots/telegram_bot/`)

- Uses `python-telegram-bot` 21.0
- Command handlers with MarkdownV2 formatting
- Chain filtering support
- Async handlers throughout

### 4. Configuration Layer (`config/`)

| File | Purpose |
|------|---------|
| `settings.py` | Pydantic-based settings management |
| `chains.py` | Chain registry with metadata (chain ID, block time, explorer, etc.) |

#### Chain Registry (`config/chains.py`)

```python
CHAINS = {
    "ethereum": {
        "rpc_url_env": "ALCHEMY_ETH",
        "chain_id": 1,
        "explorer": "etherscan.io",
        "native_token": "ETH",
        "color": "#627EEA",
        "block_time": 12,
        "emoji": "⬛"
    },
    "base": {
        "rpc_url_env": "ALCHEMY_BASE",
        "chain_id": 8453,
        "explorer": "basescan.org",
        "native_token": "ETH",
        "color": "#0052FF",
        "block_time": 2,
        "emoji": "🔵"
    },
    "arbitrum": {
        "rpc_url_env": "ALCHEMY_ARB",
        "chain_id": 42161,
        "explorer": "arbiscan.io",
        "native_token": "ETH",
        "color": "#28A0F0",
        "block_time": 0.25,
        "emoji": "🔶"
    }
}
```

---

## 🔄 Data Flow

### Tracking a Wallet (Multi-Chain)

```
User → Discord/Telegram → API POST /wallets/track?chain=base → Database → Wallet Added (with chain)
```

### Multi-Chain Whale Detection Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    MultiChainTracker Polling Loop                           │
│         (per-chain intervals: ETH=12s, Base=2s, Arb=1s)                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
           ┌────────────┐  ┌────────────┐  ┌────────────┐
           │  Ethereum  │  │    Base    │  │  Arbitrum  │
           │  Scanner   │  │  Scanner   │  │  Scanner   │
           │  (async)   │  │  (async)   │  │  (async)   │
           └──────┬─────┘  └──────┬─────┘  └──────┬─────┘
                  │               │               │
                  │               │               │
    ┌─────────────▼───────────────▼───────────────▼─────────────┐
    │                     Concurrent Scanning                    │
    │                (asyncio.gather per chain)                  │
    └─────────────────────────────┬─────────────────────────────┘
                                  │
                                  ▼
         ┌────────────────────────────────────────────────┐
         │  For each chain, scan recent blocks:           │
         │  1. Get latest block number                    │
         │  2. Detect missed blocks (batch up to 20)      │
         │  3. Fetch ERC-20 Transfer events (eth_getLogs) │
         │  4. Filter for tracked wallets                 │
         │  5. Get token prices (with cache)              │
         │  6. Calculate USD value                        │
         │  7. Check threshold (>= $10,000)               │
         └────────────────────────────────────────────────┘
                                  │
                                  ▼
         ┌────────────────────────────────────────────────┐
         │  Create WhaleAlert with chain attribution      │
         │  Update TokenActivity (per-chain stats)        │
         │  Commit to SQLite                              │
         │  Update last_checked_block per chain           │
         └────────────────────────────────────────────────┘
```

---

## 🗄️ Database Schema (Multi-Chain)

### Entity Relationship Diagram

```
┌───────────────────────┐       ┌───────────────────────┐
│    TrackedWallet      │       │      WhaleAlert       │
├───────────────────────┤       ├───────────────────────┤
│ id (PK)               │       │ id (PK)               │
│ address               │◄──────│ wallet_id (FK)        │
│ chain                 │       │ tx_hash (UNIQUE)      │
│ label                 │       │ chain                 │
│ is_active             │       │ from_address          │
│ added_at              │       │ to_address            │
│ last_checked_block    │       │ token_address         │
└───────────────────────┘       │ token_symbol          │
                                │ amount_token          │
  UNIQUE: (address, chain)      │ amount_usd            │
                                │ direction             │
                                │ block_number          │
                                │ detected_at           │
                                └───────────────────────┘

┌───────────────────────┐
│    TokenActivity      │
├───────────────────────┤
│ id (PK)               │
│ token_address         │
│ chain                 │
│ token_symbol          │
│ buy_count             │
│ sell_count            │
│ total_volume_usd      │
│ last_activity         │
└───────────────────────┘
```

### Indexes

- `tracked_wallets(address, chain)` — Unique composite index
- `whale_alerts.wallet_id` — Index for efficient wallet-alert joins
- `whale_alerts.chain` — Index for chain-filtered queries
- `whale_alerts.token_address` — Index for token-filtered queries

---

## 🔌 External Integrations

### Alchemy (Multi-Chain RPC)

| Chain | RPC URL Pattern | Rate Limits |
|-------|-----------------|-------------|
| Ethereum | `eth-mainnet.g.alchemy.com/v2/{key}` | 300M CU/month (free) |
| Base | `base-mainnet.g.alchemy.com/v2/{key}` | 300M CU/month (free) |
| Arbitrum | `arb-mainnet.g.alchemy.com/v2/{key}` | 300M CU/month (free) |

### CoinGecko (Price API)

- **Purpose**: Token price in USD
- **Usage**: Converting token amounts to USD value
- **Rate Limits**: ~10-50 calls/minute (free tier)
- **Optimization**: TTL cache (60s) reduces calls significantly

---

## 📊 Performance Optimizations

### 1. Batch Block Scanning

When the scanner detects it has fallen behind (e.g., during downtime), it batches blocks:

```
Missed 50 blocks?
→ Batch into groups of 20
→ Scan 5 groups concurrently
→ Process all transfers efficiently
```

### 2. Single eth_getLogs Call

Instead of querying per-wallet transfers, the scanner:

```
1. Build topic filter for Transfer event
2. Query ALL Transfer events in block range
3. Client-side filter for tracked wallets
```

### 3. Price Cache

```
Request price for token X
    │
    ├── Cache hit (< 60s old)? → Return cached price
    │
    └── Cache miss? → Fetch from CoinGecko → Cache for 60s
```

### 4. Per-Chain Polling Intervals

Optimized based on chain block time:

| Chain | Block Time | Poll Interval | Rationale |
|-------|------------|---------------|-----------|
| Ethereum | 12s | 12s | One block per scan |
| Base | 2s | 2s | One block per scan |
| Arbitrum | 0.25s | 1s | Batch 4 blocks per scan |

---

## 🔐 Security Architecture

### Secrets Management

```
┌─────────────────┐
│    .env file    │ (Never committed to Git)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Pydantic Settings│
│ (type-safe load) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Application    │
│  (runtime only) │
└─────────────────┘
```

### API Security Considerations

- **CORS**: Currently open (`*`) for development; restrict in production
- **Rate Limiting**: Not implemented; consider adding for production
- **Authentication**: Not implemented; consider API keys for production

---

## 📈 Scalability Considerations

### Current Architecture

1. **Single Process**: All components run in one Python process
2. **SQLite**: Suitable for moderate loads, not high write volumes
3. **Per-Chain Polling**: Concurrent async tasks per chain

### Scaling Path

| Component | Current | Scale To |
|-----------|---------|----------|
| API | Single uvicorn | Multiple workers / load balancer |
| Database | SQLite | PostgreSQL |
| Queue | None | Redis + Celery |
| Real-time | Polling | WebSocket / Webhooks |
| Chain Scanners | Single process | Distributed workers |

---

## 🧪 Testing Strategy

```
tests/
├── unit/
│   ├── test_chain_scanner.py
│   ├── test_multi_chain_tracker.py
│   └── test_price_cache.py
├── integration/
│   ├── test_api.py
│   └── test_whale_tracker.py
└── e2e/
    └── test_bot_flows.py
```

---

## 📦 Deployment Architecture

### Development

```
Single Terminal → python start.py
```

### Docker

```
docker-compose up -d
```

### Production (Recommended)

```
                    ┌─────────────┐
                    │   Nginx     │
                    │ (reverse    │
                    │  proxy)     │
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
    │   FastAPI   │ │   FastAPI   │ │   FastAPI   │
    │  (worker 1) │ │  (worker 2) │ │  (worker 3) │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │               │               │
           └───────────────┼───────────────┘
                           │
                    ┌──────▼──────┐
                    │ PostgreSQL  │
                    │   Database  │
                    └─────────────┘
```

---

## 🔧 Adding a New Chain

To add a new chain (e.g., Optimism):

1. **Add to `config/chains.py`**:
```python
"optimism": {
    "rpc_url_env": "ALCHEMY_OPTIMISM",
    "chain_id": 10,
    "explorer": "optimistic.etherscan.io",
    "native_token": "ETH",
    "color": "#FF0420",
    "block_time": 2,
    "emoji": "🔴"
}
```

2. **Add RPC URL to `.env`** (optional):
```
ALCHEMY_OPTIMISM=https://opt-mainnet.g.alchemy.com/v2/YOUR_KEY
```

3. **Restart the application** — The MultiChainTracker will automatically pick up the new chain.

---

*This architecture is designed to be simple for development while allowing for production scaling.*