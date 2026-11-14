"""Rebuild data/items.csv (purchases) and data/price_tags.csv (shelf prices seen).

Usage:
    python scripts/build_ledger.py

Both CSVs are GENERATED from data/receipts/, data/price_tags/, products.json
and unit_basis.json. Never edit them by hand -- fix the JSON instead and run
this again.
"""

import argparse
import csv
import sys

from common import LEDGER_COLUMNS, TAG_LEDGER_COLUMNS, Paths, price_tag_rows, purchase_rows


def format_cell(column, value):
    if value is None:
        return ""  # e.g. unit_price when the package size is unknown -- blank, never 0
    if isinstance(value, bool):
        return "true" if value else "false"
    if column in ("price_paid", "regular_price", "price"):
        return f"{value:.2f}"
    if column == "unit_price":
        return f"{value:.4f}"
    if column == "quantity":
        return f"{value:g}"
    return str(value)


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([format_cell(col, row[col]) for col in columns])


def build_ledger(paths):
    rows = purchase_rows(paths)
    write_csv(paths.ledger, LEDGER_COLUMNS, rows)
    tag_rows = price_tag_rows(paths)
    write_csv(paths.tag_ledger, TAG_LEDGER_COLUMNS, tag_rows)
    return rows, tag_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)
    paths = Paths(args.root)
    rows, tag_rows = build_ledger(paths)
    print(f"Wrote {len(rows)} purchase rows to {paths.ledger}")
    print(f"Wrote {len(tag_rows)} shelf-tag rows to {paths.tag_ledger}")
    no_unit = sum(1 for r in rows + tag_rows if r["unit_price"] is None)
    if no_unit:
        print(f"  {no_unit} row(s) have no unit price (package size unknown or units not comparable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
