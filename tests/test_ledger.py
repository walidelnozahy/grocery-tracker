"""Tests for unit conversion and scripts/build_ledger.py."""

import csv

import pytest

import build_ledger
from common import LEDGER_COLUMNS, Paths, convert, load_json


@pytest.fixture
def ub():
    return load_json(Paths().unit_basis)


@pytest.mark.parametrize("amount, unit, basis, expected", [
    (1, "gal", "fl_oz", 128),
    (1, "qt", "fl_oz", 32),
    (1, "pt", "fl_oz", 16),
    (1, "L", "fl_oz", 33.814),
    (500, "ml", "fl_oz", 16.907),
    (1, "lb", "oz", 16),
    (32, "oz", "lb", 2),
    (1, "kg", "lb", 2.2046),
    (454, "g", "lb", 1.0009),
    (12, "count", "count", 12),
    (800, "sheets", "100_sheets", 8),
])
def test_unit_conversion(ub, amount, unit, basis, expected):
    assert convert(amount, unit, basis, ub) == pytest.approx(expected, rel=1e-3)


def test_incompatible_units_give_none(ub):
    assert convert(12, "count", "oz", ub) is None
    assert convert(1, "gal", "lb", ub) is None
    assert convert(1, "furlong", "oz", ub) is None


def test_every_category_uses_a_known_basis(ub):
    assert len(ub["categories"]) >= 25
    for category, basis in ub["categories"].items():
        assert basis in ub["bases"], category


def read_ledger(root):
    with open(root / "data" / "items.csv", newline="") as f:
        return list(csv.DictReader(f))


def test_ledger_columns_and_rows(root):
    build_ledger.main(["--root", str(root)])
    with open(root / "data" / "items.csv", newline="") as f:
        header = next(csv.reader(f))
    assert header == LEDGER_COLUMNS
    assert len(read_ledger(root)) == 10


def test_unit_price_blank_when_size_unknown(root):
    build_ledger.main(["--root", str(root)])
    bread = [r for r in read_ledger(root) if r["product_key"] == "bread-white-sandwich-store-brand"]
    assert len(bread) == 1
    assert bread[0]["unit_price"] == ""          # blank, not "0"
    assert bread[0]["price_paid"] == "1.99"


def test_unit_prices(root):
    build_ledger.main(["--root", str(root)])
    rows = {(r["receipt_id"], r["product_key"]): r for r in read_ledger(root)}
    milk = rows[("2026-10-01_freshmart_1015", "milk-2pct-gallon-store-brand")]
    assert float(milk["unit_price"]) == pytest.approx(3.49 / 128, abs=1e-4)
    assert milk["unit_price_basis"] == "fl_oz"
    bananas = rows[("2026-10-01_freshmart_1015", "bananas")]   # weighed: 2.4 lb for $1.42
    assert float(bananas["unit_price"]) == pytest.approx(1.42 / 2.4, abs=1e-4)
    assert bananas["unit_price_basis"] == "lb"
    eggs = rows[("2026-10-01_freshmart_1015", "eggs-large-12ct-store-brand")]
    assert float(eggs["unit_price"]) == pytest.approx(2.99 / 12, abs=1e-4)
    sale_milk = rows[("2026-10-20_valuegrocer_1805", "milk-2pct-gallon-store-brand")]
    assert sale_milk["on_sale"] == "true"
    assert sale_milk["price_paid"] == "2.99" and sale_milk["regular_price"] == "3.79"


def test_ledger_is_deterministic_and_sorted(root):
    build_ledger.main(["--root", str(root)])
    first = (root / "data" / "items.csv").read_text()
    build_ledger.main(["--root", str(root)])
    assert (root / "data" / "items.csv").read_text() == first
    dates = [r["date"] for r in read_ledger(root)]
    assert dates == sorted(dates)


def test_ledger_skips_non_product_lines(root):
    import json
    path = root / "data" / "receipts" / "2026-10-01_freshmart_1015.json"
    receipt = json.loads(path.read_text())
    receipt["items"].append({
        "line_no": 5, "receipt_text": "BAG FEE", "product_key": None, "quantity": 1,
        "unit_of_sale": "each", "price_paid": 0.10, "regular_price": 0.10, "discount": 0,
        "on_sale": False, "weighed": False, "confidence": "high",
        "needs_review": False, "review_note": None})
    path.write_text(json.dumps(receipt))
    build_ledger.main(["--root", str(root)])
    assert len(read_ledger(root)) == 10
