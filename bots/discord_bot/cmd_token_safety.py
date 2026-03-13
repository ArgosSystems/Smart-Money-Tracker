"""
bots/discord_bot/cmd_token_safety.py
--------------------------------------
Solana token safety scanner:

  /scan_token <mint>  -- rug-check a Solana token mint address
"""

from __future__ import annotations

import logging

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from ._shared import (
    API_BASE,
    COLOR_BUY, COLOR_ERROR, COLOR_SELL, COLOR_WARN,
    cv2_error, cv2_send, fmt_usd, short_addr,
)

logger = logging.getLogger(__name__)


async def _fetch_safety(mint: str) -> tuple[dict | None, str]:
    """
    Call /token-safety/{mint} and return (data, error_detail).
    Unlike api_get(), this preserves the error message from the API response
    so the user sees the real reason a scan failed.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{API_BASE}/token-safety/{mint}")
            if resp.status_code == 200:
                return resp.json(), ""
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text[:300]
            return None, str(detail)
    except httpx.ConnectError:
        return None, "Cannot reach the API — is `python start.py` running?"
    except Exception as exc:
        logger.error("token-safety request error: %s", exc)
        return None, str(exc)

_LEVEL_EMOJI = {
    "danger": "🔴",
    "warn":   "🟡",
    "info":   "🔵",
}


def setup_token_safety(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="scan_token",
        description="◎ Solana token safety check — detect rug pulls, mint authority, LP lock & top holders",
    )
    @app_commands.describe(mint="Solana token mint address (base58, e.g. So11111...)")
    async def scan_token(
        interaction: discord.Interaction,
        mint: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        data, err = await _fetch_safety(mint)
        if data is None:
            await cv2_error(
                interaction,
                "Scan failed",
                err or "Could not fetch safety data.",
            )
            return

        risk_level: str = data.get("risk_level", "UNKNOWN")
        score: int      = data.get("score", 0)
        rugged: bool    = data.get("rugged", False)
        name: str       = data.get("name") or "Unknown Token"
        symbol: str     = data.get("symbol") or "???"

        if rugged:
            color   = COLOR_ERROR
            verdict = "⛔ RUGGED"
        elif risk_level == "SAFE":
            color   = COLOR_BUY
            verdict = "✅ SAFE"
        elif risk_level == "CAUTION":
            color   = COLOR_WARN
            verdict = "⚠️ CAUTION"
        else:
            color   = COLOR_SELL
            verdict = "🚨 DANGER"

        mint_auth   = "✅ Revoked" if data.get("mint_authority_revoked")   else "❌ Active (dev can print tokens)"
        freeze_auth = "✅ Revoked" if data.get("freeze_authority_revoked") else "❌ Active (dev can freeze wallets)"
        liquidity   = fmt_usd(data.get("total_liquidity_usd", 0))
        lp_locked   = f"{data.get('lp_locked_pct', 0):.1f}%"
        top1        = f"{data.get('top_holder_pct', 0):.1f}%"
        top5        = f"{data.get('top5_holders_pct', 0):.1f}%"

        lines = [
            f"**Verdict:** {verdict}\n**Risk Score:** {score:,}",
            f"**Mint Authority:** {mint_auth}\n**Freeze Authority:** {freeze_auth}",
            f"**Total Liquidity:** {liquidity}\n**LP Locked:** {lp_locked}",
            f"**Top Holder:** {top1} of supply\n**Top 5 Holders:** {top5} of supply",
        ]

        risks = data.get("risks") or []
        if risks:
            risk_lines = []
            for r in risks:
                emoji = _LEVEL_EMOJI.get(r.get("level", "info"), "⚪")
                desc  = r.get("description") or ""
                risk_lines.append(
                    f"{emoji} **{r['name']}** *(+{r['score']})*"
                    + (f"\n{desc}" if desc else "")
                )
            lines.append("**Risk Factors:**\n" + "\n".join(risk_lines))

        short_mint = short_addr(mint)
        await cv2_send(
            interaction,
            title=f"◎ {name} ({symbol}) — Token Safety",
            lines=lines,
            color=color,
            footer=f"Mint: {short_mint} · Powered by RugCheck.xyz",
        )
