"""
run_pipeline.py
Minimal multi-agent pipeline: Extraction → Inventory → Procurement
Based on system design (design.md) and day1.json scenario.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List

import uuid

import os

# PDF pipeline imports (only used when --pdf flag is passed)
try:
    from src.extraction.pdf_extractor import extract_all
    from src.extraction.normalizer import normalize_extracted_invoice
    _PDF_SUPPORT = True
except ImportError as e:
    # print("IMPORT ERROR:", e)
    _PDF_SUPPORT = False

from procurement_agent import ProcurementAgent

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(),                      # console
        logging.FileHandler("logs/day2.log", "w")     # file
    ]
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Structures (schema from design.md §5)
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    item_name_raw: str
    quantity: float
    unit_price: float


@dataclass
class Invoice:
    invoice_id: str
    document_type: str          # "purchase" or "sales"
    counterparty_name: str
    invoice_date: str
    total_amount: float
    line_items: List[LineItem]


# ---------------------------------------------------------------------------
# Product name normalization
# ---------------------------------------------------------------------------

_NON_PRODUCT_KEYWORDS = frozenset({
    "shipping", "freight", "tax", "insurance", "handling",
    "surcharge", "discount", "fee", "charge",
})

_TRAILING_QTY_RE = re.compile(
    r"\s+\d+(?:[.,]\d+)?\s*(?:kg|g|lbs?|oz|units?|pcs?|pieces?)\s*$",
    re.IGNORECASE,
)


def _normalize_product_name(name: str) -> str | None:
    """Normalize a line-item description to a clean product name.

    Strips trailing quantity/unit tokens such as '5 kg' or '20 kg' that LLMs
    sometimes embed in the description field.  Returns None for non-product
    charge lines (Shipping, Freight, Tax, etc.) which should not touch stock.
    """
    name = (name or "").strip()
    if not name or name.lower() == "unknown":
        return None
    lower = name.lower()
    for kw in _NON_PRODUCT_KEYWORDS:
        if kw in lower:
            return None
    name = _TRAILING_QTY_RE.sub("", name).strip()
    return name or None


def _resolve_inventory_key(normalized: str, inventory: dict) -> str:
    """Return the existing inventory key matching normalized (case-insensitive),
    or normalized itself when no existing key matches."""
    if normalized in inventory:
        return normalized
    lower = normalized.lower()
    for key in inventory:
        if key.lower() == lower:
            return key
    return normalized


@dataclass
class InventoryRecord:
    item_name: str
    current_stock: float
    reorder_point: float


@dataclass
class InventoryTransaction:
    item_name: str
    quantity_change: float      # positive = increase, negative = decrease
    transaction_type: str       # "purchase" or "sales"
    transaction_date: str


# ---------------------------------------------------------------------------
# Extraction Agent
# ---------------------------------------------------------------------------

def extraction_agent(raw_invoice: dict) -> Invoice:
    """
    Converts a raw invoice dict into a structured Invoice object.
    Responsibilities: parse fields, parse line items, determine document_type.
    """
    line_items = [
        LineItem(
            item_name_raw=li["item_name_raw"],
            quantity=li["quantity"],
            unit_price=li["unit_price"],
        )
        for li in raw_invoice["line_items"]
    ]
    return Invoice(
        invoice_id=raw_invoice["invoice_id"],
        document_type=raw_invoice["document_type"],
        counterparty_name=raw_invoice["counterparty_name"],
        invoice_date=raw_invoice["invoice_date"],
        total_amount=raw_invoice["total_amount"],
        line_items=line_items,
    )


# ---------------------------------------------------------------------------
# Inventory Agent
# ---------------------------------------------------------------------------

def inventory_agent(
    invoice: Invoice,
    inventory: dict,            # { item_name: InventoryRecord }
) -> List[InventoryTransaction]:
    """
    Updates inventory based on the invoice type.
    purchase → increase stock
    sales    → decrease stock
    Returns the list of transactions applied.
    """
    transactions = []

    for li in invoice.line_items:
        item_name = _normalize_product_name(li.item_name_raw)
        if item_name is None:
            logger.info("  Skipping non-product line item: %r", li.item_name_raw)
            continue
        item_name = _resolve_inventory_key(item_name, inventory)

        try:
            quantity = float(li.quantity)
        except (TypeError, ValueError):
            quantity = 0.0

        if invoice.document_type == "purchase":
            quantity_change = +quantity
        elif invoice.document_type == "sales":
            quantity_change = -quantity
        else:
            logger.warning("Unknown document_type %s; treating as purchase", invoice.document_type)
            quantity_change = +quantity

        if item_name not in inventory:
            logger.warning("[INVENTORY] Unknown product skipped / needs review: %s", item_name)
            continue

        inventory[item_name].current_stock += quantity_change

        transactions.append(InventoryTransaction(
            item_name=item_name,
            quantity_change=quantity_change,
            transaction_type=invoice.document_type,
            transaction_date=invoice.invoice_date,
        ))

    return transactions

# Deprecated: low stock logic moved to ProcurementAgent
def detect_low_stock(inventory: dict) -> List[str]:
    """Returns item names whose current_stock is below their reorder_point."""
    return [
        rec.item_name
        for rec in inventory.values()
        if rec.current_stock < rec.reorder_point
    ]


# ---------------------------------------------------------------------------
# Procurement Agent  (deprecated — replaced by ProcurementAgent class below)
# ---------------------------------------------------------------------------

# def procurement_agent(inventory: dict, low_stock_items: List[str]) -> List[str]:
#     """
#     DEPRECATED: use ProcurementAgent().generate_reorder_suggestions() instead.
#     Generates a simple reorder recommendation for each low-stock item.
#     """
#     recommendations = []
#     for item_name in low_stock_items:
#         rec = inventory[item_name]
#         recommendations.append(
#             f"REORDER RECOMMENDED — {item_name}: "
#             f"current stock {rec.current_stock} is below reorder point {rec.reorder_point}."
#         )
#     return recommendations


# ---------------------------------------------------------------------------
# Inventory DB sync
# ---------------------------------------------------------------------------

def sync_inventory_to_db(inventory: dict, db_path: str = "inventory.db") -> None:
    """
    Write the current in-memory inventory state to the active database (SQLite or Postgres).

    Uses the shared database abstraction from database.py so the same code works
    for both local SQLite (default) and Railway Postgres (when DATABASE_URL is set).
    The db_path parameter is kept for backward compatibility but is ignored when
    database.py's get_connection() is active.

    Maps InventoryRecord fields to the canonical stock table schema:
      item_name     -> product_name
      current_stock -> quantity_kg
      reorder_point -> reorder_at
    """
    from database import get_connection, _execute, init_db
    init_db()
    conn = get_connection()
    try:
        for rec in inventory.values():
            reorder_at = (
                rec.reorder_point
                if rec.reorder_point is not None and rec.reorder_point > 0
                else max(10, 0.2 * rec.current_stock)
            )
            _execute(
                conn,
                """
                INSERT INTO stock (product_name, quantity_kg, reorder_at)
                VALUES (?, ?, ?)
                ON CONFLICT (product_name) DO UPDATE SET
                    quantity_kg  = excluded.quantity_kg,
                    reorder_at   = excluded.reorder_at,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (rec.item_name, rec.current_stock, reorder_at),
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("sync_inventory_to_db -> wrote %d item(s) to database", len(inventory))


# ---------------------------------------------------------------------------
# Gmail invoice bridge
# ---------------------------------------------------------------------------

def _gmail_invoice_to_raw(inv: dict) -> dict:
    """Map extract_invoice_data() output → raw_invoice dict for extraction_agent()."""
    invoice_number = inv.get("invoice_number")

    if not invoice_number:
        invoice_number = f"unknown_{uuid.uuid4().hex[:6]}"
    return {
        "invoice_id": invoice_number,
        "invoice_number":    invoice_number,
        "document_type":     inv.get("document_type", "purchase"),
        "counterparty_name": inv.get("vendor_name", "unknown"),
        "invoice_date":      inv.get("date", ""),
        "total_amount":      inv.get("total_amount", 0),
        "line_items": [
            {
                "item_name_raw": li.get("description", "unknown"),
                "quantity":      li.get("quantity", 0),
                "unit_price":    li.get("unit_price", 0),
            }
            for li in inv.get("line_items", [])
        ],
    }


def load_inventory_from_stock_db(db_path: str = "inventory.db") -> list:
    """Load inventory from the active database (SQLite or Postgres via database.py)."""
    try:
        from database import get_connection, _execute
        conn = get_connection()
        rows = _execute(conn, "SELECT product_name, quantity_kg, reorder_at FROM stock").fetchall()
        conn.close()
        return [
            {"item_name": r["product_name"], "current_stock": r["quantity_kg"], "reorder_point": r["reorder_at"]}
            for r in rows
        ]
    except Exception:
        return []


def run_pipeline_from_gmail_invoices(extracted_invoices: list) -> None:
    """
    Entry point for `cli.py agent once`.
    Takes the list of invoice dicts returned by invoice_agent.extract_invoice_data()
    and runs them through the same inventory + procurement pipeline as `run pdf`.
    """
    scenario = {
        "scenario_id":       "gmail_import",
        "description":       f"Gmail invoices: {len(extracted_invoices)} email(s)",
        "initial_inventory": load_inventory_from_stock_db(),
        "invoices":          [_gmail_invoice_to_raw(inv) for inv in extracted_invoices],
    }
    run_pipeline(scenario)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def log_invoice_processing(invoice: Invoice, transactions: List[InventoryTransaction]):
    logger.info(f"\nProcessing {invoice.invoice_id} ({invoice.document_type})")
    for t in transactions:
        sign = "+" if t.quantity_change >= 0 else ""
        logger.info(f"  {t.item_name}  {sign}{t.quantity_change:g}")


def log_inventory_state(inventory: dict):
    logger.info("Current Inventory:")
    for rec in inventory.values():
        logger.info(f"  {rec.item_name}: {rec.current_stock:g}  (reorder point: {rec.reorder_point:g})")


def log_low_stock(low_stock_items: List[str]):
    if low_stock_items:
        logger.warning("\nLOW STOCK TRIGGERED:")
        for item in low_stock_items:
            logger.warning(f"  {item}")


def log_procurement(suggestions: list):
    if suggestions:
        logger.warning("\nPROCUREMENT RECOMMENDATIONS:")
        for s in suggestions:
            logger.warning(
                f"  {s['item_name']}"
                f"  |  stock: {s['current_stock']}"
                f"  |  reorder at: {s['reorder_point']}"
                f"  |  shortfall: {s['shortfall']}"
                f"  |  recommended: {s['recommended_qty']}"
            )
    else:
        logger.info("\nNo procurement action required.")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(scenario: dict):
    logger.info(f"=== Pipeline Start  |  Scenario: {scenario['scenario_id']} ===")
    logger.info(f"    {scenario['description']}\n")

    # --- Initialise inventory ---
    inventory: dict = {}
    for entry in scenario["initial_inventory"]:
        inventory[entry["item_name"]] = InventoryRecord(
            item_name=entry["item_name"],
            current_stock=entry["current_stock"],
            reorder_point=entry["reorder_point"],
        )

    logger.info("Initial Inventory:")
    for rec in inventory.values():
        logger.info(f"  {rec.item_name}: {rec.current_stock:g}  (reorder point: {rec.reorder_point:g})")

    # --- Process each invoice ---
    processed_invoice_ids = set()
    for raw_invoice in scenario["invoices"]:

        # 1. Extraction Agent
        invoice = extraction_agent(raw_invoice)

        # Deduplication
        invoice_key = invoice.invoice_id or "unknown"
        if invoice_key != "unknown":
            if invoice_key in processed_invoice_ids:
                logger.warning(f"Skipping duplicate invoice: {invoice_key}")
                continue
            processed_invoice_ids.add(invoice_key)

        # 2. Inventory Agent
        transactions = inventory_agent(invoice, inventory)

        # 3. Log invoice + updated state
        log_invoice_processing(invoice, transactions)
        log_inventory_state(inventory)

    # --- Sync in-memory inventory to DB so ProcurementAgent reads current state ---
    sync_inventory_to_db(inventory)

    # --- Procurement Agent (uses database.py abstraction — no hardcoded db paths) ---
    agent = ProcurementAgent()
    procurement_summary = agent.run()
    logger.info(
        "\nProcurement Agent complete — checked: %d | created: %d | skipped: %d",
        procurement_summary["total_items_checked"],
        procurement_summary["recommendations_created"],
        procurement_summary["skipped_duplicates"],
    )

    logger.info("\n=== Pipeline Complete ===")


# ---------------------------------------------------------------------------
# PDF loader  (bridges pdf_extractor + normalizer → existing pipeline shape)
# ---------------------------------------------------------------------------

def load_invoices_from_pdfs(pdf_dir: str) -> dict:
    """
    Extract and normalize every PDF in pdf_dir, then wrap the results
    in the same scenario dict shape that run_pipeline() already expects.

    The returned scenario has an empty initial_inventory; seed it manually
    if you need reorder-point logic, or patch this function later.
    """
    if not _PDF_SUPPORT:
        raise ImportError(
            "pdf_extractor.py / normalizer.py not found. "
            "Make sure both files are in the same directory as run_pipeline.py."
        )

    raw_invoices = extract_all(pdf_dir)                          # list[dict]
    normalized   = [normalize_extracted_invoice(r) for r in raw_invoices]

    return {
        "scenario_id":       f"pdf_import_{pdf_dir}",
        "description":       f"Invoices loaded from PDFs in: {pdf_dir}",
        # "initial_inventory": [],   # no seed data when running from PDFs
        "initial_inventory": [
        {"item_name": "Plant Extract - Echinacea", "current_stock": 50, "reorder_point": 200},
        {"item_name": "Hydrolyzed Marine Collagen", "current_stock": 50, "reorder_point": 300},
        ], # add initial inventory for checking
        "invoices":          normalized,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Run the invoice pipeline.")
    parser.add_argument(
        "scenario",
        nargs="?",
        default="day1.json",
        help="Path to a scenario JSON file (default: day1.json)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Load invoices from PDFs instead of a scenario JSON file.",
    )
    parser.add_argument(
        "--pdf-dir",
        default="invoices/",
        help="Folder containing PDF invoices (used with --pdf, default: invoices/)",
    )
    args = parser.parse_args()

    if args.pdf:
        # PDF mode
        scenario = load_invoices_from_pdfs(args.pdf_dir)
    else:
        # JSON mode (original behaviour, unchanged)
        with open(args.scenario, "r") as f:
            scenario = json.load(f)

    run_pipeline(scenario)