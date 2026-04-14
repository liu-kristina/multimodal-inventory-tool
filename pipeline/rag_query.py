"""
Step 4 — RAG query layer.

Retrieves relevant invoices from ChromaDB and passes them to Claude
to generate a natural language answer.

Setup:
    pip install anthropic openai chromadb python-dotenv

Add to your .env file:
    OPENAI_API_KEY=your-key-here
    ANTHROPIC_API_KEY=your-key-here

Run interactively:
    python rag_query.py

Or import and use in your app:
    from rag_query import ask
    answer = ask("who supplies shark cartilage powder?")
"""

import os
import chromadb
import anthropic
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

CHROMA_DIR       = "chroma_db"
COLLECTION_NAME  = "invoices"
OPENAI_MODEL     = "text-embedding-3-small"
CLAUDE_MODEL     = "claude-sonnet-4-6"
TOP_K            = 5       # number of invoices to retrieve per query
MAX_TOKENS       = 1024

# ── Clients ────────────────────────────────────────────────────────────────────

openai_client    = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
chroma_client    = chromadb.PersistentClient(path=CHROMA_DIR)
collection       = chroma_client.get_collection(COLLECTION_NAME)


# ── Step 1: Intent detection ───────────────────────────────────────────────────

def detect_intent(query: str) -> str:
    """
    Classify the query to decide which invoices to search.
    Returns 'supplier', 'customer', or 'all'.
    """
    q = query.lower()

    supplier_signals = [
        "supplier", "supplies", "supply", "who makes", "who produces",
        "lead time", "port", "shipment", "from china", "manufacturer",
        "source", "vendor", "reorder", "order from", "purchase from",
    ]
    customer_signals = [
        "customer", "client", "buyer", "buys", "purchases", "who buys",
        "who orders", "sells to", "selling to", "who do we sell",
    ]

    supplier_score = sum(1 for w in supplier_signals if w in q)
    customer_score = sum(1 for w in customer_signals if w in q)

    if customer_score > supplier_score:
        return "customer"
    elif supplier_score > 0:
        return "supplier"
    else:
        return "all"


# ── Step 2: Retrieve relevant invoices ────────────────────────────────────────

def get_embedding(text: str) -> list:
    response = openai_client.embeddings.create(
        model=OPENAI_MODEL,
        input=text,
    )
    return response.data[0].embedding


def retrieve(query: str, intent: str, n: int = TOP_K) -> list:
    """
    Query ChromaDB and return the top N most relevant invoice documents.
    Filters by invoice type based on detected intent.
    """
    embedding    = get_embedding(query)
    query_params = {
        "query_embeddings": [embedding],
        "n_results":        n,
        "include":          ["documents", "metadatas", "distances"],
    }
    if intent in ("supplier", "customer"):
        query_params["where"] = {"invoice_type": intent}

    results = collection.query(**query_params)

    retrieved = []
    for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]):
        retrieved.append({
            "text":     doc,
            "metadata": meta,
            "score":    round(1 - dist, 3),
        })
    return retrieved


# ── Step 3: Format context for Claude ─────────────────────────────────────────

def format_context(retrieved: list) -> str:
    """
    Format the retrieved invoice summaries into a clean context block
    for Claude to read.
    """
    lines = []
    for i, item in enumerate(retrieved):
        meta  = item["metadata"]
        score = item["score"]
        inv_type = meta.get("invoice_type", "")
        inv_num  = meta.get("invoice_number", "")

        lines.append(f"--- Invoice {i+1} (relevance: {score}) ---")
        lines.append(item["text"])
        lines.append("")

    return "\n".join(lines)


# ── Step 4: Generate answer with Claude ───────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful assistant for California Nutraceuticals Inc., 
a raw material distributor based in Los Angeles that sources products from Chinese 
suppliers and sells to American nutraceutical brands.

You have access to the company's invoice history. When answering questions:
- Be specific — mention supplier names, product names, prices, and lead times
- If multiple suppliers provide the same product, compare them
- For lead time questions, give the specific days and typical range
- For price questions, give the actual unit prices from the invoices
- If the answer isn't in the provided invoices, say so clearly
- Keep answers concise and practical — this is a business tool
"""

def generate_answer(query: str, context: str) -> str:
    """Pass the query and retrieved context to Claude and return the answer."""
    message = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"""Here are the most relevant invoices from our records:

{context}

Question: {query}"""
            }
        ]
    )
    return message.content[0].text


# ── Main RAG function ──────────────────────────────────────────────────────────

def ask(query: str, verbose: bool = False) -> str:
    """
    Full RAG pipeline:
    1. Detect intent (supplier / customer / all)
    2. Retrieve relevant invoices from ChromaDB
    3. Format as context
    4. Generate answer with Claude

    Args:
        query:   Natural language question about invoices
        verbose: If True, print retrieved invoices before the answer

    Returns:
        Claude's answer as a string
    """
    intent    = detect_intent(query)
    retrieved = retrieve(query, intent)
    context   = format_context(retrieved)

    if verbose:
        print(f"\nIntent detected: {intent}")
        print(f"Retrieved {len(retrieved)} invoices:")
        for item in retrieved:
            meta  = item["metadata"]
            party = meta.get("supplier_name") or meta.get("customer_name") or "?"
            print(f"  score={item['score']:.3f}  "
                  f"{meta.get('invoice_number','?'):<12}  {party}")
        print()

    return generate_answer(query, context)


# ── Interactive mode ───────────────────────────────────────────────────────────

def run_interactive():
    """Run a simple interactive Q&A loop in the terminal."""
    print("=" * 60)
    print("California Nutraceuticals — Invoice Assistant")
    print("Type 'quit' to exit, 'verbose' to toggle debug output")
    print("=" * 60)
    print()

    verbose = False

    # Run a set of demo queries first
    # demo_queries = [
    #     "who supplies shark cartilage powder?",
    #     "what is the lead time from Jiaxing Natural Products?",
    #     "which customers buy collagen from us?",
    #     "what is the best price we have paid for shark cartilage?",
    #     "which customer is in Boulder Colorado?",
    # ]

    print("Running demo queries...\n")
    for query in demo_queries:
        print(f"Q: {query}")
        answer = ask(query, verbose=verbose)
        print(f"A: {answer}")
        print()

    # Interactive loop
    print("-" * 60)
    print("Now ask your own questions:")
    print()

    while True:
        try:
            query = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue
        if query.lower() == "quit":
            print("Goodbye.")
            break
        if query.lower() == "verbose":
            verbose = not verbose
            print(f"Verbose mode: {'on' if verbose else 'off'}")
            continue

        answer = ask(query, verbose=verbose)
        print(f"A: {answer}")
        print()


if __name__ == "__main__":
    run_interactive()
