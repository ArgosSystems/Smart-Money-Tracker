"""
tests/test_api_alerts.py
-------------------------
Tests for GET /api/v1/alerts and related endpoints.

Row seeding is done directly via db_session so tests aren't coupled to the
whale scanner internals.
"""

from api.models import TrackedWallet, WhaleAlert

_TX = "0x" + "ab" * 32          # valid 66-char tx hash
_FROM = "0x" + "1" * 40
_TO   = "0x" + "2" * 40
_TOKEN = "0x" + "3" * 40


async def _seed_alert(db_session, chain="ethereum", direction="BUY", symbol="USDC"):
    """Insert one TrackedWallet + one WhaleAlert and commit both."""
    wallet = TrackedWallet(address=_FROM, chain=chain, is_active=True)
    db_session.add(wallet)
    await db_session.flush()   # get wallet.id without closing the session

    alert = WhaleAlert(
        wallet_id=wallet.id,
        chain=chain,
        tx_hash=_TX,
        from_address=_FROM,
        to_address=_TO,
        token_symbol=symbol,
        token_address=_TOKEN,
        amount_token=50_000.0,
        amount_usd=50_000.0,
        direction=direction,
        block_number=1_000,
    )
    db_session.add(alert)
    await db_session.commit()
    return alert


# ── GET /api/v1/alerts ─────────────────────────────────────────────────────────

async def test_alerts_empty_on_fresh_db(client):
    r = await client.get("/api/v1/alerts")
    assert r.status_code == 200
    assert r.json() == []


async def test_alerts_returns_seeded_row(client, db_session):
    await _seed_alert(db_session)
    r = await client.get("/api/v1/alerts")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["token_symbol"] == "USDC"
    assert data[0]["direction"] == "BUY"
    assert data[0]["amount_usd"] == 50_000.0


async def test_alerts_filter_by_chain(client, db_session):
    await _seed_alert(db_session, chain="ethereum")
    r = await client.get("/api/v1/alerts", params={"chain": "solana"})
    assert r.status_code == 200
    assert r.json() == []


async def test_alerts_filter_by_direction(client, db_session):
    await _seed_alert(db_session, direction="SELL")
    r = await client.get("/api/v1/alerts", params={"direction": "BUY"})
    assert r.status_code == 200
    assert r.json() == []


async def test_alerts_response_has_required_fields(client, db_session):
    await _seed_alert(db_session)
    alert = (await client.get("/api/v1/alerts")).json()[0]
    required = {
        "id", "chain", "tx_hash", "from_address", "to_address",
        "token_symbol", "amount_token", "amount_usd", "direction",
        "block_number", "detected_at",
    }
    assert required.issubset(alert.keys())


async def test_alerts_limit_parameter(client, db_session):
    # Seed two wallets + two alerts with different tx hashes
    for i in range(2):
        wallet = TrackedWallet(address="0x" + str(i) * 40, chain="ethereum", is_active=True)
        db_session.add(wallet)
        await db_session.flush()
        db_session.add(WhaleAlert(
            wallet_id=wallet.id,
            chain="ethereum",
            tx_hash="0x" + str(i) * 64,
            from_address="0x" + str(i) * 40,
            to_address=_TO,
            token_symbol="ETH",
            amount_token=10.0,
            amount_usd=30_000.0,
            direction="SEND",
            block_number=100 + i,
        ))
    await db_session.commit()

    r = await client.get("/api/v1/alerts", params={"limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── GET /api/v1/alerts/token/{token} ──────────────────────────────────────────

async def test_token_alerts_by_symbol(client, db_session):
    await _seed_alert(db_session, symbol="PEPE")
    r = await client.get("/api/v1/alerts/token/PEPE")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["token_symbol"] == "PEPE"


async def test_token_alerts_by_address(client, db_session):
    await _seed_alert(db_session)
    r = await client.get(f"/api/v1/alerts/token/{_TOKEN}")
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_token_alerts_no_match_returns_empty(client, db_session):
    await _seed_alert(db_session, symbol="USDC")
    r = await client.get("/api/v1/alerts/token/DOGE")
    assert r.status_code == 200
    assert r.json() == []


# ── GET /health ────────────────────────────────────────────────────────────────

async def test_health_endpoint_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "chains" in data
    assert "solana" in data["chains"]
    assert "whale_threshold_usd" in data
