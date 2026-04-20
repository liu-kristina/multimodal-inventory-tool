"""
Invoice Agent — California Nutraceuticals
Automation pipeline for invoice processing.
Claude is only called when there is genuine ambiguity:
  - Unknown products that need matching
  - Price anomalies (>50% change from historical)

Normal clean invoices: ~$0.003 (extraction only)
Invoices with anomalies: ~$0.01-0.02 (extraction + one focused check)

Setup:
    pip install anthropic imaplib2 python-dotenv

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
import re
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

# ── Filename convention ────────────────────────────────────────────────────────

def _clean(s: str, max_len: int = 30) -> str:
    """Remove special chars, replace spaces with hyphens, cap length."""
    s = str(s).strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s[:max_len].strip("-")


def _clean_date(date_str: str) -> str:
    """Sanitize date string without capping — preserves full year e.g. 12-Nov-2026."""
    s = str(date_str).strip()
    s = re.sub(r"[^\w-]", "", s)
    return s


def build_canonical_filename(extracted: dict) -> str:
    """
    Build standardised filename from extracted invoice data.
    Convention: {type}_{counterparty}_{date}_{invoice_number}.pdf
    e.g. supplier_Jiaxing-Natural-Products-Ltd_12-Nov-2026_INV-0034.pdf
    """
    inv_type     = extracted.get("invoice_type", "unknown")
    counterparty = (extracted.get("supplier_name") or
                    extracted.get("customer_name") or "unknown")
    date         = extracted.get("invoice_date", "nodate")
    inv_number   = extracted.get("invoice_number", "noinv")

    return (
        f"{inv_type}"
        f"_{_clean(counterparty, max_len=30)}"
        f"_{_clean_date(date)}"
        f"_{_clean(inv_number, max_len=20)}"
        f".pdf"
    )


# ── Config ─────────────────────────────────────────────────────────────────────

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_LABEL        = "invoices"
IMAP_SERVER        = "imap.gmail.com"
POLL_INTERVAL      = 300        # seconds — change to 86400 for production
PRICE_ANOMALY_PCT  = 0.50       # flag if price changes by more than 50%
INVOICE_SAVE_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "invoices")

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


# ── Free checks (no Claude) ────────────────────────────────────────────────────

def is_duplicate(invoice_number: str) -> bool:
    """Check if invoice number already exists in the database."""
    if not invoice_number or invoice_number.startswith("unknown_"):
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = ?",
            (invoice_number,)
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def get_price_anomalies(extracted: dict) -> list:
    """
    Compare invoice line item prices against historical prices in stock table.
    Returns list of anomaly dicts for any item with >PRICE_ANOMALY_PCT change.
    Free — no Claude call needed.
    """
    anomalies = []
    conn = get_connection()
    for item in extracted.get("line_items", []):
        product = item.get("product", "")
        invoice_price = float(item.get("unit_price", 0))
        if not product or invoice_price <= 0:
            continue
        row = conn.execute(
            "SELECT unit_price FROM stock WHERE product_name = ?", (product,)
        ).fetchone()
        if row and row["unit_price"] and row["unit_price"] > 0:
            ratio = invoice_price / row["unit_price"]
            if ratio > (1 + PRICE_ANOMALY_PCT) or ratio < (1 - PRICE_ANOMALY_PCT):
                anomalies.append({
                    "product": product,
                    "historical_price": row["unit_price"],
                    "invoice_price": invoice_price,
                    "change_pct": round((ratio - 1) * 100, 1),
                })
    conn.close()
    return anomalies


def get_unknown_products(extracted: dict) -> list:
    """Return line items flagged as unknown products by the extractor."""
    return [
        item.get("product", "")
        for item in extracted.get("line_items", [])
        if item.get("is_unknown_product")
    ]


# ── Stock update (free, no Claude) ────────────────────────────────────────────

def update_stock(extracted: dict):
    """
    Update stock levels from an invoice.
    Supplier invoices ADD stock, customer invoices SUBTRACT stock.
    No Claude call — pure automation.
    """
    invoice_type = extracted.get("invoice_type")
    if invoice_type not in ("supplier", "customer"):
        return

    conn = get_connection()
    updated = []

    for item in extracted.get("line_items", []):
        product_name = item.get("product", "")
        quantity = float(item.get("quantity", 0))
        unit_price = float(item.get("unit_price", 0))

        if not product_name or quantity <= 0:
            continue

        if invoice_type == "supplier":
            conn.execute("""
                INSERT INTO stock (product_name, quantity_kg, unit_price, last_updated)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(product_name) DO UPDATE SET
                    quantity_kg = quantity_kg + excluded.quantity_kg,
                    unit_price = excluded.unit_price,
                    last_updated = CURRENT_TIMESTAMP
            """, (product_name, quantity, unit_price))
            updated.append(f"{product_name} +{quantity}kg")

        elif invoice_type == "customer":
            conn.execute("""
                INSERT INTO stock (product_name, quantity_kg, last_updated)
                VALUES (?, MAX(0, -?), CURRENT_TIMESTAMP)
                ON CONFLICT(product_name) DO UPDATE SET
                    quantity_kg = MAX(0, quantity_kg - excluded.quantity_kg),
                    last_updated = CURRENT_TIMESTAMP
            """, (product_name, quantity))
            updated.append(f"{product_name} -{quantity}kg")

    conn.commit()
    conn.close()

    if updated:
        print(f"    Stock updated: {', '.join(updated)}")


# ── Flag for review (free) ─────────────────────────────────────────────────────

def flag_for_review(reason: str, details: str = ""):
    """Log an item to agent_flags table for display in Agent Control page."""
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
    print(f"    Flagged: {reason}")


# ── Save PDF to disk (free) ────────────────────────────────────────────────────

def save_pdf_to_disk(tmp_path: str, filename: str, extracted: dict = None) -> str:
    """
    Save PDF to disk using the canonical naming convention if extracted data
    is provided, otherwise fall back to the original filename.
    Returns the final saved path.
    """
    os.makedirs(INVOICE_SAVE_DIR, exist_ok=True)

    if extracted:
        canonical = build_canonical_filename(extracted)
        if canonical != filename:
            print(f"    Renaming: {filename} → {canonical}")
        filename = canonical

    dest = os.path.join(INVOICE_SAVE_DIR, filename)
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        dest = os.path.join(INVOICE_SAVE_DIR, f"{base}_{int(time.time())}{ext}")

    shutil.copy2(tmp_path, dest)
    print(f"    Saved PDF: data/invoices/{os.path.basename(dest)}")
    return dest


# ── Claude anomaly check (only called when needed) ────────────────────────────

ANOMALY_PROMPT = """You are reviewing an invoice for California Nutraceuticals.
The automated system flagged the following issues. Assess each one and decide:
1. Is this a real problem or likely a typo/rounding/currency difference?
2. Should it be flagged for human review or is it safe to proceed?

Return ONLY valid JSON — no preamble, no markdown:
{{
  "safe_to_proceed": true or false,
  "flags": [
    {{"issue": "short description", "severity": "low/medium/high", "recommendation": "what to do"}}
  ]
}}

Invoice number: {invoice_number}
Invoice type: {invoice_type}
Counterparty: {counterparty}

Issues found:
{issues}
"""


def claude_anomaly_check(extracted: dict, anomalies: list, unknown_products: list) -> dict:
    """
    Single focused Claude call — only runs when anomalies or unknown products exist.
    Returns dict with safe_to_proceed bool and list of flags.
    """
    issues = []
    if unknown_products:
        issues.append(f"Unknown products not in database: {', '.join(unknown_products)}")
    for a in anomalies:
        direction = "increase" if a["change_pct"] > 0 else "decrease"
        issues.append(
            f"{a['product']}: price {direction} of {abs(a['change_pct'])}% "
            f"(was ${a['historical_price']}/kg, now ${a['invoice_price']}/kg)"
        )

    prompt = ANOMALY_PROMPT.format(
        invoice_number=extracted.get("invoice_number", "unknown"),
        invoice_type=extracted.get("invoice_type", "unknown"),
        counterparty=extracted.get("supplier_name") or extracted.get("customer_name") or "unknown",
        issues="\n".join(f"- {i}" for i in issues),
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model — sufficient for this task
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── Main invoice pipeline ──────────────────────────────────────────────────────

def process_invoice(tmp_path: str, filename: str):
    """
    Full pipeline for one invoice PDF.
    Claude is only called if unknown products or price anomalies are detected.
    """
    print(f"\n  Processing: {filename}")

    # 1. Extract invoice data first — needed for canonical filename
    print(f"    Extracting invoice data...")
    extracted = extract_invoice(tmp_path)

    # 2. Save PDF to disk with canonical filename — free
    save_pdf_to_disk(tmp_path, filename, extracted=extracted)

    invoice_number = extracted.get("invoice_number", "unknown")
    invoice_type   = extracted.get("invoice_type", "unknown")

    # 3. Check invoice type — free
    if invoice_type == "unknown":
        flag_for_review(
            f"Unknown invoice type: {filename}",
            "Could not determine if supplier or customer invoice."
        )
        return

    # 4. Check for duplicate — free
    if is_duplicate(invoice_number):
        flag_for_review(
            f"Duplicate invoice: {invoice_number}",
            f"Invoice {invoice_number} from {filename} already exists in database."
        )
        return

    # 5. Check for anomalies — free
    anomalies        = get_price_anomalies(extracted)
    unknown_products = get_unknown_products(extracted)

    # 6. Claude anomaly check — only if needed
    if anomalies or unknown_products:
        print(f"    Anomalies detected — running Claude check...")
        try:
            result = claude_anomaly_check(extracted, anomalies, unknown_products)

            # Log each flag from Claude
            for f in result.get("flags", []):
                flag_for_review(
                    f"{f['severity'].upper()}: {f['issue']}",
                    f"Recommendation: {f['recommendation']} (Invoice: {invoice_number})"
                )

            # If Claude says not safe to proceed, stop here
            if not result.get("safe_to_proceed", True):
                print(f"    Claude flagged invoice {invoice_number} — skipping stock update.")
                save_invoice(extracted)  # save record but don't update stock
                return

        except Exception as e:
            # If Claude check fails, flag it and continue cautiously
            flag_for_review(
                f"Anomaly check failed: {invoice_number}",
                f"Error: {e}. Manual review recommended."
            )

    # 7. Save invoice to SQLite — free
    save_invoice(extracted)
    counterparty = extracted.get("supplier_name") or extracted.get("customer_name") or "unknown"
    print(f"    Saved invoice {invoice_number} from {counterparty}")

    # 8. Update stock — free
    update_stock(extracted)

    # 9. Embed into ChromaDB — cheap
    print(f"    Embedding into ChromaDB...")
    embed_new_invoices()

    print(f"    Done.")


# ── Gmail run ──────────────────────────────────────────────────────────────────

def run_once() -> str:
    """Check Gmail once and process all unread invoice emails."""
    print("=" * 60)
    print("Invoice Agent — checking Gmail...")
    print("=" * 60)

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return "ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env"

    init_db()
    processed = 0
    errors    = 0

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
                print("  No PDFs found, skipping.")
                mark_as_read(mail, message_id)
                continue

            for tmp_path, filename in pdf_files:
                try:
                    process_invoice(tmp_path, filename)
                    processed += 1
                except Exception as e:
                    print(f"  [ERROR] {filename}: {e}")
                    errors += 1
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            mark_as_read(mail, message_id)

        mail.logout()
        print("=" * 60)
        print(f"Done. {processed} processed, {errors} errors.")
        return f"Done — {processed} invoice(s) processed, {errors} error(s)."

    except imaplib.IMAP4.error as e:
        return f"Gmail connection error: {e}"


# ── Background thread (controlled from app UI) ─────────────────────────────────

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

    # Default — return stock levels
    try:
        conn = get_connection()
        rows = conn.execute("""
            SELECT product_name, quantity_kg, reorder_at, unit
            FROM stock ORDER BY quantity_kg ASC
        """).fetchall()
        conn.close()

        if not rows:
            return "No stock data found."

        lines, alerts = [], []
        for r in rows:
            qty   = r["quantity_kg"]
            reord = r["reorder_at"] or 0
            unit  = r["unit"] or "kg"
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