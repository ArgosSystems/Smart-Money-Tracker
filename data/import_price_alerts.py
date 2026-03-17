"""
data/import_price_alerts.py
----------------------------
Bulk-import price alerts from a CSV file.

CSV format (header required):
  chain,token_address,token_symbol,condition,target_price_usd,label
  ethereum,0x...,PEPE,above,0.00002,PEPE ATH target
  ethereum,0x...,PEPE,below,0.000010,PEPE support
  base,0x...,BRETT,above,0.50,BRETT $0.50

Usage:
  python data/import_price_alerts.py data/price_alerts.csv
  python data/import_price_alerts.py data/price_alerts.csv --api-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import csv
import sys
import httpx

DEFAULT_API = "http://localhost:8000"
ENDPOINT    = "/api/v1/price-alerts"


def import_csv(path: str, api_url: str, dry_run: bool) -> None:
    ok = skipped = errors = 0

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} rows in {path}\n")

    for i, row in enumerate(rows, 1):
        # Strip whitespace from all values
        row = {k.strip(): v.strip() for k, v in row.items()}

        chain           = row.get("chain", "ethereum")
        token_address   = row.get("token_address", "")
        token_symbol    = row.get("token_symbol", "")
        condition       = row.get("condition", "")
        target_price    = row.get("target_price_usd", "")
        label           = row.get("label") or None

        # Basic validation
        if not token_address or not token_symbol or not condition or not target_price:
            print(f"  [{i}] SKIP — missing required fields: {row}")
            skipped += 1
            continue

        if condition not in ("above", "below"):
            print(f"  [{i}] SKIP — condition must be 'above' or 'below', got: {condition}")
            skipped += 1
            continue

        try:
            target_price_usd = float(target_price)
        except ValueError:
            print(f"  [{i}] SKIP — invalid target_price_usd: {target_price}")
            skipped += 1
            continue

        payload = {
            "chain":            chain,
            "token_address":    token_address,
            "token_symbol":     token_symbol.upper(),
            "condition":        condition,
            "target_price_usd": target_price_usd,
        }
        if label:
            payload["label"] = label

        desc = f"{token_symbol.upper()} {condition} ${target_price_usd} on {chain}"

        if dry_run:
            print(f"  [{i}] DRY-RUN — would create: {desc}")
            ok += 1
            continue

        try:
            resp = httpx.post(f"{api_url}{ENDPOINT}", json=payload, timeout=10)
            if resp.status_code == 201:
                data = resp.json()
                print(f"  [{i}] ✅ Created #{data['id']} — {desc}")
                ok += 1
            elif resp.status_code == 409:
                print(f"  [{i}] ⚠️  Already exists — {desc}")
                skipped += 1
            else:
                print(f"  [{i}] ❌ Error {resp.status_code} — {resp.text[:120]}")
                errors += 1
        except Exception as exc:
            print(f"  [{i}] ❌ Request failed: {exc}")
            errors += 1

    print(f"\nDone — ✅ {ok} created  ⚠️  {skipped} skipped  ❌ {errors} errors")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk import price alerts from CSV")
    parser.add_argument("csv_file", help="Path to CSV file")
    parser.add_argument("--api-url", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API})")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating alerts")
    args = parser.parse_args()

    import_csv(args.csv_file, args.api_url.rstrip("/"), args.dry_run)


if __name__ == "__main__":
    main()
