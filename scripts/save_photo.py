"""Save a receipt or price-tag photo into the repo, with private metadata removed.

Phone photos carry hidden metadata, including the GPS location where they were
taken. This script makes a clean copy for the repo:
  - removes ALL metadata (GPS location, phone model, etc.)
  - turns it upright, converts iPhone HEIC to JPG, and shrinks very large photos
  - names it by the standard rule: <kind folder>/YYYY/YYYY-MM-DD_<chain>_HHMM.jpg

Usage:
    python scripts/save_photo.py PHOTO --info
        -> print when the photo was taken (and whether it has GPS data). Saves nothing.
    python scripts/save_photo.py PHOTO --kind price_tag --chain costco
        -> date/time come from the photo itself
    python scripts/save_photo.py PHOTO --kind receipt --chain kroger --date 2026-11-08 --time 14:32
        -> for receipts, use the date/time PRINTED on the receipt

It prints the id to use for the JSON file (data/receipts/<id>.json or data/price_tags/<id>.json).
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhone photos are HEIC; this plugin lets Pillow open them.
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pillow_heif = None

from common import Paths

MAX_SIDE = 3000  # pixels; plenty to read small print, keeps the repo small
FOLDERS = {
    "receipt": ("receipts", "data/receipts"),
    "price_tag": ("price_tags", "data/price_tags"),
}
EXIF_DATETIME = 306            # DateTime
EXIF_IFD = 0x8769              # Exif sub-section
EXIF_DATETIME_ORIGINAL = 36867  # when the photo was taken
GPS_IFD = 0x8825


def open_photo(path):
    try:
        return Image.open(path)
    except Exception as e:
        hint = " (HEIC photos need: pip install pillow-heif)" if pillow_heif is None else ""
        raise SystemExit(f"Cannot open {path}: {e}{hint}")


def photo_info(image):
    """(taken: datetime or None, has_gps: bool) from the photo's metadata."""
    exif = image.getexif()
    raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME)
    taken = None
    if raw:
        try:
            taken = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            taken = None
    return taken, bool(exif.get_ifd(GPS_IFD))


def choose_id(paths, kind, date, chain, hhmm):
    """YYYY-MM-DD_chain_HHMM, adding _2, _3 ... if that name is already used."""
    photo_folder, json_folder = FOLDERS[kind]
    base = f"{date}_{chain}_{hhmm}"
    n = 1
    while True:
        candidate = base if n == 1 else f"{base}_{n}"
        photo_taken = any((paths.root / photo_folder / date[:4]).glob(f"{candidate}.*"))
        json_taken = (paths.root / json_folder / f"{candidate}.json").exists()
        if not photo_taken and not json_taken:
            return candidate
        n += 1


def save_clean_copy(image, destination):
    """Upright, RGB, at most MAX_SIDE pixels, saved as JPG with NO metadata."""
    clean = ImageOps.exif_transpose(image).convert("RGB")
    clean.thumbnail((MAX_SIDE, MAX_SIDE))
    destination.parent.mkdir(parents=True, exist_ok=True)
    clean.save(destination, "JPEG", quality=88)  # no exif= argument, so nothing is copied over


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save a photo into the repo without private metadata.")
    parser.add_argument("photo")
    parser.add_argument("--info", action="store_true", help="only print when it was taken; save nothing")
    parser.add_argument("--kind", choices=sorted(FOLDERS))
    parser.add_argument("--chain", help="store chain slug, e.g. costco, trader-joes")
    parser.add_argument("--date", help="YYYY-MM-DD (default: from the photo)")
    parser.add_argument("--time", help="HH:MM, 24-hour (default: from the photo)")
    parser.add_argument("--root", help="repository root (default: this repo)")
    args = parser.parse_args(argv)

    image = open_photo(args.photo)
    taken, has_gps = photo_info(image)

    if args.info:
        print(f"taken: {taken.strftime('%Y-%m-%d %H:%M') if taken else 'unknown (no date in photo)'}")
        print(f"gps location in photo: {'yes (will be removed when saved)' if has_gps else 'no'}")
        return 0

    if not args.kind or not args.chain:
        parser.error("--kind and --chain are required unless --info is used")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", args.chain):
        parser.error("--chain must be a lowercase slug like 'costco' or 'trader-joes'")
    date = args.date or (taken.strftime("%Y-%m-%d") if taken else None)
    time = args.time or (taken.strftime("%H:%M") if taken else None)
    if not date or not time:
        parser.error("this photo has no date/time inside it; pass --date YYYY-MM-DD and --time HH:MM")
    datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")  # fails loudly on a bad value

    paths = Paths(args.root)
    photo_id = choose_id(paths, args.kind, date, args.chain, time.replace(":", ""))
    photo_folder, json_folder = FOLDERS[args.kind]
    destination = paths.root / photo_folder / date[:4] / f"{photo_id}.jpg"
    save_clean_copy(image, destination)

    print(f"id: {photo_id}")
    print(f"photo saved: {destination.relative_to(paths.root)}")
    print(f"json goes in: {json_folder}/{photo_id}.json")
    print(f"date: {date}  time: {time}")
    print("metadata removed (GPS location, camera info)." if has_gps else "metadata removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
