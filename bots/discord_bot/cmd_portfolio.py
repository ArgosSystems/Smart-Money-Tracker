"""
bots/discord_bot/cmd_portfolio.py
-----------------------------------
Portfolio-tracking slash commands:

  /portfolio_add     <address> [chain] [label]
  /portfolio_list    [chain]
  /portfolio_balance <id>
  /portfolio_remove  <id>
  /portfolio_toggle  <id>
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_ERROR, COLOR_INFO, COLOR_WARN,
    api_delete, api_get, api_patch, api_post,
    chain_badge, chain_color, cv2_error, cv2_send, fmt_usd, short_addr,
)


def setup_portfolio(bot: commands.Bot) -> None:

    # /portfolio_add
    @bot.tree.command(
        name="portfolio_add",
        description="Add a wallet to portfolio balance tracking",
    )
    @app_commands.describe(
        address="Wallet address (0x...)",
        chain="Blockchain (default: Ethereum)",
        label="Optional nickname",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def portfolio_add(
        interaction: discord.Interaction,
        address: str,
        chain: Optional[app_commands.Choice[str]] = None,
        label: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        chain_value = chain.value if chain else "ethereum"
        payload: dict = {"address": address, "chain": chain_value}
        if label:
            payload["label"] = label
        data, err = await api_post("/portfolio/wallets", payload)
        if data is None:
            await cv2_error(interaction, "Failed to add wallet", err or "Unknown error.")
            return
        cname = data.get("chain", chain_value)
        lines = [f"**Address:** `{data['address']}`"]
        if data.get("label"):
            lines.append(f"**Label:** {data['label']}")
        lines += [f"**Chain:** {chain_badge(cname)}", "**Snapshots:** Active"]
        await cv2_send(
            interaction,
            title=f"Portfolio wallet added - {chain_badge(cname)}",
            lines=lines,
            color=chain_color(cname),
            footer=f"ID: {data['id']} - use /portfolio_balance {data['id']} for live balance",
        )

    # /portfolio_list
    @bot.tree.command(
        name="portfolio_list",
        description="Show all wallets in your portfolio tracker",
    )
    @app_commands.describe(chain="Filter by chain (leave blank for all)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def portfolio_list(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        params: dict = {}
        if chain:
            params["chain"] = chain.value
        data = await api_get("/portfolio/wallets", params=params)
        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch portfolio wallets")
            return
        if not data:
            await cv2_send(
                interaction,
                title="Portfolio is empty",
                lines=["Use `/portfolio_add <address>` to start tracking."],
                color=COLOR_INFO,
            )
            return
        title = f"Portfolio{' - ' + chain_badge(chain.value) if chain else ' - All Chains'}"
        lines: list[str] = []
        for w in data[:20]:
            cname  = w.get("chain", "ethereum")
            status = "Active" if w.get("is_active") else "Paused"
            label  = w.get("label") or short_addr(w["address"])
            lines.append(
                f"**ID {w['id']} - {CHAIN_EMOJI.get(cname, '')} {label}**\n"
                f"`{w['address']}`\n"
                f"Chain: {chain_badge(cname)} | {status}"
            )
        footer = f"Showing 20 of {len(data)} wallets" if len(data) > 20 else ""
        await cv2_send(interaction, title=title, lines=lines, color=COLOR_INFO, footer=footer)

    # /portfolio_balance
    @bot.tree.command(
        name="portfolio_balance",
        description="Fetch live on-chain balance for a portfolio wallet",
    )
    @app_commands.describe(wallet_id="Portfolio wallet ID (see /portfolio_list)")
    async def portfolio_balance(
        interaction: discord.Interaction,
        wallet_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data = await api_get(f"/portfolio/wallets/{wallet_id}/balance")
        if not isinstance(data, dict):
            await cv2_error(
                interaction,
                "Could not fetch balance",
                f"Wallet ID `{wallet_id}` not found or RPC error.",
            )
            return
        cname  = data.get("chain", "ethereum")
        symbol = data.get("native_symbol", "ETH")
        bal    = data.get("native_balance", 0.0)
        price  = data.get("native_price_usd", 0.0)
        total  = data.get("total_usd", 0.0)
        addr   = data.get("address", "")
        await cv2_send(
            interaction,
            title=f"Balance - {chain_badge(cname)}",
            lines=[
                f"**Address:** `{addr}`",
                f"**{symbol} Balance:** {bal:.6f} {symbol}",
                f"**{symbol} Price:** ${price:,.2f}",
                f"**Total USD:** {fmt_usd(total)}",
            ],
            color=chain_color(cname),
            footer=f"Fetched at {data.get('fetched_at', '-')} - Snapshot saved",
        )

    # /portfolio_remove
    @bot.tree.command(
        name="portfolio_remove",
        description="Remove a wallet from portfolio tracking",
    )
    @app_commands.describe(wallet_id="Portfolio wallet ID (see /portfolio_list)")
    async def portfolio_remove(
        interaction: discord.Interaction,
        wallet_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data = await api_delete(f"/portfolio/wallets/{wallet_id}")
        if data is None:
            await cv2_error(
                interaction,
                "Could not remove wallet",
                f"Wallet ID `{wallet_id}` not found or API error.",
            )
            return
        await cv2_send(
            interaction,
            title="Portfolio wallet removed",
            lines=[f"Wallet ID `{wallet_id}` and all its snapshots have been deleted."],
            color=COLOR_WARN,
        )

    # /portfolio_toggle
    @bot.tree.command(
        name="portfolio_toggle",
        description="Pause or resume automatic balance snapshots for a wallet",
    )
    @app_commands.describe(wallet_id="Portfolio wallet ID (see /portfolio_list)")
    async def portfolio_toggle(
        interaction: discord.Interaction,
        wallet_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data, err = await api_patch(f"/portfolio/wallets/{wallet_id}/toggle")
        if data is None:
            await cv2_error(
                interaction,
                "Could not toggle wallet",
                err or f"Wallet ID `{wallet_id}` not found.",
            )
            return
        is_active = data.get("is_active", False)
        status    = "Active" if is_active else "Paused"
        action    = "resumed" if is_active else "paused"
        await cv2_send(
            interaction,
            title=f"Snapshots {action}",
            lines=[f"Wallet ID `{wallet_id}` is now **{status}**."],
            color=COLOR_BUY if is_active else COLOR_WARN,
        )