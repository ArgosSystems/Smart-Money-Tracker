"""
tests/test_api_wallets.py
--------------------------
Integration tests for wallet tracking and chain endpoints.

These hit a real in-memory SQLite database via the FastAPI test client,
so they verify the full request→validation→DB round-trip.
"""

# ── Sample addresses ───────────────────────────────────────────────────────────

# Real Ethereum address (Vitalik's public wallet)
_ETH_ADDR = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

# Real Solana address — Wrapped BTC mint (base58, 44 chars, case-sensitive)
_SOL_ADDR = "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E"


# ── POST /api/v1/wallets/track ─────────────────────────────────────────────────

async def test_track_valid_evm_wallet_returns_201(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "ethereum"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["address"] == _ETH_ADDR.lower()
    assert data["chain"] == "ethereum"
    assert data["is_active"] is True


async def test_track_evm_wallet_normalises_address_to_lowercase(client):
    upper = _ETH_ADDR.upper().replace("0X", "0x")
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": upper, "chain": "base"},
    )
    assert r.status_code == 201
    assert r.json()["address"] == _ETH_ADDR.lower()


async def test_track_valid_solana_wallet_returns_201(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _SOL_ADDR, "chain": "solana"},
    )
    assert r.status_code == 201
    data = r.json()
    # Solana addresses are case-sensitive and must NOT be lowercased
    assert data["address"] == _SOL_ADDR
    assert data["chain"] == "solana"
    assert data["is_active"] is True


async def test_track_wallet_with_label(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "ethereum", "label": "Vitalik"},
    )
    assert r.status_code == 201
    assert r.json()["label"] == "Vitalik"


async def test_track_wallet_is_idempotent(client):
    payload = {"address": _ETH_ADDR, "chain": "ethereum"}
    r1 = await client.post("/api/v1/wallets/track", json=payload)
    r2 = await client.post("/api/v1/wallets/track", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Same DB row returned — IDs must match
    assert r1.json()["id"] == r2.json()["id"]


async def test_same_address_tracked_on_different_chains(client):
    r1 = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "ethereum"},
    )
    r2 = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "base"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Separate rows — different IDs
    assert r1.json()["id"] != r2.json()["id"]


# ── Address format validation ──────────────────────────────────────────────────

async def test_invalid_address_rejected(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": "not-an-address", "chain": "ethereum"},
    )
    assert r.status_code == 422


async def test_evm_address_on_solana_chain_rejected(client):
    # 0x addresses don't match the base58 pattern
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "solana"},
    )
    assert r.status_code == 422


async def test_solana_address_on_evm_chain_rejected(client):
    # base58 addresses don't start with 0x
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _SOL_ADDR, "chain": "ethereum"},
    )
    assert r.status_code == 422


async def test_unknown_chain_rejected(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "fantom"},
    )
    assert r.status_code == 422


async def test_short_hex_address_rejected(client):
    r = await client.post(
        "/api/v1/wallets/track",
        json={"address": "0xdeadbeef", "chain": "ethereum"},
    )
    assert r.status_code == 422


# ── DELETE /api/v1/wallets/{address} ──────────────────────────────────────────

async def test_untrack_wallet_soft_deletes(client):
    await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "ethereum"},
    )
    r = await client.delete(
        f"/api/v1/wallets/{_ETH_ADDR.lower()}",
        params={"chain": "ethereum"},
    )
    assert r.status_code == 200

    wallets = (await client.get("/api/v1/wallets")).json()
    assert not any(
        w["address"] == _ETH_ADDR.lower() and w["is_active"]
        for w in wallets
    )


async def test_untrack_nonexistent_wallet_returns_404(client):
    r = await client.delete(
        "/api/v1/wallets/0x" + "0" * 40,
        params={"chain": "ethereum"},
    )
    assert r.status_code == 404


# ── GET /api/v1/wallets ────────────────────────────────────────────────────────

async def test_list_wallets_initially_empty(client):
    r = await client.get("/api/v1/wallets")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_wallets_after_tracking(client):
    await client.post(
        "/api/v1/wallets/track",
        json={"address": _ETH_ADDR, "chain": "ethereum"},
    )
    wallets = (await client.get("/api/v1/wallets")).json()
    assert len(wallets) == 1
    assert wallets[0]["address"] == _ETH_ADDR.lower()


async def test_list_wallets_filter_by_chain(client):
    await client.post("/api/v1/wallets/track", json={"address": _ETH_ADDR, "chain": "ethereum"})
    await client.post("/api/v1/wallets/track", json={"address": _ETH_ADDR, "chain": "base"})

    eth_only = (await client.get("/api/v1/wallets", params={"chain": "ethereum"})).json()
    assert all(w["chain"] == "ethereum" for w in eth_only)


# ── GET /api/v1/chains ─────────────────────────────────────────────────────────

async def test_list_chains_returns_all_chains(client):
    r = await client.get("/api/v1/chains")
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert names == {"ethereum", "base", "arbitrum", "bsc", "polygon", "optimism", "solana"}


async def test_solana_chain_has_correct_metadata(client):
    r = await client.get("/api/v1/chains")
    sol = next(c for c in r.json() if c["name"] == "solana")
    assert sol["native_token"] == "SOL"
    assert sol["explorer"] == "solscan.io"
    assert sol["block_time"] == 0.4
