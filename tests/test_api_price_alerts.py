"""
tests/test_api_price_alerts.py
-------------------------------
CRUD tests for POST/GET/DELETE/PATCH /api/v1/price-alerts.
"""

_TOKEN_ADDR = "0x" + "a" * 40

_RULE = {
    "chain": "ethereum",
    "token_address": _TOKEN_ADDR,
    "token_symbol": "usdc",     # router uppercases this
    "condition": "above",
    "target_price_usd": 1.05,
    "label": "USDC depeg alert",
}


# ── POST /api/v1/price-alerts ──────────────────────────────────────────────────

async def test_create_rule_returns_201(client):
    r = await client.post("/api/v1/price-alerts", json=_RULE)
    assert r.status_code == 201


async def test_create_rule_response_fields(client):
    data = (await client.post("/api/v1/price-alerts", json=_RULE)).json()
    assert data["token_symbol"] == "USDC"          # uppercased by the router
    assert data["token_address"] == _TOKEN_ADDR    # lowercased by the router
    assert data["condition"] == "above"
    assert data["target_price_usd"] == 1.05
    assert data["is_active"] is True
    assert data["label"] == "USDC depeg alert"
    assert "id" in data
    assert "created_at" in data


async def test_condition_below_accepted(client):
    rule = {**_RULE, "condition": "below", "target_price_usd": 0.95}
    r = await client.post("/api/v1/price-alerts", json=rule)
    assert r.status_code == 201


async def test_invalid_condition_rejected(client):
    r = await client.post("/api/v1/price-alerts", json={**_RULE, "condition": "equal"})
    assert r.status_code == 422


async def test_zero_price_rejected(client):
    r = await client.post("/api/v1/price-alerts", json={**_RULE, "target_price_usd": 0})
    assert r.status_code == 422


async def test_unknown_chain_rejected(client):
    r = await client.post("/api/v1/price-alerts", json={**_RULE, "chain": "avalanche"})
    assert r.status_code == 400


# ── GET /api/v1/price-alerts ───────────────────────────────────────────────────

async def test_list_rules_empty(client):
    r = await client.get("/api/v1/price-alerts")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_rules_returns_created(client):
    await client.post("/api/v1/price-alerts", json=_RULE)
    rules = (await client.get("/api/v1/price-alerts")).json()
    assert len(rules) == 1
    assert rules[0]["token_symbol"] == "USDC"


async def test_list_rules_filter_by_chain(client):
    await client.post("/api/v1/price-alerts", json=_RULE)
    r = await client.get("/api/v1/price-alerts", params={"chain": "base"})
    assert r.status_code == 200
    assert r.json() == []


# ── GET /api/v1/price-alerts/{id} ─────────────────────────────────────────────

async def test_get_rule_by_id(client):
    rule_id = (await client.post("/api/v1/price-alerts", json=_RULE)).json()["id"]
    r = await client.get(f"/api/v1/price-alerts/{rule_id}")
    assert r.status_code == 200
    assert r.json()["id"] == rule_id


async def test_get_nonexistent_rule_returns_404(client):
    r = await client.get("/api/v1/price-alerts/9999")
    assert r.status_code == 404


# ── DELETE /api/v1/price-alerts/{id} ──────────────────────────────────────────

async def test_delete_rule_returns_204(client):
    rule_id = (await client.post("/api/v1/price-alerts", json=_RULE)).json()["id"]
    r = await client.delete(f"/api/v1/price-alerts/{rule_id}")
    assert r.status_code == 204


async def test_deleted_rule_not_returned_in_list(client):
    rule_id = (await client.post("/api/v1/price-alerts", json=_RULE)).json()["id"]
    await client.delete(f"/api/v1/price-alerts/{rule_id}")
    rules = (await client.get("/api/v1/price-alerts")).json()
    assert all(r["id"] != rule_id for r in rules)


async def test_delete_nonexistent_rule_returns_404(client):
    r = await client.delete("/api/v1/price-alerts/9999")
    assert r.status_code == 404


# ── PATCH /api/v1/price-alerts/{id}/toggle ────────────────────────────────────

async def test_toggle_deactivates_active_rule(client):
    rule_id = (await client.post("/api/v1/price-alerts", json=_RULE)).json()["id"]
    r = await client.patch(f"/api/v1/price-alerts/{rule_id}/toggle")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


async def test_toggle_twice_restores_active(client):
    rule_id = (await client.post("/api/v1/price-alerts", json=_RULE)).json()["id"]
    await client.patch(f"/api/v1/price-alerts/{rule_id}/toggle")
    r = await client.patch(f"/api/v1/price-alerts/{rule_id}/toggle")
    assert r.json()["is_active"] is True
