"""  
config/settings.py
------------------
Central configuration — reads from .env / environment variables.
All other modules import `settings` from here; nothing reads os.environ directly.

Chain RPC URLs
--------------
Each chain expects a *full* RPC URL in its env var:
  ALCHEMY_ETH     = https://eth-mainnet.g.alchemy.com/v2/<key>
  ALCHEMY_BASE    = https://base-mainnet.g.alchemy.com/v2/<key>
  ALCHEMY_ARB     = https://arb-mainnet.g.alchemy.com/v2/<key>
  ALCHEMY_POLYGON = https://polygon-mainnet.g.alchemy.com/v2/<key>
  ALCHEMY_OPT     = https://opt-mainnet.g.alchemy.com/v2/<key>
  BSC_RPC         = https://bsc-dataseed.binance.org/  (public, no key needed)

If a chain's env var is empty the MultiChainTracker silently skips it, so
you can run with only Ethereum configured while you add other chains later.

External / Pterodactyl deployment
----------------------------------
Set API_BASE_URL to your server's public URL to let the Discord and Telegram
bots reach the API over the network instead of localhost:

  API_BASE_URL = http://your-vps.example.com:8000
  API_BASE_URL = https://tracker.yourdomain.com   (behind a reverse-proxy)

Discord OAuth2
--------------
Fill in DISCORD_CLIENT_ID + DISCORD_CLIENT_SECRET to enable the /invite
command which generates a properly-scoped OAuth2 invite link.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Bot tokens ──────────────────────────────────────────────────────────
    discord_token: str = ""
    telegram_token: str = ""

    # ── Discord OAuth2 (for /invite command & OAuth flow) ───────────────────
    discord_client_id: str = ""
    discord_client_secret: str = ""
    # Space-separated scopes for the invite URL.  Default grants slash commands
    # + bot presence.  Other useful scopes: guilds, guilds.members.read
    discord_oauth_scopes: str = "bot applications.commands"
    # Integer permission bits added to the invite URL.  Default covers:
    #   VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | READ_MESSAGE_HISTORY
    #   | USE_APPLICATION_COMMANDS
    discord_oauth_permissions: int = 2147568640    # Direct invite URL override — if set, this is used as-is by the /invite
    # command instead of building a URL from client_id + scopes + permissions.
    # Paste your pre-built OAuth2 link from the Discord Developer Portal here.
    discord_oauth_link: str = ""
    # ── Chain RPC URLs (full URL, not just the key) ───────────────────────
    alchemy_eth: str = ""     # Ethereum mainnet
    alchemy_base: str = ""    # Base mainnet
    alchemy_arb: str = ""     # Arbitrum One
    alchemy_polygon: str = "" # Polygon mainnet
    alchemy_opt: str = ""     # Optimism mainnet
    bsc_rpc: str = ""         # BNB Smart Chain (public RPC, no Alchemy key needed)

    # Legacy single-key field kept for backwards compatibility — if a chain
    # URL is not set but this key is present, URLs are derived automatically.
    alchemy_api_key: str = ""

    helius_api_key: str = ""  # Solana (future use)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./crypto_bots.db"

    # ── API server binding ────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── OAuth2 callback server ───────────────────────────────────────────────
    # Port the redirect-callback HTTP server listens on.  Discord sends users
    # here after they authorise the bot (Redirect URI in Dev Portal).
    oauth_port: int = 8080

    # ── External / public URL of this API ────────────────────────────────────
    # Leave blank to fall back to http://localhost:{api_port}.
    # Set this when deploying on a VPS, Pterodactyl node, or behind a
    # reverse-proxy so the bots and dashboard show the correct public address.
    #
    #   API_BASE_URL=http://1.2.3.4:8000          # IP + port
    #   API_BASE_URL=https://tracker.example.com  # domain behind nginx/caddy
    api_base_url: str = ""

    # ── Whale detection ───────────────────────────────────────────────────────
    whale_threshold_usd: float = 10_000.0

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def api_url(self) -> str:
        """
        Public HTTP base URL of the API (no trailing slash).
        Bots use this to build REST and /health requests.
        """
        return self.api_base_url.rstrip("/") if self.api_base_url else f"http://localhost:{self.api_port}"

    @property
    def ws_url(self) -> str:
        """WebSocket base URL derived from api_url (http→ws, https→wss)."""
        base = self.api_url
        return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)

    @property
    def discord_invite_url(self) -> str:
        """
        Full Discord OAuth2 invite URL.
        Priority:
          1. DISCORD_OAUTH_LINK  — pre-built link pasted directly from Dev Portal
          2. Built from DISCORD_CLIENT_ID + scopes + permissions
          3. Empty string if neither is configured.
        """
        if self.discord_oauth_link:
            return self.discord_oauth_link
        if not self.discord_client_id:
            return ""
        scopes = self.discord_oauth_scopes.replace(" ", "%20").replace("+", "%20")
        return (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={self.discord_client_id}"
            f"&scope={scopes}"
            f"&permissions={self.discord_oauth_permissions}"
        )

    def get_rpc_url(self, chain_name: str) -> str:
        """
        Return the RPC URL for a chain.  Prefers explicit per-chain env vars;
        falls back to deriving from alchemy_api_key if possible.
        """
        mapping = {
            "ethereum": (self.alchemy_eth,     "eth-mainnet"),
            "base":     (self.alchemy_base,    "base-mainnet"),
            "arbitrum": (self.alchemy_arb,     "arb-mainnet"),
            "polygon":  (self.alchemy_polygon, "polygon-mainnet"),
            "optimism": (self.alchemy_opt,     "opt-mainnet"),
            "bsc":      (self.bsc_rpc,         ""),  # no Alchemy subdomain
        }
        explicit_url, subdomain = mapping.get(chain_name, ("", ""))
        if explicit_url:
            return explicit_url
        if self.alchemy_api_key and subdomain:
            return f"https://{subdomain}.g.alchemy.com/v2/{self.alchemy_api_key}"
        return ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )



# Singleton — import this everywhere
settings = Settings()
