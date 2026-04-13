"""
Step 3 — Generate embeddings and store in ChromaDB.

Reads extracted_invoices.json (output of extract_entities.py),
builds a clean text summary for each invoice, generates an embedding
using a local sentence-transformers model, and stores everything in
ChromaDB for later retrieval.

No API key required — runs entirely locally.

Run after extract_entities.py:
    python3 generate_embeddings.py

Then verify retrieval works:
    python3 generate_embeddings.py --query "who supplies shark cartilage?"
"""

import json
import sys
import chromadb
from sentence_transformers import SentenceTransformer
from pathlib import Path


# ── Config ─────────────────────────────────────────────────────────────────────

EXTRACTED_JSON   = "extracted_invoices.json"
CHROMA_DIR       = "chroma_db"           # folder where ChromaDB persists data
COLLECTION_NAME  = "invoices"
MODEL_NAME       = "all-MiniLM-L6-v2"   # free, local, ~80MB download on first run


# ── Build searchable text from extracted fields ────────────────────────────────

def build_supplier_text(inv: dict) -> str:
    """
    Build a clean natural language summary of a supplier invoice.
    This is what gets embedded — focused on the fields users will query.
    """
    lines = [
        f"Invoice type: supplier invoice",
        f"Invoice number: {inv.get('invoice_number', '')}",
        f"Invoice date: {inv.get('invoice_date', '')}",
        f"Supplier: {inv.get('supplier_name', '')}",
        f"Buyer: {inv.get('buyer_name', '')}",
        f"Payment terms: {inv.get('payment_terms', '')}",
        f"Currency: {inv.get('currency', '')}",
        f"Shipping method: {inv.get('shipping_method', '')}",
        f"Port of loading: {inv.get('port_of_loading', '')}",
        f"Port of destination: {inv.get('port_of_destination', '')}",
        f"Shipment date: {inv.get('shipment_date', '')}",
        f"Expected delivery: {inv.get('expected_delivery', '')}",
        f"Actual delivery: {inv.get('actual_delivery', '')}",
        f"Total lead time: {inv.get('total_lead_time', '')}",
        f"Typical lead time: {inv.get('typical_lead_time', '')}",
        f"Grand total: {inv.get('currency', 'USD')} {inv.get('grand_total', 0):,.2f}",
    ]
    for item in inv.get("line_items", []):
        lines.append(
            f"Product: {item['product']}, "
            f"quantity: {item['quantity']} kg, "
            f"unit price: {item['unit_price']}, "
            f"total: {item['total']}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_customer_text(inv: dict) -> str:
    """
    Build a clean natural language summary of a customer (sales) invoice.
    """
    lines = [
        f"Invoice type: customer sales invoice",
        f"Invoice number: {inv.get('invoice_number', '')}",
        f"PO number: {inv.get('po_number', '')}",
        f"Invoice date: {inv.get('invoice_date', '')}",
        f"Seller: {inv.get('seller_name', '')}",
        f"Customer: {inv.get('customer_name', '')}",
        f"Customer type: {inv.get('customer_type', '')}",
        f"Payment terms: {inv.get('payment_terms', '')}",
        f"Shipping method: {inv.get('shipping_method', '')}",
        f"Ship from: {inv.get('ship_from', '')}",
        f"Ship to: {inv.get('ship_to', '')}",
        f"Shipment date: {inv.get('shipment_date', '')}",
        f"Expected delivery: {inv.get('expected_delivery', '')}",
        f"Transit time: {inv.get('transit_time', '')}",
        f"Grand total: USD {inv.get('grand_total', 0):,.2f}",
    ]
    for item in inv.get("line_items", []):
        lines.append(
            f"Product: {item['product']}, "
            f"quantity: {item['quantity']} kg, "
            f"unit price: {item['unit_price']}, "
            f"total: {item['total']}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_text(inv: dict) -> str:
    """Dispatch to the right builder based on invoice type."""
    if inv.get("invoice_type") == "supplier":
        return build_supplier_text(inv)
    elif inv.get("invoice_type") == "customer":
        return build_customer_text(inv)
    else:
        return json.dumps(inv)


# ── Main: embed and store ──────────────────────────────────────────────────────

def build_index():
    """Load extracted invoices, embed them, and store in ChromaDB."""

    # Load extracted invoices
    if not Path(EXTRACTED_JSON).exists():
        print(f"ERROR: {EXTRACTED_JSON} not found.")
        print("Run extract_entities.py first.")
        sys.exit(1)

    with open(EXTRACTED_JSON) as f:
        invoices = json.load(f)

    print(f"Loaded {len(invoices)} invoices from {EXTRACTED_JSON}")

    # Load embedding model (downloads ~80MB on first run, cached after)
    print(f"Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    print("Model loaded.")

    # Set up ChromaDB persistent client
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Delete existing collection if rebuilding
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # cosine similarity for text
    )

    print(f"Embedding {len(invoices)} invoices...")
    print("-" * 60)

    documents = []
    embeddings = []
    metadatas = []
    ids = []

    for i, inv in enumerate(invoices):
        text = build_text(inv)
        embedding = model.encode(text).tolist()

        # Metadata stored alongside the vector for filtering and display
        metadata = {
            "filename":     inv.get("filename", ""),
            "invoice_type": inv.get("invoice_type", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "invoice_date": inv.get("invoice_date", ""),
            "grand_total":  float(inv.get("grand_total", 0)),
        }

        # Add type-specific metadata
        if inv.get("invoice_type") == "supplier":
            metadata["supplier_name"] = inv.get("supplier_name", "")
            metadata["shipping_method"] = inv.get("shipping_method", "")
            metadata["total_lead_time"] = inv.get("total_lead_time", "")
        elif inv.get("invoice_type") == "customer":
            metadata["customer_name"] = inv.get("customer_name", "")
            metadata["customer_type"] = inv.get("customer_type", "")
            metadata["shipping_method"] = inv.get("shipping_method", "")

        doc_id = inv.get("invoice_number", f"doc_{i}")

        documents.append(text)
        embeddings.append(embedding)
        metadatas.append(metadata)
        ids.append(doc_id)

        # Print progress every 10 invoices
        if (i + 1) % 10 == 0 or i == 0:
            inv_type = inv.get("invoice_type", "?")
            party = (inv.get("supplier_name") or
                     inv.get("customer_name") or "?")
            print(f"  [{i+1:3d}/{len(invoices)}]  "
                  f"{doc_id:<12}  {inv_type:<8}  {party}")

    # Add all documents in one batch (faster than one at a time)
    collection.add(
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    print("-" * 60)
    print(f"Done. {len(invoices)} invoices stored in ChromaDB at ./{CHROMA_DIR}/")
    print()

    # Quick sanity check — run two test queries
    print("Running sanity checks...")
    _test_query(collection, model,
                "who supplies shark cartilage powder?", n=3)
    _test_query(collection, model,
                "which customers buy collagen from us?", n=3)


# ── Query helper ───────────────────────────────────────────────────────────────

def _test_query(collection, model, query: str, n: int = 3):
    """Run a test query and print results."""
    print(f"\nQuery: '{query}'")
    embedding = model.encode(query).tolist()
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n,
        include=["metadatas", "distances"],
    )
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        score = round(1 - dist, 3)   # cosine similarity (higher = more similar)
        inv_num  = meta.get("invoice_number", "?")
        party    = (meta.get("supplier_name") or
                    meta.get("customer_name") or "?")
        inv_type = meta.get("invoice_type", "?")
        print(f"  score={score:.3f}  {inv_num:<12}  {inv_type:<8}  {party}")


def query_index(query: str, n: int = 5):
    """
    Query the ChromaDB index interactively.
    Usage: python3 generate_embeddings.py --query "your question here"
    """
    if not Path(CHROMA_DIR).exists():
        print(f"ERROR: ChromaDB not found at ./{CHROMA_DIR}/")
        print("Run generate_embeddings.py (without --query) first.")
        sys.exit(1)

    model = SentenceTransformer(MODEL_NAME)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_collection(COLLECTION_NAME)

    print(f"\nQuery: '{query}'")
    print(f"Top {n} results:")
    print("-" * 60)

    embedding = model.encode(query).tolist()
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n,
        include=["metadatas", "distances", "documents"],
    )

    for i, (meta, dist, doc) in enumerate(zip(
            results["metadatas"][0],
            results["distances"][0],
            results["documents"][0])):
        score = round(1 - dist, 3)
        inv_num  = meta.get("invoice_number", "?")
        party    = (meta.get("supplier_name") or
                    meta.get("customer_name") or "?")
        inv_type = meta.get("invoice_type", "?")
        total    = meta.get("grand_total", 0)
        print(f"\n[{i+1}] score={score:.3f}  {inv_num}  {inv_type}  {party}")
        print(f"     total=${total:,.2f}")
        print(f"     {doc[:120]}...")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--query" in sys.argv:
        idx = sys.argv.index("--query")
        if idx + 1 < len(sys.argv):
            query_index(sys.argv[idx + 1])
        else:
            print("Usage: python3 generate_embeddings.py --query 'your question'")
    else:
        build_index()
