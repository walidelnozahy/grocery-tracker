"""Where did I buy (or see) something cheapest?

Usage:
    python scripts/cheapest.py milk
    python scripts/cheapest.py "2% milk" --any-brand      # compare every product in that category
    python scripts/cheapest.py produce --category         # match the query against categories only
    python scripts/cheapest.py eggs --since 2026-09-01    # ignore older prices
    python scripts/cheapest.py bananas --history          # list every price, newest first

Two kinds of prices are shown, always labeled:
  - "paid":      from receipts (things you actually bought)
  - "shelf tag": from photos of shelf price tags (prices you saw, not bought)

Prices are compared by UNIT price (e.g. $ per fl oz) when the package size
is known. When it isn't, the per-package price is shown and labeled as such.
"""

import argparse
import re
import sys
from datetime import date
from difflib import SequenceMatcher

from common import Paths, load_json, price_tag_rows, purchase_rows

STALE_AFTER_DAYS = 60
SOURCES = (("paid", "paid"), ("tag", "shelf tag"))


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
    """Product keys whose name/key/category/brand/model contain every word of the query (fuzzy)."""
    query_tokens = tokens(query)
    matches = []
    for key, p in products.items():
        if category_only:
            fields = [p.get("category", "")]
        else:
            fields = [key, p.get("name", ""), p.get("category", ""), p.get("brand") or "", p.get("model") or ""]
        words = [w for field in fields for w in tokens(field)]
        if query_tokens and all(any(token_matches(q, w) for w in words) for q in query_tokens):
            matches.append(key)
    return sorted(matches)


# ---------------------------------------------------------------- price observations

def make_obs(row, line_price, source, from_sale=False):
    """One price we know about. `line_price` is for the whole receipt line / one tag."""
    amount = row["_amount"]
    return {
        "row": row,
        "source": source,  # "paid" or "tag"
        "line_price": line_price,
        "unit_price": line_price / amount if amount else None,
        "package_price": line_price / row["quantity"] if row["_unit_of_sale"] == "each" else None,
        "from_sale": from_sale,
    }


def observations(purchases, tags):
    """Split every known price into REGULAR and SALE observations.

    Purchases: every purchase tells us the regular (shelf) price that day, even if
    bought on sale; only on-sale purchases are SALE observations.
    Shelf tags: same idea, using the tag's price and its printed regular price.
    """
    regular, sale = [], []
    for r in purchases:
        regular.append(make_obs(r, r["regular_price"], "paid", from_sale=r["on_sale"]))
        if r["on_sale"]:
            sale.append(make_obs(r, r["price_paid"], "paid"))
    for t in tags:
        if t["on_sale"]:
            sale.append(make_obs(t, t["price"], "tag"))
            if t["regular_price"] is not None:
                regular.append(make_obs(t, t["regular_price"], "tag", from_sale=True))
        else:
            regular.append(make_obs(t, t["price"], "tag"))
    return regular, sale


def row_id(row):
    return row.get("receipt_id") or row.get("tag_id")


def cheapest(obs_list, single_product):
    """Return (best_obs, how) where how is 'unit' or 'package', or (None, reason)."""
    with_unit = [o for o in obs_list if o["unit_price"] is not None]
    newest_first = lambda o: (o["row"]["date"], row_id(o["row"]))
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
    unit = row["_unit_of_sale"]
    if unit != "each" and obs["source"] == "tag":
        text = f"${obs['line_price']:.2f} per {unit}"
    elif unit != "each":
        text = f"${obs['line_price']:.2f} for {row['quantity']:g} {unit}"
    elif row["quantity"] != 1:
        text = f"${obs['line_price']:.2f} for {row['quantity']:g} = ${obs['package_price']:.2f} each"
    else:
        text = f"${obs['line_price']:.2f}"
    if obs["unit_price"] is None:
        if unit == "each":
            text += " per package (size unknown, no unit price)"
        return text
    if obs["package_price"] is not None and row["_amount"] == row["quantity"]:
        return text  # one item per package (e.g. a TV): the unit price would just repeat the price
    if obs["source"] == "tag" and unit != "each" and basis_label == unit:
        return text  # already says "per lb"
    text += f" = ${obs['unit_price']:.4f} per {basis_label}"
    return text


def where(row):
    return f"{row['store_name']} ({row['store_chain']}) on {row['date']}"


def sale_details(obs):
    row = obs["row"]
    parts = []
    if obs["source"] == "tag" and row["sale_label"]:
        parts.append(row["sale_label"])
    regular = row["regular_price"]
    paid = row["price_paid"] if obs["source"] == "paid" else row["price"]
    if regular is not None:
        parts.append(f"regular ${regular:.2f}, saves ${regular - paid:.2f}")
    if obs["source"] == "tag" and row["sale_ends"]:
        parts.append(f"until {row['sale_ends']}")
    return f" ({'; '.join(parts)})" if parts else ""


def print_best(label, obs_list, single_product, basis_label, today, name_of):
    print(f"  {label}:")
    if not obs_list:
        print("      none recorded")
        return
    for source, source_label in SOURCES:
        subset = [o for o in obs_list if o["source"] == source]
        if not subset:
            continue
        best, how = cheapest(subset, single_product)
        prefix = f"      {source_label + ':':<11}"
        if best is None:
            print(f"{prefix}{how}")
            continue
        r = best["row"]
        if label.startswith("Cheapest SALE"):
            extra = sale_details(best)
        elif best["from_sale"]:
            extra = " (regular price; it was on sale that day)"
        else:
            extra = ""
        print(f"{prefix}{name_of(r)}{describe_price(best, basis_label)}{extra}")
        print(f"{' ' * 17}at {where(r)}{stale_tag(r['date'], today)}")
        skipped = sum(1 for o in subset if o["unit_price"] is None) if how == "unit" else 0
        if skipped:
            print(f"{' ' * 17}({skipped} price(s) with unknown size not included in this comparison)")


def current_obs(row, source):
    """The price actually charged / shown, for 'latest' and 'history' lists."""
    price = row["price_paid"] if source == "paid" else row["price"]
    return make_obs(row, price, source)


def print_report(title, purchases, tags, unit_basis, today, single_product, show_history):
    print(f"=== {title} ===")
    rows = purchases + tags
    if not rows:
        print("  No prices recorded.\n")
        return
    basis = rows[0]["unit_price_basis"]
    basis_label = unit_basis["bases"][basis]["label"] if basis in unit_basis["bases"] else "unit"
    if basis:
        print(f"  Compared by price per {basis_label}.")
    name_of = (lambda r: "") if single_product else (lambda r: f"{r['product_name']}: ")

    regular, sale = observations(purchases, tags)
    print_best("Cheapest REGULAR price", regular, single_product, basis_label, today, name_of)
    print_best("Cheapest SALE price", sale, single_product, basis_label, today, name_of)

    # Every price we know, labeled with where it came from.
    everything = [(r, "paid") for r in purchases] + [(t, "tag") for t in tags]
    everything.sort(key=lambda x: (x[0]["date"], row_id(x[0]), x[0]["_line_no"]))

    print("  Latest price at each store:")
    latest = {}
    for r, source in everything:  # oldest -> newest, so later prices win
        latest[(r["store_name"], r["store_chain"], r["product_key"])] = (r, source)
    for key in sorted(latest):
        r, source = latest[key]
        obs = current_obs(r, source)
        sale_note = " SALE" if r["on_sale"] else ""
        source_note = "shelf tag" if source == "tag" else "paid"
        print(f"    {r['store_name']} ({r['store_chain']}): {name_of(r)}{describe_price(obs, basis_label)}"
              f"{sale_note} [{source_note}] on {r['date']}{stale_tag(r['date'], today)}")

    if show_history:
        print("  History (newest first):")
        for r, source in reversed(everything):
            obs = current_obs(r, source)
            sale_note = f" SALE{sale_details(obs)}" if r["on_sale"] else ""
            source_note = "shelf tag" if source == "tag" else "paid"
            print(f"    {r['date']}  {r['store_name']}: {name_of(r)}{describe_price(obs, basis_label)}"
                  f"{sale_note} [{source_note}]  [{row_id(r)}]")
    print()


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description="Find where you bought or saw something cheapest.")
    parser.add_argument("query", help="what to look for, e.g. 'milk', 'chicken breast', 'samsung tv'")
    parser.add_argument("--category", action="store_true", help="match the query against categories only")
    parser.add_argument("--any-brand", action="store_true",
                        help="compare ALL products in the matching category by unit price")
    parser.add_argument("--since", help="only prices on or after this date (YYYY-MM-DD)")
    parser.add_argument("--history", action="store_true", help="list every price, newest first")
    parser.add_argument("--today", help=argparse.SUPPRESS)  # for tests: pretend today is this date
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)

    paths = Paths(args.root)
    products = load_json(paths.products)
    unit_basis = load_json(paths.unit_basis)
    today = date.fromisoformat(args.today) if args.today else date.today()

    purchases = purchase_rows(paths)
    tags = price_tag_rows(paths)
    if args.since:
        date.fromisoformat(args.since)  # fails loudly on a bad date
        purchases = [r for r in purchases if r["date"] >= args.since]
        tags = [r for r in tags if r["date"] >= args.since]

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
    print("'paid' = from your receipts; 'shelf tag' = from price tag photos (seen, not bought).")
    if args.since:
        print(f"Only prices since {args.since}.")
    print()
    found_any = False
    for title, keys, single in groups:
        group_purchases = [r for r in purchases if r["product_key"] in keys]
        group_tags = [r for r in tags if r["product_key"] in keys]
        if not group_purchases and not group_tags:
            if not single:
                print(f"=== {title} ===\n  No prices recorded.\n")
            continue  # don't clutter output with every never-seen product
        found_any = True
        print_report(title, group_purchases, group_tags, unit_basis, today, single, args.history)
    if not found_any:
        print(f"Matched {len(matched)} product(s) but no prices are recorded"
              f"{' since ' + args.since if args.since else ''}: {', '.join(matched)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
