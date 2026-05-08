"""
database.py — Database abstraction layer
Supports both Postgres (production/Railway) and SQLite (local dev/testing).

Postgres is used when DATABASE_URL is set in the environment.
SQLite is used as fallback for local development and CI.

Postgres:  set DATABASE_URL=postgresql://user:pass@host:5432/dbname
SQLite:    set DB_PATH=/path/to/inventory.db  (default: /app/data/inventory.db)
"""

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH      = os.environ.get("DB_PATH", str(Path(__file__).parent / "inventory.db"))

_use_postgres = bool(DATABASE_URL)

if _use_postgres:
    import psycopg2
    import psycopg2.extras
    print(f"[DB] Using PostgreSQL via DATABASE_URL")
else:
    print(f"[DB] Using SQLite: {DB_PATH}")


# ── Connection ─────────────────────────────────────────────────────────────────

def get_connection():
    if _use_postgres:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def _execute(conn, sql: str, params=None):
    """Execute a statement — handles both Postgres (%s) and SQLite (?) placeholders.

    When params is None, execute() is called without arguments so psycopg2 does
    not attempt to interpret % characters in the SQL (e.g. LIKE 'ELT%' patterns).
    """
    if _use_postgres:
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, params)
    return cur


def _executemany(conn, sql: str, params_list):
    if _use_postgres:
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.executemany(sql, params_list)
    return cur


def _insert_id(conn, sql: str, params) -> int:
    """Execute an INSERT and return the new row id for both Postgres and SQLite.

    Postgres does not populate cursor.lastrowid; use RETURNING id instead.
    """
    if _use_postgres:
        sql = sql.replace("?", "%s")
        cur = conn.cursor()
        cur.execute(sql + " RETURNING id", params)
        return cur.fetchone()["id"]
    else:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.lastrowid


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_connection()
    cur  = conn.cursor()

    if _use_postgres:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id           SERIAL PRIMARY KEY,
                product_name TEXT NOT NULL UNIQUE,
                supplier     TEXT,
                quantity_kg  REAL DEFAULT 0,
                reorder_at   REAL DEFAULT 0,
                unit         TEXT DEFAULT 'kg',
                unit_price   REAL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id                SERIAL PRIMARY KEY,
                invoice_number    TEXT NOT NULL UNIQUE,
                invoice_type      TEXT NOT NULL,
                counterparty_name TEXT,
                invoice_date      TEXT,
                port_of_loading   TEXT,
                shipment_date     TEXT,
                expected_delivery TEXT,
                lead_time         TEXT,
                transit_time      TEXT,
                total_amount      REAL DEFAULT 0,
                filename          TEXT,
                embedded          INTEGER DEFAULT 0,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS invoice_line_items (
                id             SERIAL PRIMARY KEY,
                invoice_number TEXT NOT NULL,
                product_name   TEXT,
                quantity_kg    REAL DEFAULT 0,
                unit_price     REAL DEFAULT 0,
                line_total     REAL DEFAULT 0,
                FOREIGN KEY (invoice_number) REFERENCES invoices(invoice_number)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id           SERIAL PRIMARY KEY,
                product_name TEXT NOT NULL UNIQUE,
                active       INTEGER DEFAULT 1,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_products (
                id           SERIAL PRIMARY KEY,
                product_name TEXT NOT NULL UNIQUE,
                status       TEXT DEFAULT 'pending',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_state (
                id          INTEGER PRIMARY KEY,
                active      INTEGER DEFAULT 0,
                last_run    TEXT,
                last_status TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_log (
                id         SERIAL PRIMARY KEY,
                message    TEXT,
                status     TEXT DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_flags (
                id         SERIAL PRIMARY KEY,
                reason     TEXT,
                details    TEXT,
                resolved   INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS procurement_recommendations (
                id                  SERIAL PRIMARY KEY,
                product_name        TEXT NOT NULL,
                supplier            TEXT,
                supplier_email      TEXT,
                current_stock_kg    REAL NOT NULL,
                reorder_at_kg       REAL NOT NULL,
                suggested_order_qty REAL NOT NULL,
                urgency             TEXT,
                reason              TEXT,
                status              TEXT DEFAULT 'pending_approval',
                parent_rec_id       INTEGER,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS procurement_email_drafts (
                id                TEXT PRIMARY KEY,
                recommendation_id INTEGER REFERENCES procurement_recommendations(id),
                product_name      TEXT NOT NULL,
                supplier          TEXT,
                supplier_email    TEXT,
                subject           TEXT NOT NULL,
                body              TEXT NOT NULL,
                status            TEXT DEFAULT 'draft',
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at           TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS procurement_replies (
                id              SERIAL PRIMARY KEY,
                draft_id        TEXT,
                sender          TEXT,
                subject         TEXT,
                raw_body        TEXT,
                parsed_action   TEXT,
                parsed_supplier TEXT,
                parsed_quantity REAL,
                parsed_reason   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                id         SERIAL PRIMARY KEY,
                message_id TEXT NOT NULL,
                label      TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (message_id, label)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS procurement_memory (
                id                     SERIAL PRIMARY KEY,
                product_name           TEXT NOT NULL,
                supplier               TEXT NOT NULL,
                supplier_email         TEXT,
                unit_price             REAL,
                lead_time              TEXT,
                shipping_cost          REAL,
                estimated_total_cost   REAL,
                availability           TEXT,
                recommendation_id      INTEGER,
                recommendation_action  TEXT,
                user_action            TEXT,
                user_reason            TEXT,
                outcome_status         TEXT,
                run_id                 TEXT,
                created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_supplier_alternates (
                id             SERIAL PRIMARY KEY,
                product_name   TEXT NOT NULL,
                supplier       TEXT NOT NULL,
                supplier_email TEXT,
                priority       INTEGER DEFAULT 1,
                UNIQUE(product_name, supplier)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS procurement_memory_notes (
                id           SERIAL PRIMARY KEY,
                supplier     TEXT NOT NULL,
                product_name TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                note         TEXT NOT NULL,
                impact       TEXT,
                embedding    TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                supplier     TEXT,
                quantity_kg  REAL DEFAULT 0,
                reorder_at   REAL DEFAULT 0,
                unit         TEXT DEFAULT 'kg',
                unit_price   REAL,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number    TEXT NOT NULL UNIQUE,
                invoice_type      TEXT NOT NULL,
                counterparty_name TEXT,
                invoice_date      TEXT,
                port_of_loading   TEXT,
                shipment_date     TEXT,
                expected_delivery TEXT,
                lead_time         TEXT,
                transit_time      TEXT,
                total_amount      REAL DEFAULT 0,
                filename          TEXT,
                embedded          INTEGER DEFAULT 0,
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_line_items (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                product_name   TEXT,
                quantity_kg    REAL DEFAULT 0,
                unit_price     REAL DEFAULT 0,
                line_total     REAL DEFAULT 0,
                FOREIGN KEY (invoice_number) REFERENCES invoices(invoice_number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                active       INTEGER DEFAULT 1,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                status       TEXT DEFAULT 'pending',
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procurement_recommendations (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name        TEXT NOT NULL,
                supplier            TEXT,
                supplier_email      TEXT,
                current_stock_kg    REAL NOT NULL,
                reorder_at_kg       REAL NOT NULL,
                suggested_order_qty REAL NOT NULL,
                urgency             TEXT,
                reason              TEXT,
                status              TEXT DEFAULT 'pending_approval',
                parent_rec_id       INTEGER,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procurement_email_drafts (
                id                TEXT PRIMARY KEY,
                recommendation_id INTEGER,
                product_name      TEXT NOT NULL,
                supplier          TEXT,
                supplier_email    TEXT,
                subject           TEXT NOT NULL,
                body              TEXT NOT NULL,
                status            TEXT DEFAULT 'draft',
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
                sent_at           TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procurement_replies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id        TEXT,
                sender          TEXT,
                subject         TEXT,
                raw_body        TEXT,
                parsed_action   TEXT,
                parsed_supplier TEXT,
                parsed_quantity REAL,
                parsed_reason   TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                label      TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (message_id, label)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procurement_memory (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name           TEXT NOT NULL,
                supplier               TEXT NOT NULL,
                supplier_email         TEXT,
                unit_price             REAL,
                lead_time              TEXT,
                shipping_cost          REAL,
                estimated_total_cost   REAL,
                availability           TEXT,
                recommendation_id      INTEGER,
                recommendation_action  TEXT,
                user_action            TEXT,
                user_reason            TEXT,
                outcome_status         TEXT,
                run_id                 TEXT,
                created_at             TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_supplier_alternates (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name   TEXT NOT NULL,
                supplier       TEXT NOT NULL,
                supplier_email TEXT,
                priority       INTEGER DEFAULT 1,
                UNIQUE(product_name, supplier)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_state (
                id          INTEGER PRIMARY KEY,
                active      INTEGER DEFAULT 0,
                last_run    TEXT,
                last_status TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message    TEXT,
                status     TEXT DEFAULT 'ok',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_flags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                reason     TEXT,
                details    TEXT,
                resolved   INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procurement_memory_notes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier     TEXT NOT NULL,
                product_name TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                note         TEXT NOT NULL,
                impact       TEXT,
                embedding    TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # Lightweight migrations for columns added after the initial schema.
    #
    # Postgres: each ALTER TABLE is wrapped in a SAVEPOINT so a "column already
    # exists" error rolls back only that single statement, not the entire
    # transaction (psycopg2 aborts the whole tx on any error otherwise).
    # SQLite: a failed statement does not abort the connection, so a plain
    # try/except is sufficient.
    _invoice_cols = [
        ("port_of_loading", "TEXT"),
        ("shipment_date",   "TEXT"),
        ("expected_delivery", "TEXT"),
        ("lead_time",       "TEXT"),
        ("transit_time",    "TEXT"),
    ]
    _pm_cols = [
        ("availability",      "TEXT"),
        ("recommendation_id", "INTEGER"),
    ]
    _rec_cols = [
        ("parent_rec_id", "INTEGER"),
    ]

    if _use_postgres:
        for col_name, col_type in _invoice_cols:
            try:
                cur.execute(f"SAVEPOINT mig_inv_{col_name}")
                cur.execute(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}")
                cur.execute(f"RELEASE SAVEPOINT mig_inv_{col_name}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT mig_inv_{col_name}")
        for col_name, col_type in _pm_cols:
            try:
                cur.execute(f"SAVEPOINT mig_pm_{col_name}")
                cur.execute(f"ALTER TABLE procurement_memory ADD COLUMN {col_name} {col_type}")
                cur.execute(f"RELEASE SAVEPOINT mig_pm_{col_name}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT mig_pm_{col_name}")
        for col_name, col_type in _rec_cols:
            try:
                cur.execute(f"SAVEPOINT mig_rec_{col_name}")
                cur.execute(f"ALTER TABLE procurement_recommendations ADD COLUMN {col_name} {col_type}")
                cur.execute(f"RELEASE SAVEPOINT mig_rec_{col_name}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT mig_rec_{col_name}")
    else:
        for col_name, col_type in _invoice_cols:
            try:
                conn.execute(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass
        for col_name, col_type in _pm_cols:
            try:
                conn.execute(f"ALTER TABLE procurement_memory ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass
        for col_name, col_type in _rec_cols:
            try:
                conn.execute(f"ALTER TABLE procurement_recommendations ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass

    conn.commit()
    conn.close()


# ── Invoice operations ─────────────────────────────────────────────────────────

def save_invoice(extracted: dict):
    """Save an extracted invoice to the database. Skips duplicates."""
    conn = get_connection()

    invoice_type   = extracted.get("invoice_type", "unknown")
    counterparty   = extracted.get("supplier_name") or extracted.get("customer_name") or ""
    invoice_number = extracted.get("invoice_number")
    if not invoice_number:
        invoice_number = f"unknown_{hash(str(extracted)) % 10000}"

    try:
        if _use_postgres:
            _execute(conn, """
                INSERT INTO invoices
                    (invoice_number, invoice_type, counterparty_name, invoice_date,
                     port_of_loading, shipment_date, expected_delivery, lead_time, transit_time,
                     total_amount, filename)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (invoice_number) DO NOTHING
            """, (
                invoice_number, invoice_type, counterparty,
                extracted.get("invoice_date", ""),
                extracted.get("port_of_loading", ""),
                extracted.get("shipment_date", ""),
                extracted.get("expected_delivery", ""),
                extracted.get("lead_time", ""),
                extracted.get("transit_time", ""),
                float(extracted.get("grand_total", 0)),
                extracted.get("filename", ""),
            ))
        else:
            _execute(conn, """
                INSERT OR IGNORE INTO invoices
                    (invoice_number, invoice_type, counterparty_name, invoice_date,
                     port_of_loading, shipment_date, expected_delivery, lead_time, transit_time,
                     total_amount, filename)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_number, invoice_type, counterparty,
                extracted.get("invoice_date", ""),
                extracted.get("port_of_loading", ""),
                extracted.get("shipment_date", ""),
                extracted.get("expected_delivery", ""),
                extracted.get("lead_time", ""),
                extracted.get("transit_time", ""),
                float(extracted.get("grand_total", 0)),
                extracted.get("filename", ""),
            ))

        for item in extracted.get("line_items", []):
            _execute(conn, """
                INSERT INTO invoice_line_items
                    (invoice_number, product_name, quantity_kg, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?)
            """, (
                invoice_number,
                item.get("product", ""),
                float(item.get("quantity", 0)),
                float(item.get("unit_price", 0)),
                float(item.get("total", 0)),
            ))

        conn.commit()
    finally:
        conn.close()


def get_unembedded_invoices() -> list:
    """Return invoice numbers not yet embedded into ChromaDB."""
    conn = get_connection()
    cur  = _execute(conn, "SELECT invoice_number FROM invoices WHERE embedded = 0")
    rows = cur.fetchall()
    conn.close()
    if _use_postgres:
        return [row["invoice_number"] for row in rows]
    return [row["invoice_number"] for row in rows]


def mark_embedded(invoice_number: str):
    """Mark an invoice as embedded in ChromaDB."""
    conn = get_connection()
    _execute(conn, "UPDATE invoices SET embedded = 1 WHERE invoice_number = ?", (invoice_number,))
    conn.commit()
    conn.close()


# ── Stock operations ───────────────────────────────────────────────────────────

def seed_initial_inventory():
    """Insert starting inventory. Safe to run multiple times."""
    conn = get_connection()
    rows = [
        ("Collagen Powder",             "Jiaxing Supplier", 450,  100, "kg", 12.50),
        ("Shark Cartilage Powder",      "Jiaxing Supplier", 80,   150, "kg", 18.00),
        ("Fish Collagen Peptides",      "Jiaxing Supplier", 210,  100, "kg", 15.00),
        ("Hydrolyzed Marine Collagen",  "Jiaxing Supplier", 55,   100, "kg", 22.00),
        ("Bovine Gelatin Type A",       "Jiaxing Supplier", 320,  80,  "kg", 10.00),
        ("Bovine Cartilage Extract",    "Jiaxing Supplier", 40,   80,  "kg", 20.00),
        ("Plant Extract - Turmeric",    "Alt Distributor",  180,  60,  "kg", 35.00),
        ("Plant Extract - Ashwagandha", "Alt Distributor",  95,   60,  "kg", 40.00),
        ("Plant Extract - Elderberry",  "Alt Distributor",  30,   60,  "kg", 45.00),
        ("Hyaluronic Acid Powder",      "Alt Distributor",  120,  50,  "kg", 55.00),
        ("Chondroitin Sulfate",         "Jiaxing Supplier", 200,  80,  "kg", 25.00),
        ("Glucosamine HCl",             "Jiaxing Supplier", 170,  80,  "kg", 18.00),
    ]
    if _use_postgres:
        for row in rows:
            _execute(conn, """
                INSERT INTO stock (product_name, supplier, quantity_kg, reorder_at, unit, unit_price)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (product_name) DO NOTHING
            """, row)
    else:
        _executemany(conn, """
            INSERT OR IGNORE INTO stock (product_name, supplier, quantity_kg, reorder_at, unit, unit_price)
            VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
    conn.commit()
    conn.close()


# ── Product operations ─────────────────────────────────────────────────────────

def get_known_products() -> list:
    """Return list of active product names for use in pdf_extractor."""
    conn = get_connection()
    cur  = _execute(conn, "SELECT product_name FROM products WHERE active = 1 ORDER BY product_name")
    rows = cur.fetchall()
    conn.close()
    return [row["product_name"] for row in rows]


def add_product(product_name: str) -> bool:
    """Add a new product. Returns True if inserted, False if already exists."""
    conn = get_connection()
    try:
        if _use_postgres:
            cur = _execute(conn, """
                INSERT INTO products (product_name) VALUES (?)
                ON CONFLICT (product_name) DO NOTHING
            """, (product_name,))
            inserted = cur.rowcount > 0
        else:
            _execute(conn, "INSERT OR IGNORE INTO products (product_name) VALUES (?)", (product_name,))
            inserted = conn.execute("SELECT changes()").fetchone()[0] > 0
        conn.commit()
    finally:
        conn.close()
    return inserted


def seed_products():
    """Seed the products table with the initial known product list."""
    conn = get_connection()
    products = [
        ("Collagen Powder",),
        ("Shark Cartilage Powder",),
        ("Bovine Gelatin Type A",),
        ("Fish Collagen Peptides",),
        ("Hydrolyzed Marine Collagen",),
        ("Bovine Cartilage Extract",),
        ("Plant Extract - Ginseng Root",),
        ("Plant Extract - Turmeric",),
        ("Plant Extract - Ashwagandha",),
        ("Plant Extract - Elderberry",),
        ("Plant Extract - Echinacea",),
        ("Hyaluronic Acid Powder",),
        ("Chondroitin Sulfate",),
        ("Glucosamine HCl",),
        ("Collagen Peptides Type I",),
    ]
    if _use_postgres:
        for p in products:
            _execute(conn, """
                INSERT INTO products (product_name) VALUES (?)
                ON CONFLICT (product_name) DO NOTHING
            """, p)
    else:
        _executemany(conn, "INSERT OR IGNORE INTO products (product_name) VALUES (?)", products)
    conn.commit()
    conn.close()


# ── Email dedup & feedback ─────────────────────────────────────────────────────

def is_message_processed(message_id: str, label: str = "") -> bool:
    """Return True if this message_id + label has already been processed."""
    conn = get_connection()
    try:
        row = _execute(
            conn,
            "SELECT id FROM processed_messages WHERE message_id = ? AND label = ?",
            (str(message_id), label),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_message_processed(message_id: str, label: str = "") -> None:
    """Record a message as processed to prevent duplicate handling."""
    conn = get_connection()
    try:
        if _use_postgres:
            _execute(
                conn,
                "INSERT INTO processed_messages (message_id, label) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (str(message_id), label),
            )
        else:
            _execute(
                conn,
                "INSERT OR IGNORE INTO processed_messages (message_id, label) VALUES (?, ?)",
                (str(message_id), label),
            )
        conn.commit()
    finally:
        conn.close()


def save_user_feedback(
    run_id: str,
    message_id: str,
    action: str,
    supplier: str | None = None,
    quantity: float | None = None,
    reason: str | None = None,
) -> None:
    """Save a parsed procurement reply into procurement_replies."""
    conn = get_connection()
    try:
        _execute(
            conn,
            """
            INSERT INTO procurement_replies
                (draft_id, sender, subject, raw_body, parsed_action,
                 parsed_supplier, parsed_quantity, parsed_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, "", "", "", action, supplier, quantity, reason),
        )
        conn.commit()
    finally:
        conn.close()


def has_feedback_for_run(run_id: str) -> bool:
    """Return True if procurement_replies already has a row for this run_id."""
    conn = get_connection()
    try:
        row = _execute(
            conn,
            "SELECT id FROM procurement_replies WHERE draft_id = ?",
            (run_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_procurement_memory(
    product_name: str,
    supplier: str,
    supplier_email: str | None = None,
    unit_price: float | None = None,
    lead_time: str | None = None,
    shipping_cost: float | None = None,
    estimated_total_cost: float | None = None,
    availability: str | None = None,
    recommendation_id: int | None = None,
    recommendation_action: str | None = None,
    user_action: str | None = None,
    user_reason: str | None = None,
    outcome_status: str | None = None,
    run_id: str | None = None,
) -> None:
    """Record supplier performance data for future procurement decisions."""
    conn = get_connection()
    try:
        _execute(
            conn,
            """
            INSERT INTO procurement_memory
                (product_name, supplier, supplier_email, unit_price, lead_time,
                 shipping_cost, estimated_total_cost, availability, recommendation_id,
                 recommendation_action, user_action, user_reason, outcome_status, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_name, supplier, supplier_email, unit_price, lead_time,
                shipping_cost, estimated_total_cost, availability, recommendation_id,
                recommendation_action, user_action, user_reason, outcome_status, run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    seed_initial_inventory()
    seed_products()
    print(f"Database initialised ({'Postgres' if _use_postgres else 'SQLite'}) and seeded.")
