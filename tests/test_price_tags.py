"""Tests for shelf price tags (validate, ledger, cheapest) and scripts/save_photo.py.

All data here is FAKE (tests/fixtures/price_tags/ and generated images).
"""

import csv
import json

import pytest
from PIL import Image

import build_ledger
import cheapest
import save_photo
import validate
from common import TAG_LEDGER_COLUMNS, load_json
from conftest import TODAY

FM_TAG = "2026-11-01_freshmart_1100.json"
VG_TAG = "2026-11-05_valuegrocer_0900.json"


def edit_tag(root, name, change):
    path = root / "data" / "price_tags" / name
    tag = load_json(path)
    change(tag)
    path.write_text(json.dumps(tag, indent=2))


def run_validate(root):
    return validate.main(["--root", str(root)])


# ---------------------------------------------------------------- validate

def test_fixture_tags_validate(root, capsys):
    assert run_validate(root) == 0
    out = capsys.readouterr().out
    assert "2026-11-05_valuegrocer_0900.json" in out


def test_tag_schema_error(root, capsys):
    edit_tag(root, FM_TAG, lambda t: t["items"][0].update(price_unit="bunch"))
    assert run_validate(root) == 1
    assert "SCHEMA" in capsys.readouterr().out


def test_tag_privacy_check(root, capsys):
    edit_tag(root, FM_TAG, lambda t: t.update(notes="Customer service (555) 123-4567"))
    assert run_validate(root) == 1
    assert "PRIVACY" in capsys.readouterr().out


def test_tag_needs_alias_for_its_chain(root, capsys):
    edit_tag(root, FM_TAG, lambda t: t["items"][0].update(tag_text="SOMETHING ELSE"))
    assert run_validate(root) == 1
    assert "no alias in aliases.json under 'freshmart' for this tag_text" in capsys.readouterr().out


def test_regular_price_rules(root, capsys):
    edit_tag(root, FM_TAG, lambda t: t["items"][0].update(regular_price=3.59))  # not on sale
    assert run_validate(root) == 1
    assert "not on sale, so regular_price must equal price" in capsys.readouterr().out


def test_sale_price_must_be_below_regular(root, capsys):
    edit_tag(root, VG_TAG, lambda t: t["items"][0].update(regular_price=2.50))
    assert run_validate(root) == 1
    assert "is not higher than price" in capsys.readouterr().out


def test_printed_unit_price_mismatch_warns(root, capsys):
    # 3.19 per gallon is $0.025/fl oz; a tag saying $0.05 means the size or the reading is wrong.
    edit_tag(root, FM_TAG, lambda t: t["items"][0]["printed_unit_price"].update(value=0.05))
    assert run_validate(root) == 0  # warning only
    assert "tag says $0.05 per fl_oz but our size gives $0.025" in capsys.readouterr().out


def test_unknown_size_gets_suggestion_from_printed_unit_price(root, capsys):
    run_validate(root)
    assert "implies about 19.9 oz" in capsys.readouterr().out


# ---------------------------------------------------------------- ledger

def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_tag_ledger(root):
    build_ledger.main(["--root", str(root)])
    with open(root / "data" / "price_tags.csv", newline="") as f:
        assert next(csv.reader(f)) == TAG_LEDGER_COLUMNS
    rows = read_csv(root / "data" / "price_tags.csv")
    assert len(rows) == 5
    by_key = {r["product_key"]: r for r in rows if r["store_chain"] == "valuegrocer"}
    assert by_key["bread-white-sandwich-store-brand"]["unit_price"] == ""   # size unknown -> blank
    assert by_key["tv-55in-4k-brandz"]["model"] == "BZ55X"
    assert by_key["tv-55in-4k-brandz"]["sale_label"] == "Rollback"
    bananas = [r for r in rows if r["product_key"] == "bananas"][0]
    assert bananas["price_unit"] == "lb" and bananas["unit_price"] == "0.5500"


def test_tags_are_not_purchases(root):
    build_ledger.main(["--root", str(root)])
    assert len(read_csv(root / "data" / "items.csv")) == 10  # same as receipts alone


# ---------------------------------------------------------------- cheapest

def run_cheapest(root, capsys, *args):
    cheapest.main([*args, "--root", str(root), "--today", TODAY])
    return capsys.readouterr().out


def test_paid_and_shelf_tag_prices_are_labeled_separately(root, capsys):
    out = run_cheapest(root, capsys, "milk-2pct-gallon-store-brand")
    regular = out.split("Cheapest REGULAR price:")[1].split("Cheapest SALE price:")[0]
    sale = out.split("Cheapest SALE price:")[1].split("Latest price")[0]
    assert "paid:      $3.29" in regular and "shelf tag: $3.19" in regular
    assert "paid:      $2.99" in sale
    assert "shelf tag: $2.79" in sale and "Weekly Special" in sale and "until 2026-11-11" in sale


def test_find_by_model_number(root, capsys):
    out = run_cheapest(root, capsys, "bz55x")
    assert "TV, 55 inch 4K (BrandZ BZ55X)" in out
    assert "shelf tag: $299.99 (Rollback; regular $399.99, saves $100.00)" in out
    assert "= $" not in out.split("Cheapest REGULAR")[1]  # a one-item product needs no unit price


def test_weighed_shelf_tag(root, capsys):
    out = run_cheapest(root, capsys, "bananas")
    assert "shelf tag: $0.55 per lb" in out


# ---------------------------------------------------------------- save_photo

def make_photo(path, taken="2026:11:09 16:06:50", gps=True):
    image = Image.new("RGB", (400, 300), "white")
    exif = Image.Exif()
    if taken:
        exif[306] = taken
    if gps:
        exif[0x8825] = {1: "N", 2: (37.0, 46.0, 30.0), 3: "W", 4: (122.0, 25.0, 10.0)}
    image.save(path, "JPEG", exif=exif.tobytes())
    return path


def test_save_photo_strips_metadata_and_names_file(root, tmp_path, capsys):
    src = make_photo(tmp_path / "IMG_0001.jpg")
    assert save_photo.photo_info(Image.open(src))[1] is True  # the fake photo has GPS
    assert save_photo.main([str(src), "--kind", "price_tag", "--chain", "costco", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "id: 2026-11-09_costco_1606" in out
    saved = root / "price_tags" / "2026" / "2026-11-09_costco_1606.jpg"
    assert saved.exists()
    exif = Image.open(saved).getexif()
    assert not exif.get_ifd(0x8825)   # no GPS
    assert len(exif) == 0             # no metadata at all


def test_save_photo_adds_suffix_when_name_is_taken(root, tmp_path, capsys):
    src = make_photo(tmp_path / "IMG_0001.jpg")
    args = [str(src), "--kind", "price_tag", "--chain", "costco", "--root", str(root)]
    save_photo.main(args)
    save_photo.main(args)
    assert "id: 2026-11-09_costco_1606_2" in capsys.readouterr().out


def test_save_photo_receipt_date_override(root, tmp_path, capsys):
    src = make_photo(tmp_path / "IMG_0002.jpg")
    save_photo.main([str(src), "--kind", "receipt", "--chain", "kroger", "--date", "2026-11-08",
                     "--time", "14:32", "--root", str(root)])
    assert (root / "receipts" / "2026" / "2026-11-08_kroger_1432.jpg").exists()


def test_save_photo_without_date_needs_one(root, tmp_path):
    src = make_photo(tmp_path / "IMG_0003.jpg", taken=None, gps=False)
    with pytest.raises(SystemExit):
        save_photo.main([str(src), "--kind", "price_tag", "--chain", "costco", "--root", str(root)])


def test_save_photo_info(tmp_path, capsys):
    src = make_photo(tmp_path / "IMG_0004.jpg")
    save_photo.main([str(src), "--info"])
    out = capsys.readouterr().out
    assert "taken: 2026-11-09 16:06" in out and "gps location in photo: yes" in out
