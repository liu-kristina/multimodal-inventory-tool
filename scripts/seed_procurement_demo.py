"""
seed_procurement_demo.py

Seeds realistic procurement history data for the Agent Control quality dashboard.
Inserts 12 weeks of procurement recommendations, drafts, and replies.

Run once after deployment:
    python seed_procurement_demo.py

Safe to re-run — checks for existing data first.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta
import random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection, _execute, _use_postgres, init_db

# ── Demo data ──────────────────────────────────────────────────────────────────

PRODUCTS = [
    ("Shark Cartilage Powder",    "Pacific Rim BioMaterials Co.",      "jchen@pacificrimbiomaterials.com"),
    ("Collagen Peptides Type I",  "Jiaxing Natural Products Ltd",      "lwang@jiaxingnatural.com"),
    ("Fish Collagen Peptides",    "Jiaxing Natural Products Ltd",      "lwang@jiaxingnatural.com"),
    ("Bovine Cartilage Extract",  "Shanghai BioSupply International",  "mzhou@shibiosupply.com"),
    ("Hydrolyzed Marine Collagen","Zhejiang Green Botanicals Corp",    "sliu@zjgreenbotanicals.com"),
    ("Collagen Powder",           "Guangzhou Nutra Raw Materials Inc", "dliang@gznutraraw.com"),
]

APPROVAL_USER = "yingh16@uci.edu"

# Weighted actions per supplier — some suppliers are more reliable than others
SUPPLIER_WEIGHTS = {
    "Pacific Rim BioMaterials Co.":      ["APPROVE", "APPROVE", "APPROVE", "APPROVE", "CHANGE", "REJECT"],
    "Jiaxing Natural Products Ltd":      ["APPROVE", "APPROVE", "APPROVE", "APPROVE", "APPROVE", "CHANGE"],
    "Shanghai BioSupply International":  ["APPROVE", "APPROVE", "APPROVE", "CHANGE", "REJECT", "REJECT"],
    "Zhejiang Green Botanicals Corp":    ["APPROVE", "APPROVE", "APPROVE", "APPROVE", "CHANGE", "CHANGE"],
    "Guangzhou Nutra Raw Materials Inc": ["APPROVE", "APPROVE", "CHANGE", "CHANGE", "REJECT", "REJECT"],
}

REJECTION_REASONS = [
    "Price too high",
    "Lead time too long",
    "Minimum order quantity too large",
    "Quality concerns from last shipment",
    "Found better pricing elsewhere",
    "Stock situation changed",
]

CHANGE_REASONS = [
    "Reduce quantity by half",
    "Use alternate supplier this time",
    "Need faster delivery option",
    "Adjust to 500 kg instead",
]


def _random_date(weeks_ago: int) -> datetime:
    base = datetime.now() - timedelta(weeks=weeks_ago)
    jitter = timedelta(days=random.randint(0, 4))
    return base + jitter


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def seed(conn):
    # Check if already seeded
    existing = _execute(conn, "SELECT COUNT(*) AS cnt FROM procurement_replies").fetchone()
    count = existing["cnt"] if existing else 0
    if count > 20:
        print(f"Already have {count} procurement replies — skipping seed.")
        return

    print("Seeding procurement demo data...")
    inserted_drafts = 0
    inserted_replies = 0

    for weeks_ago in range(12, 0, -1):
        # Pick 3-4 products to procure this week
        n_products = random.randint(3, 4)
        week_products = random.sample(PRODUCTS, n_products)

        for product_name, supplier, supplier_email in week_products:
            created_at = _random_date(weeks_ago)
            sent_at    = created_at + timedelta(hours=random.randint(1, 8))
            replied_at = sent_at + timedelta(hours=random.randint(2, 48))

            qty        = random.choice([200, 300, 500, 750, 1000])
            reorder_at = qty / 2
            urgency    = random.choice(["high", "medium"])
            subject    = f"Reorder Suggestion - {product_name}"
            body       = (
                f"Hello {supplier},\n\n"
                f"We would like to place a reorder for {product_name}.\n"
                f"Our current stock is {reorder_at} kg, with a reorder threshold of {reorder_at} kg.\n"
                f"Please confirm availability, lead time, and pricing for {qty} kg.\n\n"
                f"Best regards,\nCalifornia Nutraceuticals"
            )

            # Insert recommendation
            if _use_postgres:
                rec_row = _execute(conn, """
                    INSERT INTO procurement_recommendations
                        (product_name, supplier, supplier_email, current_stock_kg,
                         reorder_at_kg, suggested_order_qty, urgency, reason, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?)
                    RETURNING id
                """, (
                    product_name, supplier, supplier_email,
                    reorder_at, reorder_at, qty, urgency,
                    f"Stock below reorder threshold.",
                    _format_dt(created_at), _format_dt(sent_at),
                )).fetchone()
                rec_id = rec_row["id"] if rec_row else None
            else:
                cur = _execute(conn, """
                    INSERT INTO procurement_recommendations
                        (product_name, supplier, supplier_email, current_stock_kg,
                         reorder_at_kg, suggested_order_qty, urgency, reason, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?)
                """, (
                    product_name, supplier, supplier_email,
                    reorder_at, reorder_at, qty, urgency,
                    f"Stock below reorder threshold.",
                    _format_dt(created_at), _format_dt(sent_at),
                ))
                rec_id = cur.lastrowid

            # Insert draft
            draft_id = str(uuid.uuid4())[:8]
            _execute(conn, """
                INSERT INTO procurement_email_drafts
                    (id, recommendation_id, product_name, supplier, supplier_email,
                     subject, body, status, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?)
            """, (
                draft_id, rec_id, product_name, supplier, supplier_email,
                subject, body, _format_dt(created_at), _format_dt(sent_at),
            ))
            inserted_drafts += 1

            # Pick action based on supplier reliability
            weights = SUPPLIER_WEIGHTS.get(supplier, ["APPROVE", "APPROVE", "REJECT"])
            action = random.choice(weights)

            parsed_reason   = None
            parsed_supplier = None
            parsed_quantity = None

            if action == "REJECT":
                parsed_reason = random.choice(REJECTION_REASONS)
            elif action == "CHANGE":
                parsed_reason   = random.choice(CHANGE_REASONS)
                parsed_quantity = qty * random.choice([0.5, 0.75])

            raw_body = action
            if parsed_reason:
                raw_body += f"\nReason: {parsed_reason}"
            if parsed_supplier:
                raw_body += f"\nSupplier: {parsed_supplier}"

            _execute(conn, """
                INSERT INTO procurement_replies
                    (draft_id, sender, subject, raw_body, parsed_action,
                     parsed_supplier, parsed_quantity, parsed_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                draft_id, APPROVAL_USER,
                f"Re: {subject}",
                raw_body, action,
                parsed_supplier, parsed_quantity, parsed_reason,
                _format_dt(replied_at),
            ))
            inserted_replies += 1

            # Update draft and recommendation status
            final_status = {
                "APPROVE": "approved",
                "REJECT":  "rejected",
                "CHANGE":  "discarded",
            }.get(action, "sent")

            _execute(conn,
                "UPDATE procurement_email_drafts SET status=? WHERE id=?",
                (final_status, draft_id))
            _execute(conn,
                "UPDATE procurement_recommendations SET status=?, updated_at=? WHERE id=?",
                (final_status, _format_dt(replied_at), rec_id))

    conn.commit()
    print(f"Done. Inserted {inserted_drafts} drafts and {inserted_replies} replies.")
    print("The Agent Control quality dashboard should now have data.")


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    try:
        seed(conn)
    finally:
        conn.close()

