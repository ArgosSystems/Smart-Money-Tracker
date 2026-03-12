# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Smart Money Tracker is a multi-chain cryptocurrency whale wallet monitoring system. It scans 6 EVM chains (Ethereum, Base, Arbitrum, BSC, Polygon, Optimism) for large transactions above a USD threshold and delivers real-time alerts via Discord and/or Telegram bots, backed by a FastAPI REST/WebSocket API.

## Commands

### Running the Application

```bash
# Recommended launcher (auto-creates venv, cross-platform)
python start.py              # API + Discord bot
python start.py --telegram   # API + Telegram bot
python start.py --api-only   # API only (no bot)
python start.py --bot-only   # Discord bot only (API must already be running)

# API with hot-reload (development)
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Docker
docker compose up -d
docker compose logs -f
```

### Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows
pip install -r requirements.txt
cp .env.example .env           # Then fill in tokens and RPC URLs
```

### Testing

There is no test suite currently. To add tests:
```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

## Architecture

### Component Layers

```
Discord Bot / Telegram Bot / Web Dashboard
         ↓ HTTP/WebSocket
FastAPI (api/main.py, port 8000)
         ↓
Background Services (api/services/)
         ↓
Blockchain RPC nodes (6 chains)  +  SQLite DB
```

### Key Files

| File | Role |
|------|------|
| `start.py` | Unified launcher — spawns API + bot as subprocesses, handles venv |
| `config/settings.py` | Pydantic `BaseSettings` — reads `.env`, computes `api_url`/`ws_url` |
| `config/chains.py` | `CHAINS` registry — one `ChainConfig` dataclass per chain |
| `api/main.py` | FastAPI app, lifespan startup (starts background services), built-in dashboard HTML |
| `api/models.py` | SQLAlchemy async ORM models + auto-migration (`create_all`) |
| `api/services/whale_tracker.py` | `MultiChainTracker` orchestrates one `ChainScanner` per chain; polls blocks concurrently |
| `api/services/price_alerts.py` | 60-second loop checking price rules against CoinGecko |
| `api/services/portfolio_tracker.py` | 5-minute loop fetching wallet balances per chain |
| `api/services/broadcaster.py` | WebSocket pub/sub singleton — services publish, clients subscribe |
| `bots/discord_bot/commands.py` | Entry point — calls `setup_*` from each `cmd_*.py` module |
| `bots/discord_bot/_shared.py` | Shared constants, Components V2 builders, HTTP helpers used by all `cmd_*.py` |

### Discord Bot Command Modules

Commands are split into focused modules under `bots/discord_bot/`:
- `cmd_whale.py` — whale tracking (track/untrack/list wallets, recent alerts)
- `cmd_portfolio.py` — portfolio snapshot commands
- `cmd_price_alerts.py` — price alert CRUD
- `cmd_info.py` — chain status, `/status`, `/invite`
- `cmd_help.py` — `/help` with autocomplete over all 18 slash commands

All modules import shared utilities from `_shared.py` (e.g., `make_container()`, `api_get()`, `api_post()`).

### Discord Components V2

This project uses **discord.py 2.7.1 with Components V2** (`use_components_v2=True`). Responses use `LayoutView` + `Container` + `TextDisplay` instead of traditional `Embed`. Do not mix old embed patterns with CV2 components.

### Multi-Chain Scanning

`config/chains.py` defines the `CHAINS` dict. Each entry has its own RPC URL resolved from env vars (e.g., `ALCHEMY_ETH` or fallback to `ALCHEMY_API_KEY`). Adding a new chain requires only adding a `ChainConfig` entry here — the `MultiChainTracker` iterates `CHAINS` automatically.

### `API_BASE_URL` Pattern

Bots communicate with the API via `settings.api_url`. In local dev this defaults to `http://localhost:{API_PORT}`. For remote deployments (VPS, Pterodactyl), set `API_BASE_URL` in `.env` and the bots will use that URL. WebSocket URL is auto-derived (`http→ws`, `https→wss`).

## Environment Variables

**Required — at least one RPC source:**
- `ALCHEMY_API_KEY` (single key for all Alchemy chains), OR
- Individual: `ALCHEMY_ETH`, `ALCHEMY_BASE`, `ALCHEMY_ARB`, `ALCHEMY_POLYGON`, `ALCHEMY_OPT`, `BSC_RPC`

**Required — at least one bot:**
- `DISCORD_TOKEN`
- `TELEGRAM_TOKEN`

**Optional:**
- `API_BASE_URL` — external URL for remote deployments
- `WHALE_THRESHOLD_USD` — minimum USD value to trigger an alert (default: `10000`)
- `DATABASE_URL` — defaults to `sqlite+aiosqlite:///./crypto_bots.db`
- `DISCORD_CLIENT_ID` + `DISCORD_CLIENT_SECRET` — enables `/invite` OAuth2 link generation
- `DISCORD_OAUTH_LINK` — pre-built invite link (alternative to client ID/secret)

See `.env.example` for the full reference with comments.

## Database

SQLite with async SQLAlchemy (`aiosqlite`). Schema is defined in `api/models.py` and auto-migrated via `create_all` at startup. To switch to PostgreSQL, change `DATABASE_URL` to a `postgresql+asyncpg://` connection string and install `asyncpg`.
