"""
tests/test_pipeline.py — CI test suite for California Nutraceuticals
Tests that run without any API keys — no Claude, no OpenAI, no Gmail.

Run locally:
    pytest tests/test_pipeline.py -v

Run in CI:
    pytest tests/test_pipeline.py -v --tb=short
"""

import os
import sys
import tempfile
import pytest

# Point to a temp DB so tests never touch the real inventory.db
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_inventory.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Database tests ─────────────────────────────────────────────────────────────

from database import init_db, get_connection, save_invoice, get_unembedded_invoices, mark_embedded


def test_init_db_creates_tables():
    """init_db() should create all required tables."""
    init_db()
    conn = get_connection()
    tables = {
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert "stock"               in tables
    assert "invoices"            in tables
    assert "invoice_line_items"  in tables
    assert "products"            in tables
    assert "pending_products"    in tables


def test_init_db_is_idempotent():
    """Running init_db() twice should not raise."""
    init_db()
    init_db()


def test_save_invoice_supplier():
    """save_invoice() should persist a supplier invoice and its line items."""
    init_db()
    extracted = {
        "invoice_type":   "supplier",
        "invoice_number": "TEST-001",
        "invoice_date":   "12-Nov-2026",
        "supplier_name":  "Test Supplier Ltd",
        "grand_total":    1500.00,
        "filename":       "test.pdf",
        "line_items": [
            {"product": "Collagen Powder", "quantity": 100, "unit_price": 12.50, "total": 1250.00},
            {"product": "Shark Cartilage Powder", "quantity": 15, "unit_price": 16.67, "total": 250.00},
        ],
    }
    save_invoice(extracted)

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM invoices WHERE invoice_number = ?", ("TEST-001",)
    ).fetchone()
    items = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_number = ?", ("TEST-001",)
    ).fetchall()
    conn.close()

    assert row is not None
    assert row["invoice_type"]      == "supplier"
    assert row["counterparty_name"] == "Test Supplier Ltd"
    assert row["total_amount"]      == 1500.00
    assert len(items)               == 2


def test_save_invoice_skips_duplicate():
    """save_invoice() should silently skip duplicate invoice numbers."""
    init_db()
    extracted = {
        "invoice_type":   "customer",
        "invoice_number": "TEST-DUP-001",
        "supplier_name":  "Supplier A",
        "grand_total":    500.00,
        "line_items":     [],
    }
    save_invoice(extracted)
    save_invoice(extracted)  # second call should not raise

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE invoice_number = ?", ("TEST-DUP-001",)
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_embedded_flag():
    """get_unembedded_invoices() and mark_embedded() should work correctly."""
    init_db()
    extracted = {
        "invoice_type":   "supplier",
        "invoice_number": "TEST-EMBED-001",
        "supplier_name":  "Embed Supplier",
        "grand_total":    100.00,
        "line_items":     [],
    }
    save_invoice(extracted)

    unembedded = get_unembedded_invoices()
    assert "TEST-EMBED-001" in unembedded

    mark_embedded("TEST-EMBED-001")

    unembedded_after = get_unembedded_invoices()
    assert "TEST-EMBED-001" not in unembedded_after


def test_save_invoice_without_invoice_number():
    """save_invoice() should handle missing invoice_number gracefully."""
    init_db()
    extracted = {
        "invoice_type":  "supplier",
        "supplier_name": "No Number Supplier",
        "grand_total":   200.00,
        "line_items":    [],
    }
    save_invoice(extracted)  # should not raise


# ── Filename convention tests ──────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from invoice_agent import build_canonical_filename


def test_canonical_filename_supplier():
    extracted = {
        "invoice_type":   "supplier",
        "supplier_name":  "Jiaxing Natural Products Ltd",
        "invoice_date":   "12-Nov-2026",
        "invoice_number": "INV-0034",
    }
    name = build_canonical_filename(extracted)
    assert name.startswith("supplier_")
    assert "Jiaxing-Natural-Products-Ltd" in name
    assert "12-Nov-2026" in name
    assert "INV-0034" in name
    assert name.endswith(".pdf")


def test_canonical_filename_customer():
    extracted = {
        "invoice_type":   "customer",
        "customer_name":  "Rocky Mountain Nutraceuticals",
        "invoice_date":   "05-Jan-2026",
        "invoice_number": "CUST-0012",
    }
    name = build_canonical_filename(extracted)
    assert name.startswith("customer_")
    assert "Rocky-Mountain-Nutraceuticals" in name
    assert name.endswith(".pdf")


def test_canonical_filename_missing_fields():
    """build_canonical_filename() should not raise on missing fields."""
    name = build_canonical_filename({})
    assert name.endswith(".pdf")
    assert "unknown" in name


# ── RAG intent detection tests ─────────────────────────────────────────────────

from pipeline.rag_query import detect_intent


def test_detect_intent_supplier():
    assert detect_intent("who supplies shark cartilage powder?") == "supplier"
    assert detect_intent("what is the lead time from Jiaxing?")  == "supplier"
    assert detect_intent("which vendor sells us collagen?")       == "supplier"


def test_detect_intent_customer():
    assert detect_intent("which customers buy collagen from us?") == "customer"
    assert detect_intent("who are our buyers in Colorado?")       == "customer"


def test_detect_intent_all():
    assert detect_intent("what is the total invoice value?") == "all"


# ── PDF text extraction test (no API) ─────────────────────────────────────────

def test_extract_text_from_pdf():
    """extract_text() should return a non-empty string from a real PDF."""
    import glob
    from pipeline.pdf_extractor import extract_text

    pdfs = (
        glob.glob("data/invoices/*.pdf") +
        glob.glob("data/customer_invoices/*.pdf")
    )
    if not pdfs:
        pytest.skip("No PDFs found in data/ — skipping extraction test")

    text = extract_text(pdfs[0])
    assert isinstance(text, str)
    assert len(text) > 50
