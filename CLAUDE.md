# Grocery price tracker: instructions for Claude

This repo is a personal grocery price tracker. The owner attaches photos of
grocery receipts in a session; you read them **yourself** (no OCR library, no
external API) and turn them into structured JSON. Scripts then build a ledger
and answer "where is X cheapest?".

Plain files only. No UI, no web app, no database server, no external API calls.
The owner is not an expert: keep code simple and explain what you did in plain words.

## Layout

| Path | What it is | Edit by hand? |
|---|---|---|
| `receipts/YYYY/` | Receipt photos, named `YYYY-MM-DD_<chain>_HHMM.jpg` | Add only |
| `data/receipts/` | One JSON per receipt, same base name as its photo | Yes |
| `data/aliases.json` | `{ "<chain>": { "<receipt_text>": "<product_key>" } }` | Yes |
| `data/products.json` | `{ "<product_key>": { name, category, brand, is_store_brand, size_value, size_unit } }` | Yes |
| `data/items.csv` | **Generated** by `build_ledger.py` | **Never** |
| `config/unit_basis.json` | Category → comparison basis, plus the unit conversion table | Rarely |
| `schema/receipt.schema.json` | JSON Schema for a receipt | Rarely |
| `scripts/validate.py` | Checks receipts (schema, totals, aliases, privacy) | |
| `scripts/build_ledger.py` | Rebuilds `data/items.csv` | |
| `scripts/cheapest.py` | Answers "where is X cheapest?" | |
| `scripts/common.py` | Shared helpers (loading, unit conversion) | |
| `tests/` | pytest, with FAKE receipts in `tests/fixtures/` | |

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

## Workflow A: "Here's a receipt" (photo attached)

1. **Save the photo** into `receipts/YYYY/` with the standard name (see naming rules).
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

## Workflow B: "Where should I buy X?" / "Where was X cheapest?"

1. Run `scripts/cheapest.py` and answer **only from its output**, never from memory or
   general knowledge of store prices.
   - Specific product: `python scripts/cheapest.py "2% milk"`
   - "Any milk", "cheapest pasta of any brand": add `--any-brand`
   - A whole category ("produce", "poultry"): `--category`
   - Recent prices only: `--since YYYY-MM-DD`
   - "Show me every time I bought X": `--history`
   If nothing matches, try a shorter or different word, or look at `data/products.json` to find
   the right name, then run it again.
2. In the answer, say **both** the cheapest regular price and the cheapest sale price (they are
   different things: a sale may not be there next week), with store and date.
   Mention when a price is marked **STALE** (older than 60 days).
   When the output says "per package (size unknown)", say so: those can't be compared by unit price.
   Offer to fill in the missing size (Workflow D).

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

- Never hand-edit `data/items.csv`.
- Never put real receipts in `tests/` or fake receipts in `data/`.
- Never record personal/payment data (see Workflow A step 4).
- Never invent prices, sizes or stores to fill gaps; ask instead.
- Don't add UI, web servers, databases or external API calls.
