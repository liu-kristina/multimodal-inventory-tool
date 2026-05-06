"""
scripts/generate_demo_invoices.py

Generates deterministic demo invoice PDFs for the live Gmail label routing demo.
No randomness — every run produces identical byte-stable content.

Requires: fpdf2  (pip install fpdf2)  — already in requirements.txt as fpdf2==2.8.7

Output files:
  data/demo/customer_invoices/CUST-DEMO-001.pdf
      Sales invoice: California Nutraceuticals Inc. (seller) -> Pacific Health Supplements LLC
      Attach to email, apply label: sales/invoice
      Expected inventory: Collagen Powder -380 kg, Fish Collagen Peptides -100 kg

  data/demo/purchase_receipts/SUP-DEMO-COLLAGEN-001.pdf   [primary demo receipt]
      Purchase receipt: Pacific Rim BioMaterials Co. (supplier) -> California Nutraceuticals Inc.
      Attach to email, apply label: purchase/receipt
      Expected inventory: Collagen Powder +400 kg  (matches APPROVE path from CUST-DEMO-001)

  data/demo/purchase_receipts/SUP-DEMO-001.pdf   [secondary / optional]
      Purchase receipt: Pacific Rim BioMaterials Co. -> California Nutraceuticals Inc.
      Expected inventory: Hydrolyzed Marine Collagen +180 kg, Collagen Peptides Type I +150 kg
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
CUST_INVOICE_DIR  = _ROOT / "data" / "demo" / "customer_invoices"
PURCH_RECEIPT_DIR = _ROOT / "data" / "demo" / "purchase_receipts"


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

_PAGE_W    = 210          # A4 width  mm
_MARGIN    = 15           # left / right margin mm
_COL_W     = _PAGE_W - 2 * _MARGIN


def _make_pdf():
    """Return a blank FPDF object configured for A4."""
    from fpdf import FPDF
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    return pdf


def _title(pdf, text: str) -> None:
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(_COL_W, 10, text, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _rule(pdf) -> None:
    """Draw a thin horizontal rule at the current Y position."""
    y = pdf.get_y()
    pdf.set_draw_color(160, 160, 160)
    pdf.set_line_width(0.3)
    pdf.line(_MARGIN, y, _PAGE_W - _MARGIN, y)
    pdf.ln(3)


def _section_label(pdf, text: str) -> None:
    pdf.set_font("Helvetica", style="B", size=9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(_COL_W, 5, text.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30, 30, 30)


def _kv(pdf, key: str, value: str) -> None:
    pdf.set_font("Helvetica", size=10)
    pdf.cell(45, 6, key, new_x="RIGHT", new_y="TOP")
    pdf.set_font("Helvetica", style="B", size=10)
    pdf.cell(_COL_W - 45, 6, value, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)


def _address_block(pdf, heading: str, name: str, lines: list[str]) -> None:
    _section_label(pdf, heading)
    pdf.set_font("Helvetica", style="B", size=10)
    pdf.cell(_COL_W, 6, name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for line in lines:
        pdf.cell(_COL_W, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)


def _table_header(pdf, cols: list[tuple[str, float, str]]) -> None:
    """
    cols: list of (label, width_fraction, align)
    width_fraction values should sum to 1.0
    """
    pdf.set_font("Helvetica", style="B", size=9)
    pdf.set_fill_color(230, 230, 230)
    pdf.set_text_color(50, 50, 50)
    row_h = 7
    for label, frac, align in cols:
        pdf.cell(_COL_W * frac, row_h, label, border=1, align=align, fill=True,
                 new_x="RIGHT", new_y="TOP")
    pdf.ln(row_h)
    pdf.set_text_color(30, 30, 30)


def _table_row(pdf, cols: list[tuple[str, float, str]]) -> None:
    pdf.set_font("Courier", size=9)
    row_h = 7
    for text, frac, align in cols:
        pdf.cell(_COL_W * frac, row_h, text, border=1, align=align,
                 new_x="RIGHT", new_y="TOP")
    pdf.ln(row_h)


def _totals_block(pdf, subtotal: str, tax: str, total: str) -> None:
    pdf.ln(4)
    right_start = _MARGIN + _COL_W * 0.55
    label_w = _COL_W * 0.25
    value_w = _COL_W * 0.20

    for label, value, bold in [
        ("Subtotal", subtotal, False),
        ("Tax / Fees", tax, False),
        ("TOTAL (USD)", total, True),
    ]:
        pdf.set_x(right_start)
        style = "B" if bold else ""
        pdf.set_font("Helvetica", style=style, size=10)
        pdf.cell(label_w, 7, label, align="R", new_x="RIGHT", new_y="TOP")
        pdf.cell(value_w, 7, value, align="R", new_x="LMARGIN", new_y="NEXT")


def _footer_note(pdf, text: str) -> None:
    pdf.ln(6)
    _rule(pdf)
    pdf.set_font("Helvetica", style="I", size=8)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(_COL_W, 5, text)
    pdf.set_text_color(30, 30, 30)


# ---------------------------------------------------------------------------
# Invoice generators
# ---------------------------------------------------------------------------

def _gen_sales_invoice(out_path: Path) -> None:
    """
    CUST-DEMO-001: California Nutraceuticals Inc. is the SELLER.
    LLM must classify document_type = 'sales'.
    Inventory decreases: Collagen Powder -380 kg, Fish Collagen Peptides -100 kg.
    """
    pdf = _make_pdf()

    _title(pdf, "SALES INVOICE")
    _rule(pdf)

    pdf.ln(2)
    _kv(pdf, "Invoice No :", "CUST-DEMO-001")
    _kv(pdf, "Date       :", "2025-06-01")
    _kv(pdf, "Due Date   :", "2025-07-01")
    _kv(pdf, "Terms      :", "Net 30")
    pdf.ln(5)

    _address_block(pdf, "Sold by (Seller / Supplier)",
                   "California Nutraceuticals Inc.",
                   ["123 Business Street, San Jose, CA 95110",
                    "billing@canutra.com"])

    _address_block(pdf, "Bill to (Buyer / Customer)",
                   "Pacific Health Supplements LLC",
                   ["800 Wellness Drive, Austin, TX 78701",
                    "orders@pacifichealthsupplements.com"])

    _rule(pdf)
    pdf.ln(2)

    col_defs_h = [
        ("Description",  0.42, "L"),
        ("Qty",          0.13, "R"),
        ("Unit",         0.10, "C"),
        ("Unit Price",   0.18, "R"),
        ("Line Total",   0.17, "R"),
    ]
    _table_header(pdf, col_defs_h)

    items = [
        ("Collagen Powder",       "380", "kg", "USD 120.00", "USD 45,600.00"),
        ("Fish Collagen Peptides", "100", "kg", "USD 180.00", "USD 18,000.00"),
    ]
    for desc, qty, unit, up, lt in items:
        _table_row(pdf, [
            (desc, 0.42, "L"),
            (qty,  0.13, "R"),
            (unit, 0.10, "C"),
            (up,   0.18, "R"),
            (lt,   0.17, "R"),
        ])

    _totals_block(pdf,
                  subtotal="USD 63,600.00",
                  tax="USD 0.00",
                  total="USD 63,600.00")

    _footer_note(pdf,
        "Document type: Sales Invoice  |  California Nutraceuticals Inc. is the SELLER on this document.\n"
        "Inventory effect when labeled sales/invoice: "
        "Collagen Powder -380 kg, Fish Collagen Peptides -100 kg.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))


def _gen_purchase_receipt(out_path: Path) -> None:
    """
    SUP-DEMO-001: Pacific Rim BioMaterials Co. is the SELLER; we are the BUYER.
    LLM must classify document_type = 'purchase'.
    Inventory increases: Hydrolyzed Marine Collagen +180 kg, Collagen Peptides Type I +150 kg.
    """
    pdf = _make_pdf()

    _title(pdf, "PURCHASE RECEIPT")
    _rule(pdf)

    pdf.ln(2)
    _kv(pdf, "Invoice No :", "SUP-DEMO-001")
    _kv(pdf, "Date       :", "2025-06-02")
    _kv(pdf, "Due Date   :", "2025-07-02")
    _kv(pdf, "Terms      :", "Net 30")
    pdf.ln(5)

    _address_block(pdf, "Sold by (Seller / Supplier)",
                   "Pacific Rim BioMaterials Co.",
                   ["450 Harbor View Blvd, Seattle, WA 98101",
                    "jchen@pacificrimbiomaterials.com"])

    _address_block(pdf, "Bill to (Buyer / Purchaser)",
                   "California Nutraceuticals Inc.",
                   ["123 Business Street, San Jose, CA 95110",
                    "purchasing@canutra.com"])

    _rule(pdf)
    pdf.ln(2)

    col_defs_h = [
        ("Description",  0.42, "L"),
        ("Qty",          0.13, "R"),
        ("Unit",         0.10, "C"),
        ("Unit Price",   0.18, "R"),
        ("Line Total",   0.17, "R"),
    ]
    _table_header(pdf, col_defs_h)

    items = [
        ("Hydrolyzed Marine Collagen", "180", "kg", "USD 125.00", "USD 22,500.00"),
        ("Collagen Peptides Type I",   "150", "kg", "USD  98.00", "USD 14,700.00"),
    ]
    for desc, qty, unit, up, lt in items:
        _table_row(pdf, [
            (desc, 0.42, "L"),
            (qty,  0.13, "R"),
            (unit, 0.10, "C"),
            (up,   0.18, "R"),
            (lt,   0.17, "R"),
        ])

    _totals_block(pdf,
                  subtotal="USD 37,200.00",
                  tax="USD 0.00",
                  total="USD 37,200.00")

    _footer_note(pdf,
        "Document type: Purchase Receipt  |  California Nutraceuticals Inc. is the BUYER on this document.\n"
        "Pacific Rim BioMaterials Co. is the supplier/seller.\n"
        "Inventory effect when labeled purchase/receipt: "
        "Hydrolyzed Marine Collagen +180 kg, Collagen Peptides Type I +150 kg.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))


def _gen_collagen_purchase_receipt(out_path: Path) -> None:
    """
    SUP-DEMO-COLLAGEN-001: Pacific Rim BioMaterials Co. is the SELLER; we are the BUYER.
    Primary demo receipt — matches the Collagen Powder order approved from CUST-DEMO-001.
    LLM must classify document_type = 'purchase'.
    Inventory increases: Collagen Powder +400 kg.
    """
    pdf = _make_pdf()

    _title(pdf, "PURCHASE RECEIPT")
    _rule(pdf)

    pdf.ln(2)
    _kv(pdf, "Invoice No :", "SUP-DEMO-COLLAGEN-001")
    _kv(pdf, "Date       :", "2025-06-15")
    _kv(pdf, "Due Date   :", "2025-07-15")
    _kv(pdf, "Terms      :", "Net 30")
    pdf.ln(5)

    _address_block(pdf, "Sold by (Seller / Supplier)",
                   "Pacific Rim BioMaterials Co.",
                   ["450 Harbor View Blvd, Seattle, WA 98101",
                    "jchen@pacificrimbiomaterials.com"])

    _address_block(pdf, "Bill to (Buyer / Purchaser)",
                   "California Nutraceuticals Inc.",
                   ["123 Business Street, San Jose, CA 95110",
                    "purchasing@canutra.com"])

    _rule(pdf)
    pdf.ln(2)

    col_defs_h = [
        ("Description",  0.42, "L"),
        ("Qty",          0.13, "R"),
        ("Unit",         0.10, "C"),
        ("Unit Price",   0.18, "R"),
        ("Line Total",   0.17, "R"),
    ]
    _table_header(pdf, col_defs_h)

    items = [
        ("Collagen Powder", "400", "kg", "USD  66.00", "USD 26,400.00"),
    ]
    for desc, qty, unit, up, lt in items:
        _table_row(pdf, [
            (desc, 0.42, "L"),
            (qty,  0.13, "R"),
            (unit, 0.10, "C"),
            (up,   0.18, "R"),
            (lt,   0.17, "R"),
        ])

    _totals_block(pdf,
                  subtotal="USD 26,400.00",
                  tax="USD 180.00",
                  total="USD 26,580.00")

    _footer_note(pdf,
        "Document type: Purchase Receipt  |  California Nutraceuticals Inc. is the BUYER on this document.\n"
        "Pacific Rim BioMaterials Co. is the supplier/seller.\n"
        "Inventory effect when labeled purchase/receipt: Collagen Powder +400 kg.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_demo_invoices() -> bool:
    """Generate all demo invoice PDFs. Returns True on success."""
    try:
        import fpdf  # noqa: F401
    except ImportError:
        print("ERROR: fpdf2 is not installed. Run: pip install fpdf2")
        return False

    sales_path  = CUST_INVOICE_DIR  / "CUST-DEMO-001.pdf"
    collagen_path = PURCH_RECEIPT_DIR / "SUP-DEMO-COLLAGEN-001.pdf"
    purch_path  = PURCH_RECEIPT_DIR / "SUP-DEMO-001.pdf"

    _gen_sales_invoice(sales_path)
    print(f"  Generated: {sales_path.relative_to(_ROOT)}")

    _gen_collagen_purchase_receipt(collagen_path)
    print(f"  Generated: {collagen_path.relative_to(_ROOT)}")

    _gen_purchase_receipt(purch_path)
    print(f"  Generated: {purch_path.relative_to(_ROOT)}")

    return True


if __name__ == "__main__":
    print("Generating demo invoice PDFs...")
    ok = generate_demo_invoices()
    if ok:
        print("Done.")
    else:
        raise SystemExit(1)
