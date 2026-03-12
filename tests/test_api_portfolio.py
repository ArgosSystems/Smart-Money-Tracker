"""
tests/test_api_portfolio.py
----------------------------
CRUD tests for POST/GET/DELETE/PATCH /api/v1/portfolio/wallets and snapshots.
"""

from unittest.mock import AsyncMock, patch

_ADDR = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
_ADDR_LOWER = _ADDR.lower()

_WALLET = {
    "address": _ADDR,
    "chain": "ethereum",
    "label": "Vitalik",
}


# ── POST /api/v1/portfolio/wallets ─────────────────────────────────────────────

async def test_add_wallet_returns_201(client):
    r = await client.post("/api/v1/portfolio/wallets", json=_WALLET)
    assert r.status_code == 201


async def test_add_wallet_response_fields(client):
    data = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()
    assert data["address"] == _ADDR_LOWER
    assert data["chain"] == "ethereum"
    assert data["label"] == "Vitalik"
    assert data["is_active"] is True
    assert "id" in data
    assert "added_at" in data


async def test_add_wallet_without_label(client):
    payload = {"address": _ADDR, "chain": "ethereum"}
    r = await client.post("/api/v1/portfolio/wallets", json=payload)
    assert r.status_code == 201
    assert r.json()["label"] is None


async def test_add_wallet_duplicate_returns_409(client):
    await client.post("/api/v1/portfolio/wallets", json=_WALLET)
    r = await client.post("/api/v1/portfolio/wallets", json=_WALLET)
    assert r.status_code == 409


async def test_add_wallet_same_address_different_chain_ok(client):
    r1 = await client.post("/api/v1/portfolio/wallets", json={**_WALLET, "chain": "ethereum"})
    r2 = await client.post("/api/v1/portfolio/wallets", json={**_WALLET, "chain": "base"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


async def test_add_wallet_unknown_chain_returns_400(client):
    r = await client.post("/api/v1/portfolio/wallets", json={**_WALLET, "chain": "fantom"})
    assert r.status_code == 400


# ── GET /api/v1/portfolio/wallets ──────────────────────────────────────────────

async def test_list_wallets_empty(client):
    r = await client.get("/api/v1/portfolio/wallets")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_wallets_returns_created(client):
    await client.post("/api/v1/portfolio/wallets", json=_WALLET)
    wallets = (await client.get("/api/v1/portfolio/wallets")).json()
    assert len(wallets) == 1
    assert wallets[0]["address"] == _ADDR_LOWER


async def test_list_wallets_filter_by_chain(client):
    await client.post("/api/v1/portfolio/wallets", json=_WALLET)
    r = await client.get("/api/v1/portfolio/wallets", params={"chain": "solana"})
    assert r.status_code == 200
    assert r.json() == []


async def test_list_wallets_active_only_filter(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    # Deactivate it
    await client.patch(f"/api/v1/portfolio/wallets/{wallet_id}/toggle")
    r = await client.get("/api/v1/portfolio/wallets", params={"active_only": True})
    assert r.status_code == 200
    assert r.json() == []


# ── GET /api/v1/portfolio/wallets/{id} ────────────────────────────────────────

async def test_get_wallet_by_id(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    r = await client.get(f"/api/v1/portfolio/wallets/{wallet_id}")
    assert r.status_code == 200
    assert r.json()["id"] == wallet_id


async def test_get_nonexistent_wallet_returns_404(client):
    r = await client.get("/api/v1/portfolio/wallets/9999")
    assert r.status_code == 404


# ── DELETE /api/v1/portfolio/wallets/{id} ─────────────────────────────────────

async def test_delete_wallet_returns_204(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    r = await client.delete(f"/api/v1/portfolio/wallets/{wallet_id}")
    assert r.status_code == 204


async def test_deleted_wallet_not_returned_in_list(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    await client.delete(f"/api/v1/portfolio/wallets/{wallet_id}")
    wallets = (await client.get("/api/v1/portfolio/wallets")).json()
    assert all(w["id"] != wallet_id for w in wallets)


async def test_delete_nonexistent_wallet_returns_404(client):
    r = await client.delete("/api/v1/portfolio/wallets/9999")
    assert r.status_code == 404


# ── PATCH /api/v1/portfolio/wallets/{id}/toggle ───────────────────────────────

async def test_toggle_deactivates_active_wallet(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    r = await client.patch(f"/api/v1/portfolio/wallets/{wallet_id}/toggle")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


async def test_toggle_twice_restores_active(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    await client.patch(f"/api/v1/portfolio/wallets/{wallet_id}/toggle")
    r = await client.patch(f"/api/v1/portfolio/wallets/{wallet_id}/toggle")
    assert r.json()["is_active"] is True


async def test_toggle_nonexistent_wallet_returns_404(client):
    r = await client.patch("/api/v1/portfolio/wallets/9999/toggle")
    assert r.status_code == 404


# ── GET /api/v1/portfolio/wallets/{id}/snapshots ──────────────────────────────

async def test_snapshots_empty_initially(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    r = await client.get(f"/api/v1/portfolio/wallets/{wallet_id}/snapshots")
    assert r.status_code == 200
    assert r.json() == []


async def test_snapshots_nonexistent_wallet_returns_404(client):
    r = await client.get("/api/v1/portfolio/wallets/9999/snapshots")
    assert r.status_code == 404


# ── GET /api/v1/portfolio/wallets/{id}/balance ────────────────────────────────

async def test_balance_returns_503_when_chain_not_configured(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    # fetch_wallet_balance raises ValueError when no RPC URL is configured
    with patch(
        "api.routers.portfolio.fetch_wallet_balance",
        new=AsyncMock(side_effect=ValueError("No RPC URL configured for ethereum")),
    ):
        r = await client.get(f"/api/v1/portfolio/wallets/{wallet_id}/balance")
    assert r.status_code == 503


async def test_balance_saves_snapshot(client):
    wallet_id = (await client.post("/api/v1/portfolio/wallets", json=_WALLET)).json()["id"]
    mock_data = {
        "address": _ADDR_LOWER,
        "chain": "ethereum",
        "native_symbol": "ETH",
        "native_balance": 1.5,
        "native_price_usd": 3_000.0,
        "total_usd": 4_500.0,
    }
    with patch(
        "api.routers.portfolio.fetch_wallet_balance",
        new=AsyncMock(return_value=mock_data),
    ):
        r = await client.get(f"/api/v1/portfolio/wallets/{wallet_id}/balance")
    assert r.status_code == 200
    data = r.json()
    assert data["native_balance"] == 1.5
    assert data["native_symbol"] == "ETH"

    # Snapshot should now exist
    snaps = (await client.get(f"/api/v1/portfolio/wallets/{wallet_id}/snapshots")).json()
    assert len(snaps) == 1
    assert snaps[0]["native_balance"] == 1.5
