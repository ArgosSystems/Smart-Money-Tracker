"""
api/main.py
-----------
FastAPI application entry point.

Lifespan
--------
startup  → run schema migration → create tables → start MultiChainTracker
shutdown → cancel tracker task gracefully
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from api.models import init_db, migrate_db, AsyncSessionLocal
from api.routers import alerts, whales
from api.routers.alerts import ws_router
from api.routers import price_alerts, portfolio, token_safety, twitter
from api.services.whale_tracker import MultiChainTracker
from api.services.price_alerts import PriceAlertChecker
from api.services.portfolio_tracker import PortfolioTracker
from api.services.broadcaster import alert_broadcaster
from api.events.dispatcher import event_dispatcher, WebSocketBroadcasterPlugin
from config.chains import CHAINS, active_chains
from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_tracker_task: asyncio.Task | None = None
_price_checker_task: asyncio.Task | None = None
_portfolio_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tracker_task, _price_checker_task, _portfolio_task

    logger.info("Running database migration (safe on fresh DB)…")
    await migrate_db()
    await init_db()

    logger.info("Starting MultiChainTracker…")
    tracker = MultiChainTracker()
    _tracker_task = asyncio.create_task(tracker.start(), name="multi_chain_tracker")

    logger.info("Starting PriceAlertChecker…")
    checker = PriceAlertChecker()
    _price_checker_task = asyncio.create_task(checker.start(), name="price_alert_checker")

    logger.info("Starting PortfolioTracker…")
    port_tracker = PortfolioTracker()
    _portfolio_task = asyncio.create_task(port_tracker.start(), name="portfolio_tracker")

    # ── Event dispatcher + broadcaster plugins ─────────────────────────────
    logger.info("Initializing EventDispatcher…")
    event_dispatcher.register(WebSocketBroadcasterPlugin(alert_broadcaster))

    if settings.twitter.enabled:
        from api.services.twitter.broadcaster import TwitterBroadcaster  # noqa: PLC0415
        twitter_broadcaster = TwitterBroadcaster(
            config=settings.twitter,
            session_factory=AsyncSessionLocal,
        )
        event_dispatcher.register(twitter_broadcaster)
        logger.info(
            "TwitterBroadcaster registered (dry_run=%s)", settings.twitter.dry_run
        )

    await event_dispatcher.start_all()

    yield

    await event_dispatcher.stop_all()

    for task in (_tracker_task, _price_checker_task, _portfolio_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Smart Money Tracker API",
    description=(
        "Multi-chain whale wallet tracker.\n\n"
        "**Supported chains:** Ethereum, Base, Arbitrum\n\n"
        "Connect any client (Discord, Telegram, web dashboard) to this single API."
    ),
    version="2.0.0",  # Twitter/X broadcasting + typed event dispatcher
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whales.router)
app.include_router(alerts.router)
app.include_router(ws_router)
app.include_router(price_alerts.router)
app.include_router(portfolio.router)
app.include_router(token_safety.router)
app.include_router(twitter.router)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Smart Money Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css" />
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0b0c18;
    --surface:   #12132a;
    --card:      rgba(255,255,255,0.035);
    --border:    rgba(255,255,255,0.08);
    --text:      #e8eaf0;
    --muted:     #7b7f9e;
    --radius:    16px;
    --font:      'Inter', system-ui, sans-serif;
  }

  html { scroll-behavior: smooth; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── Background glow blobs ── */
  body::before {
    content: '';
    position: fixed;
    top: -200px; left: -200px;
    width: 700px; height: 700px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(99,102,241,.18) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }
  body::after {
    content: '';
    position: fixed;
    bottom: -300px; right: -200px;
    width: 800px; height: 800px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(20,184,166,.12) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  /* ── Layout ── */
  .wrapper {
    position: relative;
    z-index: 1;
    max-width: 1200px;
    margin: 0 auto;
    padding: 48px 24px 80px;
  }

  /* ── Header ── */
  header {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    margin-bottom: 56px;
  }

  .logo-badge {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    background: rgba(99,102,241,.15);
    border: 1px solid rgba(99,102,241,.35);
    border-radius: 999px;
    padding: 8px 20px;
    font-size: 13px;
    font-weight: 600;
    color: #a5b4fc;
    letter-spacing: .04em;
    text-transform: uppercase;
    margin-bottom: 24px;
  }
  .logo-badge i { font-size: 16px; }

  h1 {
    font-size: clamp(2rem, 5vw, 3.4rem);
    font-weight: 800;
    letter-spacing: -0.03em;
    line-height: 1.1;
    background: linear-gradient(135deg, #e0e7ff 30%, #a5b4fc 70%, #34d399 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 16px;
  }

  .subtitle {
    font-size: 17px;
    color: var(--muted);
    font-weight: 400;
    max-width: 520px;
    line-height: 1.65;
    margin-bottom: 36px;
  }

  /* ── Chain pills ── */
  #chain-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
  }

  .chain-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 14px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: .01em;
    border: 1px solid;
    transition: transform .15s, box-shadow .15s;
  }
  .chain-pill:hover { transform: translateY(-1px); box-shadow: 0 4px 14px rgba(0,0,0,.5); }
  .chain-pill.active   { background: rgba(52,211,153,.12); border-color: rgba(52,211,153,.35); color: #6ee7b7; }
  .chain-pill.inactive { background: rgba(255,255,255,.04); border-color: rgba(255,255,255,.1); color: var(--muted); }
  .chain-pill .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .chain-pill.active .dot   { background: #34d399; box-shadow: 0 0 6px #34d399; }
  .chain-pill.inactive .dot { background: #4b5563; }

  /* ── Section label ── */
  .section-label {
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 20px;
  }

  /* ── Card grid ── */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 18px;
    margin-bottom: 48px;
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 28px 26px 24px;
    text-decoration: none;
    display: flex;
    flex-direction: column;
    gap: 14px;
    position: relative;
    overflow: hidden;
    cursor: pointer;
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
    backdrop-filter: blur(12px);
  }
  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent);
    border-radius: var(--radius) var(--radius) 0 0;
    opacity: .8;
  }
  .card:hover {
    transform: translateY(-4px);
    box-shadow: 0 16px 40px rgba(0,0,0,.45);
    border-color: rgba(255,255,255,.14);
    background: rgba(255,255,255,.055);
  }

  .card-icon {
    width: 48px; height: 48px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    background: var(--icon-bg);
    flex-shrink: 0;
  }
  .card-icon i { font-size: 24px; color: var(--icon-color); }

  .card-body { flex: 1; }

  .card-title {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 6px;
  }

  .card-desc {
    font-size: 13.5px;
    color: var(--muted);
    line-height: 1.6;
  }

  .card-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 4px;
  }

  .card-tag {
    font-size: 11.5px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 999px;
    background: var(--icon-bg);
    color: var(--icon-color);
    letter-spacing: .02em;
  }

  .card-arrow {
    font-size: 18px;
    color: var(--muted);
    transition: color .15s, transform .15s;
  }
  .card:hover .card-arrow {
    color: var(--icon-color);
    transform: translateX(3px);
  }

  /* ── WS card special ── */
  .card.ws-card .card-tag { cursor: default; }

  /* ── Divider ── */
  .divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 8px 0 28px;
  }

  /* ── Footer ── */
  footer {
    text-align: center;
    font-size: 13px;
    color: var(--muted);
    margin-top: 16px;
    line-height: 2;
  }
  footer a { color: #818cf8; text-decoration: none; }
  footer a:hover { text-decoration: underline; }

  /* ── Responsive ── */
  @media (max-width: 600px) {
    .grid { grid-template-columns: 1fr; }
    .wrapper { padding: 32px 16px 60px; }
  }
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <header>
    <div class="logo-badge">
      <i class="ti ti-currency-ethereum"></i>
      Smart Money Tracker
    </div>
    <h1>Track Whales.<br/>Stay Ahead.</h1>
    <p class="subtitle">
      Real-time whale wallet monitoring across 6 EVM chains,
      with price alerts, portfolio tracking, and WebSocket streaming.
    </p>
    <div id="chain-pills">
      <span class="chain-pill inactive"><span class="dot"></span>Loading chains…</span>
    </div>
  </header>

  <!-- Main navigation cards -->
  <p class="section-label">Explore the API</p>
  <hr class="divider" />

  <div class="grid">

    <a href="/docs" class="card" style="--accent:#6366f1; --icon-bg:rgba(99,102,241,.14); --icon-color:#a5b4fc;">
      <div class="card-icon"><i class="ti ti-book-2"></i></div>
      <div class="card-body">
        <div class="card-title">API Documentation</div>
        <div class="card-desc">Explore all endpoints interactively with the Swagger UI. Try requests right in the browser.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">Swagger UI</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <a href="/api/v1/alerts" class="card" style="--accent:#8b5cf6; --icon-bg:rgba(139,92,246,.14); --icon-color:#c4b5fd;">
      <div class="card-icon"><i class="ti ti-bell-ringing"></i></div>
      <div class="card-body">
        <div class="card-title">Whale Alerts</div>
        <div class="card-desc">Latest large transactions detected across all monitored wallets and chains.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /alerts</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <a href="/api/v1/wallets" class="card" style="--accent:#06b6d4; --icon-bg:rgba(6,182,212,.14); --icon-color:#67e8f9;">
      <div class="card-icon"><i class="ti ti-wallet"></i></div>
      <div class="card-body">
        <div class="card-title">Tracked Wallets</div>
        <div class="card-desc">Manage whale addresses under active monitoring. Add, remove, and inspect wallets per chain.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /wallets</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <a href="/api/v1/tokens/trending" class="card" style="--accent:#22c55e; --icon-bg:rgba(34,197,94,.14); --icon-color:#86efac;">
      <div class="card-icon"><i class="ti ti-trending-up"></i></div>
      <div class="card-body">
        <div class="card-title">Trending Tokens</div>
        <div class="card-desc">Tokens whales are accumulating or dumping most aggressively right now, ranked by volume.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /trending</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <a href="/api/v1/price-alerts" class="card" style="--accent:#f59e0b; --icon-bg:rgba(245,158,11,.14); --icon-color:#fcd34d;">
      <div class="card-icon"><i class="ti ti-currency-dollar"></i></div>
      <div class="card-body">
        <div class="card-title">Price Alerts</div>
        <div class="card-desc">Set <em>above</em> / <em>below</em> price rules for any token. Triggers a WebSocket broadcast when hit.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /price-alerts</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <a href="/api/v1/portfolio/wallets" class="card" style="--accent:#14b8a6; --icon-bg:rgba(20,184,166,.14); --icon-color:#5eead4;">
      <div class="card-icon"><i class="ti ti-chart-donut"></i></div>
      <div class="card-body">
        <div class="card-title">Portfolio Tracking</div>
        <div class="card-desc">Monitor native-coin balances (ETH, BNB, POL) for any wallet. Auto-snapshots every 5 minutes.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /portfolio</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

    <div class="card ws-card" style="--accent:#eab308; --icon-bg:rgba(234,179,8,.14); --icon-color:#fde047;" onclick="copyWs()">
      <div class="card-icon"><i class="ti ti-plug-connected"></i></div>
      <div class="card-body">
        <div class="card-title">WebSocket Stream</div>
        <div class="card-desc">
          Real-time whale &amp; price alerts pushed instantly.<br/>
          <code id="ws-url-display" style="font-size:12px;color:#fde047;word-break:break-all;">ws://…/ws/alerts</code>
        </div>
      </div>
      <div class="card-footer">
        <span class="card-tag" id="ws-tag">Click to copy URL</span>
        <i class="ti ti-copy card-arrow"></i>
      </div>
    </div>

    <a href="/health" class="card" style="--accent:#ef4444; --icon-bg:rgba(239,68,68,.14); --icon-color:#fca5a5;">
      <div class="card-icon"><i class="ti ti-heartbeat"></i></div>
      <div class="card-body">
        <div class="card-title">Health Check</div>
        <div class="card-desc">Live status of the API, configured chains, whale threshold, and background services.</div>
      </div>
      <div class="card-footer">
        <span class="card-tag">GET /health</span>
        <i class="ti ti-arrow-right card-arrow"></i>
      </div>
    </a>

  </div>

  <!-- Footer -->
  <footer>
    Smart Money Tracker &nbsp;·&nbsp;
    <a href="/docs">Swagger UI</a> &nbsp;·&nbsp;
    <a href="/redoc">ReDoc</a> &nbsp;·&nbsp;
    <a href="https://github.com/aymenelouadi/Smart-Money-Tracker" target="_blank">GitHub</a>
    <br />
    <span style="font-size:12px;">v2.0.0 &nbsp;·&nbsp; Ethereum · Base · Arbitrum · BSC · Polygon · Optimism · Solana</span>
  </footer>

</div>

<script>
  // Load chain status from /health
  async function loadChains() {
    try {
      const r = await fetch('/health');
      const data = await r.json();
      const container = document.getElementById('chain-pills');
      container.innerHTML = '';
      const emojis = {
        ethereum: '⬛', base: '🔵', arbitrum: '🔶',
        bsc: '🟡', polygon: '🟣', optimism: '🔴'
      };
      for (const [name, info] of Object.entries(data.chains)) {
        const pill = document.createElement('span');
        const active = info.configured;
        pill.className = 'chain-pill ' + (active ? 'active' : 'inactive');
        pill.innerHTML =
          '<span class="dot"></span>' +
          (emojis[name] || '') + ' ' +
          name.charAt(0).toUpperCase() + name.slice(1);
        pill.title = active ? 'Configured & scanning' : 'No RPC URL configured';
        container.appendChild(pill);
      }
    } catch (e) {
      console.warn('Could not fetch /health', e);
    }
  }

  // Copy WebSocket URL
  function getWsUrl() {
    const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    return proto + location.host + '/ws/alerts';
  }
  function copyWs() {
    const url = getWsUrl();
    navigator.clipboard.writeText(url).then(() => {
      const tag = document.getElementById('ws-tag');
      tag.textContent = 'Copied!';
      setTimeout(() => { tag.textContent = 'Click to copy URL'; }, 2000);
    });
  }
  // Populate the WS URL display based on actual host
  document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('ws-url-display');
    if (el) el.textContent = getWsUrl();
  });

  loadChains();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Serve the visual navigation dashboard."""
    return HTMLResponse(content=_DASHBOARD_HTML)


_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Smart Money Tracker — API Docs</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui.css" />
  <style>
    /* ── Reset & base ── */
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      background: #0b0c18;
      font-family: 'Inter', system-ui, sans-serif;
      color: #e2e4ef;
    }

    /* ── Top nav bar ── */
    .smt-topbar {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(11,12,24,.92);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid rgba(255,255,255,.07);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      height: 56px;
      gap: 12px;
    }
    .smt-topbar-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .smt-logo {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -.01em;
      color: #a5b4fc;
      white-space: nowrap;
    }
    .smt-badge {
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(99,102,241,.18);
      border: 1px solid rgba(99,102,241,.35);
      color: #818cf8;
      letter-spacing: .04em;
    }
    .smt-topbar-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .smt-nav-btn {
      padding: 6px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      border: 1px solid rgba(255,255,255,.1);
      color: #9ca3af;
      background: transparent;
      transition: border-color .15s, color .15s, background .15s;
      white-space: nowrap;
    }
    .smt-nav-btn:hover {
      color: #e2e4ef;
      background: rgba(255,255,255,.06);
      border-color: rgba(255,255,255,.18);
    }

    /* ── Swagger UI wrapper ── */
    #swagger-ui {
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 80px;
    }

    /* ── Hide default swagger topbar ── */
    .swagger-ui .topbar { display: none !important; }

    /* ── Info block ── */
    .swagger-ui .info {
      margin: 0 0 28px !important;
      padding: 28px 32px !important;
      background: rgba(255,255,255,.03) !important;
      border: 1px solid rgba(255,255,255,.08) !important;
      border-radius: 14px !important;
    }
    .swagger-ui .info .title {
      font-family: 'Inter', sans-serif !important;
      font-size: 26px !important;
      font-weight: 800 !important;
      color: #e2e4ef !important;
      letter-spacing: -.02em !important;
    }
    .swagger-ui .info .title small { color: #6366f1 !important; font-size: 13px !important; }
    .swagger-ui .info p,
    .swagger-ui .info li { color: #9ca3af !important; font-size: 14px !important; line-height: 1.65 !important; }
    .swagger-ui .info a { color: #818cf8 !important; }

    /* ── Base typography ── */
    .swagger-ui, .swagger-ui * {
      font-family: 'Inter', system-ui, sans-serif !important;
    }
    .swagger-ui .opblock-tag {
      font-size: 17px !important;
      font-weight: 700 !important;
      color: #c7d2fe !important;
      border-bottom: 1px solid rgba(255,255,255,.07) !important;
      padding: 14px 0 10px !important;
      margin: 24px 0 8px !important;
    }
    .swagger-ui .opblock-tag:hover { background: transparent !important; }
    .swagger-ui .opblock-tag-section h3 { color: #c7d2fe !important; }

    /* ── Operation blocks ── */
    .swagger-ui .opblock {
      background: rgba(255,255,255,.03) !important;
      border: 1px solid rgba(255,255,255,.07) !important;
      border-radius: 10px !important;
      margin-bottom: 10px !important;
      box-shadow: none !important;
    }
    .swagger-ui .opblock:hover { border-color: rgba(255,255,255,.13) !important; }
    .swagger-ui .opblock.is-open { border-color: rgba(255,255,255,.12) !important; }

    .swagger-ui .opblock-summary {
      padding: 12px 16px !important;
      align-items: center !important;
    }
    .swagger-ui .opblock-summary-path {
      font-size: 14px !important;
      font-weight: 600 !important;
      color: #e2e4ef !important;
    }
    .swagger-ui .opblock-summary-description {
      font-size: 13px !important;
      color: #6b7280 !important;
    }

    /* ── Method colours ── */
    .swagger-ui .opblock.opblock-get    { border-left: 3px solid #22c55e !important; }
    .swagger-ui .opblock.opblock-post   { border-left: 3px solid #6366f1 !important; }
    .swagger-ui .opblock.opblock-delete { border-left: 3px solid #ef4444 !important; }
    .swagger-ui .opblock.opblock-patch  { border-left: 3px solid #f59e0b !important; }
    .swagger-ui .opblock.opblock-put    { border-left: 3px solid #3b82f6 !important; }
    .swagger-ui .opblock.opblock-get    .opblock-summary-method { background: #166534 !important; }
    .swagger-ui .opblock.opblock-post   .opblock-summary-method { background: #312e81 !important; }
    .swagger-ui .opblock.opblock-delete .opblock-summary-method { background: #7f1d1d !important; }
    .swagger-ui .opblock.opblock-patch  .opblock-summary-method { background: #78350f !important; }
    .swagger-ui .opblock.opblock-put    .opblock-summary-method { background: #1e3a5f !important; }
    .swagger-ui .opblock-summary-method {
      font-size: 12px !important;
      font-weight: 700 !important;
      min-width: 70px !important;
      border-radius: 6px !important;
      padding: 5px 8px !important;
      letter-spacing: .05em !important;
      text-align: center !important;
    }

    /* ── Expanded body ── */
    .swagger-ui .opblock-body {
      background: transparent !important;
    }
    .swagger-ui .opblock-section-header {
      background: rgba(255,255,255,.04) !important;
      border-radius: 6px !important;
    }
    .swagger-ui .opblock-section-header h4 {
      color: #9ca3af !important;
      font-size: 12px !important;
      font-weight: 600 !important;
      letter-spacing: .07em !important;
      text-transform: uppercase !important;
    }
    .swagger-ui table thead tr td,
    .swagger-ui table thead tr th {
      color: #6b7280 !important;
      font-size: 12px !important;
      font-weight: 600 !important;
      border-bottom: 1px solid rgba(255,255,255,.07) !important;
    }
    .swagger-ui .parameter__name { color: #c7d2fe !important; font-weight: 600 !important; }
    .swagger-ui .parameter__type { color: #6ee7b7 !important; }
    .swagger-ui .parameter__in   { color: #fca5a5 !important; font-size: 11px !important; }
    .swagger-ui .prop-type       { color: #6ee7b7 !important; }
    .swagger-ui .prop-format     { color: #fde68a !important; }

    /* ── Models / Schemas section ── */
    .swagger-ui section.models {
      background: rgba(255,255,255,.02) !important;
      border: 1px solid rgba(255,255,255,.07) !important;
      border-radius: 10px !important;
      padding: 4px 12px 12px !important;
    }
    .swagger-ui section.models h4 {
      color: #c7d2fe !important;
      font-weight: 700 !important;
    }
    .swagger-ui .model-title { color: #a5b4fc !important; font-weight: 600 !important; }
    .swagger-ui .model { color: #d1d5db !important; }

    /* json-schema-2020-12 (Swagger UI v5 schemas) */
    .swagger-ui .json-schema-2020-12 {
      background: rgba(255,255,255,.025) !important;
      border: 1px solid rgba(255,255,255,.08) !important;
      border-radius: 10px !important;
      margin-bottom: 8px !important;
      overflow: hidden !important;
    }
    .swagger-ui .json-schema-2020-12-head {
      display: flex !important;
      align-items: center !important;
      gap: 10px !important;
      padding: 10px 16px !important;
      background: rgba(255,255,255,.03) !important;
    }
    .swagger-ui .json-schema-2020-12-accordion {
      background: none !important;
      border: none !important;
      cursor: pointer !important;
      display: flex !important;
      align-items: center !important;
      gap: 8px !important;
      padding: 0 !important;
      flex: 1 !important;
    }
    .swagger-ui .json-schema-2020-12__title {
      font-size: 14px !important;
      font-weight: 700 !important;
      color: #a5b4fc !important;
      letter-spacing: -.01em !important;
    }
    .swagger-ui .json-schema-2020-12-accordion__icon svg { fill: #6b7280 !important; }
    .swagger-ui .json-schema-2020-12-accordion__icon--expanded svg { fill: #a5b4fc !important; }
    .swagger-ui .json-schema-2020-12__attribute {
      font-size: 11px !important;
      font-weight: 600 !important;
      padding: 2px 8px !important;
      border-radius: 999px !important;
      letter-spacing: .04em !important;
    }
    .swagger-ui .json-schema-2020-12__attribute--primary {
      background: rgba(99,102,241,.18) !important;
      color: #818cf8 !important;
      border: 1px solid rgba(99,102,241,.3) !important;
    }
    .swagger-ui .json-schema-2020-12-expand-deep-button {
      font-size: 11px !important;
      font-weight: 600 !important;
      color: #6b7280 !important;
      background: none !important;
      border: 1px solid rgba(255,255,255,.1) !important;
      border-radius: 6px !important;
      padding: 3px 10px !important;
      cursor: pointer !important;
      transition: color .15s, border-color .15s !important;
    }
    .swagger-ui .json-schema-2020-12-expand-deep-button:hover {
      color: #c7d2fe !important;
      border-color: rgba(99,102,241,.4) !important;
    }
    .swagger-ui .json-schema-2020-12-body {
      padding: 0 16px 14px !important;
    }
    .swagger-ui .json-schema-2020-12-body--collapsed { display: none !important; }
    /* Nested schema key/value rows */
    .swagger-ui .json-schema-2020-12 table,
    .swagger-ui .json-schema-2020-12 tr,
    .swagger-ui .json-schema-2020-12 td,
    .swagger-ui .json-schema-2020-12 th {
      background: transparent !important;
      border-color: rgba(255,255,255,.06) !important;
      color: #d1d5db !important;
      font-size: 13px !important;
    }
    .swagger-ui .json-schema-2020-12 .json-schema-2020-12__title {
      color: #fde68a !important;
      font-weight: 600 !important;
      font-size: 13px !important;
    }
    .swagger-ui .json-schema-2020-12 .json-schema-2020-12__attribute--secondary {
      background: rgba(20,184,166,.14) !important;
      color: #5eead4 !important;
      border: 1px solid rgba(20,184,166,.25) !important;
    }
    .swagger-ui section.models .models-control {
      color: #c7d2fe !important;
      font-weight: 700 !important;
      font-size: 15px !important;
      background: none !important;
      border: none !important;
      cursor: pointer !important;
      display: flex !important;
      align-items: center !important;
      gap: 8px !important;
      padding: 12px 0 !important;
    }
    .swagger-ui section.models .models-control svg { fill: #6b7280 !important; }
    .swagger-ui section.models .no-margin { padding-top: 4px !important; }

    /* ── Execute button & inputs ── */
    .swagger-ui .btn.execute {
      background: #4f46e5 !important;
      border-color: #4f46e5 !important;
      border-radius: 8px !important;
      font-weight: 600 !important;
      font-size: 13px !important;
      color: #fff !important;
      padding: 8px 20px !important;
      transition: background .15s !important;
    }
    .swagger-ui .btn.execute:hover { background: #4338ca !important; }
    .swagger-ui .btn.try-out__btn {
      border-radius: 7px !important;
      font-weight: 600 !important;
      font-size: 12px !important;
      border-color: rgba(99,102,241,.5) !important;
      color: #a5b4fc !important;
    }
    .swagger-ui .btn.authorize {
      border-color: #22c55e !important;
      border-radius: 8px !important;
      color: #86efac !important;
      font-weight: 600 !important;
    }
    .swagger-ui input[type=text], .swagger-ui input[type=password],
    .swagger-ui textarea, .swagger-ui select {
      background: rgba(255,255,255,.05) !important;
      border: 1px solid rgba(255,255,255,.12) !important;
      border-radius: 8px !important;
      color: #e2e4ef !important;
      font-family: 'Inter', sans-serif !important;
      font-size: 13px !important;
      padding: 8px 12px !important;
    }
    .swagger-ui input[type=text]:focus, .swagger-ui textarea:focus {
      border-color: #6366f1 !important;
      outline: none !important;
      box-shadow: 0 0 0 2px rgba(99,102,241,.25) !important;
    }

    /* ── Response codes ── */
    .swagger-ui .responses-inner h4,
    .swagger-ui .responses-inner h5 { color: #9ca3af !important; font-size: 12px !important; }
    .swagger-ui .response-col_status { color: #6ee7b7 !important; font-weight: 700 !important; }
    .swagger-ui .response-col_description { color: #d1d5db !important; }
    .swagger-ui .highlight-code pre, .swagger-ui .microlight {
      background: rgba(0,0,0,.35) !important;
      border-radius: 8px !important;
      padding: 12px !important;
      color: #a5f3fc !important;
      font-size: 12.5px !important;
    }

    /* ── Scheme selector ── */
    .swagger-ui .scheme-container {
      background: rgba(255,255,255,.03) !important;
      border: 1px solid rgba(255,255,255,.08) !important;
      border-radius: 10px !important;
      padding: 16px 20px !important;
      margin-bottom: 20px !important;
      box-shadow: none !important;
    }
    .swagger-ui .scheme-container .schemes > label {
      color: #9ca3af !important;
      font-size: 12px !important;
      font-weight: 600 !important;
    }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,.12); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,.22); }

    /* ── Misc ── */
    .swagger-ui .markdown p, .swagger-ui .markdown li { color: #9ca3af !important; }
    .swagger-ui .label { color: #c7d2fe !important; }
    .swagger-ui span.prop-name { color: #fde68a !important; }

    @media (max-width: 640px) {
      #swagger-ui { padding: 16px 10px 60px; }
      .smt-topbar  { padding: 0 14px; }
      .smt-logo    { font-size: 13px; }
      .smt-nav-btn { padding: 5px 10px; font-size: 12px; }
    }
  </style>
</head>
<body>

  <!-- Custom top bar -->
  <div class="smt-topbar">
    <div class="smt-topbar-left">
      <span class="smt-logo">⬛ Smart Money Tracker</span>
      <span class="smt-badge">API v2.0</span>
    </div>
    <div class="smt-topbar-right">
      <a href="/" class="smt-nav-btn">← Dashboard</a>
      <a href="/redoc" class="smt-nav-btn">ReDoc</a>
    </div>
  </div>

  <div id="swagger-ui"></div>

  <script src="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({
      url: '/openapi.json',
      dom_id: '#swagger-ui',
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: 'BaseLayout',
      deepLinking: true,
      filter: true,
      tryItOutEnabled: false,
      defaultModelsExpandDepth: 1,
      defaultModelExpandDepth: 2,
      docExpansion: 'list',
      syntaxHighlight: { theme: 'monokai' },
    });
  </script>
</body>
</html>"""


_REDOC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Smart Money Tracker — ReDoc</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; background: #0b0c18; font-family: 'Inter', sans-serif; }

    .smt-topbar {
      position: sticky; top: 0; z-index: 100;
      background: rgba(11,12,24,.92); backdrop-filter: blur(14px);
      border-bottom: 1px solid rgba(255,255,255,.07);
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 24px; height: 56px; gap: 12px;
    }
    .smt-topbar-left { display: flex; align-items: center; gap: 12px; }
    .smt-logo { font-size: 15px; font-weight: 700; color: #a5b4fc; }
    .smt-badge {
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
      background: rgba(99,102,241,.18); border: 1px solid rgba(99,102,241,.35); color: #818cf8;
    }
    .smt-nav-btn {
      padding: 6px 14px; border-radius: 8px; font-size: 13px; font-weight: 500;
      text-decoration: none; border: 1px solid rgba(255,255,255,.1); color: #9ca3af;
      background: transparent; transition: border-color .15s, color .15s, background .15s;
    }
    .smt-nav-btn:hover { color: #e2e4ef; background: rgba(255,255,255,.06); border-color: rgba(255,255,255,.18); }

    redoc { display: block; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,.12); border-radius: 3px; }
  </style>
</head>
<body>
  <div class="smt-topbar">
    <div class="smt-topbar-left">
      <span class="smt-logo">⬛ Smart Money Tracker</span>
      <span class="smt-badge">API v2.0</span>
    </div>
    <div style="display:flex;gap:8px;">
      <a href="/" class="smt-nav-btn">← Dashboard</a>
      <a href="/docs" class="smt-nav-btn">Swagger UI</a>
    </div>
  </div>
  <div id="redoc-container"></div>
  <script src="https://cdn.jsdelivr.net/npm/redoc@2.2.0/bundles/redoc.standalone.js"></script>
  <script>
    Redoc.init('/openapi.json', {
      theme: {
        colors: { primary: { main: '#6366f1' } },
        typography: {
          fontFamily: "'Inter', system-ui, sans-serif",
          headings: { fontFamily: "'Inter', system-ui, sans-serif", fontWeight: '700' },
          code: { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: '13px' },
        },
        sidebar: { backgroundColor: '#0d0e1d', textColor: '#9ca3af', activeTextColor: '#a5b4fc' },
        rightPanel: { backgroundColor: '#06070f' },
        schema: { typeNameColor: '#6ee7b7', typeTitleColor: '#fde68a' },
      },
      expandResponses: '200',
      hideDownloadButton: false,
      nativeScrollbars: false,
    }, document.getElementById('redoc-container'));
  </script>
</body>
</html>"""


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def custom_docs() -> HTMLResponse:
    return HTMLResponse(content=_DOCS_HTML)


@app.get("/redoc", response_class=HTMLResponse, include_in_schema=False)
async def custom_redoc() -> HTMLResponse:
    return HTMLResponse(content=_REDOC_HTML)


@app.get("/health", tags=["System"])
async def health() -> dict:
    configured = active_chains()
    return {
        "status": "ok",
        "whale_threshold_usd": settings.whale_threshold_usd,
        "chains": {
            name: {
                "configured": name in configured,
                "poll_interval": CHAINS[name].poll_interval,
                "emoji": CHAINS[name].emoji,
            }
            for name in CHAINS
        },
        "broadcasters": event_dispatcher.plugin_status,
    }


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools() -> dict:
    """Silences Chrome DevTools probe — returns empty valid JSON."""
    return {}
