"""
admin/import_pro_labels.py
---------------------------
Bulk-import PRO smart labels from a CSV file.

CSV format (header row required):
    address,name,entity_type,chain,tier

The `tier` column is accepted but always forced to "pro".

Usage
-----
    python admin/import_pro_labels.py labels.csv
    python admin/import_pro_labels.py labels.csv --update    # overwrite existing rows
    python admin/import_pro_labels.py labels.csv --dry-run   # preview, no DB writes
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from sqlalchemy import select

from api.models import AsyncSessionLocal, SmartLabel, init_db

REQUIRED_COLUMNS = {"address", "name", "entity_type", "chain"}


async def import_labels(csv_path: str, update: bool, dry_run: bool) -> None:
    path = Path(csv_path)
    if not path.exists():
        print(f"❌  File not found: {csv_path}")
        sys.exit(1)

    # Parse CSV first so we catch format errors before touching the DB
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("❌  CSV file is empty.")
            sys.exit(1)

        missing = REQUIRED_COLUMNS - {c.strip().lower() for c in reader.fieldnames}
        if missing:
            print(f"❌  CSV is missing required columns: {', '.join(sorted(missing))}")
            sys.exit(1)

        for i, raw in enumerate(reader, start=2):  # row 1 = header
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not row.get("address"):
                print(f"   Row {i}: skipping — empty address")
                continue
            rows.append(row)

    if not rows:
        print("⚠️  No data rows found in CSV.")
        return

    mode_label = "[DRY-RUN] " if dry_run else ""
    print(f"{mode_label}Processing {len(rows)} rows from {path.name} …\n")

    if not dry_run:
        await init_db()

    inserted = updated = skipped = errors = 0

    if dry_run:
        # In dry-run mode just show what would happen
        seen: set[tuple[str, str]] = set()
        for row in rows:
            address = row["address"].lower()
            chain = row["chain"].lower() or "ethereum"
            key = (address, chain)
            if key in seen:
                print(f"   [DRY-RUN] DUPLICATE in CSV: [{chain}] {address}")
                skipped += 1
                continue
            seen.add(key)
            print(
                f"   [DRY-RUN] INSERT  [{chain}] {address}  →  {row['name']} ({row['entity_type']}, pro)"
            )
            inserted += 1
    else:
        async with AsyncSessionLocal() as db:
            for i, row in enumerate(rows, start=1):
                try:
                    address = row["address"].lower()
                    chain = (row.get("chain") or "ethereum").lower()
                    name = row["name"]
                    entity_type = row["entity_type"]

                    existing = await db.scalar(
                        select(SmartLabel).where(
                            SmartLabel.address == address,
                            SmartLabel.chain == chain,
                        )
                    )

                    if existing:
                        if update:
                            existing.name = name
                            existing.entity_type = entity_type
                            existing.tier = "pro"
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        db.add(
                            SmartLabel(
                                address=address,
                                chain=chain,
                                name=name,
                                entity_type=entity_type,
                                tier="pro",
                            )
                        )
                        inserted += 1

                except Exception as exc:
                    print(f"   ❌  Row {i} error ({row.get('address', '?')}): {exc}")
                    errors += 1

            if inserted > 0 or updated > 0:
                await db.commit()

    print(
        f"\n{'[DRY-RUN] ' if dry_run else ''}Done.\n"
        f"  ✅  Inserted : {inserted}\n"
        f"  🔄  Updated  : {updated}\n"
        f"  ⏭   Skipped  : {skipped}\n"
        f"  ❌  Errors   : {errors}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-import PRO smart labels from a CSV file."
    )
    parser.add_argument("csv_file", help="Path to the CSV file")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Overwrite existing (address, chain) entries instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without writing to the database",
    )
    args = parser.parse_args()
    asyncio.run(import_labels(args.csv_file, args.update, args.dry_run))


if __name__ == "__main__":
    main()
