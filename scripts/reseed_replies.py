"""
reseed_replies.py — Reseed procurement drafts + replies using real suppliers from invoices.

Run from app root:
    python3 scripts/reseed_replies.py
"""

import sys, os, uuid, random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection, _execute

SUPPLIERS = [
    "Beijing Herbal Sciences Ltd.",
    "Zhejiang Green Botanicals Corp",
    "Shenzhen Phyto Ingredients Co.",
    "Pacific Rim BioMaterials Co.",
    "Guangzhou Natural Extracts Co.",
    "Jiaxing Natural Products Ltd",
    "Shanghai BioSupply International",
]

PRODUCTS = [
    "Collagen Powder",
    "Shark Cartilage Powder",
    "Fish Collagen Peptides",
    "Hydrolyzed Marine Collagen",
    "Bovine Gelatin Type A",
    "Bovine Cartilage Extract",
    "Hyaluronic Acid Powder",
    "Chondroitin Sulfate",
    "Glucosamine HCl",
    "Plant Extract - Turmeric",
    "Plant Extract - Ashwagandha",
    "Plant Extract - Elderberry",
]

# (action, reason, weight)
ACTIONS = [
    ("APPROVE",           None,                            10),
    ("APPROVE ANYWAY",    None,                             3),
    ("REJECT",            "Price too high",                 3),
    ("REJECT",            "Lead time unacceptable",         2),
    ("REJECT",            "Supplier not certified",         1),
    ("CHANGE",            "Reduce quantity by 20%",         3),
    ("CHANGE",            "Request lower MOQ",              2),
    ("PROVIDE NEW QUOTE", "Need updated pricing",           2),
    ("STOP PURCHASE",     "Found alternative supplier",     1),
]

def weighted_action():
    pool = []
    for action, reason, weight in ACTIONS:
        pool.extend([(action, reason)] * weight)
    return random.choice(pool)

def random_dt(days_back=90):
    return datetime.now() - timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )

def run():
    conn = get_connection()

    print("Clearing old replies only...")
    _execute(conn, "DELETE FROM procurement_replies")
    conn.commit()

    print("Creating 47 replies linked to new drafts...")
    for i in range(47):
        supplier = random.choice(SUPPLIERS)
        product  = random.choice(PRODUCTS)
        qty      = random.choice([100, 150, 200, 250, 300, 400, 500])
        price    = round(random.uniform(20, 120), 2)
        total    = round(qty * price, 2)
        draft_id = uuid.uuid4().hex[:8]
        created  = random_dt(90)
        sent     = created + timedelta(minutes=random.randint(5, 60))

        subject = f"Order Approval Required - {product} [RUN_ID={draft_id}]"
        body = (
            f"Hello,\n\n"
            f"Procurement recommendation for {product}:\n"
            f"  Supplier: {supplier}\n"
            f"  Quantity: {qty} kg\n"
            f"  Unit price: ${price}/kg\n"
            f"  Estimated total: ${total:,.2f}\n\n"
            f"Reply APPROVE or REJECT (with optional reason).\n\n"
            f"RUN_ID={draft_id}"
        )

        _execute(conn, """
            INSERT INTO procurement_email_drafts
                (id, product_name, supplier, subject, body, status, created_at, sent_at)
            VALUES (%s, %s, %s, %s, %s, 'sent', %s, %s)
        """, (draft_id, product, supplier, subject, body, created, sent))

        action, reason = weighted_action()
        replied_qty = (
            qty              if action in ("APPROVE", "APPROVE ANYWAY") else
            round(qty * 0.8) if action == "CHANGE" else
            None
        )
        replied_at = sent + timedelta(hours=random.randint(1, 48))

        _execute(conn, """
            INSERT INTO procurement_replies
                (draft_id, sender, subject, raw_body,
                 parsed_action, parsed_supplier, parsed_quantity, parsed_reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            draft_id,
            supplier,
            f"Re: {subject}",
            f"Action: {action}" + (f"\nReason: {reason}" if reason else ""),
            action,
            supplier,
            replied_qty,
            reason,
            replied_at,
        ))

    conn.commit()
    conn.close()
    print("Done — 47 new drafts and linked replies created. Existing data untouched.")

if __name__ == "__main__":
    run()