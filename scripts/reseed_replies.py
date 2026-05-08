"""
reseed_replies.py
Clears mismatched procurement data and reseeds with realistic
drafts + replies properly linked together.

Run once on Railway:
    python3 reseed_replies.py
"""

import sys, os, uuid, random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from database import get_connection, _execute

SUPPLIERS = [
    {"name": "Jiaxing Natural Products Ltd",   "email": "orders@jiaxing-natural.com"},
    {"name": "Guangzhou Nutra Raw Materials Inc", "email": "sales@guangzhou-nutra.com"},
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

ACTIONS = [
    ("APPROVE",            None,                         8),
    ("APPROVE ANYWAY",     None,                         4),
    ("REJECT",             "Price too high",             3),
    ("REJECT",             "Lead time unacceptable",     2),
    ("REJECT",             "Supplier not certified",     2),
    ("CHANGE",             "Reduce quantity by 20%",     3),
    ("CHANGE",             "Request lower MOQ",          2),
    ("PROVIDE NEW QUOTE",  "Need updated pricing",       2),
    ("STOP PURCHASE",      "Found alternative supplier", 1),
]

def weighted_action():
    pool = []
    for action, reason, weight in ACTIONS:
        pool.extend([(action, reason)] * weight)
    return random.choice(pool)

def random_date(days_back=90):
    delta = random.randint(0, days_back)
    dt = datetime.now() - timedelta(days=delta)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def run():
    conn = get_connection()

    print("Clearing old mismatched data...")
    _execute(conn, "DELETE FROM procurement_replies")
    _execute(conn, "DELETE FROM procurement_email_drafts WHERE id != 'glc01001'")
    conn.commit()

    print("Creating drafts and replies...")
    count = 0
    for i in range(47):
        supplier = random.choice(SUPPLIERS)
        product  = random.choice(PRODUCTS)
        qty      = random.choice([100, 150, 200, 250, 300, 400, 500])
        draft_id = str(uuid.uuid4())[:8]
        created  = random_date(90)
        sent     = created

        # Insert draft
        _execute(conn, """
            INSERT INTO procurement_email_drafts
                (id, product_name, supplier, supplier_email, status, created_at, sent_at)
            VALUES (?, ?, ?, ?, 'sent', ?, ?)
        """, (draft_id, product, supplier["name"], supplier["email"], created, sent))

        # Insert reply
        action, reason = weighted_action()
        replied_qty = qty if action in ("APPROVE", "APPROVE ANYWAY") else (
            round(qty * 0.8) if action == "CHANGE" else None
        )
        replied_at = (
            datetime.strptime(created, "%Y-%m-%d %H:%M:%S") + timedelta(hours=random.randint(1, 48))
        ).strftime("%Y-%m-%d %H:%M:%S")

        _execute(conn, """
            INSERT INTO procurement_replies
                (draft_id, sender, subject, raw_body,
                 parsed_action, parsed_supplier, parsed_quantity, parsed_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            draft_id,
            supplier["email"],
            f"Re: Reorder request for {product}",
            f"Action: {action}",
            action,
            supplier["name"],
            replied_qty,
            reason,
            replied_at,
        ))
        count += 1

    conn.commit()
    conn.close()
    print(f"Done — {count} drafts and replies created.")

if __name__ == "__main__":
    run()
