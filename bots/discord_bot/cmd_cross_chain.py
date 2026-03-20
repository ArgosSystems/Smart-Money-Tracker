"""
bots/discord_bot/cmd_cross_chain.py
--------------------------------------
Cross-chain entity detection slash commands:

  /entity <name_or_address> [hours]
    — Full cross-chain profile: all known addresses, per-chain volume,
      combined P&L, recent alerts.  Answers "What is Jump Trading doing
      across ALL chains right now?"

  /entity_lookup <address> [chain]
    — Resolve any address to its entity and see all chain addresses.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES,
    CHAIN_EMOJI,
    COLOR_BUY,
    COLOR_INFO,
    COLOR_SELL,
    COLOR_WARN,
    api_get,
    chain_badge,
    cv2_error,
    cv2_send,
    dir_emoji,
    fmt_usd,
    short_addr,
    tx_link,
)


def setup_cross_chain(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="entity",
        description="Cross-chain entity profile — see what one entity is doing across ALL chains",
    )
    @app_commands.describe(
        name="Entity name (e.g. 'Jump Trading') or wallet address (0x...)",
        hours="Activity look-back window in hours (1–168, default 24)",
    )
    async def entity(
        interaction: discord.Interaction,
        name: str,
        hours: Optional[int] = 24,
    ) -> None:
        await interaction.response.defer(thinking=True)
        hours = max(1, min(hours or 24, 168))

        # If it looks like an address, resolve to entity first
        if name.startswith("0x") and len(name) == 42:
            lookup = await api_get(f"/entity/lookup/{name}")
            if lookup is None:
                await cv2_error(
                    interaction,
                    "Unknown address",
                    f"`{short_addr(name)}` is not in the SmartLabel database.\n"
                    "Only labelled entities (exchanges, VCs, funds, etc.) can be looked up.",
                )
                return
            entity_name = lookup.get("entity_name", name)
        else:
            entity_name = name

        data = await api_get(f"/entity/{entity_name}", params={"hours": hours})
        if data is None:
            await cv2_error(
                interaction,
                "Entity not found",
                f"No entity matching **{entity_name}** found.\n"
                "Use `/entity_lookup <address>` to find the entity for a specific wallet.",
            )
            return

        e_name = data.get("entity_name", entity_name)
        e_type = data.get("entity_type", "unknown")
        addresses = data.get("addresses", [])
        chains_active = data.get("chains_active_today", [])
        total_vol = data.get("total_volume_usd", 0.0)
        buy_vol = data.get("buy_volume_usd", 0.0)
        sell_vol = data.get("sell_volume_usd", 0.0)
        alert_count = data.get("alert_count", 0)
        per_chain = data.get("per_chain", {})
        recent = data.get("recent_alerts", [])
        pnl = data.get("pnl", {})

        # Group addresses by chain
        chains_map: dict[str, list[dict]] = {}
        for a in addresses:
            chains_map.setdefault(a["chain"], []).append(a)

        # ── Build header ─────────────────────────────────────────────────
        type_emoji = {
            "exchange": "🏦", "vc": "🏢", "founder": "👤",
            "foundation": "🏛️", "dao": "🗳️", "bridge": "🌉",
            "defi": "🔄", "staking": "📊", "lending": "💰",
            "mev": "🤖", "custodian": "🔐",
        }.get(e_type, "🔍")

        sentiment = "📈 Net buying" if buy_vol > sell_vol else "📉 Net selling" if sell_vol > buy_vol else "⚖️ Balanced"

        lines: list[str] = [
            f"**{type_emoji} {e_name}**  ·  {e_type.replace('_', ' ').title()}",
            f"📡 {len(addresses)} known address(es) across {len(chains_map)} chain(s)",
        ]

        # ── Active chains ────────────────────────────────────────────────
        if chains_active:
            active_badges = " · ".join(
                f"{CHAIN_EMOJI.get(c, '')} {c.capitalize()}" for c in chains_active
            )
            lines.append(f"⚡ Active in last {hours}h: {active_badges}")
        else:
            lines.append(f"💤 No activity in the last {hours}h")

        # ── Volume summary ───────────────────────────────────────────────
        if alert_count > 0:
            lines.append(
                f"💰 **{fmt_usd(total_vol)}** total volume  ·  "
                f"🟢 {fmt_usd(buy_vol)} buys  ·  🔴 {fmt_usd(sell_vol)} sells"
            )
            lines.append(f"📊 {alert_count} alert(s)  ·  {sentiment}")

        # ── Per-chain breakdown ──────────────────────────────────────────
        if per_chain:
            lines.append("")
            lines.append("**Per-chain breakdown:**")
            for chain_name, stats in sorted(per_chain.items()):
                c_emoji = CHAIN_EMOJI.get(chain_name, "")
                cv = stats.get("volume_usd", 0)
                ca = stats.get("alert_count", 0)
                cb = stats.get("buy_count", 0)
                cs = stats.get("sell_count", 0)
                lines.append(
                    f"{c_emoji} **{chain_name.capitalize()}** — {fmt_usd(cv)}  ·  "
                    f"{ca} alerts  ·  🟢{cb} 🔴{cs}"
                )

        # ── P&L ──────────────────────────────────────────────────────────
        realized = pnl.get("total_realized_usd", 0.0)
        bought = pnl.get("total_bought_usd", 0.0)
        sold = pnl.get("total_sold_usd", 0.0)
        positions = pnl.get("positions", 0)

        if positions > 0:
            pnl_emoji = "📈" if realized >= 0 else "📉"
            sign = "+" if realized >= 0 else ""
            lines.append("")
            lines.append(
                f"**Combined P&L** ({positions} positions)\n"
                f"{pnl_emoji} Realized: **{sign}{fmt_usd(realized)}**  ·  "
                f"Bought: {fmt_usd(bought)}  ·  Sold: {fmt_usd(sold)}"
            )

        # ── Known addresses ──────────────────────────────────────────────
        lines.append("")
        lines.append("**Known addresses:**")
        for chain_name, addrs in sorted(chains_map.items()):
            c_emoji = CHAIN_EMOJI.get(chain_name, "")
            addr_parts = " · ".join(
                f"`{short_addr(a['address'])}` _{a['name']}_"
                for a in addrs[:4]
            )
            more = f" +{len(addrs) - 4} more" if len(addrs) > 4 else ""
            lines.append(f"{c_emoji} **{chain_name.capitalize()}**: {addr_parts}{more}")

        # ── Recent alerts ────────────────────────────────────────────────
        if recent:
            lines.append("")
            lines.append("**Recent alerts:**")
            for alert in recent[:5]:
                d = alert.get("direction", "SEND")
                sym = alert.get("token_symbol") or "native"
                usd = alert.get("amount_usd", 0)
                ch = alert.get("chain", "ethereum")
                c_emoji = CHAIN_EMOJI.get(ch, "")
                lines.append(
                    f"{dir_emoji(d)} **{d}** {sym} · {fmt_usd(usd)} {c_emoji}"
                )

        color = COLOR_WARN if e_type == "vc" else COLOR_INFO
        await cv2_send(
            interaction,
            title=f"🌐 Cross-Chain Entity — {e_name}",
            lines=lines,
            color=color,
            footer=f"Smart Money Tracker — Cross-Chain Entity Detection · {hours}h window",
        )

    # ─────────────────────────────────────────────────────────────────────────

    @bot.tree.command(
        name="entity_lookup",
        description="Resolve any wallet address to its cross-chain entity",
    )
    @app_commands.describe(
        address="Wallet address (0x...)",
        chain="Hint chain to narrow search (optional)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def entity_lookup(
        interaction: discord.Interaction,
        address: str,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        params: dict = {}
        if chain:
            params["chain"] = chain.value

        data = await api_get(f"/entity/lookup/{address}", params=params)

        if data is None:
            await cv2_error(
                interaction,
                "Unknown address",
                f"`{short_addr(address)}` is not in the SmartLabel database.\n\n"
                "Only known entities (exchanges, VCs, funds, protocols) are indexed.\n"
                "Use `/entity <name>` if you know the entity name.",
            )
            return

        e_name = data.get("entity_name", "Unknown")
        e_type = data.get("entity_type", "unknown")
        addresses = data.get("addresses", [])
        chains = data.get("chains", [])

        type_emoji = {
            "exchange": "🏦", "vc": "🏢", "founder": "👤",
            "foundation": "🏛️", "dao": "🗳️", "bridge": "🌉",
            "defi": "🔄", "staking": "📊", "lending": "💰",
            "mev": "🤖", "custodian": "🔐",
        }.get(e_type, "🔍")

        chain_badges = " · ".join(
            f"{CHAIN_EMOJI.get(c, '')} {c.capitalize()}" for c in chains
        )

        lines: list[str] = [
            f"**{type_emoji} {e_name}**  ·  {e_type.replace('_', ' ').title()}",
            f"🔗 {len(addresses)} address(es) across {len(chains)} chain(s)",
            f"Chains: {chain_badges}",
            "",
            "**All known addresses:**",
        ]

        for a in addresses:
            c_emoji = CHAIN_EMOJI.get(a["chain"], "")
            tier_badge = " 🔒" if a.get("tier") == "pro" else ""
            lines.append(
                f"{c_emoji} `{short_addr(a['address'])}` — {a['name']}{tier_badge}"
            )

        lines.append("")
        lines.append(f"Use `/entity {e_name}` to see full cross-chain activity and P&L.")

        await cv2_send(
            interaction,
            title=f"🔍 Entity Lookup — `{short_addr(address)}`",
            lines=lines,
            color=COLOR_INFO,
            footer="Smart Money Tracker — Cross-Chain Entity Detection",
        )
