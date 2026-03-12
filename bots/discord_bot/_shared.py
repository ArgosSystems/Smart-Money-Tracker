"""
bots/discord_bot/_shared.py
----------------------------
Shared constants, HTTP helpers, and formatting utilities used by all
cmd_*.py command modules.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
import httpx
from discord import app_commands

from config.settings import settings

logger = logging.getLogger(__name__)

# ── API endpoints ──────────────────────────────────────────────────────────────────
# Resolved once at import time from settings.api_url.
# Override via API_BASE_URL in .env for external/Pterodactyl deployments.

API_BASE   = settings.api_url.rstrip("/") + "/api/v1"
HEALTH_URL = settings.api_url.rstrip("/") + "/health"

# ── Embed colours ─────────────────────────────────────────────────────────────

COLOR_BUY   = discord.Color.green()
COLOR_SELL  = discord.Color.red()
COLOR_INFO  = discord.Color.blue()
COLOR_WARN  = discord.Color.orange()
COLOR_ERROR = discord.Color.dark_red()

# ── Per-chain data (all 6 supported chains) ───────────────────────────────────

CHAIN_COLORS: dict[str, discord.Color] = {
    "ethereum": discord.Color.from_str("#627EEA"),
    "base":     discord.Color.from_str("#0052FF"),
    "arbitrum": discord.Color.from_str("#28A0F0"),
    "bsc":      discord.Color.from_str("#F3BA2F"),
    "polygon":  discord.Color.from_str("#8247E5"),
    "optimism": discord.Color.from_str("#FF0420"),
}

CHAIN_EMOJI: dict[str, str] = {
    "ethereum": "⬛",
    "base":     "🔵",
    "arbitrum": "🔶",
    "bsc":      "🟡",
    "polygon":  "🟣",
    "optimism": "🔴",
}

CHAIN_EXPLORER: dict[str, str] = {
    "ethereum": "etherscan.io",
    "base":     "basescan.org",
    "arbitrum": "arbiscan.io",
    "bsc":      "bscscan.com",
    "polygon":  "polygonscan.com",
    "optimism": "optimistic.etherscan.io",
}

CHAIN_CHOICES = [
    app_commands.Choice(name="⬛ Ethereum", value="ethereum"),
    app_commands.Choice(name="🔵 Base",     value="base"),
    app_commands.Choice(name="🔶 Arbitrum", value="arbitrum"),
    app_commands.Choice(name="🟡 BSC",      value="bsc"),
    app_commands.Choice(name="🟣 Polygon",  value="polygon"),
    app_commands.Choice(name="🔴 Optimism", value="optimism"),
]

# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def api_get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("GET %s → %s", path, exc.response.text)
        return None
    except Exception as exc:
        logger.error("GET %s error: %s", path, exc)
        return None


async def api_post(path: str, payload: dict) -> tuple[dict | None, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{API_BASE}{path}", json=payload)
            resp.raise_for_status()
            return resp.json(), ""
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except Exception:
            detail = exc.response.text
        return None, str(detail)
    except httpx.ConnectError:
        return None, "Cannot reach the API — is `python start.py` running?"
    except Exception as exc:
        return None, str(exc)


async def api_patch(path: str) -> tuple[dict | None, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(f"{API_BASE}{path}")
            resp.raise_for_status()
            return resp.json(), ""
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except Exception:
            detail = exc.response.text
        return None, str(detail)
    except Exception as exc:
        return None, str(exc)


async def api_delete(path: str, params: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{API_BASE}{path}", params=params)
            resp.raise_for_status()
            # 204 No Content returns no body
            if resp.status_code == 204:
                return {}
            return resp.json()
    except Exception as exc:
        logger.error("DELETE %s error: %s", path, exc)
        return None


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.2f}"


def fmt_price(v: float) -> str:
    """Format a token price — keeps significant decimals for sub-cent tokens."""
    if v >= 1_000:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.4f}"
    if v >= 0.0001:
        return f"${v:.6f}"
    return f"${v:.10f}"


def short_addr(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 10 else addr


def dir_emoji(d: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "SEND": "🔵"}.get(d.upper(), "⚪")


def chain_color(chain: str) -> discord.Color:
    return CHAIN_COLORS.get(chain.lower(), COLOR_INFO)


def tx_link(tx_hash: str, chain: str) -> str:
    explorer = CHAIN_EXPLORER.get(chain.lower(), "etherscan.io")
    return f"https://{explorer}/tx/{tx_hash}"


def chain_badge(chain: str) -> str:
    emoji = CHAIN_EMOJI.get(chain.lower(), "🔗")
    return f"{emoji} {chain.capitalize()}"


# ── Components V2 helpers (discord.py ≥ 2.6) ─────────────────────────────────
# Pylance bundled stubs don't yet include 2.6+ CV2 classes — alias once here.
_LayoutView: Any = discord.ui.LayoutView  # type: ignore[attr-defined]
_Container: Any = discord.ui.Container  # type: ignore[attr-defined]
_TextDisplay: Any = discord.ui.TextDisplay  # type: ignore[attr-defined]
_Separator: Any = discord.ui.Separator  # type: ignore[attr-defined]
_SeparatorSpacing: Any = discord.SeparatorSpacing  # type: ignore[attr-defined]


def build_cv2(
    title: str,
    *,
    lines: list[str] | None = None,
    color: discord.Color | None = None,
    footer: str = "",
) -> Any:
    """
    Build a LayoutView (Components V2) that visually replaces a discord.Embed.

    Parameters
    ----------
    title:
        Rendered as a markdown ##-heading inside the container.
    lines:
        Each string becomes a separate TextDisplay separated by a thin divider.
        Supports full Discord markdown (bold, code-blocks, links, etc.).
    color:
        Accent colour on the left edge of the container.
    footer:
        Small-text footnote appended at the bottom.
    """
    items: list[Any] = [_TextDisplay(f"## {title}")]

    for line in lines or []:
        items.append(_Separator(spacing=_SeparatorSpacing.small))
        items.append(_TextDisplay(line))

    if footer:
        items.append(_Separator(spacing=_SeparatorSpacing.small, visible=False))
        items.append(_TextDisplay(f"-# {footer}"))

    container = _Container(*items, accent_colour=color or COLOR_INFO)
    view = _LayoutView(timeout=None)
    view.add_item(container)
    return view


async def cv2_send(
    interaction: discord.Interaction,
    *,
    title: str,
    lines: list[str] | None = None,
    color: discord.Color | None = None,
    footer: str = "",
    ephemeral: bool = False,
) -> None:
    """Send a Components V2 message via an existing deferred interaction."""
    view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    await interaction.followup.send(view=view, ephemeral=ephemeral)


async def cv2_error(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    ephemeral: bool = True,
) -> None:
    """Shortcut – send a red error CV2 message."""
    await cv2_send(
        interaction,
        title=title,
        lines=[description] if description else None,
        color=COLOR_ERROR,
        ephemeral=ephemeral,
    )
