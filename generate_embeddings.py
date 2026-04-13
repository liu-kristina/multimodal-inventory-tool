"""
Step 3 — Generate embeddings and store in ChromaDB.
Uses OpenAI text-embedding-3-small for high quality retrieval.

Setup:
    pip install openai chromadb python-dotenv

Add to your .env file:
    OPENAI_API_KEY=your-key-here

Run:
    python generate_embeddings.py

Test queries:
    python generate_embeddings.py --query "who supplies shark cartilage powder?"
    python generate_embeddings.py --query "which customers buy collagen from us?"
    python generate_embeddings.py --query "what is the lead time from Jiaxing?"
"""

import json
import sys
import os
import chromadb
from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

EXTRACTED_JSON  = "extracted_invoices.json"
CHROMA_DIR      = "chroma_db"
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


# ── Text builders ──────────────────────────────────────────────────────────────

def build_supplier_text(inv: dict) -> str:
    supplier = inv.get("supplier_name", "")
    products = inv.get("line_items", [])
    product_names = ", ".join(p["product"] for p in products)

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
        f"Shipping method: {inv.get('shipping_method', '')}",
        f"Port of loading: {inv.get('port_of_loading', '')}",
        f"Total lead time: {inv.get('total_lead_time', '')}",
        f"Typical lead time: {inv.get('typical_lead_time', '')}",
        f"Payment terms: {inv.get('payment_terms', '')}",
        f"Currency: {inv.get('currency', '')}",
        f"Grand total: {inv.get('currency', 'USD')} {inv.get('grand_total', 0):,.2f}",
    ]
    for item in products:
        lines.append(
            f"Product: {item['product']}, "
            f"quantity: {item['quantity']} kg, "
            f"unit price: {item['unit_price']}, "
            f"line total: {item['total']}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_customer_text(inv: dict) -> str:
    customer = inv.get("customer_name", "")
    customer_type = inv.get("customer_type", "")
    products = inv.get("line_items", [])
    product_names = ", ".join(p["product"] for p in products)

    lead = (
        f"{customer} purchases {product_names} from California Nutraceuticals. "
        f"This is a sales invoice to customer {customer}, a {customer_type}."
    )
    lines = [
        lead,
        f"Customer: {customer}",
        f"Customer type: {customer_type}",
        f"Products purchased: {product_names}",
        f"Invoice number: {inv.get('invoice_number', '')}",
        f"PO number: {inv.get('po_number', '')}",
        f"Invoice date: {inv.get('invoice_date', '')}",
        f"Shipping method: {inv.get('shipping_method', '')}",
        f"Ship to: {inv.get('ship_to', '')}",
        f"Transit time: {inv.get('transit_time', '')}",
        f"Payment terms: {inv.get('payment_terms', '')}",
        f"Grand total: USD {inv.get('grand_total', 0):,.2f}",
    ]
    for item in products:
        lines.append(
            f"Product: {item['product']}, "
            f"quantity: {item['quantity']} kg, "
            f"unit price: {item['unit_price']}, "
            f"line total: {item['total']}"
        )
    return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


def build_text(inv: dict) -> str:
    if inv.get("invoice_type") == "supplier":
        return build_supplier_text(inv)
    elif inv.get("invoice_type") == "customer":
        return build_customer_text(inv)
    return json.dumps(inv)


# ── Build index ────────────────────────────────────────────────────────────────

def build_index():
    if not Path(EXTRACTED_JSON).exists():
        print(f"ERROR: {EXTRACTED_JSON} not found.")
        print("Run extract_entities.py first.")
        sys.exit(1)

    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not found.")
        print("Add it to your .env file: OPENAI_API_KEY=your-key-here")
        sys.exit(1)

    with open(EXTRACTED_JSON) as f:
        invoices = json.load(f)

    print(f"Loaded {len(invoices)} invoices from {EXTRACTED_JSON}")
    print(f"Embedding model: {OPENAI_MODEL}")
    print(f"Estimated cost: ~${len(invoices) * 0.00002:.4f} USD")
    print()

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    collection = chroma_client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    print(f"Embedding {len(invoices)} invoices...")
    print("-" * 60)

    documents, embeddings, metadatas, ids = [], [], [], []

    for i, inv in enumerate(invoices):
        text      = build_text(inv)
        embedding = get_embedding(text)

        metadata = {
            "filename":       inv.get("filename", ""),
            "invoice_type":   inv.get("invoice_type", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "invoice_date":   inv.get("invoice_date", ""),
            "grand_total":    float(inv.get("grand_total", 0)),
        }
        if inv.get("invoice_type") == "supplier":
            metadata["supplier_name"]   = inv.get("supplier_name", "")
            metadata["shipping_method"] = inv.get("shipping_method", "")
            metadata["total_lead_time"] = inv.get("total_lead_time", "")
        elif inv.get("invoice_type") == "customer":
            metadata["customer_name"]   = inv.get("customer_name", "")
            metadata["customer_type"]   = inv.get("customer_type", "")
            metadata["shipping_method"] = inv.get("shipping_method", "")

        doc_id = inv.get("invoice_number", f"doc_{i}")
        documents.append(text)
        embeddings.append(embedding)
        metadatas.append(metadata)
        ids.append(doc_id)

        party = inv.get("supplier_name") or inv.get("customer_name") or "?"
        print(f"  [{i+1:3d}/{len(invoices)}]  "
              f"{doc_id:<12}  {inv.get('invoice_type','?'):<8}  {party}")

    collection.add(
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    print("-" * 60)
    print(f"Done. {len(invoices)} invoices stored in ChromaDB at ./{CHROMA_DIR}/")
    print()
    print("Running sanity checks...")
    _test_query(collection, "who supplies shark cartilage powder?",   "supplier")
    _test_query(collection, "which customers buy collagen from us?",  "customer")
    _test_query(collection, "lead time from Jiaxing Natural Products","supplier")
    _test_query(collection, "which customer is in Boulder Colorado?", "customer")


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
        print(f"ERROR: ChromaDB not found. Run generate_embeddings.py first.")
        sys.exit(1)

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection    = chroma_client.get_collection(COLLECTION_NAME)

    # Simple intent detection
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
    if "--query" in sys.argv:
        idx = sys.argv.index("--query")
        if idx + 1 < len(sys.argv):
            query_index(sys.argv[idx + 1])
        else:
            print("Usage: python generate_embeddings.py --query 'your question'")
    else:
        build_index()
