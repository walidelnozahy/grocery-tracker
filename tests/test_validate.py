"""Tests for scripts/validate.py using the FAKE fixture receipts."""

import json

import pytest

import validate
from common import Paths, load_json

FRESHMART = "2026-10-01_freshmart_1015.json"
VALUEGROCER = "2026-10-20_valuegrocer_1805.json"


def edit_receipt(root, name, change):
    """Load a fixture receipt, apply `change(receipt)`, save it back."""
    path = root / "data" / "receipts" / name
    receipt = load_json(path)
    change(receipt)
    path.write_text(json.dumps(receipt, indent=2))
    return path


def run(root, *files):
    return validate.main([*map(str, files), "--root", str(root)])


def test_all_fixture_receipts_pass(root, capsys):
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "0 error(s)" in out


def test_schema_error_fails(root, capsys):
    def change(r):
        del r["total"]
        r["items"][0]["confidence"] = "very sure"
    edit_receipt(root, FRESHMART, change)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "SCHEMA" in out and "'total' is a required property" in out
    assert "very sure" in out


def test_subtotal_mismatch_reports_gap(root, capsys):
    edit_receipt(root, FRESHMART, lambda r: r.update(subtotal=11.40, total=11.40))
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "SUBTOTAL MISMATCH" in out
    assert "gap $+1.00" in out


def test_subtotal_within_tolerance_passes(root):
    edit_receipt(root, FRESHMART, lambda r: r.update(subtotal=10.44, total=10.44))
    assert run(root) == 0


def test_subtotal_hint_when_store_discounts_after_subtotal(root, capsys):
    # Pre-discount subtotal printed on the receipt: 3.79 + 1.25 + 2.49 = 7.53
    edit_receipt(root, VALUEGROCER, lambda r: r.update(subtotal=7.53, total=7.53))
    assert run(root) == 1
    assert "matches the sum of REGULAR prices" in capsys.readouterr().out


def test_total_mismatch(root, capsys):
    edit_receipt(root, FRESHMART, lambda r: r.update(tax=0.50))
    assert run(root) == 1
    assert "TOTAL MISMATCH" in capsys.readouterr().out


@pytest.mark.parametrize("text, label", [
    ("Paid with 4111 1111 1111 1111", "card number"),
    ("VISA ************1234", "masked card number"),
    ("card ending in 4242", "card last digits"),
    ("Store phone (555) 123-4567", "phone number"),
    ("Member # 0012345678", "loyalty/member ID"),
    ("Rewards ID: 98765432", "loyalty/member ID"),
    ("Cashier: Jamie", "person's name"),
])
def test_privacy_leaks_fail_loudly(root, capsys, text, label):
    edit_receipt(root, FRESHMART, lambda r: r.update(notes=text))
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "PRIVACY" in out and label in out
    assert "$.notes" in out


def test_privacy_check_ignores_normal_receipt_text(root):
    # Dates, times, prices, store numbers and addresses must not trigger false alarms.
    def change(r):
        r["store"]["name"] = "FreshMart #0412"
        r["store"]["address"] = "1234 Elm St"
        r["notes"] = "2 @ $1.99, bought 2026-10-01 at 10:15"
    edit_receipt(root, FRESHMART, change)
    assert run(root) == 0


def test_card_number_in_item_text_is_caught(root, capsys):
    edit_receipt(root, FRESHMART,
                 lambda r: r["items"][0].update(receipt_text="ACCT 5500-0000-0000-0004"))
    assert run(root) == 1
    assert "$.items[0].receipt_text" in capsys.readouterr().out


def test_unknown_product_and_missing_alias(root, capsys):
    def change(r):
        r["items"][0]["product_key"] = "milk-made-up"
        r["items"][1]["receipt_text"] = "SOMETHING NEW"
    edit_receipt(root, FRESHMART, change)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "'milk-made-up' is not in products.json" in out
    assert "no alias in aliases.json under 'freshmart'" in out


def test_alias_must_be_keyed_by_chain(root, capsys):
    # "FM 2% MILK GAL" is a freshmart alias; it must not be accepted at valuegrocer.
    edit_receipt(root, VALUEGROCER, lambda r: r["items"][0].update(receipt_text="FM 2% MILK GAL"))
    assert run(root) == 1
    assert "under 'valuegrocer'" in capsys.readouterr().out


def test_needs_review_and_low_confidence_are_warnings_not_errors(root, capsys):
    def change(r):
        r["items"][1].update(confidence="low", needs_review=True, review_note="price smudged")
    edit_receipt(root, FRESHMART, change)
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "NEEDS REVIEW - price smudged" in out
    assert "LOW CONFIDENCE" in out


def test_discount_arithmetic_must_add_up(root, capsys):
    edit_receipt(root, VALUEGROCER, lambda r: r["items"][0].update(discount=0.50))
    assert run(root) == 1
    assert "regular_price $3.79 - discount $0.50 != price_paid $2.99" in capsys.readouterr().out


def test_weighed_item_must_be_sold_by_weight(root, capsys):
    edit_receipt(root, FRESHMART, lambda r: r["items"][2].update(unit_of_sale="each"))
    assert run(root) == 1
    assert "weighed=True" in capsys.readouterr().out


def test_non_product_line_needs_no_alias(root):
    # A bag fee: product_key null, no alias needed, still counts toward the subtotal.
    def change(r):
        r["items"].append({
            "line_no": 5, "receipt_text": "BAG FEE", "product_key": None, "quantity": 1,
            "unit_of_sale": "each", "price_paid": 0.10, "regular_price": 0.10, "discount": 0,
            "on_sale": False, "weighed": False, "confidence": "high",
            "needs_review": False, "review_note": None})
        r.update(subtotal=10.50, total=10.50)
    edit_receipt(root, FRESHMART, change)
    assert run(root) == 0


def test_only_named_files_are_checked(root, capsys):
    edit_receipt(root, FRESHMART, lambda r: r.update(subtotal=99.0))
    good = root / "data" / "receipts" / VALUEGROCER
    assert run(root, good) == 0
    assert "1 receipt(s) checked" in capsys.readouterr().out


def test_real_seed_data_is_consistent():
    """The repo's own products.json / aliases.json / unit_basis.json have no errors or warnings."""
    paths = Paths()
    errors, warnings = validate.check_shared_data(
        load_json(paths.products), load_json(paths.aliases), load_json(paths.unit_basis))
    assert errors == [] and warnings == []
