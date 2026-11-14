# Grocery price tracker: instructions for Claude

This repo is a personal price tracker (mostly groceries, but any product works,
e.g. TVs). The owner attaches photos of **receipts** (things bought) and of
**shelf price tags** (prices seen in a store); you read them **yourself** (no OCR
library, no external API) and turn them into structured JSON. Scripts then build
ledgers and answer "where is X cheapest?".

Plain files only. No UI, no web app, no database server, no external API calls.
The owner is not an expert: keep code simple and explain what you did in plain words.

## Layout

| Path | What it is | Edit by hand? |
|---|---|---|
| `receipts/YYYY/` | Receipt photos, named `YYYY-MM-DD_<chain>_HHMM.jpg` | Add only (via `save_photo.py`) |
| `price_tags/YYYY/` | Shelf price-tag photos, same naming | Add only (via `save_photo.py`) |
| `data/receipts/` | One JSON per receipt, same base name as its photo | Yes |
| `data/price_tags/` | One JSON per price-tag photo (can hold several tags) | Yes |
| `data/aliases.json` | `{ "<chain>": { "<receipt_text>": "<product_key>" } }` | Yes |
| `data/products.json` | `{ "<product_key>": { name, category, brand, is_store_brand, size_value, size_unit, model? } }` | Yes |
| `data/items.csv` | Purchases. **Generated** by `build_ledger.py` | **Never** |
| `data/price_tags.csv` | Shelf prices seen. **Generated** by `build_ledger.py` | **Never** |
| `config/unit_basis.json` | Category → comparison basis, plus the unit conversion table | Rarely |
| `schema/receipt.schema.json` | JSON Schema for a receipt | Rarely |
| `schema/price_tag.schema.json` | JSON Schema for a price-tag photo | Rarely |
| `scripts/save_photo.py` | Saves a photo into the repo with GPS/metadata removed; reads when it was taken | |
| `scripts/validate.py` | Checks receipts and price tags (schema, totals, aliases, privacy) | |
| `scripts/build_ledger.py` | Rebuilds `data/items.csv` and `data/price_tags.csv` | |
| `scripts/cheapest.py` | Answers "where is X cheapest?" from receipts and price tags | |
| `scripts/common.py` | Shared helpers (loading, unit conversion) | |
| `tests/` | pytest, with FAKE receipts and price tags in `tests/fixtures/` | |

Setup in a fresh session: `pip install -r requirements.txt`.
Run the tests with `python -m pytest -q`.

## Naming rules

- **Chain slug** (`store.chain`, also the top-level key in `aliases.json`): lowercase, hyphens,
  no punctuation. "Trader Joe's" → `trader-joes`, "H-E-B" → `heb`, "Kroger" → `kroger`.
  Check existing receipts/aliases and reuse the slug already in use for that chain.
- **store.name**: human-readable, location included: `"Kroger - Main St"`.
- **Receipt id / file names**: `YYYY-MM-DD_<chain>_HHMM` using the time printed on the receipt
  (24-hour). If two receipts collide, add `_2`. Photo: `receipts/YYYY/<id>.jpg` (keep the original
  extension if it is png/heic/webp; don't convert). JSON: `data/receipts/<id>.json`.
- **product_key**: lowercase-with-hyphens, general → specific:
  `<item>-<variant>-<size>-<brand>`, e.g. `milk-2pct-gallon-store-brand`,
  `pasta-spaghetti-16oz-store-brand`, `ramen-chicken-3oz-maruchan`.
  - `%` → `pct`. Sizes like `16oz`, `52floz`, `12ct`, `gallon`.
  - Store brands end in `-store-brand` (shared across chains, so Kroger's and Aldi's
    store-brand milk compare directly), with `brand: null, is_store_brand: true`.
  - Name brands end in the brand slug, with `brand: "Maruchan", is_store_brand: false`.
  - Loose produce and meat sold by weight have no size or brand: `bananas`, `chicken-breast-boneless-skinless`.
  - Electronics and other one-item products: `<item>-<size>-<brand>-<model>`, e.g.
    `tv-65in-samsung-un65m80hafxza`. Set `"model"` to the manufacturer model number when it is
    printed (it makes the exact model searchable), `size_value: 1, size_unit: "count"`, and a
    category such as `tvs` or `electronics`. If only a partial model is printed (e.g. "M70"),
    put it in the name and key, and leave `model` out.
  - Leave the size out of the key when it is unknown (`bread-white-sandwich-store-brand`).
    A product key means **one package size**. If the owner later buys a different size of a
    product whose size is already set, create a new key with the size in it; don't change the old one.
- **category** must be one of the keys in `config/unit_basis.json` → `categories`.
  If nothing fits, add a category there (with a basis) rather than inventing one silently.
- **size_unit** must be one of: `gal qt pt L ml fl_oz lb oz kg g count sheets`.

## Receipt JSON

See `schema/receipt.schema.json` for the exact shape. Key points:

- `price_paid` = what the whole line actually cost **after** discounts.
  `regular_price` = what it would have cost without discounts. `discount` = the difference (positive).
  `regular_price - discount == price_paid` must hold.
- `quantity`: number of packages for `unit_of_sale: "each"`; the weight for `"lb"`/`"kg"`.
  `weighed: true` exactly when `unit_of_sale` is `lb` or `kg`.
  Example `BANANAS 2.40 lb @ $0.59/lb  1.42` → `quantity 2.4, unit_of_sale "lb", weighed true, price_paid 1.42`.
  Example `2 @ $1.25  2.50` → `quantity 2, unit_of_sale "each", price_paid 2.50` (line total, not each).
- `on_sale: true` when a discount applies to the line.
- `receipt_text`: the item description **as printed** (keep the store's abbreviations and
  spacing; leave out item/UPC codes, the price column and tax flags like `F`/`T`).
  This exact string is the alias key, so be consistent.
- `product_key: null` is allowed **only** for lines that are not a product: bag fee, bottle
  deposit, or a whole-order coupon (e.g. "$5 off $50"; record it with a negative `price_paid`
  and `regular_price`, `discount: 0`). These lines count toward the subtotal but stay out of the ledger.
- `subtotal`, `tax`, `total`: as printed. `payment_type`: one of
  `card cash ebt mobile gift_card mixed other unknown` — **never** any digits of a card.
- `parsed_at`: now, ISO 8601 UTC, e.g. `2026-11-09T18:30:00Z`.
- `confidence`: `high` if you read it clearly, `medium` if you inferred something
  (e.g. which product an abbreviation means), `low` if you're guessing.
  Set `needs_review: true` + a `review_note` for anything the owner should confirm.

## Photos: always save through `save_photo.py`

Phone photos contain hidden metadata, **including the GPS location**. Never copy a photo into
the repo directly. Use:
```
python scripts/save_photo.py PHOTO --info        # when was it taken? (saves nothing)
python scripts/save_photo.py PHOTO --kind receipt   --chain <slug> --date YYYY-MM-DD --time HH:MM
python scripts/save_photo.py PHOTO --kind price_tag --chain <slug>   # date/time from the photo
```
It strips all metadata, turns the photo upright, converts HEIC to JPG, picks the standard file
name (adding `_2` if taken), and prints the `id` to use for the JSON file.
Uploaded files usually live under `/root/.claude/uploads/...`; the owner's message gives the path.
To *look at* a HEIC photo yourself, first make a JPG copy in your scratchpad (e.g. with Pillow +
pillow-heif) and zoom/crop into small print rather than guessing.

## Workflow A: "Here's a receipt" (photo attached)

1. **Save the photo** with `save_photo.py --kind receipt`, passing the date and time **printed on
   the receipt** (not the photo's time).
   If the photo isn't available as a file in the session (only visible inline), say so,
   still do the JSON, and ask the owner to upload/commit the file; validate.py will warn
   "photo not found" until then.
2. **Read the receipt yourself** and write `data/receipts/<id>.json` per the schema.
   - Attach discount / coupon / loyalty-savings lines (e.g. `SC SAVINGS -0.80`,
     `MVP PRICE`, `COUPON`) to the item they apply to: set that item's `discount`,
     `price_paid = regular_price - discount`, `on_sale: true`. Don't record them as separate items.
     If you can't tell which item a discount belongs to, ask.
   - Weighed items: use the weight as `quantity`, see the example above.
   - Multi-buy lines (`2 @ 1.25`): `quantity` = count, prices = line totals.
   - Some stores print the subtotal **before** discounts. Record the printed subtotal as-is;
     validate.py will report a mismatch and hint at this. Ask the owner how to handle it
     (don't fudge numbers to make the check pass).
3. **Map each line to a product.** First look in `aliases.json` under this chain for the exact
   `receipt_text`. If missing, look for an existing product in `products.json` that is the same
   thing (same item, variant and size) and add an alias. Only create a new product if nothing fits,
   following the naming rules. Show the owner any new products/aliases you created.
4. **Privacy: never record** card numbers (not even the last 4 digits), loyalty/member IDs,
   names (cashier or customer), phone numbers, or emails — not in any field, not in `notes`.
   The store address is fine. validate.py fails loudly if something looks like one of these.
5. **Validate**: `python scripts/validate.py data/receipts/<id>.json`.
   If there are errors, or any line has `needs_review`/low confidence, or the totals don't match:
   **stop and ask the owner before committing.** List the specific lines (line number, receipt
   text, what you read, what you're unsure about). Fix, then re-validate.
6. **Build and commit**:
   ```
   python scripts/build_ledger.py
   python -m pytest -q
   git add receipts/ data/
   git commit -m "Add receipt: <Store name> <YYYY-MM-DD> (\$<total>)"
   git push origin main
   ```
   Example message: `Add receipt: Kroger - Main St 2026-11-08 ($42.17)`.

**Branch:** commit receipt ingestion directly to `main` and push, unless the owner says otherwise
(this is the owner's explicit instruction and takes priority over a default "work on a feature
branch" habit). Before committing, `git fetch origin main` and make sure you're on an up-to-date `main`.

## Workflow F: "Here's a price tag" (photo of a shelf tag)

A price tag is a price the owner **saw**, not a purchase. It never counts as money spent.
One photo can show several tags; record each as one entry in `items`.

1. Look at the photo carefully (zoom into small print). Work out the **store chain** from the tag's
   style or text (e.g. "Rollback" = Walmart, "Comp Value"/"My Best Buy" = Best Buy, Costco's white
   tags with item number + "PRICE PER OUNCE"). If you're not sure which chain, or which branch/
   location, **ask**. Never guess the location from GPS data.
2. `python scripts/save_photo.py PHOTO --kind price_tag --chain <slug>`. Date and time come from
   the photo. If the photo has no date, ask the owner and pass `--date`/`--time`.
3. Write `data/price_tags/<id>.json` per `schema/price_tag.schema.json`:
   - `tag_text`: product description as printed on the tag (no item number, no price).
   - `price`: what you'd pay now for one package. `price_unit: "lb"`/`"kg"` when the tag is a
     per-pound/kilo price (produce, meat); otherwise `"each"`.
   - `regular_price`: equals `price` when not on sale. On sale: the printed was/regular/"Comp Value"
     price, or `null` if none is printed. Mention in `notes` when it's a "Comp Value" (Best Buy's
     comparison price, which may not be what that store ever charged).
   - `on_sale`, `sale_label` (e.g. "Rollback", "Black Friday Deal", "Instant Savings"),
     `sale_ends` (if printed).
   - `printed_unit_price`: the tag's unit price, e.g. `{"value": 0.454, "per": "oz"}`. validate.py
     uses it to double-check the package size, or to suggest one when the size is unknown.
   - `store_item_id`: the store's item number / SKU if printed. Also add it as an alias
     (`"#990551": "<product_key>"`) so a later receipt or tag with that number finds the product.
   - Ignore financing offers ("$27.78/mo"), QR codes and shelf location codes.
   - Never record names, phone numbers, or loyalty/card numbers (same privacy rules as receipts).
4. Map each tag to a product exactly as in Workflow A step 3 (aliases are per chain; add the
   `tag_text` as an alias). Tags usually print the size, so fill in `size_value`/`size_unit` for
   new products. If a tag shows a size for an existing product with `size_value: null`, add it
   and mention it.
5. `python scripts/validate.py data/price_tags/<id>.json`. If anything is unclear (glare,
   blurry digits, unknown store or location), **ask before committing**, listing the specific tags.
6. Build and commit, same as Workflow A step 6, but:
   `git add price_tags/ data/` and message `Add price tags: <Store name> <YYYY-MM-DD> (<n> items)`.

## Workflow B: "Where should I buy X?" / "Where was X cheapest?"

1. Run `scripts/cheapest.py` and base the answer on its output, never on memory or
   general knowledge of store prices. (For any other kind of question, see Workflow E.)
   - Specific product: `python scripts/cheapest.py "2% milk"`
   - "Any milk", "cheapest pasta of any brand": add `--any-brand`
   - A whole category ("produce", "poultry"): `--category`
   - Recent prices only: `--since YYYY-MM-DD`
   - "Show me every time I bought X": `--history`
   If nothing matches, try a shorter or different word, or look at `data/products.json` to find
   the right name, then run it again.
2. In the answer, say **both** the cheapest regular price and the cheapest sale price (they are
   different things: a sale may not be there next week), with store and date.
   Keep the two sources apart, as the output does: **paid** (from receipts) vs **shelf tag**
   (seen in a store, not bought). Mention sale end dates when shown.
   Mention when a price is marked **STALE** (older than 60 days).
   When the output says "per package (size unknown)", say so: those can't be compared by unit price.
   Offer to fill in the missing size (Workflow D).

## Workflow E: any other question about the history

The owner can ask anything about their purchases: "how much did I spend in October?",
"list everything I bought at Aldi", "what do I buy most often?", "did egg prices go up?",
"make me a chart of my weekly spending". Answer these from the data.

1. Run `python scripts/build_ledger.py` first so `data/items.csv` is current.
2. Pick the right source:
   - **`data/items.csv`**: one row per product purchased, with unit prices. Use it for
     questions about items, products, categories, brands and prices.
   - **`data/price_tags.csv`**: one row per shelf tag seen. Use it for "what did X cost at Y" and
     price trends, but never for money spent.
   - **`data/receipts/*.json`**: use it for **money spent**. Receipt `total` includes tax and
     non-product lines (bag fees, deposits, order coupons) that are *not* in items.csv, so
     summing `price_paid` from the CSV under-counts spending.
   - For "where is X cheapest?", still use `cheapest.py` (Workflow B); it applies the
     sale/regular/stale/unknown-size rules.
3. **Compute with code, not in your head.** Write a short Python snippet (the standard
   `csv`/`json` modules are enough) for any sum, average, count or comparison, and answer from
   its output. Small lookups ("what did I buy on Nov 8?") can be read straight from the file.
4. Lists and tables: show them in the reply as a markdown table.
   Charts: make them as an HTML page/artifact or with a throwaway script in a scratch folder.
   Don't add plotting libraries to `requirements.txt` and don't commit one-off analysis
   scripts unless the owner asks to keep them (then put them in `scripts/` with a test).
5. Say what the answer covers ("based on 14 receipts from Sep 2 to Nov 8") and point out
   gaps: items with unknown size, lines needing review, STALE prices, or periods with no receipts.
   Never fill gaps with outside knowledge of prices.

## Workflow C: "Fix X"

(e.g. "line 3 was actually oat milk", "that store is Aldi not Lidl", "the eggs were 18 count")

1. Edit the relevant receipt JSON, `aliases.json` and/or `products.json`. If a product key changes,
   update every receipt and alias that uses it (`grep -r "<old-key>" data/`).
2. `python scripts/validate.py` (all receipts), then `python scripts/build_ledger.py`.
3. Commit with a message saying what was fixed, e.g. `Fix receipt: Kroger 2026-11-08 line 3 is oat milk`.
   Push to `main`.

## Workflow D: missing package size

Receipts often don't print the package size. **Never guess a size silently.**
- If a product has `size_value: null` and you see it on a receipt, ask the owner **once**
  ("How big is the store-brand white bread you buy, e.g. 20 oz?").
- Store the answer in `products.json` (`size_value` + `size_unit`), rebuild the ledger, commit.
- If they don't know, leave it null; cheapest.py will show per-package prices for it.
- If the size is printed on the receipt or obvious from the text (e.g. `16OZ`), use it,
  and mention it in your summary.

## Never

- Never hand-edit `data/items.csv` or `data/price_tags.csv`.
- Never copy a photo into the repo without `save_photo.py` (it removes the GPS location).
- Never put real receipts in `tests/` or fake receipts in `data/`.
- Never record personal/payment data (see Workflow A step 4).
- Never invent prices, sizes or stores to fill gaps; ask instead.
- Don't add UI, web servers, databases or external API calls.
