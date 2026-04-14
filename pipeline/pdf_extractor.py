"""
Entity extractor for California Nutraceuticals invoices.
Uses regex and string parsing — no API required.

Handles two invoice types:
  - Supplier invoices  (COMMERCIAL INVOICE) from Chinese suppliers
  - Customer invoices  (SALES INVOICE)      to American buyers
"""

import re
import json
import fitz          # pip install pymupdf
import os
from pathlib import Path


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text(pdf_path: str) -> str:
    """Extract raw text from a PDF using PyMuPDF."""
    doc = fitz.open(pdf_path)
    pages = [page.get_text() for page in doc]
    return "\n".join(pages)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find(pattern: str, text: str, default: str = "") -> str:
    """Return first capture group or default if no match."""
    m = re.search(pattern, text)
    return m.group(1).strip() if m else default


def _find_float(pattern: str, text: str, default: float = 0.0) -> float:
    """Return first capture group as a float (strips commas)."""
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return default


def _detect_invoice_type(text: str) -> str:
    """Return 'supplier' or 'customer' based on the title line."""
    if "COMMERCIAL INVOICE" in text:
        return "supplier"
    if "SALES INVOICE" in text:
        return "customer"
    return "unknown"


# ── Product line parser ────────────────────────────────────────────────────────

KNOWN_PRODUCTS = [
    "Collagen Powder",
    "Shark Cartilage Powder",
    "Bovine Gelatin Type A",
    "Fish Collagen Peptides",
    "Hydrolyzed Marine Collagen",
    "Bovine Cartilage Extract",
    "Plant Extract - Ginseng Root",
    "Plant Extract - Turmeric",
    "Plant Extract - Ashwagandha",
    "Plant Extract - Elderberry",
    "Plant Extract - Echinacea",
    "Hyaluronic Acid Powder",
    "Chondroitin Sulfate",
    "Glucosamine HCl",
    "Collagen Peptides Type I",
]

def _extract_line_items(text: str) -> list:
    """
    Parse product line items from the invoice text.
    Each product line looks like:
        Collagen Powder\n145\nkg\nUSD 76.49\nUSD 11,091.05
    """
    items = []
    for product in KNOWN_PRODUCTS:
        # Match product name followed by qty, unit, unit_price, total
        pattern = (
            re.escape(product) +
            r"\s+(\d+)\s+kg\s+(?:USD|EUR)\s+([\d,]+\.\d{1,2})\s+(?:USD|EUR)\s+([\d,]+\.\d{2})"
        )
        for m in re.finditer(pattern, text):
            items.append({
                "product":    product,
                "quantity":   int(m.group(1)),
                "unit":       "kg",
                "unit_price": float(m.group(2).replace(",", "")),
                "total":      float(m.group(3).replace(",", "")),
            })
    return items


# ── Supplier invoice extractor ─────────────────────────────────────────────────

def extract_supplier_invoice(text: str, filename: str) -> dict:
    """Extract all fields from a COMMERCIAL INVOICE (supplier → Cal Nutra)."""

    # Supplier name is the first line after SELLER / EXPORTER header
    supplier_name = ""
    m = re.search(r"SELLER / EXPORTER\s*\nBUYER / IMPORTER\s*\n(.+?)\n", text)
    if m:
        supplier_name = m.group(1).strip()

    return {
        "filename":        filename,
        "invoice_type":    "supplier",
        "invoice_number":  _find(r"Invoice (?:No|Number):\s*\n?([\w-]+)", text),
        "invoice_date":    _find(r"Invoice Date:\s*\n?(.+?)(?:\n|$)", text),
        "payment_due":     _find(r"(?:Due Date|Payment Due):\s*\n?(.+?)(?:\n|$)", text),
        "payment_terms":   _find(r"Payment Terms:\s*\n?(.+?)(?:\n|$)", text),
        "currency":        _find(r"Currency:\s*\n?(\w+)", text),
        "supplier_name":   supplier_name,
        "buyer_name":      "California Nutraceuticals Inc.",
        # Shipping & logistics
        "shipping_method": _find(r"Shipping Method:\s*\n?(.+?)(?:\n|$)", text),
        "port_of_loading": _find(r"Port of Loading:\s*\n?(.+?)(?:\n|$)", text),
        "port_of_destination": _find(
            r"Port of Destination:\s*\n?(.+?)(?:\n|$)", text),
        "shipment_date":   _find(r"Shipment Date:\s*\n?(.+?)(?:\n|$)", text),
        "expected_delivery": _find(
            r"Expected Delivery:\s*\n?(.+?)(?:\n|$)", text),
        "typical_lead_time": _find(
            r"Typical Lead Time:\s*\n?(.+?)(?:\n|$)", text),
        # Financials
        "line_items":      _extract_line_items(text),
        "subtotal":        _find_float(r"Subtotal:\s*USD\s*([\d,]+\.\d{2})", text),
        "freight":         _find_float(
            r"Freight & Insurance:\s*USD\s*([\d,]+\.\d{2})", text),
        "grand_total":     _find_float(
            r"Grand Total:\s*USD\s*([\d,]+\.\d{2})", text),
        # Banking
        "swift_code":      _find(r"SWIFT Code:\s*(\w+)", text),
    }


# ── Customer invoice extractor ─────────────────────────────────────────────────

def extract_customer_invoice(text: str, filename: str) -> dict:
    """Extract all fields from a SALES INVOICE (Cal Nutra → customer)."""

    # Customer name is the first company name after BILL TO / SHIP TO
    customer_name = ""
    m = re.search(r"BILL TO / SHIP TO\s*\n(?:California Nutraceuticals Inc\.\s*\n)?(.+?)\n", text)
    if m:
        customer_name = m.group(1).strip()

    return {
        "filename":        filename,
        "invoice_type":    "customer",
        "invoice_number":  _find(r"Invoice No:\s*\n?([\w-]+)", text),
        "po_number":       _find(r"PO Number:\s*\n?([\w-]+)", text),
        "invoice_date":    _find(r"Invoice Date:\s*\n?(.+?)(?:\n|$)", text),
        "payment_due":     _find(r"Payment Due:\s*\n?(.+?)(?:\n|$)", text),
        "payment_terms":   _find(r"Payment Terms:\s*\n?(.+?)(?:\n|$)", text),
        "currency":        "USD",
        "seller_name":     "California Nutraceuticals Inc.",
        "customer_name":   customer_name,
        "customer_type":   _find(r"Type:\s*(.+?)(?:\n|$)", text),
        # Shipping
        "shipping_method": _find(r"Shipping Method:\s*\n?(.+?)(?:\n|$)", text),
        "ship_from":       _find(r"Ship From:\s*\n?(.+?)(?:\n|$)", text),
        "ship_to":         _find(r"Ship To:\s*\n?(.+?)(?:\n|$)", text),
        "shipment_date":   _find(r"Shipment Date:\s*\n?(.+?)(?:\n|$)", text),
        "expected_delivery": _find(
            r"Expected Delivery:\s*\n?(.+?)(?:\n|$)", text),
        "transit_time":    _find(r"Transit Time:\s*\n?(.+?)(?:\n|$)", text),
        # Financials
        "line_items":      _extract_line_items(text),
        "subtotal":        _find_float(r"Subtotal:\s*USD\s*([\d,]+\.\d{2})", text),
        "shipping_cost":   _find_float(r"Shipping:\s*USD\s*([\d,]+\.\d{2})", text),
        "grand_total":     _find_float(r"Total Due:\s*USD\s*([\d,]+\.\d{2})", text),
    }


# ── Main dispatcher ────────────────────────────────────────────────────────────

def extract_invoice(pdf_path: str) -> dict:
    """
    Extract all entities from any invoice PDF.
    Auto-detects supplier vs customer invoice type.
    """
    text = extract_text(pdf_path)
    filename = Path(pdf_path).name
    invoice_type = _detect_invoice_type(text)

    if invoice_type == "supplier":
        return extract_supplier_invoice(text, filename)
    elif invoice_type == "customer":
        return extract_customer_invoice(text, filename)
    else:
        return {"filename": filename, "invoice_type": "unknown", "raw_text": text}


def extract_all(folder: str) -> list:
    """Process every PDF in a folder and return list of extracted dicts."""
    results = []
    pdfs = sorted(Path(folder).glob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs in {folder}")
    for pdf in pdfs:
        result = extract_invoice(str(pdf))
        results.append(result)
        inv_num = result.get("invoice_number", "???")
        supplier_or_customer = (result.get("supplier_name") or
                                result.get("customer_name") or "???")
        n_items = len(result.get("line_items", []))
        total = result.get("grand_total", 0)
        print(f"  {inv_num:<12}  {supplier_or_customer:<40}  "
              f"{n_items} item(s)  ${total:>10,.2f}")
    return results


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    supplier_dir = "/mnt/user-data/outputs/invoices"
    customer_dir = "/mnt/user-data/outputs/customer_invoices"

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

    # Save to JSON so you can inspect everything
    output_path = "extracted_invoices.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)

    print()
    print("=" * 72)
    print(f"Done. {len(all_results)} invoices extracted.")
    print(f"Full results saved to {output_path}")
    print()

    # Show one example in full
    print("SAMPLE — first supplier invoice:")
    print(json.dumps(supplier_results[0], indent=2))