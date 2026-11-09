"""Check receipt JSON files for mistakes and private data.

Usage:
    python scripts/validate.py                 # check every receipt in data/receipts/
    python scripts/validate.py FILE [FILE...]  # check only these files

ERRORS make the script exit with code 1 (fix them before committing).
WARNINGS are printed but don't fail (e.g. lines that need a human to look at them).
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from common import Paths, load_json, receipt_files

MONEY_TOLERANCE = 0.05   # subtotal / total may differ from our sums by up to 5 cents
LINE_TOLERANCE = 0.01    # regular_price - discount must equal price_paid within 1 cent

# ---------------------------------------------------------------- privacy checks
# Anything that looks like personal data is an ERROR. Better a false alarm than a leak.
PRIVACY_PATTERNS = [
    ("card number", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),          # 13-19 digits
    ("masked card number", re.compile(r"[*Xx#•]{4,}[ -]?\d{4}")),          # ****1234, XXXXXXXX1234
    ("card last digits", re.compile(r"\b(?:ending|ends)\s+(?:in|with)\s*:?\s*\d{4}\b", re.I)),
    ("phone number", re.compile(
        r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)")),
    ("loyalty/member ID", re.compile(
        r"\b(?:member(?:ship)?|loyalty|rewards?|club|card|acct|account|customer)"
        r"\s*(?:id|no\.?|number|#)?\s*[:#]?\s*[*xX]*\d{4,}", re.I)),
    ("person's name", re.compile(
        r"\b(?:cashier|operator|served by|customer name|member name|card ?holder)\s*[:#-]", re.I)),
]


def walk_strings(value, path="$"):
    """Yield (json_path, text) for every string anywhere in the receipt."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk_strings(child, f"{path}[{i}]")


def privacy_problems(receipt):
    problems = []
    for path, text in walk_strings(receipt):
        for label, pattern in PRIVACY_PATTERNS:
            if pattern.search(text):
                problems.append(f"PRIVACY: {path} looks like it contains a {label}. Remove it.")
    return problems


# ---------------------------------------------------------------- shared data checks

def check_shared_data(products, aliases, unit_basis):
    """Problems in products.json / aliases.json themselves."""
    errors, warnings = [], []
    for key, p in products.items():
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", key):
            errors.append(f"products.json: key '{key}' should be lowercase-with-hyphens")
        for field in ("name", "category", "brand", "is_store_brand", "size_value", "size_unit"):
            if field not in p:
                errors.append(f"products.json: '{key}' is missing '{field}'")
        basis = unit_basis["categories"].get(p.get("category"))
        if basis is None:
            warnings.append(f"products.json: '{key}' has category '{p.get('category')}' "
                            "which is not in config/unit_basis.json (no unit price)")
        size_unit = p.get("size_unit")
        if (p.get("size_value") is None) != (size_unit is None):
            errors.append(f"products.json: '{key}' must set both size_value and size_unit, or neither")
        if size_unit is not None:
            unit = unit_basis["units"].get(size_unit)
            if unit is None:
                errors.append(f"products.json: '{key}' has unknown size_unit '{size_unit}'")
            elif basis and unit["dimension"] != unit_basis["bases"][basis]["dimension"]:
                warnings.append(f"products.json: '{key}' size is in {size_unit} but category "
                                f"'{p['category']}' compares by {basis}; no unit price possible")
    for chain, table in aliases.items():
        for text, key in table.items():
            if key not in products:
                errors.append(f"aliases.json: [{chain}] '{text}' points to unknown product '{key}'")
    return errors, warnings


# ---------------------------------------------------------------- one receipt

def check_receipt(path, receipt, schema, products, aliases, unit_basis, root):
    errors, warnings = [], []

    errors += privacy_problems(receipt)

    schema_errors = sorted(Draft202012Validator(schema).iter_errors(receipt), key=lambda e: list(e.path))
    for e in schema_errors:
        where = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.path)
        errors.append(f"SCHEMA: {where}: {e.message}")
    if schema_errors:
        return errors, warnings  # the checks below assume the shape is right

    # --- id / file name / date consistency
    stem = Path(path).stem
    if receipt["id"] != stem:
        errors.append(f"id '{receipt['id']}' does not match file name '{stem}'")
    expected_prefix = f"{receipt['date']}_{receipt['store']['chain']}_{receipt['time'].replace(':', '')}"
    if not receipt["id"].startswith(expected_prefix):
        errors.append(f"id should start with '{expected_prefix}' (date_chain_HHMM)")
    try:
        datetime.strptime(receipt["date"], "%Y-%m-%d")
    except ValueError:
        errors.append(f"date '{receipt['date']}' is not a real date")
    image = receipt["image_path"]
    if Path(image).stem != receipt["id"]:
        errors.append(f"image_path '{image}' should be named after the id '{receipt['id']}'")
    if not image.startswith(f"receipts/{receipt['date'][:4]}/"):
        errors.append(f"image_path should be in receipts/{receipt['date'][:4]}/")
    if not (root / image).exists():
        warnings.append(f"photo not found at {image}")

    # --- money
    items = receipt["items"]
    items_sum = round(sum(i["price_paid"] for i in items), 2)
    gap = round(receipt["subtotal"] - items_sum, 2)
    if abs(gap) > MONEY_TOLERANCE + 1e-9:
        msg = (f"SUBTOTAL MISMATCH: items add up to ${items_sum:.2f} but subtotal is "
               f"${receipt['subtotal']:.2f} (gap ${gap:+.2f})")
        regular_sum = round(sum(i["regular_price"] for i in items), 2)
        if abs(receipt["subtotal"] - regular_sum) <= MONEY_TOLERANCE + 1e-9:
            msg += " -- the subtotal matches the sum of REGULAR prices, so this store may apply discounts after the subtotal"
        errors.append(msg)
    total_gap = round(receipt["total"] - (receipt["subtotal"] + receipt["tax"]), 2)
    if abs(total_gap) > MONEY_TOLERANCE + 1e-9:
        errors.append(f"TOTAL MISMATCH: subtotal ${receipt['subtotal']:.2f} + tax ${receipt['tax']:.2f} "
                      f"!= total ${receipt['total']:.2f} (gap ${total_gap:+.2f})")

    # --- each line
    chain = receipt["store"]["chain"]
    chain_aliases = aliases.get(chain, {})
    seen_line_numbers = set()
    for item in items:
        label = f"line {item['line_no']} '{item['receipt_text']}'"
        if item["line_no"] in seen_line_numbers:
            errors.append(f"{label}: duplicate line_no")
        seen_line_numbers.add(item["line_no"])

        if abs(item["regular_price"] - item["discount"] - item["price_paid"]) > LINE_TOLERANCE + 1e-9:
            errors.append(f"{label}: regular_price ${item['regular_price']:.2f} - discount "
                          f"${item['discount']:.2f} != price_paid ${item['price_paid']:.2f}")
        if item["discount"] > 0 and not item["on_sale"]:
            warnings.append(f"{label}: has a discount but on_sale is false")
        if item["on_sale"] and item["discount"] == 0:
            warnings.append(f"{label}: on_sale is true but discount is 0")
        weighed_unit = item["unit_of_sale"] in ("lb", "kg")
        if item["weighed"] != weighed_unit:
            errors.append(f"{label}: weighed={item['weighed']} but unit_of_sale='{item['unit_of_sale']}' "
                          "(weighed items must be sold by lb/kg and vice versa)")

        key = item["product_key"]
        if key is None:
            # Non-product line (bag fee, deposit, order coupon): no alias/product needed.
            pass
        else:
            if item["price_paid"] < 0:
                errors.append(f"{label}: price_paid is negative; only non-product lines may be negative")
            if key not in products:
                errors.append(f"{label}: product_key '{key}' is not in products.json")
            alias = chain_aliases.get(item["receipt_text"])
            if alias is None:
                errors.append(f"{label}: no alias in aliases.json under '{chain}' for this receipt_text")
            elif alias != key:
                errors.append(f"{label}: aliases.json maps this text to '{alias}' but item says '{key}'")
            elif weighed_unit:
                category = products[key]["category"]
                basis = unit_basis["categories"].get(category)
                if basis and unit_basis["bases"][basis]["dimension"] != "mass":
                    warnings.append(f"{label}: sold by weight but category '{category}' compares by {basis}")

        if item["needs_review"]:
            warnings.append(f"{label}: NEEDS REVIEW - {item['review_note'] or '(no note)'}")
        if item["confidence"] == "low":
            warnings.append(f"{label}: LOW CONFIDENCE - {item['review_note'] or '(no note)'}")

    return errors, warnings


# ---------------------------------------------------------------- main

def validate(files, paths):
    """Return (all_errors, all_warnings) as lists of strings. Also prints a report."""
    schema = load_json(paths.schema)
    products = load_json(paths.products)
    aliases = load_json(paths.aliases)
    unit_basis = load_json(paths.unit_basis)

    all_errors, all_warnings = [], []
    errors, warnings = check_shared_data(products, aliases, unit_basis)
    report("products.json / aliases.json", errors, warnings)
    all_errors += errors
    all_warnings += warnings

    for path in files:
        try:
            receipt = load_json(path)
        except (OSError, json.JSONDecodeError) as e:
            errors, warnings = [f"cannot read JSON: {e}"], []
        else:
            errors, warnings = check_receipt(path, receipt, schema, products, aliases,
                                             unit_basis, paths.root)
        report(str(path), errors, warnings)
        all_errors += errors
        all_warnings += warnings
    return all_errors, all_warnings


def report(name, errors, warnings):
    status = "FAIL" if errors else ("OK (with warnings)" if warnings else "OK")
    print(f"{status}: {name}")
    for e in errors:
        print(f"  ERROR   {e}")
    for w in warnings:
        print(f"  WARNING {w}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate receipt JSON files.")
    parser.add_argument("files", nargs="*", help="receipt JSON files (default: all)")
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)
    paths = Paths(args.root)
    files = [Path(f) for f in args.files] or receipt_files(paths)
    if not files:
        print("No receipts found in data/receipts/.")
    errors, warnings = validate(files, paths)
    print()
    print(f"{len(files)} receipt(s) checked: {len(errors)} error(s), {len(warnings)} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
