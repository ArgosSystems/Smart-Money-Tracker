"""
tests/test_config.py
---------------------
Pure-Python tests for config/chains.py and config/settings.py.

These do not touch the database or the HTTP server, so they run instantly
and are the first line of defence for chain-registry regressions.
"""

from config.chains import CHAINS
from config.settings import Settings

_EVM_CHAINS = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]


# ── Chain registry ─────────────────────────────────────────────────────────────

def test_solana_is_registered():
    assert "solana" in CHAINS


def test_solana_chain_type():
    assert CHAINS["solana"].chain_type == "solana"


def test_all_evm_chains_have_evm_type():
    for name in _EVM_CHAINS:
        assert CHAINS[name].chain_type == "evm", f"{name} should have chain_type='evm'"


def test_solana_native_token():
    assert CHAINS["solana"].native_token == "SOL"


def test_solana_rpc_env_var():
    assert CHAINS["solana"].rpc_url_env == "HELIUS_RPC_URL"


def test_solana_explorer():
    assert CHAINS["solana"].explorer == "solscan.io"


def test_solana_coingecko_platform():
    assert CHAINS["solana"].coingecko_platform == "solana"


def test_solana_chain_id_zero():
    # Solana has no EVM chain_id; we use 0 as a sentinel
    assert CHAINS["solana"].chain_id == 0


def test_solana_poll_interval():
    # 4s covers ~10 slots at 2.5 slots/s — reasonable for Helius free tier
    assert CHAINS["solana"].poll_interval == 4


def test_all_chains_have_explorer():
    for name, cfg in CHAINS.items():
        assert cfg.explorer, f"{name} is missing an explorer URL"


def test_all_chains_have_native_token():
    for name, cfg in CHAINS.items():
        assert cfg.native_token, f"{name} is missing a native token symbol"


def test_discord_color_is_int():
    # discord_color is used to colour bot messages; must be a valid int
    for name, cfg in CHAINS.items():
        color = cfg.discord_color
        assert isinstance(color, int), f"{name}.discord_color should be an int"
        assert 0 <= color <= 0xFFFFFF, f"{name}.discord_color out of RGB range"


# ── Settings.get_rpc_url ───────────────────────────────────────────────────────

def test_get_rpc_url_solana_empty_without_keys():
    s = Settings()
    assert s.get_rpc_url("solana") == ""


def test_get_rpc_url_solana_from_helius_api_key():
    s = Settings(helius_api_key="mykey123")
    assert s.get_rpc_url("solana") == "https://mainnet.helius-rpc.com/?api-key=mykey123"


def test_get_rpc_url_solana_explicit_url_takes_precedence():
    s = Settings(helius_api_key="ignored", helius_rpc_url="https://custom.rpc.example.com")
    assert s.get_rpc_url("solana") == "https://custom.rpc.example.com"


def test_get_rpc_url_ethereum_from_alchemy_api_key():
    # alchemy_eth="" ensures the explicit-URL path is skipped so the fallback
    # derivation from alchemy_api_key is tested (a real .env might have ALCHEMY_ETH set)
    s = Settings(alchemy_api_key="alchemykey", alchemy_eth="")
    assert s.get_rpc_url("ethereum") == "https://eth-mainnet.g.alchemy.com/v2/alchemykey"


def test_get_rpc_url_ethereum_explicit_url_takes_precedence():
    s = Settings(alchemy_eth="https://my-eth-rpc.io", alchemy_api_key="ignored")
    assert s.get_rpc_url("ethereum") == "https://my-eth-rpc.io"


def test_get_rpc_url_unknown_chain_returns_empty():
    s = Settings()
    assert s.get_rpc_url("fantom") == ""


def test_get_rpc_url_bsc_no_alchemy_fallback():
    # BSC has no Alchemy subdomain — alchemy_api_key must not produce a BSC URL
    s = Settings(alchemy_api_key="anykey")
    url = s.get_rpc_url("bsc")
    assert "alchemy" not in url
