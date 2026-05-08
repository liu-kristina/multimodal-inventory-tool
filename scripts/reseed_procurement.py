"""
reseed_procurement.py

Seeds realistic procurement history for the Agent Control dashboard.
Uses real suppliers from invoices, per-supplier reliability weights,
and seeds recommendations + drafts + replies with proper status updates.

Safe to re-run — clears only procurement_replies and procurement_email_drafts.
Never touches invoices, stock, or any other table.

Run from app root:
    python3 scripts/reseed_procurement.py
"""

import os, sys, uuid, random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection, _execute

# ── Real suppliers from invoices table ────────────────────────────────────────

PRODUCTS = [
    ("Shark Cartilage Powder",      "Pacific Rim BioMaterials Co."),
    ("Fish Collagen Peptides",      "Jiaxing Natural Products Ltd"),
    ("Bovine Cartilage Extract",    "Shanghai BioSupply International"),
    ("Hydrolyzed Marine Collagen",  "Zhejiang Green Botanicals Corp"),
    ("Collagen Powder",             "Guangzhou Natural Extracts Co."),
    ("Hyaluronic Acid Powder",      "Beijing Herbal Sciences Ltd."),
    ("Chondroitin Sulfate",         "Shenzhen Phyto Ingredients Co."),
    ("Glucosamine HCl",             "Jiaxing Natural Products Ltd"),
    ("Plant Extract - Turmeric",    "Guangzhou Natural Extracts Co."),
    ("Plant Extract - Ashwagandha", "Beijing Herbal Sciences Ltd."),
    ("Plant Extract - Elderberry",  "Zhejiang Green Botanicals Corp"),
    ("Bovine Gelatin Type A",       "Shanghai BioSupply International"),
]

# Per-supplier weighted reliability — more realistic for demo
SUPPLIER_WEIGHTS = {
    "Jiaxing Natural Products Ltd":      ["APPROVE"] * 6 + ["CHANGE"],
    "Pacific Rim BioMaterials Co.":      ["APPROVE"] * 5 + ["CHANGE", "REJECT"],
    "Zhejiang Green Botanicals Corp":    ["APPROVE"] * 5 + ["CHANGE"] * 2,
    "Beijing Herbal Sciences Ltd.":      ["APPROVE"] * 4 + ["CHANGE"] * 2 + ["REJECT"],
    "Shanghai BioSupply International":  ["APPROVE"] * 4 + ["CHANGE", "REJECT"] * 2,
    "Guangzhou Natural Extracts Co.":    ["APPROVE"] * 3 + ["CHANGE"] * 2 + ["REJECT"] * 2,
    "Shenzhen Phyto Ingredients Co.":    ["APPROVE"] * 3 + ["CHANGE"] * 3 + ["REJECT"] * 2,
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
    "Need faster delivery option",
    "Adjust to 500 kg instead",
    "Use alternate supplier this time",
]

APPROVAL_USER = "procurement@californianutranews.com"


def random_dt(weeks_ago: int) -> datetime:
    base = datetime.now() - timedelta(weeks=weeks_ago)
    return base + timedelta(days=random.randint(0, 4), hours=random.randint(0, 23))


def run():
    conn = get_connection()

    print("Clearing procurement_replies and procurement_email_drafts...")
    _execute(conn, "DELETE FROM procurement_replies")
    _execute(conn, "DELETE FROM procurement_email_drafts WHERE id != 'glc01001'")
    conn.commit()

    inserted_drafts  = 0
    inserted_replies = 0

    print("Seeding 12 weeks of procurement history...")
    for weeks_ago in range(12, 0, -1):
        # 3–4 products per week
        week_products = random.sample(PRODUCTS, random.randint(3, 4))

        for product_name, supplier in week_products:
            created_at = random_dt(weeks_ago)
            sent_at    = created_at + timedelta(hours=random.randint(1, 8))
            replied_at = sent_at    + timedelta(hours=random.randint(2, 48))

            qty        = random.choice([200, 300, 500, 750, 1000])
            reorder_at = qty / 2
            urgency    = random.choice(["high", "medium"])
            draft_id   = uuid.uuid4().hex[:8]

            subject = f"Order Approval Required - {product_name} [RUN_ID={draft_id}]"
            body = (
                f"Hello,\n\n"
                f"Procurement recommendation for {product_name}:\n"
                f"  Supplier: {supplier}\n"
                f"  Quantity: {qty} kg\n"
                f"  Reorder threshold: {reorder_at} kg\n"
                f"  Urgency: {urgency}\n\n"
                f"Reply APPROVE or REJECT (with optional reason).\n\n"
                f"RUN_ID={draft_id}"
            )

            # ── Insert recommendation ──
            rec_row = _execute(conn, """
                INSERT INTO procurement_recommendations
                    (product_name, supplier, current_stock_kg, reorder_at_kg,
                     suggested_order_qty, urgency, reason, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'sent', %s, %s)
                RETURNING id
            """, (
                product_name, supplier,
                reorder_at, reorder_at, qty, urgency,
                "Stock below reorder threshold.",
                created_at, sent_at,
            )).fetchone()
            rec_id = rec_row["id"] if rec_row else None

            # ── Insert draft ──
            _execute(conn, """
                INSERT INTO procurement_email_drafts
                    (id, recommendation_id, product_name, supplier,
                     subject, body, status, created_at, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'sent', %s, %s)
            """, (draft_id, rec_id, product_name, supplier,
                  subject, body, created_at, sent_at))
            inserted_drafts += 1

            # ── Pick action based on supplier reliability ──
            weights = SUPPLIER_WEIGHTS.get(supplier, ["APPROVE", "APPROVE", "REJECT"])
            action  = random.choice(weights)

            parsed_reason   = None
            parsed_quantity = None

            if action == "REJECT":
                parsed_reason = random.choice(REJECTION_REASONS)
            elif action == "CHANGE":
                parsed_reason   = random.choice(CHANGE_REASONS)
                parsed_quantity = qty * random.choice([0.5, 0.75])

            raw_body = action
            if parsed_reason:
                raw_body += f"\nReason: {parsed_reason}"

            # ── Insert reply ──
            _execute(conn, """
                INSERT INTO procurement_replies
                    (draft_id, sender, subject, raw_body, parsed_action,
                     parsed_supplier, parsed_quantity, parsed_reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                draft_id, APPROVAL_USER,
                f"Re: {subject}",
                raw_body, action,
                supplier, parsed_quantity, parsed_reason,
                replied_at,
            ))
            inserted_replies += 1

            # ── Update statuses ──
            final_status = {
                "APPROVE": "approved",
                "REJECT":  "rejected",
                "CHANGE":  "discarded",
            }.get(action, "sent")

            _execute(conn,
                "UPDATE procurement_email_drafts SET status=%s WHERE id=%s",
                (final_status, draft_id))

            if rec_id:
                _execute(conn,
                    "UPDATE procurement_recommendations SET status=%s, updated_at=%s WHERE id=%s",
                    (final_status, replied_at, rec_id))

    conn.commit()
    conn.close()
    print(f"Done — {inserted_drafts} drafts and {inserted_replies} replies seeded.")
    print("Agent Control dashboard should now show full data.")


if __name__ == "__main__":
    run()
