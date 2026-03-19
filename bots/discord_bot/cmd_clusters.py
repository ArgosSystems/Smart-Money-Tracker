"""
bots/discord_bot/cmd_clusters.py
----------------------------------
Wallet clustering slash commands:

  /clusters  [chain] [min_confidence] [count]
    — Lists detected wallet clusters (groups of wallets = same entity).

  /cluster_info  <id>
    — Shows all members of a specific cluster + detection methods used.

  /wallet_cluster  <address> [chain]
    — Reveals which cluster (entity) a wallet address belongs to.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_INFO, COLOR_WARN,
    api_get,
    chain_badge, cv2_error, cv2_send,
    fmt_usd, short_addr,
)

_METHOD_EMOJI = {
    "funding": "💸",
    "timing":  "⏱️",
    "pattern": "🔁",
}

_METHOD_LABEL = {
    "funding": "Funding source",
    "timing":  "Timing correlation",
    "pattern": "Directional pattern",
}


def _methods_display(methods_csv: str) -> str:
    """Convert CSV methods to human-readable string with emoji."""
    methods = [m.strip() for m in methods_csv.split(",") if m.strip()]
    parts = [f"{_METHOD_EMOJI.get(m, '🔍')} {_METHOD_LABEL.get(m, m.capitalize())}" for m in methods]
    return " · ".join(parts) if parts else "Unknown"


def _confidence_bar(confidence: float) -> str:
    """Visual confidence bar: ████░░░░ 70%"""
    filled = round(confidence * 8)
    bar = "█" * filled + "░" * (8 - filled)
    return f"`{bar}` {confidence * 100:.0f}%"


def setup_clusters(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="clusters",
        description="List detected wallet clusters — groups of wallets controlled by the same entity",
    )
    @app_commands.describe(
        chain="Filter by chain (leave blank for all chains)",
        min_confidence="Minimum detection confidence (0.0–1.0, default 0.6)",
        count="Number of clusters to show (1–10)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def clusters(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
        min_confidence: Optional[float] = 0.6,
        count: Optional[int] = 8,
    ) -> None:
        await interaction.response.defer(thinking=True)

        count = max(1, min(count or 8, 10))
        min_confidence = max(0.0, min(min_confidence or 0.6, 1.0))

        params: dict = {"limit": count, "min_confidence": min_confidence}
        if chain:
            params["chain"] = chain.value

        data = await api_get("/clusters", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch clusters", "API error or no data.")
            return

        if not data:
            filter_desc = ""
            if chain:
                filter_desc += f" on {chain_badge(chain.value)}"
            await cv2_send(
                interaction,
                title="No wallet clusters detected",
                lines=[
                    f"No clusters found{filter_desc} with confidence ≥ {min_confidence:.0%}.",
                    "",
                    "**How clustering works:**",
                    f"💸 **Funding** — wallet A sent ETH directly to wallet B, B trades same tokens",
                    f"⏱️ **Timing** — wallets bought the same token within 5 min, 3+ times",
                    f"🔁 **Pattern** — 3+ wallets moved same direction on same token same hour, 2+ times",
                    "",
                    "Clusters appear after enough whale activity has been recorded.",
                ],
                color=COLOR_INFO,
            )
            return

        lines: list[str] = []
        for cluster in data:
            cluster_id    = cluster.get("id", "?")
            cname         = cluster.get("chain", "ethereum")
            label         = cluster.get("cluster_label") or f"Cluster #{cluster_id}"
            methods_csv   = cluster.get("detection_methods", "")
            confidence    = float(cluster.get("confidence", 0.0))
            member_count  = cluster.get("member_count", 0)
            volume        = float(cluster.get("total_volume_usd", 0.0))
            members       = cluster.get("members", [])

            methods_str   = _methods_display(methods_csv)
            conf_bar      = _confidence_bar(confidence)
            member_addrs  = " · ".join(
                f"`{short_addr(m['wallet_address'])}`"
                + (f" _{m['wallet_label']}_" if m.get("wallet_label") else "")
                for m in members[:4]
            )
            more = f" +{member_count - 4} more" if member_count > 4 else ""

            lines.append(
                f"🕵️ **{label}** {CHAIN_EMOJI.get(cname, '')} {chain_badge(cname)}\n"
                f"Confidence: {conf_bar}\n"
                f"Wallets ({member_count}): {member_addrs}{more}\n"
                f"Volume: {fmt_usd(volume)} · Methods: {methods_str}\n"
                f"`/cluster_info {cluster_id}` for full details"
            )

        chain_label = f" {chain_badge(chain.value)}" if chain else " All Chains"
        await cv2_send(
            interaction,
            title=f"Wallet Clusters{chain_label}",
            lines=lines,
            color=COLOR_WARN,
            footer=f"Smart Money Tracker — Whale Clustering · confidence ≥ {min_confidence:.0%}",
        )

    # ─────────────────────────────────────────────────────────────────────────

    @bot.tree.command(
        name="cluster_info",
        description="Show all members of a detected wallet cluster and how it was detected",
    )
    @app_commands.describe(cluster_id="Cluster ID (from /clusters)")
    async def cluster_info(
        interaction: discord.Interaction,
        cluster_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)

        data = await api_get(f"/clusters/{cluster_id}")

        if data is None:
            await cv2_error(
                interaction,
                f"Cluster #{cluster_id} not found",
                "Use `/clusters` to see all detected clusters.",
            )
            return

        cname        = data.get("chain", "ethereum")
        label        = data.get("cluster_label") or f"Cluster #{cluster_id}"
        methods_csv  = data.get("detection_methods", "")
        confidence   = float(data.get("confidence", 0.0))
        member_count = data.get("member_count", 0)
        volume       = float(data.get("total_volume_usd", 0.0))
        members      = data.get("members", [])

        methods_str = _methods_display(methods_csv)
        conf_bar    = _confidence_bar(confidence)

        lines: list[str] = [
            f"Chain: {chain_badge(cname)}",
            f"Confidence: {conf_bar}",
            f"Combined volume: {fmt_usd(volume)}",
            f"Detection: {methods_str}",
            "",
            f"**Members ({member_count}):**",
        ]

        for m in members:
            addr    = m.get("wallet_address", "")
            label_w = m.get("wallet_label")
            sigs    = m.get("detection_signals", "")
            sig_str = _methods_display(sigs) if sigs else "—"
            name    = f" **{label_w}**" if label_w else ""
            lines.append(f"• `{short_addr(addr)}`{name}\n  Linked by: {sig_str}")

        lines.append("")
        lines.append(
            "**Detection methods explained:**\n"
            "💸 Funding — wallet A sent native coin directly to B; B then traded same tokens\n"
            "⏱️ Timing — both wallets bought same token within 5 min, 3+ times\n"
            "🔁 Pattern — 3+ wallets moved same direction same hour, repeated 2+ times"
        )

        await cv2_send(
            interaction,
            title=f"🕵️ {label}",
            lines=lines,
            color=COLOR_WARN,
            footer=f"Smart Money Tracker — Cluster #{cluster_id}",
        )

    # ─────────────────────────────────────────────────────────────────────────

    @bot.tree.command(
        name="wallet_cluster",
        description="Check if a wallet belongs to a detected cluster (same entity as other wallets)",
    )
    @app_commands.describe(
        address="Wallet address to look up",
        chain="Chain to check (leave blank to check all chains)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def wallet_cluster(
        interaction: discord.Interaction,
        address: str,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        params: dict = {}
        if chain:
            params["chain"] = chain.value

        data = await api_get(f"/clusters/wallet/{address}", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "Could not look up wallet cluster", "API error.")
            return

        if not data:
            await cv2_send(
                interaction,
                title="Wallet not in any cluster",
                lines=[
                    f"Wallet `{short_addr(address)}` is not part of any detected cluster.",
                    "",
                    "This means no coordination signals were found between this wallet "
                    "and other tracked wallets in the last 7 days.",
                    "Clustering runs every 10 minutes — check back after more activity is recorded.",
                ],
                color=COLOR_INFO,
            )
            return

        lines: list[str] = []
        for cluster in data:
            cluster_id   = cluster.get("id", "?")
            cname        = cluster.get("chain", "ethereum")
            label        = cluster.get("cluster_label") or f"Cluster #{cluster_id}"
            methods_csv  = cluster.get("detection_methods", "")
            confidence   = float(cluster.get("confidence", 0.0))
            member_count = cluster.get("member_count", 0)
            volume       = float(cluster.get("total_volume_usd", 0.0))
            members      = cluster.get("members", [])

            methods_str = _methods_display(methods_csv)
            conf_bar    = _confidence_bar(confidence)

            # Find the queried wallet's membership
            my_sigs = ""
            for m in members:
                if m.get("wallet_address", "").lower() == address.lower():
                    my_sigs = m.get("detection_signals", "")
                    break

            # List other members
            other_members = [
                m for m in members
                if m.get("wallet_address", "").lower() != address.lower()
            ]
            others_str = " · ".join(
                f"`{short_addr(m['wallet_address'])}`"
                + (f" _{m['wallet_label']}_" if m.get("wallet_label") else "")
                for m in other_members[:5]
            )
            if member_count - 1 > 5:
                others_str += f" +{member_count - 6} more"

            lines.append(
                f"🕵️ **{label}** {CHAIN_EMOJI.get(cname, '')} {chain_badge(cname)}\n"
                f"Confidence: {conf_bar}\n"
                f"This wallet linked by: {_methods_display(my_sigs) if my_sigs else methods_str}\n"
                f"Co-cluster wallets: {others_str or '—'}\n"
                f"Combined volume: {fmt_usd(volume)} · {method_count(methods_csv)} method(s)\n"
                f"`/cluster_info {cluster_id}` for full cluster details"
            )

        chain_label = f" on {chain_badge(chain.value)}" if chain else ""
        await cv2_send(
            interaction,
            title=f"Cluster Membership — `{short_addr(address)}`{chain_label}",
            lines=lines,
            color=COLOR_WARN,
            footer="Smart Money Tracker — Wallet Clustering",
        )


def method_count(methods_csv: str) -> int:
    """Count the number of detection methods in a CSV string."""
    return len([m for m in methods_csv.split(",") if m.strip()])
