# Changelog

All notable changes to Smart Money Tracker will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-03-14

### Added

#### Twitter / X Auto-Broadcasting 🐦
- **Typed event system** — `api/events/` package with `BroadcasterProtocol` interface, `AlertDTO` base class, and typed event subclasses (`WhaleAlertEvent`, `PriceTriggerEvent`, `PortfolioAlertEvent`)
- **`EventDispatcher`** — central fan-out bus that replaces direct `alert_broadcaster.publish()` calls; supports pluggable broadcaster plugins via `BroadcasterProtocol`
- **`WebSocketBroadcasterPlugin`** — wraps the existing `AlertBroadcaster` as a plugin; WebSocket subscribers see no change (full backward compatibility)
- **`TwitterBroadcaster`** — production-grade plugin that auto-posts alerts to Twitter/X:
  - **Priority scoring** (0–100 pts) — Exchange whale >$500K = 90pts, VC = 80pts, Smart money >$100K = 70pts, Price ATH = 75pts, Price target hit = 30pts
  - **Token bucket rate limiter** — configurable daily budget (50/day), hourly cap (17/hour), 20% reserve for critical alerts (score > 90)
  - **Per-entity cooldown** — 4 hours between tweets about the same wallet, 2 hours for the same token
  - **Circuit breaker** — CLOSED → OPEN (after 3 consecutive 429/5xx) → HALF_OPEN (test one request); 30 min pause with exponential backoff to 2h max
  - **Tweet rendering** — entity-first formatting for whales ("Binance moved..."), milestone formatting for price alerts ("SOL hit $150"), privacy-sanitized portfolio alerts
  - **Thread composition** — 5+ alerts about the same entity within 10 min get composed into a Twitter thread
  - **Dry-run mode** — `TWITTER_DRY_RUN=true` formats and logs tweets without posting; saved to DB for review
- **`TwitterClient`** — async wrapper around tweepy for Twitter API v2 (OAuth 1.0a User Context)
- **`TwitterPost` model** — TimescaleDB hypertable storing every tweet (or dry-run log) with `tweet_id`, `content`, `priority_score`, `engagement_metrics` (JSONB), `tenant_id` (future multi-tenancy)
- **`BroadcasterMetric` model** — operational metrics for broadcaster plugins (queue depth, posts/day, circuit state)
- **`TwitterConfig`** — Pydantic settings with `TWITTER_*` env prefix: `enabled`, `dry_run`, OAuth credentials, posting budget, scoring weights, cooldowns, feature flags, circuit breaker params
- **`api/routers/twitter.py`** — REST endpoints: `GET /api/v1/twitter/status` (broadcaster state), `GET /api/v1/twitter/recent` (last N tweets), `GET /api/v1/twitter/preview` (render a tweet for a specific alert without posting)
- **`/twitter_status`** Discord slash command (admin-only) — shows mode, queue depth, rate limit budget, circuit breaker state, feature flags, and last 5 tweets
- **`/twitter_test`** Discord slash command (admin-only) — preview tweet rendering for a specific alert ID with score and gate pass/fail info
- **`tweepy[async]`** added to `requirements.txt`
- **`.env.example`** — all `TWITTER_*` variables documented with comments

### Changed
- **`api/services/whale_tracker.py`** — `alert_broadcaster.publish(dict)` replaced with `event_dispatcher.dispatch(WhaleAlertEvent(...))` — enriches alerts with `from_label`, `to_label`, `entity_type`, `smart_money_score`
- **`api/services/price_alerts.py`** — `alert_broadcaster.publish(dict)` replaced with `event_dispatcher.dispatch(PriceTriggerEvent(...))`
- **`api/main.py`** — lifespan registers `WebSocketBroadcasterPlugin` and (if enabled) `TwitterBroadcaster` with `EventDispatcher`; `/health` endpoint now includes `broadcasters` plugin status; Twitter router registered
- **`api/models.py`** — added `TwitterPost` and `BroadcasterMetric` models; `twitter_posts` added to TimescaleDB hypertable setup
- **`config/settings.py`** — added `TwitterConfig` nested model and `twitter` field on `Settings`
- **`bots/discord_bot/commands.py`** — registers `setup_twitter` for the two new admin commands
- **`README.md`** — bumped to v2.0.0; updated features table, architecture diagram, command tables, endpoint reference, project structure, configuration section, and roadmap

---

## [1.8.0] - 2026-03-13

### Added

#### PostgreSQL + TimescaleDB Support 🐘⏱️
- **`asyncpg`** driver added to `requirements.txt` — high-performance async PostgreSQL client
- **`aiosqlite`** kept for the test suite (conftest uses `sqlite+aiosqlite:///:memory:`)
- **`SeenTransaction` model** — lightweight deduplication table `(tx_hash, chain)` primary key; prevents the same whale alert from being inserted twice when a scanner restarts mid-block
- **TimescaleDB hypertables** — `whale_alerts` and `portfolio_snapshots` are converted to hypertables on first startup via `create_hypertable(..., if_not_exists=TRUE, migrate_data=TRUE)`
- **`docker-compose.yml`** — new `db` service using `timescale/timescaledb:latest-pg16`; app service gains `depends_on: db: condition: service_healthy`

### Changed
- **`config/settings.py`** — default `DATABASE_URL` changed from `sqlite+aiosqlite:///./crypto_bots.db` to `postgresql+asyncpg://smart_money:smart_money@localhost:5432/smart_money`
- **`api/models.py` — `init_db()`** — now runs `CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE` before `create_all`, then calls `create_hypertable` for time-series tables
- **`api/models.py` — `migrate_db()`** — rewrote using `information_schema.columns` (PostgreSQL) instead of `PRAGMA table_info` (SQLite); uses `DO $$ ... END $$` blocks to add unique constraints idempotently
- **`.env.example`** — `DATABASE_URL` now defaults to PostgreSQL; SQLite example kept as a comment
- **`docker-compose.yml`** — removed SQLite volume `tracker-data`; added `db-data` volume for PostgreSQL data persistence

### Migration Guide (SQLite → PostgreSQL)
> Zero data loss — all existing rows migrate cleanly.
1. Start a TimescaleDB instance (Docker: `docker compose up -d db`)
2. Set `DATABASE_URL=postgresql+asyncpg://smart_money:smart_money@localhost:5432/smart_money` in `.env`
3. Export SQLite data: `sqlite3 crypto_bots.db .dump > dump.sql`
4. Import to PostgreSQL after adjusting SQLite-specific syntax
5. Start the app — `migrate_db()` and `init_db()` run automatically on startup

---

## [1.7.0] - 2026-03-13

### Added

#### ◎ Solana Token Safety Scanner (Anti-Rug) 🛡️
- **`api/routers/token_safety.py`** — new `GET /api/v1/token-safety/{mint}` endpoint; proxies **RugCheck.xyz** API and returns a structured `TokenSafetyReport` — no API key required
  - **Mint authority** — flags whether the dev can still print unlimited tokens
  - **Freeze authority** — flags whether the dev can freeze holder wallets
  - **LP lock %** — aggregated across all liquidity pools for the token
  - **Top holder concentration** — top-1 and top-5 wallet % of total supply
  - **Risk score & level** — `SAFE` (< 500) / `CAUTION` (500–1499) / `DANGER` (≥ 1500)
  - **Risk factors** — full list from RugCheck with `danger` / `warn` / `info` severity and descriptions
  - **Rugged flag** — boolean from RugCheck if the token has already been identified as a rug
- **`bots/discord_bot/cmd_token_safety.py`** — `/scan_token <mint>` Discord slash command
  - Color-coded Components V2 verdict card: green (SAFE), orange (CAUTION), red (DANGER), dark-red (RUGGED)
  - Displays all key risk signals in a single card with RugCheck.xyz attribution in the footer

#### Wallet Label in Whale Alerts 🏷️
- **`AlertResponse`** now includes a `wallet_label` field — populated by joining `WhaleAlert` with `TrackedWallet` using `joinedload` (no N+1 queries)
- **`/whale_alerts`** Discord command displays the wallet label in bold next to the from-address when one is set

#### `/wallets` Command 📋
- New `/wallets [chain]` Discord slash command — lists all tracked whale wallets with their label, chain badge, and active/paused status

### Changed
- **`api/routers/alerts.py`** — `AlertResponse` gains `wallet_label: Optional[str]`; both alert queries use `joinedload(WhaleAlert.wallet)` for efficient label resolution
- **`api/main.py`** — registers `token_safety.router`
- **`bots/discord_bot/commands.py`** — calls `setup_token_safety(bot)`; docstring updated to include new command group
- **`bots/discord_bot/cmd_help.py`** — adds `Token Safety` category with `scan_token` entry; adds missing `wallets` entry to catalogue; `_build_overview()` maps the new category icon

---

## [1.6.5] - 2026-03-13

### Added

#### Solana Chain Support 🟣
- **SolanaScanner** — New scanner class for Solana blockchain using Helius RPC
- **Base58 address validation** — Solana addresses are validated as base58 (44 chars, case-sensitive) and must NOT be lowercased
- **_extract_parties()** — Heuristic to extract from/to addresses from Solana transaction account keys, skipping known programs (SPL Token, System, Compute Budget)
- **Solana in Discord bot** — `/track_wallet` and `/whale_alerts` now accept `solana` as a chain option
- **Solana chain metadata** — Native token SOL, 0.4s block time, 4s poll interval, Solscan explorer, CoinGecko `solana` platform

### Changed
- **config/chains.py** — Added `chain_type` field (`"evm"` or `"solana"`) for scanner dispatch; Solana has `chain_id=0`
- **config/settings.py** — `HELIUS_API_KEY` and `HELIUS_RPC_URL` for Solana RPC configuration
- **api/services/whale_tracker.py** — `MultiChainTracker._build_scanners()` dispatches `SolanaScanner` for `chain_type="solana"`
- **api/routers/whales.py** — Address validation now distinguishes EVM (0x prefix, 42 chars) vs Solana (base58, 44 chars)

#### Comprehensive Test Suite 🧪
- **tests/conftest.py** — Shared pytest fixtures with in-memory SQLite database, `StaticPool` for cross-session visibility, mocked background services
- **tests/test_api_alerts.py** — API tests for whale alerts endpoints (GET /api/v1/alerts, filtering, pagination)
- **tests/test_api_portfolio.py** — CRUD tests for portfolio wallets, snapshots, and balance endpoints
- **tests/test_api_price_alerts.py** — CRUD tests for price alert rules endpoints
- **tests/test_api_wallets.py** — Integration tests for wallet tracking, address validation, and chain endpoints
- **tests/test_broadcaster.py** — Unit tests for AlertBroadcaster pub/sub logic (subscribe, unsubscribe, publish, queue overflow)
- **tests/test_config.py** — Pure-Python tests for chain registry and settings (RPC URL resolution, chain metadata)
- **tests/test_price_alert_service.py** — Unit tests for PriceAlertChecker, fetch_token_price, fetch_prices_batch, cooldown logic
- **tests/test_scanner.py** — Unit tests for _PriceCache TTL, BaseChainScanner.scan_range batching, _extract_parties Solana heuristic
- **tests/test_whale_tracker.py** — Unit tests for EvmChainScanner.is_healthy, MultiChainTracker._build_scanners

---

---

## [1.6.0] **PUBLIC LANCH** - 2026-03-12

### Added

#### discord.py 2.7.1 — Components V2 (CV2) 🎨
- **Upgraded `discord.py`** from `2.3.2` to `2.7.1` (latest stable)
- **All bot responses** converted from `discord.Embed` to **Components V2**: `LayoutView` + `Container` + `TextDisplay` + `Separator` — renders as a native Discord card with an accent-colour left border instead of the classic embed box
- **`build_cv2(title, lines, color, footer)`** — new helper in `_shared.py` builds a `LayoutView` containing a single `Container`; each `lines` entry becomes a `TextDisplay` separated by a thin `Separator`
- **`cv2_send(interaction, …)`** — sends a CV2 view via `interaction.followup.send(view=view)`
- **`cv2_error(interaction, …)`** — red-accented shortcut for error replies
- Pylance false-positives for `LayoutView`, `Container`, `TextDisplay`, `Separator`, `SeparatorSpacing` suppressed with per-line `# type: ignore[attr-defined]` aliases (stubs lag 2.6+ release)

#### `/help` command 📖
- **`bots/discord_bot/cmd_help.py`** — new file housing an 18-command catalogue
- `/help` (no argument) — ephemeral CV2 overview of all commands grouped by category (Whale Tracking, Portfolio, Price Alerts, Info)
- `/help <command>` — detailed card with description, full usage syntax, every parameter (required/optional + explanation), and a usage tip
- Autocomplete choices for all 18 commands

#### Discord command modules split 🗂️
- Previous monolithic `commands.py` (477 lines, 7 commands) replaced by 6 focused files:
  - **`_shared.py`** — shared constants, HTTP helpers (`api_get/post/patch/delete`), CV2 builders, formatters
  - **`cmd_whale.py`** — `/track_wallet`, `/untrack_wallet`, `/whale_alerts`, `/smart_money`, `/trending`
  - **`cmd_portfolio.py`** *(new)* — `/portfolio_add`, `/portfolio_list`, `/portfolio_balance`, `/portfolio_remove`, `/portfolio_toggle`
  - **`cmd_price_alerts.py`** *(new)* — `/price_alert_add`, `/price_alerts`, `/price_alert_delete`, `/price_alert_toggle`
  - **`cmd_info.py`** — `/chains`, `/status`, `/invite`
  - **`cmd_help.py`** *(new)* — `/help [command]`
  - **`commands.py`** — slim 35-line entry-point that calls each `setup_*()` function
- `CHAIN_CHOICES` expanded from 3 chains (ETH, Base, ARB) to all **6 chains**
- `api_patch()` helper added for PATCH / toggle endpoints
- `api_delete()` handles HTTP 204 No Content correctly
- `fmt_price()` added for sub-cent token price formatting

#### `/invite` command 🔗
- **`/invite`** — ephemeral command that posts the bot's OAuth2 invite link
- Supports two configuration modes (see Configuration below)
- Shows link source, scopes, permissions, API URL, and WebSocket URL
- Displays a clear error with setup instructions when neither `DISCORD_OAUTH_LINK` nor `DISCORD_CLIENT_ID` is configured

#### Configurable external API URL 🌐
- **`API_BASE_URL`** env var — set the public HTTP URL of the API server so bots deployed on a VPS, Pterodactyl node, or behind a reverse-proxy reach it without changing code
  - `API_BASE_URL=http://1.2.3.4:8000`
  - `API_BASE_URL=https://tracker.yourdomain.com`
  - Leave blank → falls back to `http://localhost:{API_PORT}`
- **`settings.api_url`** property — returns `api_base_url` if set, else `http://localhost:{api_port}`
- **`settings.ws_url`** property — derives WebSocket URL from `api_url` (`http→ws`, `https→wss`)
- `_shared.py` (Discord) and `handlers.py` (Telegram) now read `API_BASE` and `HEALTH_URL` from `settings.api_url` instead of hard-coded `localhost`
- Web dashboard WebSocket URL display updated by JS from `location.host` so it reflects the real server address

#### Discord OAuth2 configuration 🔐
- **`DISCORD_OAUTH_LINK`** *(new, recommended)* — paste a pre-built invite URL from the Discord Developer Portal; used as-is by `/invite`
- **`DISCORD_CLIENT_ID`** / **`DISCORD_CLIENT_SECRET`** — auto-build mode: bot constructs the invite URL from these values + scopes + permissions
- **`DISCORD_OAUTH_SCOPES`** — space-separated scopes (default: `bot applications.commands`)
- **`DISCORD_OAUTH_PERMISSIONS`** — integer permission bits (default: `2147568640`)
- Priority: `DISCORD_OAUTH_LINK` → auto-built from `DISCORD_CLIENT_ID` → empty (error shown in `/invite`)

#### `.env.example` 📄
- New file documenting every supported environment variable with comments and examples
- Side-by-side comparison of Option A (`DISCORD_OAUTH_LINK` paste) vs Option B (auto-build from `DISCORD_CLIENT_ID`)
- Prominent `API_BASE_URL` block with Pterodactyl / VPS examples

### Changed
- `config/settings.py` — added `api_base_url`, `discord_oauth_link`, `discord_client_id/secret/scopes/permissions` fields; added `api_url`, `ws_url`, `discord_invite_url` computed properties
- `requirements.txt` — `discord.py==2.3.2` → `discord.py==2.7.1`
- `README.md` — updated discord.py badge, bot command table (7 → 18 commands), external deployment section, OAuth2 invite instructions, project structure

---

## [1.5.0] - 2026-03-12

### Added

#### Portfolio Tracking 📁
- **`PortfolioWallet` model** — new DB table `portfolio_wallets`; tracks addresses for balance monitoring independent of whale-alert tracking
- **`PortfolioSnapshot` model** — new DB table `portfolio_snapshots`; stores point-in-time native-coin balance readings (cascade-deletes with parent wallet)
- **`PortfolioTracker`** background task — every 5 minutes fetches native balances for all active wallets, prices from CoinGecko, and commits snapshots
- **`fetch_wallet_balance(address, chain)`** — standalone coroutine for live on-demand balance lookup via web3
- **`POST /api/v1/portfolio/wallets`** — add a wallet to portfolio (duplicate-safe via 409)
- **`GET /api/v1/portfolio/wallets`** — list wallets (optional `?chain=` and `?active_only=true` filters)
- **`GET /api/v1/portfolio/wallets/{id}`** — retrieve a single wallet
- **`DELETE /api/v1/portfolio/wallets/{id}`** — remove wallet and all its snapshots (HTTP 204)
- **`PATCH /api/v1/portfolio/wallets/{id}/toggle`** — pause / resume automatic snapshot collection
- **`GET /api/v1/portfolio/wallets/{id}/balance`** — fetch live balance from chain RPC, save snapshot, return full USD breakdown
- **`GET /api/v1/portfolio/wallets/{id}/snapshots`** — return balance history newest-first (configurable `limit`, max 500)
- Supported native tokens: ETH (Ethereum / Base / Arbitrum / Optimism), BNB (BSC), POL (Polygon)

### Changed
- `api/main.py` — imports and starts `PortfolioTracker` as a third asyncio background task; cancels it on shutdown
- `api/models.py` — added `PortfolioWallet` and `PortfolioSnapshot` ORM models; tables auto-created via `init_db()`

---

## [1.4.0] - 2026-03-12

### Added

#### Price Alerts System 💰
- **`PriceAlertRule` model** — new DB table `price_alert_rules` with fields: `chain`, `token_address`, `token_symbol`, `condition` (`above` | `below`), `target_price_usd`, `is_active`, `label`, `created_at`, `last_triggered_at`
- **`PriceAlertChecker`** background task — polls every 60 s; fires when a token price crosses the target; 1-hour cooldown per rule to prevent duplicate alerts
- **`fetch_prices_batch()`** — single CoinGecko `/simple/token_price` call for all tokens on a chain, minimising API calls
- **`POST /api/v1/price-alerts`** — create a new price alert rule
- **`GET /api/v1/price-alerts`** — list rules (optional `?chain=` and `?active_only=true` filters)
- **`GET /api/v1/price-alerts/{id}`** — retrieve a single rule
- **`DELETE /api/v1/price-alerts/{id}`** — remove a rule (returns HTTP 204)
- **`PATCH /api/v1/price-alerts/{id}/toggle`** — flip `is_active` on/off
- Triggered price alerts are broadcast over the existing WebSocket stream with `"type": "price_alert"`

### Changed
- `api/main.py` — registers the new `price_alerts` router and starts `PriceAlertChecker` as an asyncio task in the app lifespan
- `api/models.py` — added `PriceAlertRule` ORM model; table created automatically on first startup

---

## [1.3.0] - 2026-03-12

### Added

#### WebSocket Real-time Alerts 🚀
- **`GET /ws/alerts`** — WebSocket endpoint that streams new whale alerts to all connected clients instantly as they are detected
- **`?chain=` filter** — optional query parameter to receive alerts for a specific chain only (e.g. `ws://localhost:8000/ws/alerts?chain=ethereum`)
- **`api/services/broadcaster.py`** — new `AlertBroadcaster` pub/sub singleton; each WebSocket client gets an `asyncio.Queue`; slow clients drop messages gracefully without affecting others
- Multiple simultaneous clients supported; each gets an independent queue

### Changed
- `api/services/whale_tracker.py` — after committing new alerts to DB, publishes each alert to `alert_broadcaster` so all WebSocket subscribers receive it in real-time
- `api/routers/alerts.py` — added `WebSocket` + `WebSocketDisconnect` imports and the `/ws/alerts` endpoint; updated module docstring

---

## [1.2.0] - 2026-03-12

### Added

#### New Chains 🎉
- **BSC (BNB Smart Chain)** 🟡 — Chain ID 56, ~3s blocks, 6s poll interval, public RPC (`bsc-dataseed.binance.org`) — no Alchemy key required
- **Polygon** 🟣 — Chain ID 137, ~2s blocks, 6s poll interval, Alchemy or public RPC
- **Optimism** 🔴 — Chain ID 10, ~2s blocks, 6s poll interval, Alchemy supported

#### Configuration
- **`ALCHEMY_POLYGON`** — Polygon mainnet RPC override
- **`ALCHEMY_OPT`** — Optimism mainnet RPC override
- **`BSC_RPC`** — BNB Smart Chain RPC (defaults to public endpoint)

### Changed
- `config/chains.py` — Added BSC, Polygon, Optimism to the chain registry with correct USDC/WETH addresses, CoinGecko platform slugs, and block explorers
- `config/settings.py` — Extended `get_rpc_url()` mapping and added three new env var fields
- `README.md` — Updated features, chain table, bot emoji reference, and roadmap

---

## [1.1.1] - 2026-03-12

### Fixed

- **Windows compatibility** — `loop.add_signal_handler()` raises `NotImplementedError` on Windows; `start.py` now uses `signal.signal()` on `win32` and keeps the asyncio handler on Linux/macOS
- **Fresh-database migration crash** — `migrate_db()` in `api/models.py` no longer attempts to copy rows from tables that do not exist yet; migration is skipped when the table is absent (first run)
- **Inline `.env` comments parsed as RPC URLs** — Removed trailing inline comments from empty `ALCHEMY_ETH`, `ALCHEMY_BASE`, and `ALCHEMY_ARB` variables that were being read as malformed URLs

### Changed

- **Base RPC fallback** — `ALCHEMY_BASE` now defaults to the public `https://mainnet.base.org` endpoint in `.env` so Base chain works without an Alchemy plan that supports it

### Chore

- Added `out.txt` to `.gitignore` to prevent temporary terminal output files from being committed

---

## [1.1.0] - 2026-03-11

### Added

#### Multi-Chain Support 🎉
- **Ethereum, Base, and Arbitrum** — Monitor wallets across all three chains simultaneously
- **Chain-Optimized Polling** — Adaptive scan intervals based on block time (ETH: 12s, Base: 2s, Arb: 1s)
- **Chain Emojis** — Visual chain identification (⬛ Ethereum, 🔵 Base, 🔶 Arbitrum)
- **Per-Chain Explorers** — Clickable transaction links to the correct block explorer

#### Architecture Improvements
- **ChainScanner Class** — One Web3 connection per chain for efficient scanning
- **MultiChainTracker** — Concurrent polling across all chains using asyncio.gather
- **_PriceCache** — TTL-based price cache (60s) to reduce CoinGecko API calls
- **Batch Block Scanning** — Detects missed blocks and batches them (cap: 20, concurrent: 5)

#### Discord Bot Enhancements
- `/track_wallet <address> [chain] [label]` — Chain parameter with autocomplete
- `/whale_alerts [count] [chain]` — Filter alerts by chain
- `/trending [chain]` — Filter trending tokens by chain
- `/chains` — New command to list all supported chains with status
- `/status` — Now shows per-chain health (🟢 Active / ⚪ Not configured)

#### Database Changes
- **TrackedWallet.chain** — New column for chain identification
- **WhaleAlert.chain** — Track which chain generated the alert
- **TokenActivity.chain** — Per-chain token statistics
- **Unique constraint update** — (address, chain) instead of just (address)

#### Configuration
- **ALCHEMY_API_KEY** — Single key now works for all chains
- **ALCHEMY_ETH, ALCHEMY_BASE, ALCHEMY_ARB** — Optional chain-specific RPC overrides
- **config/chains.py** — Centralized chain registry with metadata

### Changed

- **WhaleTrackerService** — Refactored into ChainScanner + MultiChainTracker architecture
- **Database migration** — Automatic migration for existing databases
- **Poll intervals** — Now per-chain based on block time instead of global setting

---

## [1.0.0] - 2024-01-15

### Added

#### Core Features
- **Whale Tracking Engine** - Real-time monitoring of Ethereum wallet transactions
- **ERC-20 Token Support** - Track transfers for any ERC-20 token
- **Native ETH Transfers** - Monitor ETH transfers with USD value calculation
- **USD Threshold Filtering** - Configurable minimum alert threshold ($10,000 default)
- **CoinGecko Price Integration** - Automatic USD price conversion for tokens

#### API Backend
- **FastAPI REST API** - Full-featured API with OpenAPI documentation
- **Wallet Management** - Track/untrack wallets with optional labels
- **Alert History** - Paginated query of past whale transactions
- **Trending Tokens** - Aggregated statistics on whale activity per token
- **Health Check Endpoint** - Monitor API status and configuration

#### Database
- **SQLite with Async Support** - Zero-config persistent storage
- **TrackedWallet Model** - Store wallet addresses and metadata
- **WhaleAlert Model** - Complete transaction history
- **TokenActivity Model** - Aggregated buy/sell statistics

#### Discord Bot
- **Slash Commands** - Modern Discord slash command support
- `/track_wallet` - Add wallet to tracking with optional label
- `/untrack_wallet` - Remove wallet from tracking
- `/whale_alerts` - View recent whale transactions
- `/smart_money` - Analyze whale sentiment for a token
- `/trending` - See top tokens by whale accumulation
- `/status` - Check API health and configuration

#### Telegram Bot
- **Command Handlers** - Full command parity with Discord
- `/start` - Welcome message with command overview
- `/track` - Add wallet to tracking
- `/untrack` - Remove wallet from tracking
- `/alerts` - View recent whale transactions
- `/smartmoney` - Analyze whale sentiment for a token
- `/trending` - See top tokens by whale accumulation
- `/status` - Check API health

#### Configuration
- **Environment Variables** - Secure configuration via `.env`
- **Pydantic Settings** - Type-safe configuration management
- **Configurable Thresholds** - Adjust whale detection sensitivity
- **Configurable Poll Intervals** - Balance between speed and API limits

#### Architecture
- **Async Throughout** - Full asyncio implementation
- **Separation of Concerns** - Clean architecture with service layers
- **Multi-Bot Support** - Run Discord, Telegram, or both simultaneously
- **Unified Launcher** - Single entry point with CLI options

#### Developer Experience
- **Comprehensive Docstrings** - Google-style documentation
- **Type Hints** - Full type annotation coverage
- **Clean Project Structure** - Intuitive file organization

---

## [0.1.0] - 2024-01-01

### Added

- Initial project scaffold
- Basic FastAPI setup
- Discord bot connection
- Telegram bot connection
- Database models design
- Configuration management

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| **2.0.0** | **2026-03-14** | **Twitter/X auto-broadcasting, typed event dispatcher, priority scoring, rate limiting, circuit breaker** |
| 1.8.0 | 2026-03-13 | PostgreSQL + TimescaleDB, SeenTransaction dedup, docker-compose with TimescaleDB |
| 1.7.0 | 2026-03-13 | Solana token safety scanner (/scan_token), wallet labels in alerts, /wallets command |
| 1.6.5 | 2026-03-13 | Solana chain support, comprehensive test suite (~120 tests) |
| 1.6.0 | 2026-03-12 | **PUBLIC LAUNCH** discord.py 2.7.1, Components V2, 18 slash commands, /help, /invite, API_BASE_URL, DISCORD_OAUTH_LINK |
| 1.5.0 | 2026-03-12 | Portfolio wallet tracking with balance snapshots |
| 1.4.0 | 2026-03-12 | Price alert rules system with WebSocket broadcast |
| 1.3.0 | 2026-03-12 | WebSocket real-time alert stream |
| 1.2.0 | 2026-03-12 | BSC, Polygon, Optimism support |
| 1.1.1 | 2026-03-12 | Windows fix, fresh-DB migration fix, .env RPC URL fix |
| 1.1.0 | 2026-03-11 | Multi-chain support (ETH, Base, Arbitrum) |
| 1.0.0 | 2024-01-15 | First stable release |
| 0.1.0 | 2024-01-01 | Initial development |

---

## Upcoming Features (Roadmap)

These features are planned for future releases:

### [2.1.0] - Planned

- Smart money labeling — entity resolution for known exchanges, VCs, DAOs, MEV bots
- Real-time Discord push notifications (alerts sent directly to a channel, not just on-demand)
- Web dashboard with live charts

### [3.0.0] - Planned

- Machine learning for whale behavior prediction
- Kubernetes Helm charts
- Multi-tenant support for SaaS deployment
- Telegram bot full feature parity

---

## How to Read This Changelog

- **Added**: New features
- **Changed**: Changes to existing features
- **Deprecated**: Features to be removed in future releases
- **Removed**: Features removed in this release
- **Fixed**: Bug fixes
- **Security**: Security-related changes

---

*This changelog is maintained according to [Keep a Changelog](https://keepachangelog.com/).*