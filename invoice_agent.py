"""
Procurement Agent — monitors a Gmail label for invoice emails,
extracts PDFs, saves to SQLite, updates stock, and embeds to ChromaDB.

Setup:
    pip install imaplib2 python-dotenv

Add to your .env file:
    GMAIL_ADDRESS=your@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Run once manually:
    python invoice_agent.py

Run continuously (checks every 5 minutes):
    python invoice_agent.py --watch

Or control via the Agent Control page in the app.
"""

import imaplib
import email
import os
import sys
import time
import tempfile
import threading
import shutil
from email.header import decode_header
from pathlib import Path
from dotenv import load_dotenv

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
POLL_INTERVAL      = 300  # seconds (5 minutes — change to 86400 for production)
INVOICE_SAVE_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "invoices")


# ── Gmail connection ───────────────────────────────────────────────────────────

def connect():
    """Connect to Gmail via IMAP."""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    return mail


# ── Email processing ───────────────────────────────────────────────────────────

def get_unprocessed_emails(mail):
    """Fetch unread emails from the invoices label."""
    mail.select(f'"{GMAIL_LABEL}"')
    _, message_ids = mail.search(None, "UNSEEN")
    return message_ids[0].split() if message_ids[0] else []


def extract_pdfs_from_email(mail, message_id) -> list:
    """Download all PDF attachments from an email. Returns list of (tmp_path, filename) tuples."""
    _, msg_data = mail.fetch(message_id, "(RFC822)")
    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    subject = decode_header(msg["Subject"])[0][0]
    if isinstance(subject, bytes):
        subject = subject.decode()
    sender = msg.get("From", "unknown")
    print(f"  Processing: '{subject}' from {sender}")

    pdf_files = []
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            filename = part.get_filename()
            if filename:
                decoded = decode_header(filename)[0]
                if isinstance(decoded[0], bytes):
                    filename = decoded[0].decode(decoded[1] or "utf-8")

                tmp = tempfile.NamedTemporaryFile(
                    suffix=".pdf",
                    prefix=filename.replace(".pdf", "") + "_",
                    delete=False
                )
                tmp.write(part.get_payload(decode=True))
                tmp.close()
                pdf_files.append((tmp.name, filename))
                print(f"    Found PDF: {filename}")

    return pdf_files


def mark_as_read(mail, message_id):
    """Mark email as read after processing."""
    mail.store(message_id, "+FLAGS", "\\Seen")


# ── Save PDF to disk ───────────────────────────────────────────────────────────

def save_pdf_to_disk(tmp_path: str, filename: str):
    """Save a copy of the invoice PDF to data/invoices/."""
    os.makedirs(INVOICE_SAVE_DIR, exist_ok=True)
    dest = os.path.join(INVOICE_SAVE_DIR, filename)
    # Avoid overwriting — add timestamp suffix if file already exists
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        dest = os.path.join(INVOICE_SAVE_DIR, f"{base}_{int(time.time())}{ext}")
    shutil.copy2(tmp_path, dest)
    print(f"    Saved PDF to: data/invoices/{os.path.basename(dest)}")
    return dest


# ── Stock update ───────────────────────────────────────────────────────────────

def update_stock_from_invoice(extracted: dict):
    """
    Update stock levels from an invoice.
    Supplier invoices ADD stock, customer invoices SUBTRACT stock.
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
            # Incoming stock — add quantity
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
            # Outgoing stock — subtract quantity, floor at 0
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


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_email(mail, message_id):
    """Full pipeline for a single email: extract → save to disk → save to DB → update stock → embed."""
    pdf_files = extract_pdfs_from_email(mail, message_id)

    if not pdf_files:
        print("    No PDF attachments found, skipping.")
        mark_as_read(mail, message_id)
        return

    for tmp_path, filename in pdf_files:
        try:
            # Save a copy to data/invoices/
            save_pdf_to_disk(tmp_path, filename)

            # Extract invoice data using AI
            print(f"    Extracting invoice data...")
            extracted = extract_invoice(tmp_path)

            if extracted.get("invoice_type") == "unknown":
                print(f"    [WARN] Could not determine invoice type, skipping.")
                continue

            # Save to SQLite
            save_invoice(extracted)
            inv_num = extracted.get("invoice_number", "unknown")
            counterparty = (extracted.get("supplier_name") or
                            extracted.get("customer_name") or "unknown")
            print(f"    Saved invoice {inv_num} from {counterparty}")

            # Update stock if supplier invoice
            update_stock_from_invoice(extracted)

        except Exception as e:
            print(f"    [ERROR] Failed to process {filename}: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # Embed any new invoices into ChromaDB
    print(f"    Embedding new invoices into ChromaDB...")
    embed_new_invoices()

    # Mark email as read
    mark_as_read(mail, message_id)


def run_once():
    """Check Gmail once and process all unread invoices."""
    print("=" * 60)
    print("Invoice Agent — checking Gmail...")
    print("=" * 60)

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        sys.exit(1)

    init_db()

    try:
        mail = connect()
        print(f"Connected to Gmail as {GMAIL_ADDRESS}")

        message_ids = get_unprocessed_emails(mail)
        if not message_ids:
            print(f"No unread emails in '{GMAIL_LABEL}' label.")
            mail.logout()
            return

        print(f"Found {len(message_ids)} unread email(s) in '{GMAIL_LABEL}'")
        print()

        for message_id in message_ids:
            process_email(mail, message_id)
            print()

        mail.logout()
        print("Done.")

    except imaplib.IMAP4.error as e:
        print(f"Gmail connection error: {e}")
        print("Check your GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env")


def run_watch():
    """Poll Gmail every POLL_INTERVAL seconds (terminal mode)."""
    print(f"Invoice Agent watching '{GMAIL_LABEL}' every {POLL_INTERVAL // 60} minutes...")
    print("Press Ctrl+C to stop.")
    print()
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(POLL_INTERVAL)


# ── Background thread (controlled from app UI) ─────────────────────────────────

_watch_thread  = None
_watch_running = False


def start_watch():
    """Start the background polling thread (called from Agent Control page)."""
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
    """Stop the background polling thread (called from Agent Control page)."""
    global _watch_running
    _watch_running = False
    return "Agent stopped."


def is_running():
    """Returns True if the background thread is active."""
    return _watch_running


# ── Chat query handler ─────────────────────────────────────────────────────────

def run_agent(query: str) -> str:
    """
    Handle a chat query from the Agent Control page.
    Routes between Gmail polling and stock checks.
    """
    q = query.lower()

    # --- Gmail / re-index commands ---
    if any(w in q for w in ["check email", "check gmail", "re-index", "reindex",
                              "fetch invoices", "check inbox", "run agent"]):
        try:
            run_once()
            return "Done — checked Gmail and processed any new invoices."
        except Exception as e:
            return f"Error running Gmail check: {e}"

    # --- Stock / inventory questions ---
    try:
        conn = get_connection()
        rows = conn.execute("""
            SELECT product_name, quantity_kg, reorder_at, unit
            FROM stock ORDER BY quantity_kg ASC
        """).fetchall()
        conn.close()

        if not rows:
            return "No stock data found. Upload some invoices first."

        lines = []
        alerts = []
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
        return f"Error reading stock: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--watch" in sys.argv:
        run_watch()
    else:
        run_once()
