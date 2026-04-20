"""
Invoice Agent — a real Claude-powered agent that monitors Gmail for invoices,
decides what to do with each one, and updates stock autonomously.

Claude receives a goal and a set of tools, and decides which tools to call
and in what order — rather than following a fixed hardcoded pipeline.

Setup:
    pip install anthropic python-dotenv

Add to your .env file:
    GMAIL_ADDRESS=your@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
    ANTHROPIC_API_KEY=sk-ant-...

Control via the Agent Control page in the app.
Or run manually: python invoice_agent.py
"""

import imaplib
import email
import os
import sys
import time
import json
import tempfile
import threading
import shutil
from datetime import datetime
from email.header import decode_header
from dotenv import load_dotenv
import anthropic

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import init_db, save_invoice, get_connection
from pipeline.pdf_extractor import extract_invoice
from pipeline.generate_embeddings import embed_new_invoices

# ── Config ─────────────────────────────────────────────────────────────────────

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_LABEL        = "invoices"
IMAP_SERVER        = "imap.gmail.com"
POLL_INTERVAL      = 300  # seconds — change to 86400 for production
INVOICE_SAVE_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "invoices")
CUSTOMER_SAVE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "customer_invoices")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Gmail helpers ──────────────────────────────────────────────────────────────

def connect_gmail():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    return mail


def fetch_unread_emails(mail):
    mail.select(f'"{GMAIL_LABEL}"')
    _, message_ids = mail.search(None, "UNSEEN")
    return message_ids[0].split() if message_ids[0] else []


def download_pdfs(mail, message_id) -> list:
    """Download PDF attachments. Returns list of (tmp_path, filename) tuples."""
    _, msg_data = mail.fetch(message_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    subject = decode_header(msg["Subject"])[0][0]
    if isinstance(subject, bytes):
        subject = subject.decode()
    sender = msg.get("From", "unknown")
    print(f"  Email: '{subject}' from {sender}")

    pdf_files = []
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            filename = part.get_filename() or "invoice.pdf"
            decoded = decode_header(filename)[0]
            if isinstance(decoded[0], bytes):
                filename = decoded[0].decode(decoded[1] or "utf-8")
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(part.get_payload(decode=True))
            tmp.close()
            pdf_files.append((tmp.name, filename))
            print(f"    Found: {filename}")

    return pdf_files


def mark_as_read(mail, message_id):
    mail.store(message_id, "+FLAGS", "\\Seen")


# ── Agent tools ────────────────────────────────────────────────────────────────

def tool_extract_invoice(pdf_path: str) -> dict:
    return extract_invoice(pdf_path)


def tool_get_stock_levels() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT product_name, quantity_kg, reorder_at, unit, supplier FROM stock ORDER BY product_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def tool_update_stock(product_name: str, quantity: float, direction: str, unit_price: float = None) -> str:
    conn = get_connection()
    if direction == "add":
        conn.execute("""
            INSERT INTO stock (product_name, quantity_kg, unit_price, last_updated)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(product_name) DO UPDATE SET
                quantity_kg = quantity_kg + excluded.quantity_kg,
                unit_price = COALESCE(excluded.unit_price, unit_price),
                last_updated = CURRENT_TIMESTAMP
        """, (product_name, quantity, unit_price))
        result = f"Added {quantity}kg of {product_name} to stock."
    elif direction == "subtract":
        conn.execute("""
            INSERT INTO stock (product_name, quantity_kg, last_updated)
            VALUES (?, MAX(0, -?), CURRENT_TIMESTAMP)
            ON CONFLICT(product_name) DO UPDATE SET
                quantity_kg = MAX(0, quantity_kg - excluded.quantity_kg),
                last_updated = CURRENT_TIMESTAMP
        """, (product_name, quantity))
        result = f"Subtracted {quantity}kg of {product_name} from stock."
    else:
        conn.close()
        return f"Unknown direction '{direction}'."
    conn.commit()
    conn.close()
    return result


def tool_save_invoice(extracted: dict) -> str:
    try:
        save_invoice(extracted)
        return f"Invoice {extracted.get('invoice_number', 'unknown')} saved."
    except Exception as e:
        return f"Failed to save invoice: {e}"


def tool_save_pdf(tmp_path: str, filename: str) -> str:
    if filename.startswith("customer_invoice"):
        save_dir = CUSTOMER_SAVE_DIR
        label = "data/customer_invoices"
    else:
        save_dir = INVOICE_SAVE_DIR
        label = "data/invoices"
    os.makedirs(save_dir, exist_ok=True)
    dest = os.path.join(save_dir, filename)
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        dest = os.path.join(save_dir, f"{base}_{int(time.time())}{ext}")
    shutil.copy2(tmp_path, dest)
    return f"PDF saved to {label}/{os.path.basename(dest)}"


def tool_embed_invoices() -> str:
    try:
        embed_new_invoices()
        return "Invoices embedded into ChromaDB."
    except Exception as e:
        return f"Embedding failed: {e}"


def tool_flag_for_review(reason: str, details: str = "") -> str:
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_flags (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            reason     TEXT,
            details    TEXT,
            resolved   INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO agent_flags (reason, details) VALUES (?, ?)",
        (reason, details)
    )
    conn.commit()
    conn.close()
    return f"Flagged for review: {reason}"


def tool_check_duplicate(invoice_number: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)
        ).fetchone()
        conn.close()
        if row:
            return f"DUPLICATE: Invoice {invoice_number} already exists."
        return f"Invoice {invoice_number} is new."
    except Exception:
        conn.close()
        return "Could not check duplicates — invoices table may not exist yet."


# ── Tool definitions for Claude API ───────────────────────────────────────────

TOOLS = [
    {
        "name": "extract_invoice",
        "description": "Extract structured data from a PDF invoice file — invoice number, type (supplier/customer), line items, totals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string", "description": "Path to the PDF temp file"}
            },
            "required": ["pdf_path"]
        }
    },
    {
        "name": "get_stock_levels",
        "description": "Get current stock levels for all products from the database.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "update_stock",
        "description": "Update stock quantity for a product. Use 'add' for supplier invoices (incoming), 'subtract' for customer invoices (outgoing).",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "quantity":     {"type": "number"},
                "direction":    {"type": "string", "enum": ["add", "subtract"]},
                "unit_price":   {"type": "number", "description": "Optional — for supplier invoices"}
            },
            "required": ["product_name", "quantity", "direction"]
        }
    },
    {
        "name": "save_invoice",
        "description": "Save the extracted invoice data to the SQLite database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "extracted": {"type": "object", "description": "The extracted invoice data dict"}
            },
            "required": ["extracted"]
        }
    },
    {
        "name": "save_pdf",
        "description": "Save the invoice PDF permanently to the data/invoices/ folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tmp_path": {"type": "string"},
                "filename": {"type": "string"}
            },
            "required": ["tmp_path", "filename"]
        }
    },
    {
        "name": "embed_invoices",
        "description": "Embed newly saved invoices into ChromaDB so they appear in RAG chat queries.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "flag_for_review",
        "description": "Flag an invoice for human review — shown in the Agent Control page. Use when invoice type is unknown, data is missing, a duplicate is detected, or anything looks suspicious.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason":  {"type": "string", "description": "Short reason for flagging"},
                "details": {"type": "string", "description": "More detail about the issue"}
            },
            "required": ["reason"]
        }
    },
    {
        "name": "check_duplicate",
        "description": "Check if an invoice number has already been processed to avoid double-counting stock.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string"}
            },
            "required": ["invoice_number"]
        }
    }
]


# ── Tool executor ──────────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict) -> str:
    print(f"    → {name}({str(inputs)[:80]})")

    if name == "extract_invoice":
        return json.dumps(tool_extract_invoice(inputs["pdf_path"]), default=str)
    elif name == "get_stock_levels":
        return json.dumps(tool_get_stock_levels(), default=str)
    elif name == "update_stock":
        return tool_update_stock(inputs["product_name"], inputs["quantity"],
                                  inputs["direction"], inputs.get("unit_price"))
    elif name == "save_invoice":
        return tool_save_invoice(inputs["extracted"])
    elif name == "save_pdf":
        return tool_save_pdf(inputs["tmp_path"], inputs["filename"])
    elif name == "embed_invoices":
        return tool_embed_invoices()
    elif name == "flag_for_review":
        return tool_flag_for_review(inputs["reason"], inputs.get("details", ""))
    elif name == "check_duplicate":
        return tool_check_duplicate(inputs["invoice_number"])
    else:
        return f"Unknown tool: {name}"


# ── Claude agent loop ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an inventory agent for California Nutraceuticals Inc, a raw material distributor.
Your job is to process invoice PDFs and keep the stock database accurate.

For each invoice PDF you must:
1. Save the PDF to disk for permanent storage
2. Extract the invoice data
3. Check for duplicates using the invoice number
4. Save the invoice to the database
5. Update stock levels — ADD for supplier invoices, SUBTRACT for customer invoices
   - Process each line item individually
6. Embed invoices into ChromaDB after all processing
7. Flag anything unusual for human review

Flag for review when:
- Invoice type cannot be determined
- Line items are missing or empty
- This is a duplicate invoice number
- A product being subtracted does not exist in stock
- Any data looks corrupt or incomplete

Be thorough. The accuracy of the inventory depends entirely on you.
"""


def run_agent_on_pdf(tmp_path: str, filename: str) -> str:
    """Run the Claude agent on a single PDF. Returns a summary of what was done."""
    messages = [{
        "role": "user",
        "content": (
            f"Process this invoice PDF completely.\n"
            f"Temp file path: {tmp_path}\n"
            f"Original filename: {filename}\n\n"
            f"Save the PDF, extract data, check for duplicates, save to database, "
            f"update stock for every line item, then embed. Flag anything unusual."
        )
    }]

    print(f"\n  Agent processing: {filename}")

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return " ".join(
                block.text for block in response.content if hasattr(block, "text")
            )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Agent completed."


# ── Main run ───────────────────────────────────────────────────────────────────

def run_once() -> str:
    """Check Gmail once and run the agent on each unread invoice email."""
    print("=" * 60)
    print("Invoice Agent — checking Gmail...")
    print("=" * 60)

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return "ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env"

    init_db()
    summaries = []

    try:
        mail = connect_gmail()
        print(f"Connected as {GMAIL_ADDRESS}")

        message_ids = fetch_unread_emails(mail)
        if not message_ids:
            print(f"No unread emails in '{GMAIL_LABEL}'.")
            mail.logout()
            return "No new emails found."

        print(f"Found {len(message_ids)} unread email(s)")

        for message_id in message_ids:
            pdf_files = download_pdfs(mail, message_id)
            if not pdf_files:
                print("  No PDFs, skipping.")
                mark_as_read(mail, message_id)
                continue

            for tmp_path, filename in pdf_files:
                try:
                    summary = run_agent_on_pdf(tmp_path, filename)
                    summaries.append(summary)
                except Exception as e:
                    err = f"Error on {filename}: {e}"
                    print(f"  [ERROR] {err}")
                    summaries.append(err)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            mark_as_read(mail, message_id)

        mail.logout()
        print("Done.")
        return "\n".join(summaries) if summaries else "Done — no output."

    except imaplib.IMAP4.error as e:
        return f"Gmail connection error: {e}"


# ── Background thread ──────────────────────────────────────────────────────────

_watch_thread  = None
_watch_running = False


def start_watch():
    global _watch_thread, _watch_running
    if _watch_running:
        return "Agent is already running."
    _watch_running = True

    def _loop():
        while _watch_running:
            try:
                run_once()
            except Exception as e:
                print(f"[ERROR] {e}")
            time.sleep(POLL_INTERVAL)

    _watch_thread = threading.Thread(target=_loop, daemon=True)
    _watch_thread.start()
    return f"Agent started — checking every {POLL_INTERVAL // 60} minutes."


def stop_watch():
    global _watch_running
    _watch_running = False
    return "Agent stopped."


def is_running():
    return _watch_running


# ── Chat query handler ─────────────────────────────────────────────────────────

def run_agent(query: str) -> str:
    """Handle a command from the Agent Control page."""
    q = query.lower()

    if any(w in q for w in ["check email", "check gmail", "fetch invoices",
                              "check inbox", "run agent", "re-index", "reindex"]):
        return run_once()

    try:
        rows = tool_get_stock_levels()
        if not rows:
            return "No stock data found."
        lines, alerts = [], []
        for r in rows:
            qty   = r["quantity_kg"]
            reord = r.get("reorder_at") or 0
            unit  = r.get("unit") or "kg"
            flag  = " ⚠ LOW" if qty < reord else ""
            lines.append(f"  {r['product_name']}: {qty}{unit}{flag}")
            if qty < reord:
                alerts.append(r["product_name"])
        summary = "\n".join(lines)
        if alerts:
            summary += f"\n\nAlert: {', '.join(alerts)} below reorder threshold."
        return summary
    except Exception as e:
        return f"Error: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--watch" in sys.argv:
        print(f"Watching '{GMAIL_LABEL}' every {POLL_INTERVAL // 60} minutes...")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[ERROR] {e}")
            time.sleep(POLL_INTERVAL)
    else:
        run_once()