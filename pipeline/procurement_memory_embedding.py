"""
procurement_memory_embedding.py — Embedding-based retrieval layer for procurement memory

Auxiliary layer only.  Embeddings surface similar historical context for
recommendation explanations.  The final procurement action is always governed
by the existing structured supplier memory score, quote fields, stock threshold,
and decision rules in procurement_agent.py.

Requires OPENAI_API_KEY   for embedding.
Requires ANTHROPIC_API_KEY for LLM explanation (uses the project's existing provider).
Both fail gracefully — the base procurement workflow continues unchanged without them.
"""

import json
import math
import os
import sys

# Allow imports from the project root (database.py lives there).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

_OPENAI_KEY      = os.environ.get("OPENAI_API_KEY")
_ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")
# Default to text-embedding-3-small; override with EMBEDDING_MODEL env var.
# text-embedding-ada-002 is never used unless explicitly set here.
_EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")


# ---------------------------------------------------------------------------
# Embedding provider
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float] | None:
    """
    Return a float embedding vector for text using OpenAI, or None if unavailable.

    Model: _EMBEDDING_MODEL (default text-embedding-3-small, override via EMBEDDING_MODEL env var).
    Vectors are stored as JSON in procurement_memory_notes.embedding — retrieval
    uses cosine similarity; this layer is auxiliary and never makes procurement decisions.
    """
    if not _OPENAI_KEY:
        print("[MEMORY EMBEDDING] Embedding unavailable; falling back to structured memory only.")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("[MEMORY EMBEDDING] openai package not installed; falling back to structured memory only.")
        return None
    try:
        client = OpenAI(api_key=_OPENAI_KEY)
        resp = client.embeddings.create(model=_EMBEDDING_MODEL, input=text)
        return resp.data[0].embedding
    except Exception as exc:
        print(f"[MEMORY EMBEDDING] Embedding error: {exc}. Falling back to structured memory only.")
        return None


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    dot  = sum(a * b for a, b in zip(vec1, vec2))
    mag1 = math.sqrt(sum(a * a for a in vec1))
    mag2 = math.sqrt(sum(b * b for b in vec2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


# ---------------------------------------------------------------------------
# CRUD for procurement_memory_notes
# ---------------------------------------------------------------------------

def create_memory_note(
    supplier: str,
    product_name: str,
    event_type: str,
    note: str,
    impact: str,
) -> bool:
    """
    Insert a procurement memory note with its embedding into the DB.

    Notes with the same (supplier, product_name, note) are skipped to prevent
    duplicates on repeated demo-seed runs.  The note is always saved regardless
    of whether embedding succeeds — embedding is auxiliary.

    Returns True when the row was inserted, False when skipped or on DB error.
    """
    from database import get_connection, _execute, _use_postgres

    conn = get_connection()
    try:
        existing = _execute(conn,
            "SELECT id FROM procurement_memory_notes "
            "WHERE supplier = ? AND product_name = ? AND note = ?",
            (supplier, product_name, note),
        ).fetchone()
        if existing:
            return False  # already present — skip

        embedding = embed_text(
            f"{supplier} {product_name} {event_type}: {note} Impact: {impact}"
        )
        embedding_json = json.dumps(embedding) if embedding is not None else None

        _execute(conn,
            "INSERT INTO procurement_memory_notes "
            "    (supplier, product_name, event_type, note, impact, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (supplier, product_name, event_type, note, impact, embedding_json),
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"[MEMORY EMBEDDING] Failed to save memory note: {exc}")
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve_similar_memory(
    product_name: str,
    supplier: str,
    query_text: str,
    top_k: int = 3,
) -> list[dict]:
    """
    Return up to top_k memory notes most similar to query_text by cosine distance.
    Returns [] if embeddings are unavailable or the notes table is empty.
    """
    query_vec = embed_text(query_text)
    if query_vec is None:
        return []

    from database import get_connection, _execute

    conn = get_connection()
    try:
        rows = _execute(conn,
            "SELECT supplier, product_name, event_type, note, impact, embedding "
            "FROM procurement_memory_notes WHERE embedding IS NOT NULL",
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    scored: list[tuple[float, dict]] = []
    for row in [dict(r) for r in rows]:
        try:
            stored_vec = json.loads(row["embedding"])
            sim = cosine_similarity(query_vec, stored_vec)
            scored.append((sim, row))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def build_memory_context_for_recommendation(
    product_name: str,
    supplier: str,
    quote_fields: dict,
) -> list[dict]:
    """
    Retrieve similar past memory notes that are relevant to a procurement decision.
    Returns a list of note dicts (may be empty).
    """
    query = (
        f"Procurement for {product_name} from {supplier}. "
        f"Availability: {quote_fields.get('availability', 'unknown')}. "
        f"Lead time: {quote_fields.get('lead_time', 'unknown')}. "
        f"Unit price: {quote_fields.get('unit_price', 'unknown')}."
    )
    return retrieve_similar_memory(product_name, supplier, query, top_k=3)


# ---------------------------------------------------------------------------
# LLM explanation (optional — falls back to None if unavailable)
# ---------------------------------------------------------------------------

def _clean_llm_text(text: str) -> str:
    """Strip markdown bold/italic markers so the text is safe for Slack plain-text lines."""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',     r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',       r'\1', text, flags=re.DOTALL)
    return ' '.join(text.split())


def generate_llm_recommendation_explanation(
    product_name: str,
    supplier: str,
    quote_fields: dict,
    structured_memory_score: dict,
    retrieved_memory_notes: list[dict],
) -> str | None:
    """
    Generate a concise procurement recommendation explanation using Claude.

    Uses retrieved similar memory notes as context and structured memory score as
    the primary signal.  Returns None when the Anthropic API is unavailable so
    callers can fall back to the existing deterministic explanation.
    """
    if not _ANTHROPIC_KEY:
        return None
    try:
        import anthropic

        score     = structured_memory_score.get("supplier_score", 0)
        approvals = structured_memory_score.get("approval_count", 0)
        rejects   = structured_memory_score.get("reject_count", 0)

        if retrieved_memory_notes:
            notes_text = "\n".join(
                f"- [{n['event_type']}] {n['supplier']} / {n['product_name']}: "
                f"{n['note']} (impact: {n['impact']})"
                for n in retrieved_memory_notes
            )
        else:
            notes_text = "No similar past memory cases found."

        prompt = (
            f"You are a procurement advisor for a nutraceutical distributor.\n\n"
            f"Product: {product_name}\n"
            f"Supplier: {supplier}\n"
            f"Quote — unit price: {quote_fields.get('unit_price', 'unknown')}, "
            f"availability: {quote_fields.get('availability', 'unknown')}, "
            f"lead time: {quote_fields.get('lead_time', 'unknown')}.\n\n"
            f"Supplier memory score: {score:.1f} "
            f"(approvals: {approvals}, rejections: {rejects})\n\n"
            f"Similar past cases:\n{notes_text}\n\n"
            "Write exactly 1-2 plain-prose sentences as a procurement recommendation.\n"
            "Rules:\n"
            "- Start with 'Proceed with', 'Exercise caution with', or 'Avoid'.\n"
            "- Do NOT use markdown: no **, no *, no _, no #.\n"
            "- Do NOT invent numbers not shown above.\n"
            "- Maximum 55 words total.\n"
            "- End with a complete sentence — no trailing ellipsis or half-sentences."
        )

        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        return _clean_llm_text(raw)

    except Exception as exc:
        print(f"[MEMORY EMBEDDING] LLM explanation error: {exc}")
        return None