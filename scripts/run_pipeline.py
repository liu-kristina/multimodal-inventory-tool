"""
run_pipeline.py
Minimal multi-agent pipeline: Extraction → Inventory → Procurement
Based on system design (design.md) and day1.json scenario.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List

import os

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(),                      # console
        logging.FileHandler("logs/day1.log", "w")     # file
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
        item_name = li.item_name_raw

        # Initialise unknown items to zero stock with no reorder point
        if item_name not in inventory:
            inventory[item_name] = InventoryRecord(
                item_name=item_name,
                current_stock=0,
                reorder_point=0,
            )

        if invoice.document_type == "purchase":
            quantity_change = +li.quantity
        elif invoice.document_type == "sales":
            quantity_change = -li.quantity
        else:
            raise ValueError(f"Unknown document_type: {invoice.document_type}")

        inventory[item_name].current_stock += quantity_change

        transactions.append(InventoryTransaction(
            item_name=item_name,
            quantity_change=quantity_change,
            transaction_type=invoice.document_type,
            transaction_date=invoice.invoice_date,
        ))

    return transactions


def detect_low_stock(inventory: dict) -> List[str]:
    """Returns item names whose current_stock is below their reorder_point."""
    return [
        rec.item_name
        for rec in inventory.values()
        if rec.current_stock < rec.reorder_point
    ]


# ---------------------------------------------------------------------------
# Procurement Agent
# ---------------------------------------------------------------------------

def procurement_agent(inventory: dict, low_stock_items: List[str]) -> List[str]:
    """
    Generates a simple reorder recommendation for each low-stock item.
    Baseline logic: if stock < threshold → recommend reorder.
    """
    recommendations = []
    for item_name in low_stock_items:
        rec = inventory[item_name]
        recommendations.append(
            f"REORDER RECOMMENDED — {item_name}: "
            f"current stock {rec.current_stock} is below reorder point {rec.reorder_point}."
        )
    return recommendations


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


def log_procurement(recommendations: List[str]):
    if recommendations:
        logger.warning("\nPROCUREMENT RECOMMENDATIONS:")
        for r in recommendations:
            logger.warning(f"  {r}")
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
    for raw_invoice in scenario["invoices"]:

        # 1. Extraction Agent
        invoice = extraction_agent(raw_invoice)

        # 2. Inventory Agent
        transactions = inventory_agent(invoice, inventory)

        # 3. Log invoice + updated state
        log_invoice_processing(invoice, transactions)
        log_inventory_state(inventory)

    # --- Low-stock detection ---
    low_stock_items = detect_low_stock(inventory)
    log_low_stock(low_stock_items)

    # --- Procurement Agent ---
    recommendations = procurement_agent(inventory, low_stock_items)
    log_procurement(recommendations)

    logger.info("\n=== Pipeline Complete ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    scenario_path = sys.argv[1] if len(sys.argv) > 1 else "day1.json"

    with open(scenario_path, "r") as f:
        scenario = json.load(f)

    run_pipeline(scenario)