"""Rebuild data/items.csv from all receipts + products.json + unit_basis.json.

Usage:
    python scripts/build_ledger.py

items.csv is GENERATED. Never edit it by hand -- fix the receipt JSON,
aliases.json or products.json instead and run this again.
"""

import argparse
import csv
import sys

from common import LEDGER_COLUMNS, Paths, purchase_rows


def format_cell(column, value):
    if value is None:
        return ""  # e.g. unit_price when the package size is unknown -- blank, never 0
    if isinstance(value, bool):
        return "true" if value else "false"
    if column in ("price_paid", "regular_price"):
        return f"{value:.2f}"
    if column == "unit_price":
        return f"{value:.4f}"
    if column == "quantity":
        return f"{value:g}"
    return str(value)


def build_ledger(paths):
    rows = purchase_rows(paths)
    paths.ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.ledger, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(LEDGER_COLUMNS)
        for row in rows:
            writer.writerow([format_cell(col, row[col]) for col in LEDGER_COLUMNS])
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)
    paths = Paths(args.root)
    rows = build_ledger(paths)
    no_unit = sum(1 for r in rows if r["unit_price"] is None)
    print(f"Wrote {len(rows)} rows to {paths.ledger}")
    if no_unit:
        print(f"  {no_unit} row(s) have no unit price (package size unknown or units not comparable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
