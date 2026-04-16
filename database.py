import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "inventory.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL UNIQUE,
            supplier TEXT,
            quantity_kg REAL DEFAULT 0,
            reorder_at REAL DEFAULT 0,
            unit TEXT DEFAULT 'kg',
            unit_price REAL,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL UNIQUE,
            invoice_type TEXT NOT NULL,
            counterparty_name TEXT,
            invoice_date TEXT,
            total_amount REAL DEFAULT 0,
            filename TEXT,
            embedded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_line_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL,
            product_name TEXT,
            quantity_kg REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0,
            FOREIGN KEY (invoice_number) REFERENCES invoices(invoice_number)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL UNIQUE,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def save_invoice(extracted: dict):
    """
    Save an extracted invoice dict (from pdf_extractor) to SQLite.
    Skips if invoice_number already exists.
    Returns True if inserted, False if skipped.
    """
    conn = get_connection()

    invoice_type = extracted.get("invoice_type", "unknown")
    counterparty = (
        extracted.get("supplier_name") or
        extracted.get("customer_name") or ""
    )
    invoice_number = extracted.get("invoice_number")
    if not invoice_number:
        invoice_number = f"unknown_{hash(str(extracted)) % 10000}"

    try:
        conn.execute("""
            INSERT OR IGNORE INTO invoices
                (invoice_number, invoice_type, counterparty_name, invoice_date, total_amount, filename)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            invoice_number,
            invoice_type,
            counterparty,
            extracted.get("invoice_date", ""),
            float(extracted.get("grand_total", 0)),
            extracted.get("filename", ""),
        ))

        for item in extracted.get("line_items", []):
            conn.execute("""
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
    """Return all invoices that haven't been embedded into ChromaDB yet."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT invoice_number FROM invoices WHERE embedded = 0
    """).fetchall()
    conn.close()
    return [row["invoice_number"] for row in rows]


def mark_embedded(invoice_number: str):
    """Mark an invoice as embedded in ChromaDB."""
    conn = get_connection()
    conn.execute("""
        UPDATE invoices SET embedded = 1 WHERE invoice_number = ?
    """, (invoice_number,))
    conn.commit()
    conn.close()

def seed_initial_inventory():
    """Insert starting inventory. Safe to run multiple times — skips existing rows."""
    conn = get_connection()
    conn.executemany("""
        INSERT OR IGNORE INTO stock (product_name, supplier, quantity_kg, reorder_at, unit, unit_price)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
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
    ])
    conn.commit()
    conn.close()


def get_known_products() -> list:
    """Return list of active product names for use in pdf_extractor."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT product_name FROM products WHERE active = 1 ORDER BY product_name
    """).fetchall()
    conn.close()
    return [row["product_name"] for row in rows]


def add_product(product_name: str) -> bool:
    """
    Add a new product to the products table.
    Returns True if inserted, False if already exists.
    """
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO products (product_name) VALUES (?)
        """, (product_name,))
        conn.commit()
        inserted = conn.execute("SELECT changes()").fetchone()[0] > 0
    finally:
        conn.close()
    return inserted


def seed_products():
    """Seed the products table with the initial known product list."""
    conn = get_connection()
    conn.executemany("""
        INSERT OR IGNORE INTO products (product_name) VALUES (?)
    """, [
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
    ])
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    seed_initial_inventory()
    seed_products()
    print("Database created and seeded.")
