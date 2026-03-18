# Changelog

All notable changes to Smart Money Tracker will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.0] - 2026-03-18

### Added

#### Bluesky (AT Protocol) Broadcaster 🦋
- **`api/services/bluesky/`** — new package housing a production-grade Bluesky broadcaster plugin implementing `BroadcasterProtocol`:
  - **Priority queue** — `asyncio.PriorityQueue` scored 0–100; overflow handler evicts the lowest-scoring pending item while protecting alerts with score ≥ 90
  - **`TokenBucketRateLimiter`** — rolling 24-hour / 1-hour deque windows; `acquire(is_critical)` reserves `critical_reserve_pct` of the daily budget for high-score alerts
  - **`EntityCooldownTracker`** — per-wallet TTL (4h default) and per-token TTL (2h default) deduplication; `wallet:` prefix routes to the wallet cooldown, all other keys to the token cooldown
  - **`CircuitBreaker`** — CLOSED → OPEN after 3 consecutive 429/5xx; 30 min pause with exponential backoff to 2h max; HALF-OPEN probe to test recovery
  - **`AlertScorer`** — reuses Twitter scoring weights; accumulation alerts score 55–95 linear on USD volume; `_score_alert()` dispatches on `AlertType`
  - **`reset_budget()`** — clears both `_daily_posts` and `_hourly_posts` deques for manual budget override
  - **`status` property** — exposes mode, queue depth, handle, `min_score`, `critical_score`, rate-limiter windows, and circuit-breaker state
  - Posts to Bluesky via `atproto.AsyncClient.send_post()`; dry-run mode formats and logs without posting
- **`BlueSkyPost` model** — `bluesky_posts` DB table; stores `post_uri`, `post_cid`, `content`, `priority_score`, `alert_type`, `alert_id`, `posted_at`; TimescaleDB hypertable registered in `init_db()`
- **`BlueSkyConfig`** — Pydantic settings with `BLUESKY_` env prefix: `enabled`, `dry_run`, `handle`, `password` (app password), `daily_budget` (50), `hourly_cap` (10), `critical_reserve_pct` (0.10), `critical_score` (80.0), `min_score` (35.0), `cooldown_wallet_hours` (4.0), `cooldown_token_hours` (2.0), feature flags per alert type, circuit breaker params, `max_queue_size` (100)
- **`api/routers/bluesky.py`** — REST endpoints:
  - `GET /api/v1/bluesky/status` — broadcaster health, budget, circuit breaker state, `min_score`, `critical_score`, last-5 posts
  - `POST /api/v1/bluesky/reset-budget` — clear both rate-limiter deques; returns new remaining counts
  - `GET /api/v1/bluesky/recent?limit=10` — last N posts from `bluesky_posts` table (max 50)
- **`/bluesky_status`** Discord slash command (admin-only) — formatted CV2 card: mode, queue depth, hourly/daily budget remaining, circuit breaker state, `min_score`, `critical_score`, last 5 post excerpts
- **`/bluesky_reset_budget`** Discord slash command (admin-only) — calls `POST /bluesky/reset-budget` and displays the new remaining budget counts

#### Telegram Channel Broadcaster 📢
- **`api/services/telegram_channel/`** — new package; identical architecture to `BlueSkyBroadcaster` but targets a Telegram Bot API channel:
  - Same components: priority queue, `TokenBucketRateLimiter`, `EntityCooldownTracker`, `CircuitBreaker`, `AlertScorer`
  - 1-second inter-post delay (vs 2s for Bluesky); larger default queue (200 vs 100)
  - Circuit breaker pauses 5 min (vs 30 min for Twitter) — Telegram API recovers faster
  - Posts via `python-telegram-bot` `Bot.send_message(chat_id=channel_id, text=..., parse_mode="HTML")`
- **`TelegramChannelPost` model** — `telegram_channel_posts` table: `message_id` (nullable in dry-run), `content`, `priority_score`, `alert_type`, `alert_id`, `posted_at`
- **`TelegramChannelConfig`** — `TELEGRAM_CHANNEL_` prefix: `enabled`, `dry_run`, `bot_token`, `channel_id` (`@handle` or `-100xxxxxxxxxx`), `daily_budget` (200), `hourly_cap` (30), `critical_score` (80.0), `min_score` (0.0 — no floor by default given generous budget), `critical_reserve_pct` (0.10), `cooldown_wallet_hours` (2.0), `cooldown_token_hours` (1.0), feature flags, circuit breaker params, `max_queue_size` (200)
- **`api/routers/telegram_channel.py`** — REST endpoints:
  - `GET /api/v1/telegram-channel/status`
  - `POST /api/v1/telegram-channel/reset-budget`
  - `GET /api/v1/telegram-channel/recent?limit=10`
- **`/telegram_channel_status`** Discord slash command (admin-only) — channel_id, budget, features, last 5 messages
- **`/telegram_channel_reset_budget`** Discord slash command (admin-only)

#### Configurable Broadcaster Rate-Limiter Controls 🎛️
Applied uniformly to all three broadcasters (Twitter, Telegram Channel, Bluesky):
- **`critical_score`** (env: `TWITTER_CRITICAL_SCORE`, `TELEGRAM_CHANNEL_CRITICAL_SCORE`, `BLUESKY_CRITICAL_SCORE`, default: `80.0`) — alerts with `priority_score >= critical_score` are flagged `is_critical=True` and can consume the reserved budget pool; replaces the former hardcoded threshold of 90 which was unreachable for whale alerts (ceiling was exactly 90, not above 90)
- **`min_score`** (env: `TWITTER_MIN_SCORE`, `BLUESKY_MIN_SCORE`, default: `35.0`; `TELEGRAM_CHANNEL_MIN_SCORE`, default: `0.0`) — alerts below this score are discarded in `handle_event()` before queuing; prevents sub-threshold noise from consuming budget
- **`critical_reserve_pct`** reduced from `0.20` to `0.10` for all broadcasters — 10% of daily budget held for critical alerts instead of 20%; frees more capacity for normal-priority alerts
- **`reset_budget()`** method on each broadcaster — clears `_daily_posts` and `_hourly_posts` deques in-memory; returns updated rate-limiter info dict
- **`POST /api/v1/twitter/reset-budget`**, **`POST /api/v1/telegram-channel/reset-budget`**, **`POST /api/v1/bluesky/reset-budget`** — REST endpoints that invoke `reset_budget()` and return `{"ok": true, "rate_limiter": {...}}`
- **`/twitter_reset_budget`** Discord slash command — parallel to the Bluesky/Telegram equivalents
- `min_score` and `critical_score` now exposed in every broadcaster's `status` dict and Discord status cards
- Rate-limit drops upgraded from `DEBUG` to `WARNING` (includes alert ID and score); cooldown skips upgraded from `DEBUG` to `INFO`

#### Unrealized P&L 📊
- **`GET /api/v1/wallets/{address}/pnl`** now computes unrealized P&L at request time for each open position:
  - `qty_held = max(0, total_bought_token − total_sold_token)`
  - ERC-20: fetches `current_price_usd` via `fetch_token_price(token_address, chain)` (DeFiLlama)
  - Native coins: resolves CoinGecko coin key from token_key sentinel (`NATIVE:ETH` → `ethereum`, `NATIVE:BNB` → `binancecoin`, `NATIVE:POL` → `matic-network`) then fetches from DeFiLlama
  - `unrealized_pnl_usd = (current_price − avg_cost_usd) × qty_held`
- **`WalletPositionResponse.unrealized_pnl_usd: float = 0.0`** — new field on the API response schema; not stored in `wallet_positions` (always computed live)

### Changed

#### `/wallet_pnl` Discord Card Redesign 🏦
- **Complete rewrite of `bots/discord_bot/cmd_pnl.py`** — new layout with three structural sections:
  - **Header** — `🏦 Wallet P&L — <label or shortened address>`; full address on a second line using `short_addr()` (6+4 chars)
  - **ASCII summary box** — box-drawing character frame containing three rows: `💚 Realized`, `📊 Unrealized`, `📈 Total P&L`; each value formatted with sign (`+$1.2M` / `−$34.5K`) using magnitude-aware suffix (`K`/`M`/`B`)
  - **Per-chain breakdown** — one section per chain that has positions; shows chain emoji + chain name header, aggregate realized/unrealized totals for that chain, and top 5 positions by `|realized + unrealized|` ranked by absolute impact; each position line: `TOKEN  ±$realized  (±$unreal unreal)  avg $cost  Nx`
- **Color coding** — `COLOR_BUY` (green) accent when total P&L ≥ 0; `COLOR_SELL` (red) when negative
- **Parallel fetch** — `asyncio.gather(api_get(...pnl...), _wallet_label(address))` fetches P&L data and wallet label concurrently
- **`_wallet_label(address)`** — queries `GET /wallets?active_only=false` and searches the list for the matching address; returns the label string or `None` if not tracked / not labeled
- New formatters: `_fmt(v)` signed USD, `_fmt_abs(v)` unsigned USD, `_fmt_price(v)` significant-decimal price for micro-cap tokens, `_summary_box(realized, unrealized, total)` box-drawing frame, `_chain_section(chain_name, positions)` per-chain aggregate + top 5

#### Docker Volume Mount 🐳
- **`docker-compose.yml`** — app service now mounts `. : /app` as a bind volume; venv lives in `/opt/venv` (baked into image, not overridden by the mount). Code changes are picked up by `git pull + docker compose restart` — a full `docker compose up --build` is only needed when `requirements.txt` changes

### Fixed

- **BSC `get_block` POA error** — BSC uses 280-byte non-standard `extraData` in block headers; web3.py raises `ValueError: The field extraData is 280 bytes, but should be 32` on every block without `ExtraDataToPOAMiddleware`. Fixed by adding `is_poa: bool = field(default=False)` to `ChainConfig` and setting `is_poa=True` for the `"bsc"` entry; `EvmChainScanner.w3` property now injects the middleware at `layer=0` when `config.is_poa` is `True`
- **BSC `-32005` rate-limit errors bypassing exponential backoff** — public BSC RPC returns JSON-RPC error `-32005` ("limit exceeded") on `eth_getLogs`; the inner `scan_block` try/except was catching it, logging a WARNING per block, and discarding it — the outer `_chain_loop` backoff never triggered. Fixed by detecting `-32005` / `"limit exceeded"` in the inner except and re-raising; `_chain_loop` now treats it as a rate-limit signal and applies the same exponential backoff (up to 300s) as HTTP 429
- **Bluesky/Twitter/Telegram alerts silently dropped after budget exhaustion** — `critical_reserve_pct=0.20` locked 20% of the daily budget for `score > 90` alerts. Whale alerts score exactly 90 at the ceiling (not above), so the reserve was permanently unavailable and all non-critical alerts were silently dropped at `DEBUG` level once the remaining 80% was spent. Fixed by: (1) lowering reserve to 10%, (2) making `critical_score` configurable (default 80.0) so VC/exchange/smart-money alerts (score 80–90) actually use the reserve pool, (3) upgrading rate-limit drops to `WARNING`
- **`atproto` / `httpx` / `pydantic` dependency conflict** — `atproto<=0.0.54` requires `httpx<0.27.0` while `python-telegram-bot==21.0.1` requires `httpx~=0.27`; `atproto>=0.0.55` requires `pydantic>=2.7`. Fixed by bumping `atproto>=0.0.55`, `pydantic>=2.7.0,<3.0.0`, `pydantic-settings>=2.2.0`
- **API task crashing silently on startup** — `asyncio.gather(*tasks, return_exceptions=True)` swallowed any startup exception from the API task without logging it, leaving the Discord bot running but the API never listening. Fixed by inspecting gather results and emitting `logger.critical(...)` with full traceback for any task that exits with an unhandled exception
- **`ExtraDataToPOAMiddleware` import error on web3 < 6** — `ExtraDataToPOAMiddleware` was introduced in web3 v6; installations running web3 v5 raised `ImportError` at startup, preventing the API from loading. Fixed with a try/except fallback: imports `ExtraDataToPOAMiddleware` (web3 ≥ 6) or `geth_poa_middleware` (web3 < 6) and aliases both to `_POAMiddleware`

---

## [2.4.0] - 2026-03-17

### Added

#### Bulk Price Alert Import 📥
- **`data/import_price_alerts.py`** — CLI tool to bulk-import price alert rules from a CSV file; reads `chain,token_address,token_symbol,condition,target_price_usd,label` rows and POSTs each to the API; skips duplicates (409) and invalid rows with a summary at the end
  - Usage: `python data/import_price_alerts.py data/my_alerts.csv`
  - `--dry-run` flag — previews what would be created without making any API calls
  - `--api-url` flag — target a remote API instance
- **`data/generate_price_alerts.py`** — auto-generates price alerts from CoinGecko top-N tokens for any chain; creates `above` (+X%) and `below` (−X%) alerts relative to the current price
  - `--chain` — target chain (ethereum, base, arbitrum, bsc, polygon, optimism)
  - `--count` — number of top tokens to process (default: 20)
  - `--above / --below` — percentage thresholds (default: ±15%)
  - `--only above|below` — generate only one direction
  - `--dry-run` — preview without creating
- **`data/top_eth_alerts.csv`** — 45 hand-curated price alerts covering ETH, WBTC, AAVE, UNI, CRV, COMP, LINK, MKR, BAL, YFI, DAI/USDC/USDT depeg, APE, AXS, SHIB, PEPE, LPT, ENS, PENDLE, rETH on Ethereum + ARB/GMX on Arbitrum + WETH on Base + BNB/WBNB on BSC

### Fixed

- **Price fetching switched from CoinGecko to DeFiLlama** — CoinGecko free tier returns 400 Bad Request on token price endpoints and aggressive 429 rate limiting; replaced with `coins.llama.fi` which is free, requires no API key, has no rate limits, and supports all 6 chains. Batch size increased from 10 to 50 addresses per request. Affects both `price_alerts.py` and `whale_tracker.py` (including native ETH price fetch)
- **`/price_alerts` Discord command crash** — `ValueError: maximum number of children exceeded (40)` when displaying 20+ rules; reduced display limit to 15 rules (each rule = 2 CV2 components) to stay under Discord's 40-child hard limit per Container
- **`/wallet_pnl` crash on positions with empty lines** — `build_cv2()` passed `""` empty strings to `_TextDisplay`; Discord rejects content of length 0 with HTTP 400. Fixed globally in `_shared.py`: `build_cv2` now skips empty lines
- **Accumulation detection never firing** — critical bug in `accumulation_detector.py`: on a BUY alert the tracked wallet is always the *receiver* (`to_address`), not the sender. The DB query was filtering on `from_address == wallet_address` which never matched any BUY. Changed to `to_address` — accumulation patterns can now be detected correctly
- **Auto-push logging gap** — `_dispatch_alert` had no visibility into whether price/accumulation alerts were received or silently filtered; added `INFO` log on every received alert (type + score) and `DEBUG` log when a channel config filter rejects an alert

---

## [2.3.0] - 2026-03-17

### Added

#### Pro Alert Cards 🎨
- **Redesigned Discord alert cards** — all three alert types (whale, price, accumulation) now use a structured, emoji-rich layout with clear visual hierarchy
- **Automated-by branding line** — every alert card shows `🤖 Automated alert by **Bot Name**` with optional clickable `[Join Discord]` and `[GitHub]` links
- **`BRAND_DISCORD_INVITE`** — env var for your Discord server invite link; shown as a clickable link in every alert footer
- **`BRAND_GITHUB_URL`** — env var for your GitHub repo URL; shown alongside the Discord invite
- **`BRAND_NAME`** — env var for your bot's display name in branding lines (default: `Smart Money Tracker`)
- Whale alert layout: direction + symbol + USD value on one bold line, from/to addresses with labels, clickable transaction link, priority score footer
- Price alert layout: broke-above / dropped-below wording, current vs target price, 24h change with directional emoji
- Accumulation alert layout: buy count + window, wallet + label, total + avg per tx

#### Wallet Label Auto-Update 🏷️
- **`/track_wallet` now updates the label** if the wallet is already tracked — previously re-tracking an active wallet silently ignored the new label
- **`label_updated` field** added to `WalletResponse` — Discord command shows `"Label updated"` title instead of `"Wallet tracked"` when an existing label was changed

#### Twitter Accumulation Support 🐦
- **`_render_accumulation()`** template in `TwitterBroadcaster` — formats accumulation alerts as tweets: wallet label, buy count, total/avg USD, chain emoji
- **`TWITTER_ENABLE_ACCUMULATION_TWEETS`** feature flag (default: `true`) — controls whether accumulation alerts are posted to Twitter
- **`accum:<wallet>:<token>` cooldown key** — prevents duplicate accumulation tweets per wallet+token pair
- Accumulation tweets exposed in `/twitter_status` features map

### Fixed

- **Price alert Discord cards showing blank/zero values** — `_format_price_alert()` was reading wrong metadata keys (`token_id`, `current_price`, `target_price`) instead of the correct ones dispatched by the service (`token_symbol`, `current_price_usd`, `target_price_usd`)
- **Twitter broadcaster using non-existent `tweepy.AsyncClient`** — `tweepy 4.x` only ships a synchronous `Client`; replaced with `tweepy.Client` wrapped in `asyncio.to_thread()` so the event loop is never blocked
- **CoinGecko 400 Bad Request on large token batches** — price fetch now splits addresses into chunks of 10 per request instead of sending all addresses in one URL (free-tier URL length limit)
- **`/twitter_status` showing `Features: Accumulation=Off`** — accumulation flag was missing from the status dict; now correctly exposed

---

## [2.2.0] - 2026-03-16

### Added

#### Wallet P&L Tracker 📈
- **`WalletPosition` model** — new DB table `wallet_positions`; tracks per-wallet, per-chain, per-token cost basis and realized profit/loss using weighted-average cost basis (WACB). `token_key` sentinel (`NATIVE:ETH`, `NATIVE:BNB`, etc.) ensures NULL-safe unique constraint `(wallet_address, chain, token_key)`
- **`api/services/position_tracker.py`** — stateless `update_position(db, alert, wallet_map)` helper called after every committed whale alert:
  - **BUY** → updates `avg_cost_usd` using WACB formula: `(old_avg × old_qty + price × new_qty) / new_qty`
  - **SELL** → realizes P&L: `(sell_price - avg_cost) × amount_token`; updates `total_sold_*` and `realized_pnl_usd`
  - **SEND** → skipped (no cost-basis impact)
- **`GET /api/v1/wallets/{address}/pnl`** — returns all `WalletPosition` rows for a wallet, sorted by `realized_pnl_usd` desc; optional `?chain=` filter
- **`/wallet_pnl <address> [chain]`** Discord command — shows realized P&L per token with buy/sell totals, average cost, and a color-coded total (green = profit, red = loss)

#### Accumulation Detection 🔁
- **`AccumulationEvent` model** — new DB table `accumulation_events`; TimescaleDB hypertable on `fired_at`; stores `wallet_address`, `chain`, `token_symbol/address`, `buy_count`, `total_usd`, `avg_per_tx_usd`, `window_hours`
- **`AlertType.ACCUMULATION`** — new enum value added to `api/events/protocol.py`
- **`AccumulationAlertEvent`** — new typed event subclass in `api/events/types.py` with full metadata documentation
- **`api/services/accumulation_detector.py`** — stateless `check_accumulation(db, alert, wallet_map)` pattern detector:
  - Fires when a wallet has **≥ 3 BUY alerts** for the same token in a **24-hour window** with **≥ $50K total volume**
  - **6-hour cooldown** per `(wallet, chain, token)` triple prevents alert spam
  - Matches token by `token_address` (ERC-20) or `token_symbol` (native) for correct deduplication
- **`GET /api/v1/alerts/accumulations`** — paginated list of recent accumulation pattern alerts; optional `?chain=` filter
- **`/accumulation_alerts [chain] [count]`** Discord command — shows wallets repeatedly buying the same token with buy count, total volume, average per tx, and detection time
- **Real-time push** — accumulation alerts broadcast via `EventDispatcher` as `AccumulationAlertEvent`; `auto_push.py` formats and delivers them to configured Discord channels

#### Two-Phase Commit Integration ⚙️
- **`EvmChainScanner._scan_block_range()`** — after the first `db.commit()` (which assigns `whale_alert.id`), runs `update_position()` and `check_accumulation()` for each new alert, then commits a second time. Errors in phase 2 are caught and logged — they never block whale alert delivery

### Changed
- **`docker-compose.yml`** — DB credentials now read from `POSTGRES_*` env vars (no more hardcoded `smart_money/smart_money`); PostgreSQL port bound to `127.0.0.1` only (not exposed to internet); app service uses `env_file: .env` instead of listing each variable individually
- **`.env.example`** — added `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` variables with a production security reminder; improved `DATABASE_URL` comments for Docker vs local dev
- **`bots/discord_bot/commands.py`** — registers `setup_pnl`, `setup_accumulation`
- **`api/services/whale_tracker.py`** — imports `AccumulationAlertEvent`, `update_position`, `check_accumulation`; dispatches `AccumulationAlertEvent` for each pattern that fires

---

## [2.1.0] - 2026-03-14

### Added

#### Real-time Discord Push Notifications 🔔
- **`bots/discord_bot/auto_push.py`** — background task that connects to the API WebSocket (`/ws/alerts`) on bot startup; auto-posts every incoming alert to all configured Discord channels. Reconnects automatically with exponential backoff (1s → 60s max).
- **`api/routers/alert_channels.py`** — full CRUD for Discord alert channel configurations:
  - `POST /api/v1/alert-channels` — register a channel (supports `min_score`, `chains`, `alert_types` filters)
  - `GET /api/v1/alert-channels` — list channels with optional `?guild_id=` filter
  - `GET /api/v1/alert-channels/active` — active channels joined with `GuildSubscription.tier` (used by the bot)
  - `DELETE /api/v1/alert-channels/{id}` — remove a configuration
  - `PATCH /api/v1/alert-channels/{id}/toggle` — enable / disable
- **`AlertChannel` model** — stores per-channel filters (`min_score`, `chains` CSV, `alert_types` CSV, `is_active`)
- **`/set_alert_channel`** Discord command — enables push in the current channel with optional score/chain/type filters (requires Manage Channels permission)
- **`/alert_channels`** Discord command — lists all configured push channels for the server with their filters and status
- **`/remove_alert_channel`** Discord command — removes a push channel config by ID
- **`/toggle_alert_channel`** Discord command — enables or disables a push channel

#### Smart Entity Labeling System 🏷️
- **`SmartLabel` model** — stores known entity addresses with `name`, `entity_type`, `tier` (`free`/`pro`), and `chain`; unique constraint on `(address, chain)`
- **`GuildSubscription` model** — per-Discord-guild subscription tier (`free`/`pro`) with optional `expires_at` for time-limited Pro access
- **`api/routers/guilds.py`** — Guild management endpoints:
  - `POST /api/v1/guilds` — auto-register guild as free tier on bot join
  - `GET /api/v1/guilds/{guild_id}/tier` — get/lazy-create guild tier
  - `PATCH /api/v1/guilds/{guild_id}/upgrade` — upgrade to Pro tier
  - `GET /api/v1/guilds/{guild_id}/whois/{address}` — smart label lookup; Pro labels shown as "🤖 Smart Entity (Pro 🔒)" for free guilds
- **`EvmChainScanner._get_smart_label()`** — per-scan in-memory label cache; enriches every whale alert with `from_smart_label_name/tier` and `to_smart_label_name/tier` at detection time
- **`/who_is`** Discord command — look up any wallet address in the Smart Label database; masks Pro labels for free servers
- **`admin/add_pro_label.py`** — CLI tool to add Pro-tier labels: `python admin/add_pro_label.py <address> <chain> <name> <entity_type>`
- **`data/seed_smart_labels.py`** — seeds **80 free-tier** labels covering Binance, Coinbase, Kraken, OKX, Bybit, Bitfinex, HTX, Vitalik, Ethereum Foundation, Uniswap, Aave, Lido, MakerDAO, Jump Trading, Wintermute, Paradigm, Arbitrum/Optimism/Base bridges, Lido staking, DeFi routers, MEV bots, and more

#### Pro / Free Tier Gating 🎯
- Free guilds: real-time push notifications + 80 free-tier Smart Labels visible in alerts and `/who_is`
- Pro guilds: real-time push notifications + all 300 Pro-tier Smart Labels (premium hedge funds, obscure exchange wallets, smart-money clusters)
- `/admin upgrade_guild <guild_id>` — bot-owner Discord command to upgrade any server to Pro

#### WebSocket Enhancements ⚡
- `/ws/alerts` now accepts `?chains=ethereum,bsc` (multi-chain), `?min_score=70`, and `?alert_types=whale,price` query parameters for server-side filtering
- `priority_score` field added to every WebSocket message (computed by `AlertScorer` in `WebSocketBroadcasterPlugin`)

#### Event Dispatcher Metrics 📊
- `EventDispatcher` now tracks per-plugin delivery counts, failure counts, and average latency (ms) in-memory
- `GET /api/v1/metrics/events` — returns total dispatched count and per-plugin stats (`delivered`, `failed`, `avg_latency_ms`)
- `AlertDelivery` model added for future persistent delivery audit log
- `alert_deliveries` TimescaleDB hypertable registered in `init_db()`

#### Bot Lifecycle
- `on_guild_join` event — auto-registers new guilds as free tier via the Guilds API when the bot is added to a server

### Fixed
- **Critical:** `WhaleAlert` constructor in `_process_erc20_log` was missing `direction=direction` and `block_number=block_number` — these are NOT NULL columns; every ERC-20 whale alert would fail to save (regression introduced during smart label enrichment refactor)
- **Critical:** `WhaleAlert` constructor in `_process_native_tx` was missing `direction=direction` — same NOT NULL violation for native ETH transfers
- **Minor:** `/trending` command in `cmd_whale.py` had `title = ...` indented inside the `for` loop — it only set the title on the last iteration. Fixed to be set after the loop.
- **Minor:** BOM character (`\ufeff`) removed from `cmd_whale.py`

### Changed
- `aiohttp>=3.9.0` added to `requirements.txt` (explicit dependency for the WebSocket client in `auto_push.py`)
- `bots/discord_bot/commands.py` — registers `setup_alert_channels`, `setup_admin`
- `bots/discord_bot/bot.py` — starts `auto_push` task on `on_ready`; registers new guild on `on_guild_join`
- `api/main.py` — registers `alert_channels`, `metrics`, `guilds` routers
- Architecture diagram updated to reflect Smart Label engine and real-time push layer

---

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
| **2.5.0** | **2026-03-18** | **Bluesky broadcaster, Telegram Channel broadcaster, unrealized P&L, configurable critical_score + min_score + reset_budget for all broadcasters, /wallet_pnl redesign, BSC POA fix** |
| 2.4.0 | 2026-03-17 | Bulk CSV price alert import, DeFiLlama price API, accumulation bug fix, /wallet_pnl crash fix, Discord CV2 limit fix |
| 2.3.0 | 2026-03-17 | Pro alert cards with branding, wallet label auto-update, Twitter accumulation support, CoinGecko + tweepy fixes |
| 2.2.0 | 2026-03-16 | Wallet P&L tracker (WACB), accumulation detection (≥3 buys / 24h / $50K), two-phase commit, Docker hardening |
| 2.1.0 | 2026-03-14 | Real-time Discord push notifications, smart entity labeling (80 free / 300 pro), pro/free tier gating |
| 2.0.0 | 2026-03-14 | Twitter/X auto-broadcasting, typed event dispatcher, priority scoring, rate limiting, circuit breaker |
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

### [2.6.0] - Planned

- Web dashboard with live charts (real-time P&L curves, accumulation heatmap)
- Telegram bot full command parity with Discord (push notifications, P&L, accumulation slash commands)

### [3.0.0] - Planned

- Machine learning for whale behavior prediction
- Kubernetes Helm charts
- Multi-tenant SaaS deployment with per-guild RPC isolation

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