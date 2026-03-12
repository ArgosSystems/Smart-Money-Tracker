"""
config/chains.py
----------------
Chain registry — single source of truth for every chain-specific constant.

Add a new chain here and the rest of the codebase picks it up automatically:
  - MultiChainTracker initialises a scanner for it
  - Discord /chains command lists it
  - API track endpoint accepts it as a valid value

Design notes
------------
- rpc_url_env:  name of the env var that holds the *full* RPC URL, e.g.
                ALCHEMY_ETH = https://eth-mainnet.g.alchemy.com/v2/<key>
                This lets you mix providers (Alchemy, Infura, public nodes).
- poll_interval: seconds between scan cycles for this chain.  Fast chains
                 (Base, Arb) need short intervals; Ethereum is fine at 12 s.
- coingecko_platform: used to build the CoinGecko token-price URL so prices
                      are fetched against the correct chain's contract registry.
- chain_type:   "evm" (default) or "solana".  Controls which scanner class
                MultiChainTracker instantiates.  EVM chains use web3.py block
                polling; Solana uses slot-range scanning via JSON-RPC 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChainConfig:
    # ── Identity ──────────────────────────────────────────────────────────────
    chain_id: int
    rpc_url_env: str          # env var name  → full RPC URL
    explorer: str             # base domain,  e.g. "etherscan.io"
    native_token: str         # "ETH" / "SOL" etc.

    # ── Display ───────────────────────────────────────────────────────────────
    color_hex: str            # hex string for Discord embed sidebar
    emoji: str                # one-char emoji shown in alerts

    # ── Timing ───────────────────────────────────────────────────────────────
    block_time: float         # approximate seconds per block/slot
    poll_interval: int        # scan cycle interval in seconds

    # ── External APIs ─────────────────────────────────────────────────────────
    coingecko_platform: str   # platform slug for CoinGecko token-price endpoint

    # ── Well-known contract/mint addresses on this chain ─────────────────────
    usdc_address: str         # native USDC (for USD value fallback)
    weth_address: str         # wrapped ETH / wSOL (for price lookups)

    # ── Scanner type ──────────────────────────────────────────────────────────
    # "evm"    → EvmChainScanner  (block polling, eth_getLogs)
    # "solana" → SolanaScanner    (slot-range, getSignaturesForAddress)
    chain_type: str = field(default="evm")

    # ── Computed helpers ──────────────────────────────────────────────────────

    @property
    def discord_color(self) -> int:
        """RGB integer for discord.Colour (parsed from hex string)."""
        return int(self.color_hex.lstrip("#"), 16)

    @property
    def rpc_url(self) -> str:
        """
        Resolve the RPC URL for this chain.
        Checks the chain-specific env var first; then falls back to deriving
        from ALCHEMY_API_KEY if that is set.  This mirrors settings.get_rpc_url().
        Solana (HELIUS_RPC_URL) auto-derives from HELIUS_API_KEY if not explicit.
        """
        explicit = os.environ.get(self.rpc_url_env, "")
        if explicit:
            return explicit
        # Fallback: derive from the legacy single-key env var (EVM only)
        api_key = os.environ.get("ALCHEMY_API_KEY", "")
        if api_key:
            subdomain_map = {
                "ALCHEMY_ETH":     "eth-mainnet",
                "ALCHEMY_BASE":    "base-mainnet",
                "ALCHEMY_ARB":     "arb-mainnet",
                "ALCHEMY_POLYGON": "polygon-mainnet",
                "ALCHEMY_OPT":     "opt-mainnet",
                # BSC_RPC has no Alchemy subdomain — public RPC only
            }
            subdomain = subdomain_map.get(self.rpc_url_env, "")
            if subdomain:
                return f"https://{subdomain}.g.alchemy.com/v2/{api_key}"
        # Helius auto-derivation from HELIUS_API_KEY
        if self.rpc_url_env == "HELIUS_RPC_URL":
            helius_key = os.environ.get("HELIUS_API_KEY", "")
            if helius_key:
                return f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
        return ""

    @property
    def is_configured(self) -> bool:
        """True if a valid RPC URL can be resolved (explicit or derived)."""
        return bool(self.rpc_url)

    def tx_url(self, tx_hash: str) -> str:
        return f"https://{self.explorer}/tx/{tx_hash}"

    def address_url(self, address: str) -> str:
        return f"https://{self.explorer}/address/{address}"


# ── Chain registry ────────────────────────────────────────────────────────────

CHAINS: dict[str, ChainConfig] = {
    "ethereum": ChainConfig(
        chain_id=1,
        rpc_url_env="ALCHEMY_ETH",
        explorer="etherscan.io",
        native_token="ETH",
        color_hex="#627EEA",
        emoji="⬛",
        block_time=12,
        poll_interval=12,
        coingecko_platform="ethereum",
        usdc_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        weth_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    ),
    "base": ChainConfig(
        chain_id=8453,
        rpc_url_env="ALCHEMY_BASE",
        explorer="basescan.org",
        native_token="ETH",
        color_hex="#0052FF",
        emoji="🔵",
        block_time=2,
        poll_interval=10,   # 2s is too aggressive for Alchemy free tier (429s)
        coingecko_platform="base",
        usdc_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        weth_address="0x4200000000000000000000000000000000000006",
    ),
    "arbitrum": ChainConfig(
        chain_id=42161,
        rpc_url_env="ALCHEMY_ARB",
        explorer="arbiscan.io",
        native_token="ETH",
        color_hex="#28A0F0",
        emoji="🔶",
        block_time=0.25,
        poll_interval=5,    # 1s hammers the RPC; batch scan covers multiple blocks anyway
        coingecko_platform="arbitrum-one",
        usdc_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        weth_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    ),
    "bsc": ChainConfig(
        chain_id=56,
        rpc_url_env="BSC_RPC",
        explorer="bscscan.com",
        native_token="BNB",
        color_hex="#F3BA2F",
        emoji="🟡",
        block_time=3,
        poll_interval=6,
        coingecko_platform="binance-smart-chain",
        usdc_address="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        weth_address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    ),
    "polygon": ChainConfig(
        chain_id=137,
        rpc_url_env="ALCHEMY_POLYGON",
        explorer="polygonscan.com",
        native_token="POL",
        color_hex="#8247E5",
        emoji="🟣",
        block_time=2,
        poll_interval=6,
        coingecko_platform="polygon-pos",
        usdc_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        weth_address="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    ),
    "optimism": ChainConfig(
        chain_id=10,
        rpc_url_env="ALCHEMY_OPT",
        explorer="optimistic.etherscan.io",
        native_token="ETH",
        color_hex="#FF0420",
        emoji="🔴",
        block_time=2,
        poll_interval=6,
        coingecko_platform="optimistic-ethereum",
        usdc_address="0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
        weth_address="0x4200000000000000000000000000000000000006",
    ),
    "solana": ChainConfig(
        chain_id=0,           # Solana has no numeric EVM chain_id
        rpc_url_env="HELIUS_RPC_URL",
        explorer="solscan.io",
        native_token="SOL",
        color_hex="#9945FF",
        emoji="◎",
        block_time=0.4,       # ~400ms per slot (2.5 slots/s)
        poll_interval=4,      # scan every 4s → covers ~10 slots per cycle
        coingecko_platform="solana",
        # SPL USDC mint (base58, 44 chars)
        usdc_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        # Wrapped SOL mint
        weth_address="So11111111111111111111111111111111111111112",
        chain_type="solana",
    ),
}

# Ordered list of chain names — used for Discord autocomplete choices
CHAIN_NAMES: list[str] = list(CHAINS.keys())

# Chains that have an RPC URL configured right now.
# Uses settings.get_rpc_url() — NOT os.environ — because pydantic-settings
# reads .env into the Settings object but does NOT populate os.environ.
def active_chains() -> list[str]:
    from config.settings import settings  # lazy import avoids circular dependency
    return [name for name in CHAINS if settings.get_rpc_url(name)]
