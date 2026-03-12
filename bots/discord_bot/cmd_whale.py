"""
bots/discord_bot/cmd_whale.py
------------------------------
Whale-tracking slash commands:

  /track_wallet   <address> [chain] [label]
  /untrack_wallet <address> [chain]
  /wallets        [chain]              -- list tracked wallets
  /whale_alerts   [chain]   [count]
  /smart_money    <token>   [chain]
  /trending       [chain]
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_ERROR, COLOR_INFO, COLOR_SELL, COLOR_WARN,
    api_delete, api_get, api_post,
    chain_badge, chain_color, cv2_error, cv2_send,
    dir_emoji, fmt_usd, short_addr, tx_link,
)


def setup_whale(bot: commands.Bot) -> None:

    # /track_wallet
    @bot.tree.command(
        name="track_wallet",
        description="Start tracking a whale wallet on a specific chain",
    )
    @app_commands.describe(
        address="Wallet address (0x...)",
        chain="Blockchain to monitor (default: Ethereum)",
        label="Optional nickname for this wallet",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def track_wallet(
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
        data, err = await api_post("/wallets/track", payload)
        if data is None:
            await cv2_error(interaction, "Failed to track wallet", err or "Unknown error.")
            return
        cname = data.get("chain", chain_value)
        lines = [f"**Address:** `{data['address']}`"]
        if data.get("label"):
            lines.append(f"**Label:** {data['label']}")
        lines += [f"**Chain:** {chain_badge(cname)}", "**Status:** Active"]
        await cv2_send(
            interaction,
            title=f"Wallet tracked on {chain_badge(cname)}",
            lines=lines,
            color=chain_color(cname),
            footer=f"ID: {data['id']} - Smart Money Tracker",
        )

    # /untrack_wallet
    @bot.tree.command(name="untrack_wallet", description="Stop tracking a wallet")
    @app_commands.describe(
        address="Wallet address to remove",
        chain="Chain to remove it from (default: Ethereum)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def untrack_wallet(
        interaction: discord.Interaction,
        address: str,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        chain_value = chain.value if chain else "ethereum"
        data = await api_delete(f"/wallets/{address}", params={"chain": chain_value})
        if data is None:
            await cv2_error(interaction, "Failed to remove wallet", "Wallet not found or API error.")
            return
        await cv2_send(
            interaction,
            title=f"Removed from {chain_badge(chain_value)}",
            lines=[data.get("message", f"No longer tracking `{address}`.")],
            color=COLOR_WARN,
        )

    # /whale_alerts
    @bot.tree.command(name="whale_alerts", description="Show recent whale moves")
    @app_commands.describe(
        chain="Filter by chain (leave blank for all chains)",
        count="Number of alerts to show (1-15)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def whale_alerts(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
        count: Optional[int] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)
        count = max(1, min(count or 10, 15))
        params: dict = {"limit": count}
        if chain:
            params["chain"] = chain.value
        data = await api_get("/alerts", params=params)
        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch alerts", "API error or no data available yet.")
            return
        if not data:
            await cv2_send(
                interaction,
                title="No whale alerts yet",
                lines=["No transactions above the threshold have been detected."],
                color=COLOR_INFO,
            )
            return
        title_chain = f" - {chain_badge(chain.value)}" if chain else " - All Chains"
        lines: list[str] = []
        for alert in data:
            cname   = alert.get("chain", "ethereum")
            symbol  = alert.get("token_symbol") or "ETH"
            usd     = fmt_usd(alert["amount_usd"])
            amount  = f"{alert['amount_token']:.4f}"
            link    = tx_link(alert["tx_hash"], cname)
            c_emoji = CHAIN_EMOJI.get(cname, "")
            lines.append(
                f"{dir_emoji(alert['direction'])} **{alert['direction']} {symbol}** - {usd}  {c_emoji}\n"
                f"Amount: {amount} {symbol}\n"
                f"From: `{short_addr(alert['from_address'])}` -> `{short_addr(alert['to_address'])}`\n"
                f"Tx: [{alert['tx_hash'][:14]}...]({link}) | Block {alert['block_number']}"
            )
        await cv2_send(
            interaction,
            title=f"Whale Alerts{title_chain}",
            lines=lines,
            color=chain_color(chain.value if chain else "ethereum"),
            footer="Smart Money Tracker - multi-chain",
        )

    # /smart_money
    @bot.tree.command(name="smart_money", description="Whale activity for a specific token")
    @app_commands.describe(
        token="Token symbol (e.g. USDC) or contract address (0x...)",
        chain="Filter by chain (leave blank for all)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def smart_money(
        interaction: discord.Interaction,
        token: str,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        params: dict = {"limit": 20}
        if chain:
            params["chain"] = chain.value
        data = await api_get(f"/alerts/token/{token}", params=params)
        if data is None:
            await cv2_error(interaction, "API Error", "Could not retrieve token alerts.")
            return
        if not data:
            await cv2_send(
                interaction,
                title=f"No whale activity for {token.upper()}",
                lines=["No large transactions detected for this token yet."],
                color=COLOR_INFO,
            )
            return
        buys     = [a for a in data if a["direction"] == "BUY"]
        sells    = [a for a in data if a["direction"] == "SELL"]
        buy_vol  = sum(a["amount_usd"] for a in buys)
        sell_vol = sum(a["amount_usd"] for a in sells)
        symbol   = data[0].get("token_symbol") or token.upper()
        chains_seen = list({a.get("chain", "ethereum") for a in data})
        lines: list[str] = [
            f"**Buys:** {len(buys)} txs - {fmt_usd(buy_vol)}\n"
            f"**Sells:** {len(sells)} txs - {fmt_usd(sell_vol)}",
            f"**Chains:** {' '.join(chain_badge(c) for c in chains_seen) or '-'}",
            f"**Sentiment:** {'Accumulating' if buy_vol > sell_vol else 'Distributing'}",
            "**Recent transactions:**",
        ]
        for alert in data[:5]:
            cname = alert.get("chain", "ethereum")
            link  = tx_link(alert["tx_hash"], cname)
            lines.append(
                f"{dir_emoji(alert['direction'])} **{alert['direction']}** {fmt_usd(alert['amount_usd'])}  "
                f"{CHAIN_EMOJI.get(cname, '')}\n"
                f"`{short_addr(alert['from_address'])}` -> `{short_addr(alert['to_address'])}`  "
                f"[View tx]({link})"
            )
        await cv2_send(
            interaction,
            title=f"Smart Money - {symbol}",
            lines=lines,
            color=COLOR_BUY if buy_vol >= sell_vol else COLOR_SELL,
            footer="Smart Money Tracker",
        )

    # /wallets
    @bot.tree.command(name="wallets", description="List all tracked whale wallets")
    @app_commands.describe(chain="Filter by chain (leave blank for all)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def wallets(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        params: dict = {}
        if chain:
            params["chain"] = chain.value
        data = await api_get("/wallets", params=params)
        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch wallets", "API error or no data available.")
            return
        if not data:
            await cv2_send(
                interaction,
                title="No wallets tracked",
                lines=["Use `/track_wallet` to start monitoring whale wallets."],
                color=COLOR_INFO,
            )
            return
        title_chain = f" - {chain_badge(chain.value)}" if chain else " - All Chains"
        lines: list[str] = []
        for wallet in data:
            cname = wallet.get("chain", "ethereum")
            addr = wallet.get("address", "")
            label = wallet.get("label")
            status = "Active" if wallet.get("is_active") else "Paused"
            label_str = f" ({label})" if label else ""
            lines.append(
                f"{CHAIN_EMOJI.get(cname, '')} `{short_addr(addr)}`{label_str} - {chain_badge(cname)} - {status}"
            )
        await cv2_send(
            interaction,
            title=f"Tracked Wallets{title_chain}",
            lines=lines,
            color=COLOR_INFO,
            footer=f"Total: {len(data)} wallets - Smart Money Tracker",
        )

    # /trending
    @bot.tree.command(name="trending", description="Top tokens whales are accumulating")
    @app_commands.describe(chain="Filter by chain (leave blank for all)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def trending(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        params: dict = {"limit": 10}
        if chain:
            params["chain"] = chain.value
        data = await api_get("/tokens/trending", params=params)
        if not data:
            await cv2_send(
                interaction,
                title="No trending data yet",
                lines=["Keep tracking wallets - data will appear as whales transact."],
                color=COLOR_INFO,
            )
            return
        lines: list[str] = []
        for i, token in enumerate(data, start=1):
            c = token.get("chain", "ethereum")
            lines.append(
                f"**#{i} {token['token_symbol']}**  {CHAIN_EMOJI.get(c, '')}\n"
                f"{token['buy_count']} buys  /  {token['sell_count']} sells  "
                f"- Vol: {fmt_usd(token['total_volume_usd'])}"
            )
        title = f"Trending{' - ' + chain_badge(chain.value) if chain else ' - All Chains'}"
        await cv2_send(interaction, title=title, lines=lines, color=COLOR_BUY)