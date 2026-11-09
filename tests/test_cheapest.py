"""Tests for scripts/cheapest.py using the FAKE fixture receipts."""

import cheapest
from common import load_json
from conftest import FIXTURES, TODAY


def run(root, capsys, *args):
    code = cheapest.main([*args, "--root", str(root), "--today", TODAY])
    return code, capsys.readouterr().out


def section(out, title_start):
    """Text of one '=== ... ===' block of the output."""
    for block in out.split("=== ")[1:]:
        if block.startswith(title_start):
            return block
    raise AssertionError(f"no section starting with {title_start!r} in:\n{out}")


def line_after(text, label):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(label):
            return line + "\n" + lines[i + 1]
    raise AssertionError(f"{label!r} not found in:\n{text}")


def test_regular_and_sale_prices_are_separate(root, capsys):
    code, out = run(root, capsys, "milk")
    assert code == 0
    store_brand = section(out, "Milk, 2% reduced fat, 1 gallon (store brand)")
    regular = line_after(store_brand, "Cheapest REGULAR price")
    sale = line_after(store_brand, "Cheapest SALE price")
    # Cheapest regular price is CornerFoods $3.29 (old), not the $2.99 sale price.
    assert "$3.29" in regular and "cornerfoods" in regular and "2026-07-15" in regular
    assert "STALE" in regular
    assert "$2.99" in sale and "valuegrocer" in sale and "regular $3.79" in sale
    assert "STALE" not in sale


def test_latest_price_at_each_store(root, capsys):
    _, out = run(root, capsys, "2% milk gallon")
    latest = section(out, "Milk, 2% reduced fat, 1 gallon").split("Latest price at each store:")[1]
    assert "FreshMart - Elm Street (freshmart): $3.49" in latest
    assert "ValueGrocer - Campus (valuegrocer): $2.99" in latest and "SALE" in latest
    assert "CornerFoods - Main St (cornerfoods): $3.29" in latest


def test_without_any_brand_each_product_is_separate(root, capsys):
    _, out = run(root, capsys, "milk")
    assert "(BrandX) [milk-2pct-half-gallon-brandx]" in out
    assert "Any brand" not in out


def test_any_brand_compares_whole_category(root, capsys):
    _, out = run(root, capsys, "brandx", "--any-brand")
    block = section(out, "Any brand, category 'milk'")
    # BrandX half gallon ($0.0391/fl oz) loses to store-brand gallons, which are now included.
    regular = line_after(block, "Cheapest REGULAR price")
    assert "1 gallon (store brand)" in regular and "$0.0257 per fl oz" in regular
    assert "BrandX" in block  # BrandX still appears in the per-store list


def test_size_unknown_shows_per_package_price(root, capsys):
    _, out = run(root, capsys, "bread")
    assert out.count("=== ") == 1  # 'bread' must not fuzzy-match 'store brand'
    assert "$1.99 per package (size unknown, no unit price)" in out


def test_weighed_item(root, capsys):
    _, out = run(root, capsys, "banana")
    assert "$1.42 for 2.4 lb = $0.5917 per lb" in out


def test_history_newest_first(root, capsys):
    _, out = run(root, capsys, "milk-2pct-gallon", "--history")
    block = section(out, "Milk, 2% reduced fat, 1 gallon (store brand)")
    history = block.split("History (newest first):")[1].strip().splitlines()
    dates = [line.split()[0] for line in history]
    assert dates == ["2026-10-20", "2026-10-01", "2026-07-15"]


def test_since_filters_old_purchases(root, capsys):
    _, out = run(root, capsys, "milk-2pct-gallon", "--since", "2026-09-01")
    assert "cornerfoods" not in out
    regular = line_after(out, "Cheapest REGULAR price")
    assert "$3.49" in regular  # FreshMart is cheapest regular once the old receipt is excluded


def test_category_flag(root, capsys):
    _, out = run(root, capsys, "poultry", "--category")
    assert "Chicken breast" in out
    _, out = run(root, capsys, "chicken", "--category")  # 'chicken' is not a category
    assert "No product matches" in out


def test_fuzzy_matching():
    products = load_json(FIXTURES / "products.json")
    assert cheapest.find_products("chiken", products) == ["chicken-breast-boneless-skinless"]
    assert cheapest.find_products("BANANA", products) == ["bananas"]
    assert cheapest.find_products("peanut butter", products) == ["peanut-butter-creamy-16oz-store-brand"]
    assert cheapest.find_products("bread", products) == ["bread-white-sandwich-store-brand"]


def test_no_match(root, capsys):
    code, out = run(root, capsys, "caviar")
    assert code == 1 and "No product matches" in out
