"""
slack_agent.py — Hermes Slack interaction layer

Thin Slack bot that reads/writes the same database as the CLI, Gmail workflow,
procurement agent, and Dash UI.

Keyword routing handles all commands.  When procurement_memory_embedding is
configured (OPENAI_API_KEY + ANTHROPIC_API_KEY), @Hermes memory and
@Hermes procurement additionally surface similar past memory notes and a
short AI-generated explanation.  All embedding / LLM steps fail gracefully —
the base responses always work without any API keys.

Run:  python cli.py slack-agent
Requires: SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables.
"""

import os
import sys
import traceback

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start_slack_agent():
    """Start the Hermes Slack bot using Socket Mode."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")

    if not bot_token:
        print("[SLACK AGENT] ERROR: SLACK_BOT_TOKEN is not set.")
        print("  Export it with:  SLACK_BOT_TOKEN=xoxb-...")
        sys.exit(1)
    if not app_token:
        print("[SLACK AGENT] ERROR: SLACK_APP_TOKEN is not set.")
        print("  Export it with:  SLACK_APP_TOKEN=xapp-...")
        sys.exit(1)

    print("[SLACK AGENT] Starting Hermes Slack agent...")
    print("[SLACK AGENT] Database mode: PostgreSQL if DATABASE_URL is set, otherwise SQLite fallback")
    print("[SLACK AGENT] Connected using Slack Socket Mode")

    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=bot_token)

    @app.event("app_mention")
    def handle_mention(event, say):
        text = event.get("text", "")
        # Strip the leading bot mention token "<@UXXXXXXXX> ..."
        if ">" in text:
            text = text[text.index(">") + 1:].strip()
        try:
            response = handle_slack_command(text.lower())
        except Exception as exc:
            traceback.print_exc()
            response = (
                f"An error occurred: {exc}\n"
                "If tables are missing, run:\n"
                "  `python cli.py init-db`\n"
                "  `python cli.py demo-seed`"
            )
        say(response)

    handler = SocketModeHandler(app, app_token)
    handler.start()


# ---------------------------------------------------------------------------
# Command routing
# ---------------------------------------------------------------------------

def handle_slack_command(text: str) -> str:
    """Route a (lowercased, mention-stripped) text string to the right handler."""
    if "help" in text:
        return _help_text()
    if "low stock" in text or "lowstock" in text:
        return format_low_stock_summary()
    if "inventory" in text or "status" in text:
        return format_inventory_summary()
    if "procurement" in text:
        return format_procurement_summary()
    if "memory" in text:
        return format_memory_summary()
    if "demo seed" in text or "demo-seed" in text:
        return _run_demo_seed()
    return (
        "I didn't understand that command.\n"
        "Try `@Hermes help` for a list of supported commands."
    )


def _help_text() -> str:
    return (
        "*Hermes — supported commands:*\n"
        "• `@Hermes inventory` — current inventory summary\n"
        "• `@Hermes status` — alias for inventory\n"
        "• `@Hermes low stock` — items below reorder threshold\n"
        "• `@Hermes procurement` — latest procurement recommendations\n"
        "• `@Hermes memory` — supplier memory & risk summary\n"
        "• `@Hermes demo seed` — seed demo data\n"
        "• `@Hermes help` — show this message"
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_inventory_summary() -> str:
    """Return a concise inventory table for Slack (top 10 rows)."""
    from database import get_connection, _execute

    conn = get_connection()
    try:
        rows = _execute(
            conn,
            "SELECT product_name, supplier, quantity_kg, reorder_at, unit "
            "FROM stock ORDER BY product_name",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return (
            "No inventory data found.\n"
            "Run `python cli.py init-db` then `python cli.py demo-seed` to initialise."
        )

    rows = [dict(r) for r in rows]
    truncated = len(rows) > 10
    display = rows[:10]

    lines = ["*Inventory Summary*"]
    for r in display:
        unit = r.get("unit") or "kg"
        qty = r["quantity_kg"] or 0
        reorder = r["reorder_at"] or 0
        flag = "  :warning: *LOW*" if qty < reorder else ""
        supplier_str = f" | {r['supplier']}" if r.get("supplier") else ""
        lines.append(
            f"• *{r['product_name']}*{supplier_str}"
            f" — {qty:.1f} {unit} (reorder at {reorder:.1f}){flag}"
        )

    if truncated:
        lines.append(
            f"_...and {len(rows) - 10} more items. "
            "Use `@Hermes low stock` to see items that need attention._"
        )

    return "\n".join(lines)


def format_low_stock_summary() -> str:
    """Return products where current stock is below reorder threshold."""
    from database import get_connection, _execute

    conn = get_connection()
    try:
        rows = _execute(
            conn,
            "SELECT product_name, supplier, quantity_kg, reorder_at, unit "
            "FROM stock WHERE quantity_kg < reorder_at ORDER BY product_name",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "No low-stock items found."

    lines = [f"*Low-Stock Items ({len(rows)})*"]
    for r in [dict(r) for r in rows]:
        unit = r.get("unit") or "kg"
        qty = r["quantity_kg"] or 0
        reorder = r["reorder_at"] or 0
        supplier_str = f" | {r['supplier']}" if r.get("supplier") else ""
        lines.append(
            f"• :warning: *{r['product_name']}*{supplier_str}"
            f" — {qty:.1f} {unit} (threshold: {reorder:.1f} {unit})"
        )

    return "\n".join(lines)


def format_procurement_summary() -> str:
    """Return latest procurement recommendations with optional embedding-based AI context."""
    from database import get_connection, _execute

    conn = get_connection()
    try:
        rows = _execute(
            conn,
            "SELECT product_name, supplier, suggested_order_qty, urgency, status, reason "
            "FROM procurement_recommendations "
            "ORDER BY created_at DESC LIMIT 10",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return (
            "No procurement recommendations found.\n"
            "Run `python cli.py run-procurement` to generate recommendations,\n"
            "or `python cli.py demo-seed` to load demo data."
        )

    rows = [dict(r) for r in rows]
    lines = ["*Latest Procurement Recommendations*"]
    for i, r in enumerate(rows):
        urgency = r.get("urgency") or "—"
        status  = r.get("status") or "—"
        supplier = r.get("supplier") or "—"
        qty = r.get("suggested_order_qty") or 0
        reason_text = (r.get("reason") or "").split("\n")[0][:80]
        lines.append(
            f"• *{r['product_name']}* | {supplier}\n"
            f"  Qty: {qty:.0f} | Urgency: {urgency} | Status: {status}\n"
            f"  _{reason_text}_"
        )
        # Embedding context only for the most recent recommendation (one API call max)
        if i == 0:
            ai_lines = _ai_context_for_rec(r)
            lines.extend(ai_lines)

    return "\n".join(lines)


def format_memory_summary() -> str:
    """Return supplier memory scores, reject reasons, and any stored memory notes."""
    from database import get_connection, _execute

    try:
        from agents.procurement_agent import get_supplier_memory_score
    except ImportError:
        return "Procurement memory module is not available."

    conn = get_connection()
    try:
        pairs = _execute(
            conn,
            "SELECT DISTINCT product_name, supplier FROM procurement_memory "
            "ORDER BY product_name, supplier",
        ).fetchall()
    except Exception:
        conn.close()
        return (
            "Procurement memory is not initialized yet.\n"
            "Run `python cli.py init-db` then `python cli.py demo-seed`."
        )

    if not pairs:
        conn.close()
        return "Procurement memory is not initialized yet."

    lines = ["*Supplier Memory Summary*"]
    for pair in [dict(p) for p in pairs]:
        product  = pair["product_name"]
        supplier = pair["supplier"]
        try:
            ms = get_supplier_memory_score(conn, product, supplier)
            score     = ms["supplier_score"]
            approvals = ms["approval_count"]
            rejects   = ms["reject_count"]
            stops     = ms["stop_count"]
            if score <= -2:
                risk_flag = "  :rotating_light: HIGH RISK"
            elif score < 0:
                risk_flag = "  :warning: CAUTION"
            else:
                risk_flag = ""
            lines.append(
                f"• *{product}* | {supplier}\n"
                f"  Score: {score:.1f} | Approvals: {approvals} | "
                f"Rejects: {rejects} | Stops: {stops}{risk_flag}"
            )
            for reason in ms.get("recent_reject_reasons", [])[:2]:
                lines.append(f"  _Rejection: {reason[:80]}_")
            # Historical context notes (direct DB lookup — no API call)
            hist_notes = _notes_for_supplier(supplier, product)[:3]
            if hist_notes:
                lines.append("  _Historical context:_")
                for note in hist_notes:
                    impact = (note.get("impact") or "").lower()
                    if impact == "positive":
                        icon = "✅"
                    elif impact == "negative":
                        icon = "❌"
                    else:
                        icon = "⚠️"
                    event = note.get("event_type", "NOTE")
                    lines.append(f"  {icon} [{event}] {note['note'][:90]}")
        except Exception:
            lines.append(f"• *{product}* | {supplier} — (error reading score)")

    conn.close()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Embedding helpers (all fail gracefully — base responses work without them)
# ---------------------------------------------------------------------------

def _truncate_at_sentence(text: str, max_chars: int = 220) -> str:
    """Return text truncated at the last full sentence within max_chars."""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = window.rfind(sep)
        if idx > max_chars // 2:
            return window[:idx + 1]
    # No sentence boundary — cut at last space to avoid mid-word breaks
    idx = window.rfind(" ")
    return (window[:idx] + "…") if idx > 0 else window


def _similar_case_line(note: dict, current_supplier: str) -> str:
    """Format one retrieved memory note as a labelled Slack line."""
    impact = (note.get("impact") or "").lower()
    if impact == "positive":
        icon  = "✅"
        label = "Positive similar case"
    elif impact == "negative":
        icon  = "❌"
        label = "Negative similar case"
    else:
        icon  = "⚠️"
        label = "Caution similar case"

    note_supplier = (note.get("supplier") or "").strip()
    if note_supplier and note_supplier != current_supplier:
        label = f"{label} from {note_supplier}"

    note_text = (note.get("note") or "")[:100]
    return f"  {icon} {label}: {note_text}"


def _notes_for_supplier(supplier: str, product_name: str) -> list[dict]:
    """Direct DB lookup of memory notes for a supplier/product (no API call)."""
    from database import get_connection, _execute
    conn = get_connection()
    try:
        rows = _execute(conn,
            "SELECT supplier, event_type, note, impact FROM procurement_memory_notes "
            "WHERE supplier = ? OR product_name = ? "
            "ORDER BY created_at DESC LIMIT 3",
            (supplier, product_name),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _ai_context_for_rec(rec: dict) -> list[str]:
    """
    Return extra Slack lines for the most recent recommendation.
    Tries embedding retrieval then LLM explanation; both fail silently.
    Similar cases are display context only — they do not alter the recommendation.
    """
    try:
        import re
        from procurement_memory_embedding import (
            build_memory_context_for_recommendation,
            generate_llm_recommendation_explanation,
        )
        from agents.procurement_agent import get_supplier_memory_score
        from database import get_connection

        product  = rec.get("product_name", "")
        supplier = rec.get("supplier", "")
        reason   = rec.get("reason", "") or ""

        # Best-effort extraction of quote fields from the stored reason text
        quote_fields: dict = {"availability": None, "lead_time": None,
                               "unit_price": None, "shipping_cost": None}
        m = re.search(r"Unit price: \$([\d,.]+)", reason)
        if m:
            quote_fields["unit_price"] = float(m.group(1).replace(",", ""))
        m = re.search(r"Lead time: ([^.\n]+)", reason)
        if m:
            quote_fields["lead_time"] = m.group(1).strip()
        m = re.search(r"Availability: ([^.\n]+)", reason)
        if m:
            quote_fields["availability"] = m.group(1).strip()

        similar = build_memory_context_for_recommendation(product, supplier, quote_fields)
        if not similar:
            return []

        conn = get_connection()
        try:
            ms = get_supplier_memory_score(conn, product, supplier)
        finally:
            conn.close()

        extra: list[str] = []
        for note in similar[:2]:
            extra.append(_similar_case_line(note, supplier))

        explanation = generate_llm_recommendation_explanation(
            product, supplier, quote_fields, ms, similar
        )
        if explanation:
            clean = _truncate_at_sentence(explanation, max_chars=220)
            extra.append(f"  💡 *Recommendation:*\n  {clean}")

        return extra

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Demo seed (delegates to cli.py logic)
# ---------------------------------------------------------------------------

def _run_demo_seed() -> str:
    """Reuse cmd_demo_seed from cli.py. Returns a Slack-friendly status message."""
    try:
        import argparse
        from cli import cmd_demo_seed
        cmd_demo_seed(argparse.Namespace())
        return "Demo data seeded successfully."
    except Exception as exc:
        traceback.print_exc()
        return f"Demo seed failed: {exc}"
