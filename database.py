import sqlite3

DB_PATH = "inventory.db"

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

if __name__ == "__main__":
    init_db()
    seed_initial_inventory()
    print("Database created and seeded.")