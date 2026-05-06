"""
scripts/generate_demo_pdfs.py

Generates two demo invoice PDFs for the Gmail label routing demo.
Requires: pip install reportlab

Output files (created under data/demo_pdfs/):
  sales_invoice_INV_SALES_001.pdf   - sales invoice, seller = California Nutraceuticals
  purchase_receipt_INV_PUR_001.pdf  - purchase receipt, seller = CarbonSupply Inc.
"""

from __future__ import annotations
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo_pdfs"


def generate_pdfs() -> bool:
    """Generate both demo PDFs. Returns True on success, False if reportlab missing."""
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        print("ERROR: reportlab is not installed. Run: python -m pip install reportlab")
        return False

    _gen_sales_invoice()
    _gen_purchase_receipt()
    return True


def _draw_pdf(path: Path, lines: list[str]) -> None:
    """Render a list of text lines to a letter-sized PDF using Courier font."""
    if path.exists():
        print(f"  Already exists (skipping): {path.name}")
        return

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - inch
    c.setFont("Courier", 11)

    for line in lines:
        if line == "---":
            c.setLineWidth(0.5)
            c.line(inch, y + 5, width - inch, y + 5)
        else:
            c.drawString(inch, y, line)
        y -= 16
        if y < inch:
            c.showPage()
            c.setFont("Courier", 11)
            y = height - inch

    try:
        c.save()
        print(f"  Generated: {path.relative_to(Path(__file__).resolve().parent.parent)}")
    except PermissionError:
        print(f"  WARNING: Cannot write {path.name} — close any open PDF viewers and retry.")


def _gen_sales_invoice() -> None:
    """
    Sales invoice: California Nutraceuticals Inc. is the SELLER.
    LLM should classify as document_type = 'sales'.
    Inventory effect: NMC Powder  -5 kg
    """
    path = DEMO_DIR / "sales_invoice_INV_SALES_001.pdf"
    lines = [
        "SALES INVOICE",
        "---",
        f"Invoice Number : INV-SALES-001",
        f"Date           : 2025-01-15",
        f"Payment Terms  : Net 30",
        f"Due Date       : 2025-02-14",
        "",
        "SOLD BY (Seller):",
        "  California Nutraceuticals Inc.",
        "  123 Business Street, San Jose, CA 95110",
        "  billing@canutra.com",
        "",
        "BILL TO (Buyer / Customer):",
        "  Demo Customer",
        "  456 Customer Avenue, Los Angeles, CA 90001",
        "  orders@democustomer.com",
        "",
        "---",
        "  DESCRIPTION         QTY       UNIT PRICE    LINE TOTAL",
        "---",
        "  NMC Powder          5 kg      $45.00/kg     $225.00",
        "---",
        "",
        f"  Subtotal :   $225.00",
        f"  Tax      :     $0.00",
        f"  TOTAL    :   $225.00",
        "",
        "---",
        "  NOTE: California Nutraceuticals Inc. is the seller on this invoice.",
        "  Document type: Sales Invoice",
    ]
    _draw_pdf(path, lines)


def _gen_purchase_receipt() -> None:
    """
    Purchase receipt: CarbonSupply Inc. is the SELLER, we are the BUYER.
    LLM should classify as document_type = 'purchase'.
    Inventory effect: Graphite Anode  +20 kg
    """
    path = DEMO_DIR / "purchase_receipt_INV_PUR_001.pdf"
    lines = [
        "PURCHASE RECEIPT / INVOICE",
        "---",
        f"Invoice Number : INV-PUR-001",
        f"Date           : 2025-01-15",
        f"Payment Terms  : Net 30",
        f"Due Date       : 2025-02-14",
        "",
        "SOLD BY (Seller / Supplier):",
        "  CarbonSupply Inc.",
        "  789 Supplier Boulevard, Chicago, IL 60601",
        "  sales@carbonsupply.com",
        "",
        "BILL TO (Buyer):",
        "  California Nutraceuticals Inc.",
        "  123 Business Street, San Jose, CA 95110",
        "  purchasing@canutra.com",
        "",
        "---",
        "  DESCRIPTION         QTY       UNIT PRICE    LINE TOTAL",
        "---",
        "  Graphite Anode      20 kg     $35.00/kg     $700.00",
        "---",
        "",
        f"  Subtotal :   $700.00",
        f"  Tax      :     $0.00",
        f"  TOTAL    :   $700.00",
        "",
        "---",
        "  NOTE: California Nutraceuticals Inc. is the buyer on this invoice.",
        "  CarbonSupply Inc. is the supplier/seller.",
        "  Document type: Purchase Receipt",
    ]
    _draw_pdf(path, lines)


if __name__ == "__main__":
    print("Generating demo PDFs...")
    ok = generate_pdfs()
    if ok:
        print("Done.")
