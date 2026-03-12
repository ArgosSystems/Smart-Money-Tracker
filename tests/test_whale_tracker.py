"""
tests/test_whale_tracker.py
-----------------------------
Unit tests for EvmChainScanner and MultiChainTracker.

All web3 RPC calls and DB access are mocked — no real network connections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from config.chains import CHAINS
from api.services.whale_tracker import EvmChainScanner, MultiChainTracker


# ── EvmChainScanner.is_healthy ────────────────────────────────────────────────

async def test_evm_scanner_is_healthy_returns_true():
    scanner = EvmChainScanner("ethereum", CHAINS["ethereum"], "http://fake-rpc")

    async def _block_number():
        return 100

    mock_w3 = MagicMock()
    mock_w3.eth.block_number = _block_number()
    scanner._w3 = mock_w3

    assert await scanner.is_healthy() is True


async def test_evm_scanner_is_healthy_returns_false_on_rpc_error():
    scanner = EvmChainScanner("ethereum", CHAINS["ethereum"], "http://fake-rpc")

    async def _block_number():
        raise ConnectionError("RPC unreachable")

    mock_w3 = MagicMock()
    mock_w3.eth.block_number = _block_number()
    scanner._w3 = mock_w3

    assert await scanner.is_healthy() is False


async def test_evm_scanner_is_healthy_returns_false_on_timeout():
    """Simulates an RPC that hangs — wait_for timeout → is_healthy returns False."""
    import asyncio

    scanner = EvmChainScanner("ethereum", CHAINS["ethereum"], "http://fake-rpc")

    async def _hang():
        await asyncio.sleep(10)   # longer than is_healthy's 5 s timeout

    mock_w3 = MagicMock()
    mock_w3.eth.block_number = _hang()
    scanner._w3 = mock_w3

    # Override internal timeout to 0.01 s so the test runs fast
    with patch("api.services.whale_tracker.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await scanner.is_healthy()

    assert result is False


# ── EvmChainScanner.get_latest_block ──────────────────────────────────────────

async def test_evm_scanner_get_latest_block_returns_block_number():
    scanner = EvmChainScanner("ethereum", CHAINS["ethereum"], "http://fake-rpc")

    async def _block_number():
        return 12_345_678

    mock_w3 = MagicMock()
    mock_w3.eth.block_number = _block_number()
    scanner._w3 = mock_w3

    assert await scanner.get_latest_block() == 12_345_678


# ── EvmChainScanner.scan_block — no wallets ───────────────────────────────────

async def test_evm_scan_block_returns_empty_when_no_wallets():
    """scan_block exits early with [] when no wallets are tracked on this chain."""
    scanner = EvmChainScanner("ethereum", CHAINS["ethereum"], "http://fake-rpc")

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session

    with patch("api.services.whale_tracker.AsyncSessionLocal", MagicMock(return_value=mock_cm)):
        alerts = await scanner.scan_block(100)

    assert alerts == []


# ── MultiChainTracker._build_scanners ─────────────────────────────────────────

def test_build_scanners_empty_when_no_rpc_urls_configured():
    """No scanners are created when all chains return empty RPC URLs."""
    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.return_value = ""
        tracker = MultiChainTracker()

    assert tracker.scanners == {}


def test_build_scanners_creates_evm_scanner_for_configured_chain():
    """EvmChainScanner is registered for ethereum when a URL is available."""
    def _fake_rpc(chain: str) -> str:
        return "https://eth-mainnet.g.alchemy.com/v2/key" if chain == "ethereum" else ""

    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.side_effect = _fake_rpc
        tracker = MultiChainTracker()

    assert "ethereum" in tracker.scanners
    assert isinstance(tracker.scanners["ethereum"], EvmChainScanner)


def test_build_scanners_skips_chains_without_rpc_url():
    """Chains whose RPC URL resolves to '' are silently skipped."""
    def _fake_rpc(chain: str) -> str:
        return "https://base-mainnet.g.alchemy.com/v2/key" if chain == "base" else ""

    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.side_effect = _fake_rpc
        tracker = MultiChainTracker()

    assert "ethereum" not in tracker.scanners
    assert "base" in tracker.scanners
    assert len(tracker.scanners) == 1


def test_build_scanners_creates_solana_scanner_for_solana_chain():
    """SolanaScanner (not EvmChainScanner) is dispatched for chain_type='solana'."""
    from api.services.solana_scanner import SolanaScanner

    def _fake_rpc(chain: str) -> str:
        return "https://mainnet.helius-rpc.com/?api-key=x" if chain == "solana" else ""

    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.side_effect = _fake_rpc
        tracker = MultiChainTracker()

    assert "solana" in tracker.scanners
    assert isinstance(tracker.scanners["solana"], SolanaScanner)
    assert not isinstance(tracker.scanners["solana"], EvmChainScanner)


def test_build_scanners_creates_multiple_chains_independently():
    """Multiple chains can be registered in the same tracker."""
    def _fake_rpc(chain: str) -> str:
        mapping = {
            "ethereum": "https://eth-rpc.example.com",
            "base": "https://base-rpc.example.com",
        }
        return mapping.get(chain, "")

    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.side_effect = _fake_rpc
        tracker = MultiChainTracker()

    assert set(tracker.scanners.keys()) == {"ethereum", "base"}


# ── MultiChainTracker.start — no scanners ─────────────────────────────────────

async def test_start_exits_cleanly_when_no_chains_configured():
    """start() logs an error and returns without raising when no scanners exist."""
    with patch("api.services.whale_tracker.settings") as mock_settings:
        mock_settings.get_rpc_url.return_value = ""
        tracker = MultiChainTracker()

    # Should return without error — no chains to poll
    await tracker.start()
    assert tracker.scanners == {}
