"""
Step 3 — Generate embeddings and store in ChromaDB.
Uses OpenAI text-embedding-3-small for high quality retrieval.

Now reads from SQLite instead of extracted_invoices.json.
Only embeds invoices that haven't been embedded yet (incremental).

Setup:
    pip install openai chromadb python-dotenv

Add to your .env file:
    OPENAI_API_KEY=your-key-here

Run (incremental — only embeds new invoices):
    python generate_embeddings.py

Rebuild entire index from scratch:
    python generate_embeddings.py --rebuild

Test queries:
    python generate_embeddings.py --query "who supplies shark cartilage powder?"
    python generate_embeddings.py --query "which customers buy collagen from us?"
    python generate_embeddings.py --query "what is the lead time from Jiaxing?"
"""

import json
import sys
import os
import sqlite3
import chromadb
from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv

# database.py lives in the project root, one level up from pipeline/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection, get_unembedded_invoices, mark_embedded, init_db

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chroma_db")
COLLECTION_NAME = "invoices"
OPENAI_MODEL    = "text-embedding-3-small"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Embedding ──────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> list:
    response = client.embeddings.create(
        model=OPENAI_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ── Load full invoice from SQLite ──────────────────────────────────────────────

def load_invoice(invoice_number: str) -> dict:
    """Load a full invoice dict from SQLite including line items."""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM invoices WHERE invoice_number = ?
    """, (invoice_number,)).fetchone()

    if not row:
        conn.close()
        return {}

    inv = dict(row)

    line_items = conn.execute("""
        SELECT * FROM invoice_line_items WHERE invoice_number = ?
    """, (invoice_number,)).fetchall()

    inv["line_items"] = [dict(item) for item in line_items]
    conn.close()
    return inv


def load_all_invoices() -> list:
    """Load all invoices from SQLite including line items."""
    conn = get_connection()
    rows = conn.execute("SELECT invoice_number FROM invoices").fetchall()
    conn.close()
    return [load_invoice(row["invoice_number"]) for row in rows]


# ── Text builders ──────────────────────────────────────────────────────────────

def build_supplier_text(inv: dict) -> str:
    supplier = inv.get("counterparty_name", "")
    products = inv.get("line_items", [])
    product_names = ", ".join(
        p.get("product_name") or p.get("product", "") for p in products
    )

    lead = (
        f"{supplier} supplies {product_names} to California Nutraceuticals. "
        f"This is a supplier invoice from {supplier}."
    )
    lines = [
        lead,
        f"Supplier: {supplier}",
        f"Products supplied: {product_names}",
        f"Invoice number: {inv.get('invoice_number', '')}",
        f"Invoice date: {inv.get('invoice_date', '')}",
        f"Grand total: USD {inv.get('total_amount', 0):,.2f}",
    ]
    for item in products:
        name = item.get("product_name") or item.get("product", "")
        lines.append(
            f"Product: {name}, "
            f"quantity: {item.get('quantity_kg', item.get('quantity', 0))} kg, "
            f"unit price: {item.get('unit_price', 0)}, "
            f"line total: {item.get('line_total', item.get('total', 0))}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_customer_text(inv: dict) -> str:
    customer = inv.get("counterparty_name", "")
    products = inv.get("line_items", [])
    product_names = ", ".join(
        p.get("product_name") or p.get("product", "") for p in products
    )

    lead = (
        f"{customer} purchases {product_names} from California Nutraceuticals. "
        f"This is a sales invoice to customer {customer}."
    )
    lines = [
        lead,
        f"Customer: {customer}",
        f"Products purchased: {product_names}",
        f"Invoice number: {inv.get('invoice_number', '')}",
        f"Invoice date: {inv.get('invoice_date', '')}",
        f"Grand total: USD {inv.get('total_amount', 0):,.2f}",
    ]
    for item in products:
        name = item.get("product_name") or item.get("product", "")
        lines.append(
            f"Product: {name}, "
            f"quantity: {item.get('quantity_kg', item.get('quantity', 0))} kg, "
            f"unit price: {item.get('unit_price', 0)}, "
            f"line total: {item.get('line_total', item.get('total', 0))}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_text(inv: dict) -> str:
    if inv.get("invoice_type") == "supplier":
        return build_supplier_text(inv)
    elif inv.get("invoice_type") == "customer":
        return build_customer_text(inv)
    return json.dumps(inv)


def build_metadata(inv: dict) -> dict:
    metadata = {
        "filename":       inv.get("filename", ""),
        "invoice_type":   inv.get("invoice_type", ""),
        "invoice_number": inv.get("invoice_number", ""),
        "invoice_date":   inv.get("invoice_date", ""),
        "grand_total":    float(inv.get("total_amount", 0)),
    }
    if inv.get("invoice_type") == "supplier":
        metadata["supplier_name"] = inv.get("counterparty_name", "")
    elif inv.get("invoice_type") == "customer":
        metadata["customer_name"] = inv.get("counterparty_name", "")
    return metadata


# ── Incremental index (default) ────────────────────────────────────────────────

def embed_new_invoices():
    """Only embed invoices not yet in ChromaDB. Safe to run anytime."""
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    unembedded = get_unembedded_invoices()
    if not unembedded:
        print("Nothing to embed — all invoices are already in ChromaDB.")
        return

    print(f"Found {len(unembedded)} new invoice(s) to embed.")
    print(f"Estimated cost: ~${len(unembedded) * 0.00002:.4f} USD")
    print()

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        collection = chroma_client.get_collection(COLLECTION_NAME)
    except Exception:
        collection = chroma_client.create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    for i, invoice_number in enumerate(unembedded):
        inv = load_invoice(invoice_number)
        if not inv:
            print(f"  [{i+1}] SKIP {invoice_number} — not found in SQLite")
            continue

        text      = build_text(inv)
        embedding = get_embedding(text)
        metadata  = build_metadata(inv)

        collection.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[invoice_number],
        )
        mark_embedded(invoice_number)

        party = inv.get("counterparty_name", "?")
        print(f"  [{i+1}/{len(unembedded)}]  "
              f"{invoice_number:<12}  {inv.get('invoice_type','?'):<8}  {party}")

    print()
    print(f"Done. {len(unembedded)} invoice(s) added to ChromaDB.")


# ── Full rebuild ───────────────────────────────────────────────────────────────

def rebuild_index():
    """Delete and rebuild the entire ChromaDB index from SQLite."""
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    invoices = load_all_invoices()
    if not invoices:
        print("No invoices found in SQLite. Upload some invoices first.")
        return

    print(f"Rebuilding index from {len(invoices)} invoices in SQLite...")
    print(f"Estimated cost: ~${len(invoices) * 0.00002:.4f} USD")
    print()

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing ChromaDB collection.")
    except Exception:
        pass

    collection = chroma_client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Reset all embedded flags
    conn = get_connection()
    conn.execute("UPDATE invoices SET embedded = 0")
    conn.commit()
    conn.close()

    for i, inv in enumerate(invoices):
        invoice_number = inv.get("invoice_number", f"doc_{i}")
        text      = build_text(inv)
        embedding = get_embedding(text)
        metadata  = build_metadata(inv)

        collection.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[invoice_number],
        )
        mark_embedded(invoice_number)

        party = inv.get("counterparty_name", "?")
        print(f"  [{i+1:3d}/{len(invoices)}]  "
              f"{invoice_number:<12}  {inv.get('invoice_type','?'):<8}  {party}")

    print()
    print(f"Done. {len(invoices)} invoices stored in ChromaDB at ./{CHROMA_DIR}/")
    print()
    _test_query(collection, "who supplies shark cartilage powder?",  "supplier")
    _test_query(collection, "which customers buy collagen from us?", "customer")


# ── Query helpers ──────────────────────────────────────────────────────────────

def _test_query(collection, query: str, invoice_type: str, n: int = 3):
    print(f"\nQuery: '{query}' (filter: {invoice_type})")
    embedding = get_embedding(query)
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n,
        where={"invoice_type": invoice_type},
        include=["metadatas", "distances"],
    )
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        score   = round(1 - dist, 3)
        inv_num = meta.get("invoice_number", "?")
        party   = meta.get("supplier_name") or meta.get("customer_name") or "?"
        print(f"  score={score:.3f}  {inv_num:<12}  {party}")


def query_index(query: str, n: int = 5):
    if not Path(CHROMA_DIR).exists():
        print("ERROR: ChromaDB not found. Run generate_embeddings.py first.")
        sys.exit(1)

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection    = chroma_client.get_collection(COLLECTION_NAME)

    query_lower    = query.lower()
    supplier_words = ["supplier", "supplies", "supply", "from china",
                      "lead time", "port", "shipment", "who makes",
                      "who produces", "source", "manufacturer"]
    customer_words = ["customer", "client", "buyer", "buys", "purchases",
                      "sells to", "who orders", "who buys"]

    if any(w in query_lower for w in customer_words):
        invoice_type = "customer"
    elif any(w in query_lower for w in supplier_words):
        invoice_type = "supplier"
    else:
        invoice_type = None

    print(f"\nQuery: '{query}'")
    print(f"Detected intent: {invoice_type or 'all invoices'}")
    print(f"Top {n} results:")
    print("-" * 60)

    embedding    = get_embedding(query)
    query_params = {
        "query_embeddings": [embedding],
        "n_results":        n,
        "include":          ["metadatas", "distances", "documents"],
    }
    if invoice_type:
        query_params["where"] = {"invoice_type": invoice_type}

    results = collection.query(**query_params)

    for i, (meta, dist, doc) in enumerate(zip(
            results["metadatas"][0],
            results["distances"][0],
            results["documents"][0])):
        score   = round(1 - dist, 3)
        inv_num = meta.get("invoice_number", "?")
        party   = meta.get("supplier_name") or meta.get("customer_name") or "?"
        itype   = meta.get("invoice_type", "?")
        total   = meta.get("grand_total", 0)
        print(f"\n[{i+1}] score={score:.3f}  {inv_num}  {itype}  {party}")
        print(f"     total=${total:,.2f}")
        print(f"     {doc[:200]}...")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    if "--rebuild" in sys.argv:
        rebuild_index()
    elif "--query" in sys.argv:
        idx = sys.argv.index("--query")
        if idx + 1 < len(sys.argv):
            query_index(sys.argv[idx + 1])
        else:
            print("Usage: python generate_embeddings.py --query 'your question'")
    else:
        embed_new_invoices()

