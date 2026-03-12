"""
tests/test_scanner.py
---------------------
Unit tests for the scanner engine — no database, no HTTP, no RPC calls.

Covers:
  - _PriceCache  TTL logic
  - BaseChainScanner.scan_range  default batching behaviour
  - _extract_parties  Solana from/to heuristic
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from api.services.solana_scanner import _extract_parties
from api.services.whale_tracker import BaseChainScanner, _PriceCache


# ── _PriceCache ────────────────────────────────────────────────────────────────

def test_price_cache_miss_on_empty():
    cache = _PriceCache()
    assert cache.get("ETH") is None


def test_price_cache_hit_within_ttl():
    cache = _PriceCache()
    cache.set("ETH", 3_500.0)
    assert cache.get("ETH") == 3_500.0


def test_price_cache_zero_price_is_valid():
    # 0.0 is a legitimate "no price found" sentinel — must survive a round-trip
    cache = _PriceCache()
    cache.set("UNKNOWN_TOKEN", 0.0)
    assert cache.get("UNKNOWN_TOKEN") == 0.0


def test_price_cache_expired_entry_returns_none():
    cache = _PriceCache()
    cache.set("ETH", 3_500.0)
    # Backdate the expiry so the entry looks stale
    price, _ = cache._data["ETH"]
    cache._data["ETH"] = (price, time.monotonic() - 1.0)
    assert cache.get("ETH") is None


def test_price_cache_overwrite():
    cache = _PriceCache()
    cache.set("ETH", 1_000.0)
    cache.set("ETH", 3_500.0)
    assert cache.get("ETH") == 3_500.0


def test_price_cache_multiple_keys_are_independent():
    cache = _PriceCache()
    cache.set("ETH", 3_500.0)
    cache.set("BTC", 60_000.0)
    assert cache.get("ETH") == 3_500.0
    assert cache.get("BTC") == 60_000.0
    assert cache.get("SOL") is None


# ── BaseChainScanner.scan_range ────────────────────────────────────────────────

class _RecordingScanner(BaseChainScanner):
    """Concrete scanner that records which blocks scan_block was called with."""

    def __init__(self):
        super().__init__("test_chain", MagicMock(), "http://fake-rpc")
        self.scanned: list[int] = []

    async def is_healthy(self) -> bool:
        return True

    async def get_latest_block(self) -> int:
        return 0

    async def scan_block(self, block_number: int) -> list:
        self.scanned.append(block_number)
        return []


async def test_scan_range_covers_every_block():
    scanner = _RecordingScanner()
    await scanner.scan_range(1, 5)
    assert sorted(scanner.scanned) == [1, 2, 3, 4, 5]


async def test_scan_range_single_block():
    scanner = _RecordingScanner()
    await scanner.scan_range(42, 42)
    assert scanner.scanned == [42]


async def test_scan_range_empty_when_from_exceeds_to():
    scanner = _RecordingScanner()
    await scanner.scan_range(10, 9)
    assert scanner.scanned == []


async def test_scan_range_covers_range_larger_than_batch_size():
    # MAX_CONCURRENT_SCANS = 5; 13 blocks must all be scanned despite batching
    scanner = _RecordingScanner()
    await scanner.scan_range(1, 13)
    assert sorted(scanner.scanned) == list(range(1, 14))


async def test_scan_range_no_duplicate_blocks():
    scanner = _RecordingScanner()
    await scanner.scan_range(100, 110)
    assert len(scanner.scanned) == len(set(scanner.scanned))


# ── _extract_parties ───────────────────────────────────────────────────────────

_OWNER = "OwnerAddr1111111111111111111111111111111111"
_SENDER = "SenderAddr11111111111111111111111111111111"
_RECIPIENT = "RecipientAddr1111111111111111111111111111"

# Known Solana programs that must be skipped when looking for a counterparty
_SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_SYSTEM    = "11111111111111111111111111111111"
_COMPUTE   = "ComputeBudget111111111111111111111111111111"


def test_extract_parties_receive_gives_sender_to_owner():
    keys = [_SENDER, _OWNER, _SPL_TOKEN]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == _SENDER
    assert to_addr == _OWNER


def test_extract_parties_send_gives_owner_to_recipient():
    keys = [_OWNER, _RECIPIENT, _SPL_TOKEN]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=False)
    assert from_addr == _OWNER
    assert to_addr == _RECIPIENT


def test_extract_parties_skips_spl_token_program():
    # SPL Token program should never be treated as the counterparty
    real_counterparty = "RealCounterpart11111111111111111111111111"
    keys = [_SPL_TOKEN, real_counterparty, _OWNER]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == real_counterparty


def test_extract_parties_skips_system_program():
    keys = [_SYSTEM, _SENDER, _OWNER]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == _SENDER


def test_extract_parties_skips_multiple_programs():
    keys = [_SYSTEM, _COMPUTE, _SPL_TOKEN, _SENDER, _OWNER]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == _SENDER


def test_extract_parties_fallback_to_owner_when_no_counterparty():
    # All non-owner keys are known programs — must fall back gracefully
    keys = [_OWNER, _SPL_TOKEN, _SYSTEM]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == _OWNER  # fallback
    assert to_addr == _OWNER


def test_extract_parties_owner_not_in_keys():
    # Edge case: owner doesn't appear as an account key at all
    keys = [_SENDER, _SPL_TOKEN]
    from_addr, to_addr = _extract_parties(keys, _OWNER, is_receive=True)
    assert from_addr == _SENDER
    assert to_addr == _OWNER
