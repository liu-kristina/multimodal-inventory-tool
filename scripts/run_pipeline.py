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
import hashlib

import os

# PDF pipeline imports (only used when --pdf flag is passed)
try:
    from src.extraction.pdf_extractor import extract_all
    from src.extraction.normalizer import normalize_extracted_invoice
    _PDF_SUPPORT = True
except ImportError as e:
    # print("IMPORT ERROR:", e)
    _PDF_SUPPORT = False

from agents.procurement_agent import ProcurementAgent

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

def _to_float(value) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _count_valid_line_items(inv: dict) -> int:
    """Count line items with a non-empty description and non-zero quantity."""
    count = 0
    for li in (inv.get("line_items") or []):
        desc = (li.get("description") or "").strip().lower()
        if desc and desc != "unknown" and _to_float(li.get("quantity")) != 0.0:
            count += 1
    return count


def _invoice_signature(inv: dict) -> str:
    """
    Compute a stable content-based dedup key for an invoice dict.

    Includes: document_type, vendor_name, total_amount, sorted line-item
    description+quantity pairs.  Deliberately excludes invoice_number so that
    an anonymous duplicate (invoice_number=None) and a named copy of the same
    invoice produce the same signature and are deduplicated.
    """
    doc_type = (inv.get("document_type") or "").strip().lower()
    vendor = re.sub(r"\s+", " ", (inv.get("vendor_name") or "").strip().lower())
    amount = f"{_to_float(inv.get('total_amount')):.4f}"
    items = inv.get("line_items") or []
    _ws = re.compile(r"\s+")
    item_tokens = sorted(
        _ws.sub(" ", (li.get("description") or "").strip().lower())
        + f":{_to_float(li.get('quantity')):.4f}"
        for li in items
        if (li.get("description") or "").strip().lower() not in ("", "unknown")
    )
    payload = json.dumps(
        {"doc_type": doc_type, "vendor": vendor, "amount": amount, "items": item_tokens},
        sort_keys=True,
    )
    return "sig_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _dedup_invoice_batch(invoices: list) -> list:
    """
    Remove duplicates from a single-email extraction result.

    Matching priority:
      1. Same invoice_number → keep the candidate with MORE valid line_items.
         When counts are equal, keep the existing one (stable ordering).
      2. Same full content signature → same completeness rule; additionally,
         if the incoming candidate has an invoice_number and the existing one
         does not, copy invoice_number onto the better entry.

    Genuinely different invoices — different invoice_number AND different
    signature — are both retained regardless of completeness.
    """
    seen_nums: dict[str, int] = {}   # inv_num  -> result index
    seen_sigs: dict[str, int] = {}   # sig      -> result index
    result: list = []

    for inv in invoices:
        inv_num = (inv.get("invoice_number") or "").strip()
        sig = _invoice_signature(inv)
        incoming_count = _count_valid_line_items(inv)

        # --- Duplicate by invoice_number ---
        if inv_num and inv_num in seen_nums:
            existing_idx = seen_nums[inv_num]
            existing = result[existing_idx]
            existing_count = _count_valid_line_items(existing)
            if incoming_count > existing_count:
                old_sig = _invoice_signature(existing)
                result[existing_idx] = inv
                if old_sig in seen_sigs and seen_sigs[old_sig] == existing_idx:
                    del seen_sigs[old_sig]
                seen_sigs[sig] = existing_idx
                logger.info(
                    "[DEDUP] invoice_number=%r upgraded: %d -> %d valid line item(s)",
                    inv_num, existing_count, incoming_count,
                )
            else:
                logger.info(
                    "[DEDUP] invoice_number=%r skipped (existing has %d >= %d line items)",
                    inv_num, existing_count, incoming_count,
                )
            continue

        # --- Duplicate by content signature ---
        if sig in seen_sigs:
            existing_idx = seen_sigs[sig]
            existing = result[existing_idx]
            existing_count = _count_valid_line_items(existing)
            existing_num = (existing.get("invoice_number") or "").strip()

            if incoming_count > existing_count:
                # Keep incoming's line items; use whichever invoice_number is available
                effective_num = inv_num or existing_num
                new_inv = dict(inv)
                if effective_num and not inv_num:
                    new_inv["invoice_number"] = effective_num
                result[existing_idx] = new_inv
                if effective_num:
                    seen_nums[effective_num] = existing_idx
                logger.info(
                    "[DEDUP] sig match upgraded: %d -> %d valid line item(s), "
                    "invoice_number=%r",
                    existing_count, incoming_count, effective_num or None,
                )
            elif inv_num and not existing_num:
                # Equal or fewer items but incoming has invoice_number — copy it
                new_existing = dict(existing)
                new_existing["invoice_number"] = inv_num
                result[existing_idx] = new_existing
                seen_nums[inv_num] = existing_idx
                logger.info("[DEDUP] sig match: copied invoice_number=%r onto existing", inv_num)
            else:
                logger.info(
                    "[DEDUP] sig match skipped (existing has %d >= %d line items)",
                    existing_count, incoming_count,
                )
            continue

        # --- New unique invoice ---
        idx = len(result)
        result.append(inv)
        seen_sigs[sig] = idx
        if inv_num:
            seen_nums[inv_num] = idx

    return result


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
    Entry point for `cli.py agent once` and the label-routing invoice path.
    Takes the list of invoice dicts returned by invoice_agent.extract_invoice_data()
    and runs them through the same inventory + procurement pipeline as `run pdf`.

    Belt-and-suspenders dedup (Layer 3): filter out invoices already applied.
    Layers 1–2 live in email_router.run_label_routing (RFC Message-ID pre-check
    + post-mark-read).

    Within-batch: delegates to _dedup_invoice_batch() which picks the most
    complete candidate (most valid line_items) when multiple dicts share the
    same invoice_number or content signature.

    Cross-batch: skips any invoice whose invoice_number (label "inv_applied")
    or content signature (label "inv_sig_applied") was already applied in a
    prior call.
    """
    from database import is_message_processed, mark_message_processed

    # Within-batch dedup — pick best candidate per unique invoice
    batch = _dedup_invoice_batch(extracted_invoices)
    if len(batch) < len(extracted_invoices):
        logger.info(
            "[PIPELINE] Within-batch dedup: %d -> %d invoice(s)",
            len(extracted_invoices), len(batch),
        )

    # Cross-batch dedup — skip already-applied invoices
    deduped = []
    for inv in batch:
        inv_num = str(inv.get("invoice_number") or "").strip()
        sig = _invoice_signature(inv)

        if inv_num and is_message_processed(inv_num, label="inv_applied"):
            logger.info("[PIPELINE] Invoice %r already applied to inventory — skipping", inv_num)
            continue
        if is_message_processed(sig, label="inv_sig_applied"):
            logger.info("[PIPELINE] Invoice sig %r already applied — skipping", sig)
            continue

        deduped.append(inv)

    if not deduped:
        return

    # Debug: log selected candidate details
    for inv in deduped:
        logger.info(
            "[PIPELINE] Applying invoice_number=%r | %d valid line item(s)",
            inv.get("invoice_number") or None,
            _count_valid_line_items(inv),
        )

    scenario = {
        "scenario_id":       "gmail_import",
        "description":       f"Gmail invoices: {len(deduped)} email(s)",
        "initial_inventory": load_inventory_from_stock_db(),
        "invoices":          [_gmail_invoice_to_raw(inv) for inv in deduped],
    }
    run_pipeline(scenario)

    # Mark each applied invoice idempotent via both invoice_number and content sig
    for inv in deduped:
        inv_num = str(inv.get("invoice_number") or "").strip()
        sig = _invoice_signature(inv)
        if inv_num:
            mark_message_processed(inv_num, label="inv_applied")
        mark_message_processed(sig, label="inv_sig_applied")


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