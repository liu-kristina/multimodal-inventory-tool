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
"""

import imaplib
import email
import os
import sys
import time
import tempfile
from email.header import decode_header
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import init_db, save_invoice, get_connection
from pipeline.pdf_extractor import extract_invoice
from pipeline.generate_embeddings import embed_new_invoices

# ── Config ─────────────────────────────────────────────────────────────────────

GMAIL_ADDRESS     = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_LABEL       = "invoices"
IMAP_SERVER       = "imap.gmail.com"
POLL_INTERVAL     = 86400  # seconds (24 hours)


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
    """Download all PDF attachments from an email. Returns list of temp file paths."""
    _, msg_data = mail.fetch(message_id, "(RFC822)")
    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    subject = decode_header(msg["Subject"])[0][0]
    if isinstance(subject, bytes):
        subject = subject.decode()
    sender = msg.get("From", "unknown")
    print(f"  Processing: '{subject}' from {sender}")

    pdf_paths = []
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            filename = part.get_filename()
            if filename:
                # Decode filename if needed
                decoded = decode_header(filename)[0]
                if isinstance(decoded[0], bytes):
                    filename = decoded[0].decode(decoded[1] or "utf-8")

                # Save to temp file
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".pdf",
                    prefix=filename.replace(".pdf", "") + "_",
                    delete=False
                )
                tmp.write(part.get_payload(decode=True))
                tmp.close()
                pdf_paths.append(tmp.name)
                print(f"    Found PDF: {filename}")

    return pdf_paths


def mark_as_read(mail, message_id):
    """Mark email as read after processing."""
    mail.store(message_id, "+FLAGS", "\\Seen")


# ── Stock update ───────────────────────────────────────────────────────────────

def update_stock_from_invoice(extracted: dict):
    """
    Update stock levels from a supplier invoice.
    Adds quantity from each line item to the stock table.
    """
    if extracted.get("invoice_type") != "supplier":
        return

    conn = get_connection()
    updated = []
    for item in extracted.get("line_items", []):
        product_name = item.get("product", "")
        quantity = float(item.get("quantity", 0))
        unit_price = float(item.get("unit_price", 0))

        if not product_name or quantity <= 0:
            continue

        # Update stock if product exists, insert if not
        conn.execute("""
            INSERT INTO stock (product_name, quantity_kg, unit_price, last_updated)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(product_name) DO UPDATE SET
                quantity_kg = quantity_kg + excluded.quantity_kg,
                unit_price = excluded.unit_price,
                last_updated = CURRENT_TIMESTAMP
        """, (product_name, quantity, unit_price))
        updated.append(f"{product_name} +{quantity}kg")

    conn.commit()
    conn.close()

    if updated:
        print(f"    Stock updated: {', '.join(updated)}")


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_email(mail, message_id):
    """Full pipeline for a single email: extract → save → update stock → embed."""
    pdf_paths = extract_pdfs_from_email(mail, message_id)

    if not pdf_paths:
        print("    No PDF attachments found, skipping.")
        mark_as_read(mail, message_id)
        return

    for pdf_path in pdf_paths:
        try:
            # Extract invoice data using AI
            print(f"    Extracting invoice data...")
            extracted = extract_invoice(pdf_path)

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
            print(f"    [ERROR] Failed to process {pdf_path}: {e}")
        finally:
            # Clean up temp file
            try:
                os.unlink(pdf_path)
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
    """Poll Gmail every POLL_INTERVAL seconds."""
    print(f"Invoice Agent watching '{GMAIL_LABEL}' every {POLL_INTERVAL // 60} minutes...")
    print("Press Ctrl+C to stop.")
    print()
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(POLL_INTERVAL)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--watch" in sys.argv:
        run_watch()
    else:
        run_once()
