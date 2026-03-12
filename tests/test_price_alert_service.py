"""
tests/test_price_alert_service.py
----------------------------------
Unit tests for PriceAlertChecker, fetch_token_price, and fetch_prices_batch.

All CoinGecko HTTP calls and DB access are mocked — no real network or database.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from api.models import PriceAlertRule
from api.services.price_alerts import (
    PriceAlertChecker,
    fetch_prices_batch,
    fetch_token_price,
)

_ADDR = "0x" + "a" * 40
_ADDR_LOWER = _ADDR.lower()


# ── fetch_token_price ─────────────────────────────────────────────────────────

async def test_fetch_token_price_returns_price():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {_ADDR_LOWER: {"usd": 1.05}}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client

    with patch("api.services.price_alerts.httpx.AsyncClient", return_value=mock_client):
        price = await fetch_token_price(_ADDR, coingecko_platform="ethereum")

    assert price == 1.05


async def test_fetch_token_price_returns_zero_on_http_error():
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("connection refused")
    mock_client.__aenter__.return_value = mock_client

    with patch("api.services.price_alerts.httpx.AsyncClient", return_value=mock_client):
        price = await fetch_token_price(_ADDR)

    assert price == 0.0


async def test_fetch_token_price_returns_zero_when_token_missing_from_response():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {}  # token not in response

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client

    with patch("api.services.price_alerts.httpx.AsyncClient", return_value=mock_client):
        price = await fetch_token_price(_ADDR)

    assert price == 0.0


# ── fetch_prices_batch ────────────────────────────────────────────────────────

async def test_fetch_prices_batch_returns_empty_for_no_addresses():
    # Should not make any HTTP call
    result = await fetch_prices_batch([], coingecko_platform="ethereum")
    assert result == {}


async def test_fetch_prices_batch_returns_mapped_prices():
    addr2 = "0x" + "b" * 40
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        _ADDR_LOWER: {"usd": 1.0},
        addr2.lower(): {"usd": 2500.0},
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client

    with patch("api.services.price_alerts.httpx.AsyncClient", return_value=mock_client):
        prices = await fetch_prices_batch([_ADDR, addr2], "ethereum")

    assert prices[_ADDR_LOWER] == 1.0
    assert prices[addr2.lower()] == 2500.0


async def test_fetch_prices_batch_returns_empty_on_http_error():
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("timeout")
    mock_client.__aenter__.return_value = mock_client

    with patch("api.services.price_alerts.httpx.AsyncClient", return_value=mock_client):
        prices = await fetch_prices_batch([_ADDR], "ethereum")

    assert prices == {}


# ── PriceAlertChecker._check_all helpers ──────────────────────────────────────

def _make_rule(**kwargs) -> PriceAlertRule:
    """Build a detached PriceAlertRule for unit-testing without a live DB."""
    rule = PriceAlertRule(
        chain=kwargs.get("chain", "ethereum"),
        token_address=kwargs.get("token_address", _ADDR),
        token_symbol=kwargs.get("token_symbol", "USDC"),
        condition=kwargs.get("condition", "above"),
        target_price_usd=kwargs.get("target_price_usd", 1.0),
        is_active=kwargs.get("is_active", True),
        label=kwargs.get("label", "test alert"),
    )
    rule.id = kwargs.get("id", 1)
    rule.last_triggered_at = kwargs.get("last_triggered_at", None)
    return rule


def _mock_db_factory(rules: list[PriceAlertRule]) -> MagicMock:
    """
    Return a mock that behaves like AsyncSessionLocal() used as an async
    context manager, yielding a session whose execute() returns `rules`.
    """
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rules

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.get.return_value = rules[0] if rules else None
    mock_session.add = MagicMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session

    return MagicMock(return_value=mock_cm)


# ── PriceAlertChecker._check_all tests ────────────────────────────────────────

async def test_check_all_no_rules_skips_price_fetch():
    factory = _mock_db_factory([])
    checker = PriceAlertChecker()

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch(
            "api.services.price_alerts.fetch_prices_batch",
            new=AsyncMock(return_value={}),
        ) as mock_fetch,
    ):
        await checker._check_all()

    mock_fetch.assert_not_called()


async def test_check_all_triggers_above_condition():
    rule = _make_rule(condition="above", target_price_usd=1.0)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 1.05}  # price is above target → should trigger

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_called_once()
    payload = mock_broadcaster.publish.call_args[0][0]
    assert payload["type"] == "price_alert"
    assert payload["token_symbol"] == "USDC"
    assert payload["current_price_usd"] == 1.05
    assert payload["condition"] == "above"


async def test_check_all_triggers_below_condition():
    rule = _make_rule(condition="below", target_price_usd=1.0)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 0.95}  # price is below target → should trigger

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_called_once()


async def test_check_all_does_not_trigger_when_condition_not_met():
    rule = _make_rule(condition="above", target_price_usd=2.0)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 1.05}  # price is below the target of 2.0

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_not_called()


async def test_check_all_skips_rule_in_cooldown():
    """Rule triggered 100 s ago is still within the 3600 s cooldown."""
    recent = datetime.datetime.utcnow() - datetime.timedelta(seconds=100)
    rule = _make_rule(condition="above", target_price_usd=1.0, last_triggered_at=recent)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 1.05}

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_not_called()


async def test_check_all_triggers_after_cooldown_expires():
    """Rule triggered 2 hours ago is outside cooldown — should fire again."""
    old = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
    rule = _make_rule(condition="above", target_price_usd=1.0, last_triggered_at=old)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 1.05}

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_called_once()


async def test_check_all_skips_zero_price():
    """A zero price from CoinGecko (fetch failure) must not fire an alert."""
    rule = _make_rule(condition="above", target_price_usd=0.5)
    factory = _mock_db_factory([rule])
    checker = PriceAlertChecker()

    prices = {_ADDR_LOWER: 0.0}  # 0.0 = price unavailable

    with (
        patch("api.services.price_alerts.AsyncSessionLocal", factory),
        patch("api.services.price_alerts.fetch_prices_batch", new=AsyncMock(return_value=prices)),
        patch("api.services.price_alerts.alert_broadcaster") as mock_broadcaster,
    ):
        mock_broadcaster.publish = AsyncMock()
        await checker._check_all()

    mock_broadcaster.publish.assert_not_called()
