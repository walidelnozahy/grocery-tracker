"""Where did I buy something cheapest?

Usage:
    python scripts/cheapest.py milk
    python scripts/cheapest.py "2% milk" --any-brand      # compare every product in that category
    python scripts/cheapest.py produce --category         # match the query against categories only
    python scripts/cheapest.py eggs --since 2026-09-01    # ignore older purchases
    python scripts/cheapest.py bananas --history          # list every purchase, newest first

Prices are compared by UNIT price (e.g. $ per fl oz) when the package size
is known. When it isn't, the per-package price is shown and labeled as such.
"""

import argparse
import re
import sys
from datetime import date
from difflib import SequenceMatcher

from common import Paths, load_json, purchase_rows

STALE_AFTER_DAYS = 60


# ---------------------------------------------------------------- matching

def tokens(text):
    """'Milk, 2% reduced fat' -> ['milk', '2pct', 'reduced', 'fat']"""
    text = text.lower().replace("%", "pct")
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def token_matches(query_token, word):
    """'banana' matches 'bananas'; a small typo like 'chiken' matches 'chicken'."""
    if query_token in word or (len(word) >= 3 and word in query_token):
        return True
    # 0.85 is strict enough that 'bread' does NOT match 'brand' (0.80).
    return len(query_token) >= 5 and SequenceMatcher(None, query_token, word).ratio() >= 0.85


def find_products(query, products, category_only=False):
    """Product keys whose name/key/category contain every word of the query (fuzzy)."""
    query_tokens = tokens(query)
    matches = []
    for key, p in products.items():
        fields = [p.get("category", "")] if category_only else [key, p.get("name", ""), p.get("category", "")]
        words = [w for field in fields for w in tokens(field)]
        if query_tokens and all(any(token_matches(q, w) for w in words) for q in query_tokens):
            matches.append(key)
    return sorted(matches)


# ---------------------------------------------------------------- price observations

def observations(rows):
    """Split purchases into regular-price and sale-price observations.

    Every purchase tells us the REGULAR (shelf) price at that store on that day,
    even when it was bought on sale. Only on-sale purchases are SALE observations.
    """
    regular, sale = [], []
    for r in rows:
        regular.append(make_obs(r, r["regular_price"], from_sale_row=r["on_sale"]))
        if r["on_sale"]:
            sale.append(make_obs(r, r["price_paid"]))
    return regular, sale


def make_obs(row, line_price, from_sale_row=False):
    amount = row["_amount"]
    return {
        "row": row,
        "line_price": line_price,
        "unit_price": line_price / amount if amount else None,
        "package_price": line_price / row["quantity"] if row["_unit_of_sale"] == "each" else None,
        "from_sale_row": from_sale_row,
    }


def cheapest(obs_list, single_product):
    """Return (best_obs, how) where how is 'unit' or 'package', or (None, reason)."""
    with_unit = [o for o in obs_list if o["unit_price"] is not None]
    newest_first = lambda o: (o["row"]["date"], o["row"]["receipt_id"])
    if with_unit:
        best = min(sorted(with_unit, key=newest_first, reverse=True), key=lambda o: o["unit_price"])
        return best, "unit"
    with_package = [o for o in obs_list if o["package_price"] is not None]
    if with_package and single_product:
        best = min(sorted(with_package, key=newest_first, reverse=True), key=lambda o: o["package_price"])
        return best, "package"
    return None, "not comparable (package sizes unknown)"


# ---------------------------------------------------------------- formatting

def days_old(day, today):
    return (today - date.fromisoformat(day)).days


def stale_tag(day, today):
    age = days_old(day, today)
    return f"  [STALE: {age} days old]" if age > STALE_AFTER_DAYS else ""


def describe_price(obs, basis_label):
    row = obs["row"]
    if row["_unit_of_sale"] != "each":
        text = f"${obs['line_price']:.2f} for {row['quantity']:g} {row['_unit_of_sale']}"
    elif row["quantity"] != 1:
        text = f"${obs['line_price']:.2f} for {row['quantity']:g} = ${obs['package_price']:.2f} each"
    else:
        text = f"${obs['line_price']:.2f}"
    if obs["unit_price"] is not None:
        text += f" = ${obs['unit_price']:.4f} per {basis_label}"
    elif row["_unit_of_sale"] == "each":
        text += " per package (size unknown, no unit price)"
    return text


def where(row):
    return f"{row['store_name']} ({row['store_chain']}) on {row['date']}"


def print_report(title, rows, products, unit_basis, today, single_product, show_history):
    print(f"=== {title} ===")
    if not rows:
        print("  No purchases recorded.\n")
        return
    basis = rows[0]["unit_price_basis"]
    basis_label = unit_basis["bases"][basis]["label"] if basis in unit_basis["bases"] else "unit"
    if basis:
        print(f"  Compared by price per {basis_label}.")
    name_of = (lambda r: "") if single_product else (lambda r: f"{r['product_name']}: ")

    regular, sale = observations(rows)
    for label, obs_list in (("Cheapest REGULAR price", regular), ("Cheapest SALE price", sale)):
        if not obs_list:
            print(f"  {label}: none recorded")
            continue
        best, how = cheapest(obs_list, single_product)
        if best is None:
            print(f"  {label}: {how}")
            continue
        r = best["row"]
        extra = ""
        if label.startswith("Cheapest SALE"):
            extra = f" (regular ${r['regular_price']:.2f}, saved ${r['regular_price'] - r['price_paid']:.2f})"
        elif best["from_sale_row"]:
            extra = " (shelf price; it was on sale that day)"
        print(f"  {label}: {name_of(r)}{describe_price(best, basis_label)}{extra}")
        print(f"      at {where(r)}{stale_tag(r['date'], today)}")
        skipped = sum(1 for o in obs_list if o["unit_price"] is None) if how == "unit" else 0
        if skipped:
            print(f"      ({skipped} purchase(s) with unknown size not included in this comparison)")

    print("  Latest price at each store:")
    latest = {}
    for r in rows:  # rows are sorted oldest -> newest, so later rows win
        latest[(r["store_chain"], r["store_name"], r["product_key"])] = r
    for (_, _, _), r in sorted(latest.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        obs = make_obs(r, r["price_paid"])
        sale_note = " SALE" if r["on_sale"] else ""
        print(f"    {r['store_name']} ({r['store_chain']}): {name_of(r)}{describe_price(obs, basis_label)}"
              f"{sale_note} on {r['date']}{stale_tag(r['date'], today)}")

    if show_history:
        print("  History (newest first):")
        for r in sorted(rows, key=lambda r: (r["date"], r["receipt_id"], r["_line_no"]), reverse=True):
            obs = make_obs(r, r["price_paid"])
            sale_note = f" SALE (regular ${r['regular_price']:.2f})" if r["on_sale"] else ""
            print(f"    {r['date']}  {r['store_name']}: {name_of(r)}{describe_price(obs, basis_label)}"
                  f"{sale_note}  [{r['receipt_id']}]")
    print()


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description="Find where you bought something cheapest.")
    parser.add_argument("query", help="what to look for, e.g. 'milk' or 'chicken breast'")
    parser.add_argument("--category", action="store_true", help="match the query against categories only")
    parser.add_argument("--any-brand", action="store_true",
                        help="compare ALL products in the matching category by unit price")
    parser.add_argument("--since", help="only purchases on or after this date (YYYY-MM-DD)")
    parser.add_argument("--history", action="store_true", help="list every purchase, newest first")
    parser.add_argument("--today", help=argparse.SUPPRESS)  # for tests: pretend today is this date
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)

    paths = Paths(args.root)
    products = load_json(paths.products)
    unit_basis = load_json(paths.unit_basis)
    today = date.fromisoformat(args.today) if args.today else date.today()

    rows = purchase_rows(paths)
    if args.since:
        date.fromisoformat(args.since)  # fails loudly on a bad date
        rows = [r for r in rows if r["date"] >= args.since]

    matched = find_products(args.query, products, category_only=args.category)
    if not matched:
        print(f"No product matches '{args.query}'. Try a shorter word, or --category.")
        return 1

    if args.any_brand:
        categories = sorted({products[k]["category"] for k in matched})
        groups = []
        for cat in categories:
            keys = {k for k, p in products.items() if p["category"] == cat}
            groups.append((f"Any brand, category '{cat}'", keys, False))
    else:
        groups = [(f"{products[k]['name']} [{k}]", {k}, True) for k in matched]

    print(f"Today is {today.isoformat()}. Prices older than {STALE_AFTER_DAYS} days are marked STALE.")
    if args.since:
        print(f"Only purchases since {args.since}.")
    print()
    found_any = False
    for title, keys, single in groups:
        group_rows = [r for r in rows if r["product_key"] in keys]
        if not group_rows and not single:
            print(f"=== {title} ===\n  No purchases recorded.\n")
            continue
        if not group_rows:
            continue  # don't clutter output with every never-bought product
        found_any = True
        print_report(title, group_rows, products, unit_basis, today, single, args.history)
    if not found_any:
        print(f"Matched {len(matched)} product(s) but none have been purchased"
              f"{' since ' + args.since if args.since else ''}: {', '.join(matched)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
