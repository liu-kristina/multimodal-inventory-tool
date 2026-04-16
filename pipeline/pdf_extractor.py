"""
Entity extractor for California Nutraceuticals invoices.
Uses Claude AI for flexible extraction — handles variations in wording,
different supplier formats, and unknown products.

Handles two invoice types:
  - Supplier invoices  (COMMERCIAL INVOICE) from Chinese suppliers
  - Customer invoices  (SALES INVOICE)      to American buyers

Setup:
    pip install anthropic pymupdf python-dotenv

Add to your .env file:
    ANTHROPIC_API_KEY=your-key-here
"""

import re
import json
import fitz
import os
import sys
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import init_db, save_invoice, get_known_products

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
CLAUDE_MODEL = "claude-haiku-4-5-20251001"


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = [page.get_text() for page in doc]
    return "\n".join(pages)


# ── AI extraction ──────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are extracting structured data from a business invoice.
Extract all fields and return ONLY valid JSON — no preamble, no markdown, no explanation.

Known products list (for matching line items):
{known_products}

Return this exact JSON structure:
{{
  "invoice_type": "supplier or customer",
  "invoice_number": "string or null",
  "invoice_date": "string or null",
  "payment_due": "string or null",
  "payment_terms": "string or null",
  "currency": "string or null",
  "supplier_name": "string or null",
  "customer_name": "string or null",
  "customer_type": "string or null",
  "shipping_method": "string or null",
  "port_of_loading": "string or null",
  "port_of_destination": "string or null",
  "ship_from": "string or null",
  "ship_to": "string or null",
  "shipment_date": "string or null",
  "expected_delivery": "string or null",
  "lead_time": "string or null",
  "transit_time": "string or null",
  "po_number": "string or null",
  "subtotal": 0.0,
  "freight": 0.0,
  "grand_total": 0.0,
  "line_items": [
    {{
      "product": "matched product name from known list, or raw name if unknown",
      "quantity": 0,
      "unit": "kg",
      "unit_price": 0.0,
      "total": 0.0,
      "is_unknown_product": false
    }}
  ],
  "unknown_products": []
}}

Rules:
- invoice_type is "supplier" for COMMERCIAL INVOICE, "customer" for SALES INVOICE
- Match line items to known products as closely as possible (e.g. "Shark Cartilage Pwd" -> "Shark Cartilage Powder")
- If a product cannot be matched, set is_unknown_product to true and add to unknown_products list
- Use null for missing fields, not empty strings
- All monetary values must be floats

Invoice text:
{invoice_text}"""


def _handle_unknown_products(unknown_products: list):
    """Flag unknown products in a pending_products table for review in the app."""
    try:
        from database import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for product in unknown_products:
            conn.execute(
                "INSERT OR IGNORE INTO pending_products (product_name) VALUES (?)",
                (product,)
            )
            print(f"  [NEW PRODUCT] '{product}' flagged for review in app.")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [WARN] Could not save unknown products: {e}")


def extract_invoice_with_ai(text: str, filename: str) -> dict:
    """Use Claude to extract all invoice fields from raw text."""
    known_products = get_known_products()
    known_products_str = "\n".join(f"- {p}" for p in known_products)

    prompt = EXTRACTION_PROMPT.format(
        known_products=known_products_str,
        invoice_text=text,
    )

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    extracted = json.loads(raw)
    extracted["filename"] = filename

    unknown_products = extracted.get("unknown_products", [])
    if unknown_products:
        _handle_unknown_products(unknown_products)

    return extracted


# ── Main dispatcher ────────────────────────────────────────────────────────────

def extract_invoice(pdf_path: str) -> dict:
    """Extract all entities from any invoice PDF using Claude AI."""
    text = extract_text(pdf_path)
    filename = Path(pdf_path).name
    try:
        return extract_invoice_with_ai(text, filename)
    except Exception as e:
        print(f"  [ERROR] AI extraction failed for {filename}: {e}")
        return {"filename": filename, "invoice_type": "unknown", "raw_text": text}


def extract_all(folder: str) -> list:
    """Process every PDF in a folder and return list of extracted dicts."""
    results = []
    pdfs = sorted(Path(folder).glob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs in {folder}")
    for pdf in pdfs:
        print(f"  Extracting {pdf.name}...")
        result = extract_invoice(str(pdf))
        results.append(result)
        inv_num = result.get("invoice_number", "???")
        counterparty = result.get("supplier_name") or result.get("customer_name") or "???"
        n_items = len(result.get("line_items", []))
        total = result.get("grand_total", 0)
        print(f"  {inv_num:<12}  {counterparty:<40}  {n_items} item(s)  ${total:>10,.2f}")
    return results


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    supplier_dir = os.path.join(BASE_DIR, "data", "invoices")
    customer_dir = os.path.join(BASE_DIR, "data", "customer_invoices")

    init_db()

    print("=" * 72)
    print("SUPPLIER INVOICES")
    print("=" * 72)
    supplier_results = extract_all(supplier_dir)

    print()
    print("=" * 72)
    print("CUSTOMER INVOICES")
    print("=" * 72)
    customer_results = extract_all(customer_dir)

    all_results = supplier_results + customer_results

    saved = 0
    for inv in all_results:
        save_invoice(inv)
        saved += 1

    print()
    print("=" * 72)
    print(f"Done. {len(all_results)} invoices extracted, {saved} saved to SQLite.")

    if supplier_results:
        print()
        print("SAMPLE — first supplier invoice:")
        print(json.dumps(supplier_results[0], indent=2))
