"""
data/generate_price_alerts.py
------------------------------
Auto-generate price alerts for top CoinGecko tokens.

Fetches the top N tokens by market cap for a given chain, then creates
two alerts per token:
  - ABOVE  current_price × (1 + above_pct/100)   e.g. +15% breakout
  - BELOW  current_price × (1 - below_pct/100)   e.g. -15% support

Usage:
  # Top 20 Ethereum tokens, ±15% alerts
  python data/generate_price_alerts.py

  # Top 50 tokens on Base, +10% / -20%
  python data/generate_price_alerts.py --chain base --count 50 --above 10 --below 20

  # Preview without creating
  python data/generate_price_alerts.py --dry-run

  # Only above or only below
  python data/generate_price_alerts.py --only above
"""

from __future__ import annotations

import argparse
import time
import httpx

DEFAULT_API   = "http://localhost:8000"
COINGECKO_API = "https://api.coingecko.com/api/v3"

# CoinGecko platform IDs for each chain
PLATFORMS = {
    "ethereum": "ethereum",
    "base":     "base",
    "arbitrum": "arbitrum-one",
    "bsc":      "binance-smart-chain",
    "polygon":  "polygon-pos",
    "optimism": "optimistic-ethereum",
}


def fetch_top_tokens(platform: str, count: int) -> list[dict]:
    """Fetch top tokens by market cap from CoinGecko for the given platform."""
    print(f"Fetching top {count} tokens on {platform} from CoinGecko...")
    tokens = []
    page = 1
    per_page = min(count, 250)

    while len(tokens) < count:
        url = (
            f"{COINGECKO_API}/coins/markets"
            f"?vs_currency=usd"
            f"&category={platform}-ecosystem"
            f"&order=market_cap_desc"
            f"&per_page={per_page}"
            f"&page={page}"
            f"&sparkline=false"
        )
        try:
            resp = httpx.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  CoinGecko error: {exc}")
            break

        if not data:
            break

        for coin in data:
            # Skip tokens without a contract address or current price
            if not coin.get("current_price") or not coin.get("id"):
                continue
            tokens.append(coin)
            if len(tokens) >= count:
                break

        if len(data) < per_page:
            break  # no more pages

        page += 1
        time.sleep(1.5)  # respect CoinGecko rate limit

    print(f"  Got {len(tokens)} tokens\n")
    return tokens


def resolve_contract_address(coin_id: str, platform: str) -> str | None:
    """Fetch the contract address for a token on the given platform."""
    url = f"{COINGECKO_API}/coins/{coin_id}?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        platforms = data.get("platforms", {})
        return platforms.get(platform) or None
    except Exception:
        return None


def create_alert(api_url: str, payload: dict, dry_run: bool, desc: str) -> bool:
    if dry_run:
        print(f"    DRY-RUN — would create: {desc}")
        return True
    try:
        resp = httpx.post(f"{api_url}/api/v1/price-alerts", json=payload, timeout=10)
        if resp.status_code == 201:
            print(f"    ✅ #{resp.json()['id']} — {desc}")
            return True
        elif resp.status_code == 409:
            print(f"    ⚠️  Already exists — {desc}")
            return True
        else:
            print(f"    ❌ {resp.status_code} — {resp.text[:100]}")
            return False
    except Exception as exc:
        print(f"    ❌ Request failed: {exc}")
        return False


def generate(
    chain: str,
    count: int,
    above_pct: float,
    below_pct: float,
    only: str | None,
    api_url: str,
    dry_run: bool,
) -> None:
    platform = PLATFORMS.get(chain)
    if not platform:
        print(f"Unsupported chain: {chain}. Supported: {list(PLATFORMS.keys())}")
        return

    tokens = fetch_top_tokens(platform, count)
    if not tokens:
        print("No tokens found. Try a different chain or check CoinGecko API.")
        return

    created = skipped = errors = 0

    for i, coin in enumerate(tokens, 1):
        symbol = coin.get("symbol", "???").upper()
        price  = coin.get("current_price", 0.0)
        name   = coin.get("name", symbol)

        print(f"[{i}/{len(tokens)}] {symbol} — ${price:,.8g}")

        # Resolve contract address
        contract = resolve_contract_address(coin["id"], platform)
        if not contract:
            print(f"  SKIP — no contract address on {platform}")
            skipped += 1
            time.sleep(0.5)
            continue

        time.sleep(1.2)  # CoinGecko rate limit between detail calls

        # ABOVE alert
        if only != "below":
            target_above = round(price * (1 + above_pct / 100), 10)
            ok = create_alert(api_url, {
                "chain":            chain,
                "token_address":    contract,
                "token_symbol":     symbol,
                "condition":        "above",
                "target_price_usd": target_above,
                "label":            f"{name} +{above_pct:.0f}% breakout",
            }, dry_run, f"{symbol} above ${target_above:,.8g} (+{above_pct}%)")
            created += 1 if ok else 0
            errors  += 0 if ok else 1

        # BELOW alert
        if only != "above":
            target_below = round(price * (1 - below_pct / 100), 10)
            ok = create_alert(api_url, {
                "chain":            chain,
                "token_address":    contract,
                "token_symbol":     symbol,
                "condition":        "below",
                "target_price_usd": target_below,
                "label":            f"{name} -{below_pct:.0f}% support",
            }, dry_run, f"{symbol} below ${target_below:,.8g} (-{below_pct}%)")
            created += 1 if ok else 0
            errors  += 0 if ok else 1

    print(f"\nDone — ✅ {created} created  ⚠️  {skipped} skipped  ❌ {errors} errors")


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-generate price alerts from CoinGecko top tokens")
    parser.add_argument("--chain",   default="ethereum",        help="Chain (default: ethereum)")
    parser.add_argument("--count",   type=int, default=20,      help="Number of top tokens (default: 20)")
    parser.add_argument("--above",   type=float, default=15.0,  help="Above alert %% from current price (default: 15)")
    parser.add_argument("--below",   type=float, default=15.0,  help="Below alert %% from current price (default: 15)")
    parser.add_argument("--only",    choices=["above", "below"], help="Create only above or below alerts")
    parser.add_argument("--api-url", default=DEFAULT_API,       help=f"API base URL (default: {DEFAULT_API})")
    parser.add_argument("--dry-run", action="store_true",       help="Preview without creating alerts")
    args = parser.parse_args()

    generate(
        chain=args.chain,
        count=args.count,
        above_pct=args.above,
        below_pct=args.below,
        only=args.only,
        api_url=args.api_url.rstrip("/"),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
