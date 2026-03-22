import re

with open("README.md", "r") as f:
    content = f.read()

# 1. Update Advanced Features section
old_adv = """## Advanced Features

Advanced behavioral intelligence, historical backtesting, and predictive clustering are available through our [managed platform](#) (coming Q2 2026)."""

new_adv = """## 🚀 Evolution to the Smart Money Platform

To provide a more powerful and scalable experience, several advanced analytical features have been transitioned to our upcoming **[Smart Money Platform](#)** (currently in the building phase, coming Q2 2026).

The following features have been successfully migrated to the new platform architecture:
- **Wallet Clustering & Funding History**
- **Historical Backtesting**
- **Cross-Chain Entity Detection**

This open-source core will continue to focus on maintaining a robust, real-time multi-chain whale tracking engine, while the managed platform will handle these complex predictive and historical analytics.
"""
content = content.replace(old_adv, new_adv)

# 2. Update architecture diagram
# We'll just carefully replace the specific lines
content = content.replace("│ • Clustering     │", "│                  │")
content = content.replace("│  Wallets · Alerts · Price Alerts · Exchange Flows · Clusters · Entity · Portfolio · Channels    │", "│  Wallets · Alerts · Price Alerts · Exchange Flows · Portfolio · Channels                        │")
content = content.replace("PriceTriggerEvent / WalletClusterAlertEvent", "PriceTriggerEvent                                  ")

# Remove cluster, cross-chain, backtester boxes
diagram_box_to_remove = """│  ┌── SmartLabel Engine ──────────┐   ┌── Exchange Flow Detector ──┐   ┌── Cluster Analyzer ──┐ │
│  │  80 free + 300 pro labels     │   │  OUTFLOW 🔴 / INFLOW 🟢    │   │  Funding · Timing    │ │
│  │  enriched at scan time        │   │  via SmartLabel exchanges  │   │  Pattern · Union-Find│ │
│  │  per-guild via /whois         │   │  1-hour cooldown           │   │  rebuild every 10 min│ │
│  └───────────────────────────────┘   └────────────────────────────┘   └──────────────────────┘ │
│  ┌── Cross-Chain Entity Detection ─────────────────────────────────────────────────────────────┐ │
│  │  normalize_entity_name() → group SmartLabel addresses across chains → unified entity view   │ │
│  │  /entity profile · /entity_lookup · alert enrichment ("Also active on BSC") · no new model │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘ │
│  ┌── Backtester ─────────────────────────────────────────────────────────────────────────────┐  │
│  │  Runs on startup + daily UTC midnight · DeFiLlama historical prices · /bot_stats command  │  │
│  │  AccumulationEvent + ExchangeFlowEvent → price@signal · +24h · +72h · +7d → win rate     │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────┘  │"""

new_diagram_box = """│  ┌── SmartLabel Engine ──────────┐   ┌── Exchange Flow Detector ──┐                            │
│  │  80 free + 300 pro labels     │   │  OUTFLOW 🔴 / INFLOW 🟢    │                            │
│  │  enriched at scan time        │   │  via SmartLabel exchanges  │                            │
│  │  per-guild via /whois         │   │  1-hour cooldown           │                            │
│  └───────────────────────────────┘   └────────────────────────────┘                            │"""
content = content.replace(diagram_box_to_remove, new_diagram_box)

content = content.replace("│ • Cluster          │  │  WhaleAlert ⏱     │  │  DeFiLlama API     │", "│                    │  │  WhaleAlert ⏱     │  │  DeFiLlama API     │")
content = content.replace("│   enrichment       │  │  ExchangeFlow ⏱   │  │   no API key)      │", "│                    │  │  ExchangeFlow ⏱   │  │   no API key)      │")
content = content.replace("│  WalletCluster    │\n", "")
content = content.replace("│  BacktestResult ⏱ │\n", "")

# 3. Update API endpoints (Wallet Clustering, Cross-Chain)
api_endpoints_to_remove = """| **Wallet Clustering** | | |
| `GET` | `/api/v1/clusters` | List detected clusters (`?chain=`, `?min_confidence=`, paginated) |
| `GET` | `/api/v1/clusters/{id}` | Single cluster with all members |
| `GET` | `/api/v1/clusters/wallet/{address}` | All clusters containing a specific wallet |
| `POST` | `/api/v1/clusters/analyze` | Trigger immediate re-analysis (background) |
| **Cross-Chain Entity Detection** | | |
| `GET` | `/api/v1/entity/list` | All known entities from SmartLabel DB with address counts |
| `GET` | `/api/v1/entity/lookup/{address}` | Resolve address to cross-chain entity + all chain addresses |
| `GET` | `/api/v1/entity/{name}` | Full cross-chain profile (per-chain volume, P&L, recent alerts) |
| `GET` | `/api/v1/entity/{name}/activity` | Recent cross-chain whale activity for an entity |
"""
content = content.replace(api_endpoints_to_remove, "")

# 4. WebSocket stream string
content = content.replace("whale, price_alert, accumulation, exchange_flow, wallet_cluster", "whale, price_alert, accumulation, exchange_flow")

# 5. Discord commands
content = content.replace("| `/whale_alerts [chain] [count]` | Show recent whale transactions (includes wallet label + cluster info) |", "| `/whale_alerts [chain] [count]` | Show recent whale transactions (includes wallet label) |")

discord_commands_to_remove = """**🕵️ Wallet Clustering**

| Command | Description |
|---------|-------------|
| `/clusters [chain] [min_confidence] [count]` | List detected wallet clusters — groups of addresses controlled by the same entity |
| `/cluster_info <id>` | Full details for a cluster: all members, detection methods, combined volume |
| `/wallet_cluster <address> [chain]` | Check which cluster (entity) a wallet belongs to |

**🌐 Cross-Chain Entity Detection**

| Command | Description |
|---------|-------------|
| `/entity <name_or_address> [hours]` | Cross-chain entity profile — per-chain volume, combined P&L, recent alerts; pass address or name |
| `/entity_lookup <address> [chain]` | Resolve any wallet address to its cross-chain entity and see all known addresses |

"""
content = content.replace(discord_commands_to_remove, "")

# 6. Telegram commands
tg_commands_to_remove = """| `/clusters [chain] [count]` | Detected wallet clusters (same-entity groups) |
| `/wallet_cluster <address> [chain]` | Which cluster a wallet belongs to |
| `/entity <name_or_address> [hours]` | Cross-chain entity profile (per-chain volume, P&L, alerts) |
| `/entity_lookup <address> [chain]` | Resolve address to its cross-chain entity |
"""
content = content.replace(tg_commands_to_remove, "")
content = content.replace(" /entity /entity_lookup /clusters", "")

# 7. Environment Variables
content = re.sub(r'\| `TWITTER_BUDGET_RESERVE_CLUSTER` .*?\n', '', content)
content = re.sub(r'\| `TWITTER_ENABLE_CLUSTER_TWEETS` .*?\n', '', content)
content = re.sub(r'\| `TELEGRAM_CHANNEL_BUDGET_RESERVE_CLUSTER` .*?\n', '', content)
content = re.sub(r'\| `TELEGRAM_CHANNEL_ENABLE_CLUSTER_POSTS` .*?\n', '', content)
content = re.sub(r'\| `BLUESKY_BUDGET_RESERVE_CLUSTER` .*?\n', '', content)
content = re.sub(r'\| `BLUESKY_ENABLE_CLUSTER_POSTS` .*?\n', '', content)

content = content.replace("/price/accum/exflow/cluster", "/price/accum/exflow")

# 8. Project Structure
proj_structure_to_remove = """│   │   ├── clusters.py           # Wallet cluster endpoints + manual trigger
│   │   ├── cross_chain.py       # Cross-chain entity detection endpoints
"""
content = content.replace(proj_structure_to_remove, "")

proj_structure_to_remove_2 = """│       ├── cluster_detector.py   # ClusterAnalyzer background service + get_wallet_cluster_info()
│       ├── cross_chain_entity.py # Cross-chain entity detection service (normalize, resolve, enrich)
"""
content = content.replace(proj_structure_to_remove_2, "")

proj_structure_to_remove_3 = """│   │   ├── cmd_clusters.py       # /clusters /cluster_info /wallet_cluster
│   │   ├── cmd_cross_chain.py   # /entity /entity_lookup
"""
content = content.replace(proj_structure_to_remove_3, "")


with open("README.md", "w") as f:
    f.write(content)

