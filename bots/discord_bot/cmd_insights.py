"""
bots/discord_bot/cmd_insights.py
----------------------------------
High-value insight slash commands using only existing DB tables.

Commands
--------
/daily_summary    — 24 h whale activity digest (also auto-posted at 8 AM UTC)
/top_wallets      — top 10 wallets by realized P&L, optional [chain] filter
/trending_buys    — most bought tokens in last 4 h, optional [chain] filter
/whale_leaderboard— top 10 wallets by total volume today, optional [chain] filter
/wallet_score     — smart wallet score: win rate, avg P&L, volume
"""

from __future__ import annotations

from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES,
    CHAIN_EMOJI,
    COLOR_BUY,
    COLOR_INFO,
    COLOR_WARN,
    api_get,
    chain_badge,
    cv2_error,
    cv2_send,
    fmt_usd,
    short_addr,
)


def setup_insights(bot: commands.Bot) -> None:
    """Register all insight slash commands."""

    # ── /daily_summary ────────────────────────────────────────────────────────

    @bot.tree.command(
        name="daily_summary",
        description="24 h whale activity digest — top moves, trending tokens, exchange flows",
    )
    async def daily_summary(interaction) -> None:
        await interaction.response.defer()
        data = await api_get("/insights/daily-summary")

        if not isinstance(data, dict):
            await cv2_error(interaction, "❌ Could not load daily summary", "API unavailable.")
            return

        date_label   = data.get("date_label", "")
        alert_count  = data.get("alert_count_24h", 0)
        total_vol    = data.get("total_volume_24h_usd", 0.0)
        top_moves    = data.get("top_moves") or []
        most_token   = data.get("most_accumulated_token")
        most_vol     = data.get("most_accumulated_volume", 0.0)
        flow_dir     = data.get("top_flow_direction")
        flow_token   = data.get("top_flow_token")
        flow_usd     = data.get("top_flow_amount_usd", 0.0)

        lines = [
            f"**📊 {alert_count} whale moves  ·  Total volume: {fmt_usd(total_vol)}**",
        ]

        if most_token:
            lines.append(
                f"🔁 Most accumulated: **{most_token}** ({fmt_usd(most_vol)} buy vol)"
            )

        if flow_dir and flow_token:
            flow_emoji = "🔴" if flow_dir == "OUTFLOW" else "🟢"
            flow_label = "sending to exchanges" if flow_dir == "OUTFLOW" else "pulling from exchanges"
            lines.append(
                f"{flow_emoji} Top exchange flow: **{flow_token}** {fmt_usd(flow_usd)} — {flow_label}"
            )

        if top_moves:
            lines.append("**🐋 Top 5 moves today:**")
            for i, move in enumerate(top_moves[:5], start=1):
                d_emoji = {"BUY": "🟢", "SELL": "🔴", "SEND": "🔵"}.get(
                    move.get("direction", "SEND"), "⚪"
                )
                c_emoji = CHAIN_EMOJI.get(move.get("chain", ""), "")
                lines.append(
                    f"`#{i}` {d_emoji} **{move.get('token', 'native')}**  ·  "
                    f"{fmt_usd(move.get('amount_usd', 0))} {c_emoji}"
                )
        else:
            lines.append("*No whale moves recorded in the last 24 h.*")

        await cv2_send(
            interaction,
            title=f"📅 Daily Whale Summary  ·  {date_label}",
            lines=lines,
            color=COLOR_INFO,
            footer="Smart Money Tracker — auto-posted daily at 08:00 UTC",
        )

    # ── /top_wallets ──────────────────────────────────────────────────────────

    @bot.tree.command(
        name="top_wallets",
        description="Top 10 wallets by realized P&L from their trading history",
    )
    @app_commands.describe(chain="Filter by chain (leave blank for all chains)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def top_wallets(interaction, chain: app_commands.Choice[str] | None = None) -> None:
        await interaction.response.defer()

        params = {}
        if chain:
            params["chain"] = chain.value

        data = await api_get("/insights/top-wallets", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "❌ Could not load wallet rankings", "API unavailable.")
            return

        if not data:
            chain_label = chain_badge(chain.value) if chain else "All Chains"
            await cv2_send(
                interaction,
                title=f"🏆 Top Wallets — {chain_label}",
                lines=["No P&L data yet. Tracking starts once whale alerts are recorded."],
                color=COLOR_INFO,
            )
            return

        chain_label = chain_badge(chain.value) if chain else "All Chains"
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for row in data:
            rank   = row.get("rank", 0)
            addr   = row.get("wallet_address", "")
            pnl    = row.get("total_pnl_usd", 0.0)
            txs    = row.get("tx_count", 0)
            pos    = row.get("positions", 0)
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            medal  = medals[rank - 1] if rank <= 3 else f"`#{rank}`"
            lines.append(
                f"{medal} `{short_addr(addr)}`  ·  "
                f"{pnl_emoji} **{pnl_sign}{fmt_usd(pnl)}**  ·  "
                f"{txs} txs · {pos} tokens"
            )

        await cv2_send(
            interaction,
            title=f"🏆 Top Wallets by P&L — {chain_label}",
            lines=lines,
            color=COLOR_BUY,
            footer="Realized P&L from wallet_positions · Smart Money Tracker",
        )

    # ── /trending_buys ────────────────────────────────────────────────────────

    @bot.tree.command(
        name="trending_buys",
        description="Most bought tokens by whales in the last 4 hours",
    )
    @app_commands.describe(chain="Filter by chain (leave blank for all chains)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def trending_buys(interaction, chain: app_commands.Choice[str] | None = None) -> None:
        await interaction.response.defer()

        params = {"hours": 4}
        if chain:
            params["chain"] = chain.value

        data = await api_get("/insights/trending-buys", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "❌ Could not load trending data", "API unavailable.")
            return

        if not data:
            chain_label = chain_badge(chain.value) if chain else "All Chains"
            await cv2_send(
                interaction,
                title=f"🔥 Trending Buys — {chain_label}",
                lines=["No whale buy activity in the last 4 h."],
                color=COLOR_BUY,
            )
            return

        chain_label = chain_badge(chain.value) if chain else "All Chains"
        lines = []
        for row in data:
            rank   = row.get("rank", 0)
            token  = row.get("token_symbol", "?")
            c_name = row.get("chain", "ethereum")
            buys   = row.get("buy_count", 0)
            vol    = row.get("buy_volume_usd", 0.0)
            buyers = row.get("unique_buyers", 0)
            c_emoji = CHAIN_EMOJI.get(c_name, "")
            lines.append(
                f"`#{rank}` **{token}** {c_emoji}  ·  "
                f"🟢 {buys} buys  ·  {fmt_usd(vol)}  ·  {buyers} wallets"
            )

        await cv2_send(
            interaction,
            title=f"🔥 Trending Buys (Last 4h) — {chain_label}",
            lines=lines,
            color=COLOR_BUY,
            footer="Whale BUY activity from whale_alerts · Smart Money Tracker",
        )

    # ── /whale_leaderboard ────────────────────────────────────────────────────

    @bot.tree.command(
        name="whale_leaderboard",
        description="Top 10 wallets by total trading volume in the last 24 hours",
    )
    @app_commands.describe(chain="Filter by chain (leave blank for all chains)")
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def whale_leaderboard(interaction, chain: app_commands.Choice[str] | None = None) -> None:
        await interaction.response.defer()

        params = {}
        if chain:
            params["chain"] = chain.value

        data = await api_get("/insights/whale-leaderboard", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "❌ Could not load leaderboard", "API unavailable.")
            return

        if not data:
            chain_label = chain_badge(chain.value) if chain else "All Chains"
            await cv2_send(
                interaction,
                title=f"🏅 Whale Leaderboard — {chain_label}",
                lines=["No whale activity in the last 24 h."],
                color=COLOR_WARN,
            )
            return

        chain_label = chain_badge(chain.value) if chain else "All Chains"
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for row in data:
            rank   = row.get("rank", 0)
            addr   = row.get("wallet_address", "")
            total  = row.get("total_volume_usd", 0.0)
            txs    = row.get("tx_count", 0)
            buy_v  = row.get("buy_volume_usd", 0.0)
            sell_v = row.get("sell_volume_usd", 0.0)
            medal  = medals[rank - 1] if rank <= 3 else f"`#{rank}`"
            lines.append(
                f"{medal} `{short_addr(addr)}`  ·  "
                f"**{fmt_usd(total)}** total  ·  "
                f"🟢 {fmt_usd(buy_v)}  🔴 {fmt_usd(sell_v)}  ·  {txs} txs"
            )

        await cv2_send(
            interaction,
            title=f"🏅 Whale Leaderboard (24h) — {chain_label}",
            lines=lines,
            color=COLOR_WARN,
            footer="Total USD volume from whale_alerts · Smart Money Tracker",
        )

    # ── /wallet_score ─────────────────────────────────────────────────────────

    @bot.tree.command(
        name="wallet_score",
        description="Smart wallet score: win rate, avg P&L, and volume for any tracked wallet",
    )
    @app_commands.describe(
        address="Wallet address (0x…)",
        chain="Filter by chain (leave blank for all chains)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def wallet_score(
        interaction,
        address: str,
        chain: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer()

        params = {}
        if chain:
            params["chain"] = chain.value

        data = await api_get(f"/insights/wallet-score/{address}", params=params)

        if not isinstance(data, dict):
            await cv2_error(interaction, "❌ Could not compute wallet score", "API unavailable.")
            return

        addr       = data.get("wallet_address", address)
        chain_disp = chain_badge(chain.value) if chain else "All Chains"
        total_sig  = data.get("total_signals", 0)
        win_rate   = data.get("win_rate_pct")
        avg_pnl    = data.get("avg_pnl_72h_pct")
        best       = data.get("best_trade_pct")
        worst      = data.get("worst_trade_pct")
        tx_count   = data.get("tx_count", 0)
        total_vol  = data.get("total_volume_usd", 0.0)
        has_bt     = data.get("has_backtest_data", False)

        lines = [
            f"**👤 `{short_addr(addr)}`  ·  {chain_disp}**",
            f"💰 Total volume: **{fmt_usd(total_vol)}**  ·  {tx_count} transactions",
        ]

        if has_bt:
            wr_str   = f"**{win_rate:.0f}%**" if win_rate is not None else "—"
            pnl_str  = f"**{avg_pnl:+.1f}%**" if avg_pnl is not None else "—"
            best_str  = f"{best:+.1f}%" if best is not None else "—"
            worst_str = f"{worst:+.1f}%" if worst is not None else "—"
            lines += [
                f"🎯 Signal win rate: {wr_str}  ·  {data.get('total_correct', 0)}/{total_sig} correct",
                f"📊 Avg P&L at 72h: {pnl_str}",
                f"🏆 Best trade: `{best_str}`  ·  Worst: `{worst_str}`",
            ]
        else:
            lines.append(
                "*No backtest data yet — accuracy tracking starts once signals are processed.*"
            )

        await cv2_send(
            interaction,
            title=f"🧠 Wallet Score  ·  {short_addr(addr)}",
            lines=lines,
            color=COLOR_BUY if (win_rate or 0) >= 50 else COLOR_INFO,
            footer=f"Based on {total_sig} signal(s) · Smart Money Tracker",
        )
