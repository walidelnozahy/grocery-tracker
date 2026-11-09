"""Shared helpers used by validate.py, build_ledger.py and cheapest.py.

Nothing clever here: load the JSON files, convert units, and turn
receipts into one flat list of purchase rows.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Columns of data/items.csv, in order.
LEDGER_COLUMNS = [
    "date", "store_chain", "store_name", "product_key", "product_name",
    "category", "brand", "is_store_brand", "quantity", "price_paid",
    "regular_price", "on_sale", "unit_price", "unit_price_basis", "receipt_id",
]


class Paths:
    """Where every file lives. `root` defaults to this repository; tests pass a temp folder."""

    def __init__(self, root=None):
        self.root = Path(root) if root else REPO_ROOT
        self.receipts_dir = self.root / "data" / "receipts"
        self.aliases = self.root / "data" / "aliases.json"
        self.products = self.root / "data" / "products.json"
        self.ledger = self.root / "data" / "items.csv"
        self.unit_basis = self.root / "config" / "unit_basis.json"
        self.schema = self.root / "schema" / "receipt.schema.json"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def receipt_files(paths):
    """All receipt JSON files, sorted by name (which sorts them by date)."""
    if not paths.receipts_dir.exists():
        return []
    return sorted(paths.receipts_dir.rglob("*.json"))


# ---------------------------------------------------------------- units

def convert(amount, from_unit, basis_name, unit_basis):
    """Convert `amount` of `from_unit` into how many comparison-basis units it is.

    Example: convert(1, "gal", "fl_oz", ub) -> 128.0
             convert(800, "sheets", "100_sheets", ub) -> 8.0
    Returns None if the units can't be converted (unknown unit, or e.g. oz vs count).
    """
    unit = unit_basis["units"].get(from_unit)
    basis = unit_basis["bases"].get(basis_name)
    if unit is None or basis is None or unit["dimension"] != basis["dimension"]:
        return None
    return amount * unit["to_base"] / basis["base_amount"]


def basis_for_category(category, unit_basis):
    return unit_basis["categories"].get(category)


def amount_in_basis(item, product, basis_name, unit_basis):
    """How many basis units one receipt line bought, or None if we can't tell.

    - Weighed items (sold by lb/kg): the weight is on the receipt, so no size is needed.
    - Items sold 'each': quantity x package size from products.json.
      If the package size is unknown (null) we return None -- we never guess.
    """
    if basis_name is None:
        return None
    if item["unit_of_sale"] in ("lb", "kg"):
        return convert(item["quantity"], item["unit_of_sale"], basis_name, unit_basis)
    size_value = product.get("size_value")
    size_unit = product.get("size_unit")
    if size_value is None or not size_unit:
        return None
    return convert(item["quantity"] * size_value, size_unit, basis_name, unit_basis)


# ---------------------------------------------------------------- ledger rows

def purchase_rows(paths):
    """Every product line from every receipt as a flat dict, in a stable order.

    Keys starting with "_" are extra helpers (not written to items.csv).
    Lines with product_key = null (bag fees, deposits, order coupons) are skipped.
    """
    products = load_json(paths.products)
    unit_basis = load_json(paths.unit_basis)
    rows = []
    for path in receipt_files(paths):
        receipt = load_json(path)
        for item in receipt["items"]:
            key = item.get("product_key")
            if not key:
                continue
            product = products.get(key, {})
            category = product.get("category", "")
            basis = basis_for_category(category, unit_basis)
            amount = amount_in_basis(item, product, basis, unit_basis)
            unit_price = item["price_paid"] / amount if amount else None
            rows.append({
                "date": receipt["date"],
                "store_chain": receipt["store"]["chain"],
                "store_name": receipt["store"]["name"],
                "product_key": key,
                "product_name": product.get("name", ""),
                "category": category,
                "brand": product.get("brand") or "",
                "is_store_brand": bool(product.get("is_store_brand", False)),
                "quantity": item["quantity"],
                "price_paid": item["price_paid"],
                "regular_price": item["regular_price"],
                "on_sale": item["on_sale"],
                "unit_price": unit_price,
                "unit_price_basis": basis or "",
                "receipt_id": receipt["id"],
                "_line_no": item["line_no"],
                "_unit_of_sale": item["unit_of_sale"],
                "_amount": amount,
            })
    rows.sort(key=lambda r: (r["date"], r["receipt_id"], r["_line_no"]))
    return rows
