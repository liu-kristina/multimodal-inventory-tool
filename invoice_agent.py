"""
invoice_agent.py

Connects to Gmail via IMAP, fetches unread emails with PDF attachments,
extracts invoice data from PDFs using Claude AI,
and stores the results in a ChromaDB vector database.

Also exposes start_watch / stop_watch / is_running / run_agent
for the Dash agent-control page.
"""

import email
import imaplib
import io
import json
import os
import re
import threading
import time
import uuid

import chromadb
import pdfplumber
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
CHROMA_PATH        = os.getenv("CHROMA_PATH", "/app/chroma_db")
IMAP_SERVER        = "imap.gmail.com"
IMAP_PORT          = 993
POLL_INTERVAL      = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))  # 5 min default

anthropic_client = Anthropic()
INVOICE_LABEL = os.getenv("INVOICE_LABEL", "invoices")

_draft_store: dict[str, dict] = {}

SUPPLIER_EMAILS = {
    "Pacific Rim BioMaterials Co.": "jchen@pacificrimbiomaterials.com",
    "Jiaxing Natural Products Ltd": "lwang@jiaxingnatural.com",
    "Shanghai BioSupply International": "mzhou@shibiosupply.com",
    "Zhejiang Green Botanicals Corp": "sliu@zjgreenbotanicals.com",
    "Guangzhou Nutra Raw Materials Inc": "dliang@gznutraraw.com",
    "Jiaxing Supplier": "lwang@jiaxingnatural.com",
    "Alt Distributor": "sliu@zjgreenbotanicals.com",
}


# ── Filename helpers ───────────────────────────────────────────────────────────

def _clean(s: str, max_len: int = 30) -> str:
    """Remove special chars, replace spaces with hyphens, cap length."""
    s = str(s).strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s[:max_len].strip("-")


def _clean_date(date_str: str) -> str:
    """Sanitize date string without capping — preserves full year."""
    s = str(date_str).strip()
    s = re.sub(r"[^\w-]", "", s)
    return s


def build_canonical_filename(extracted: dict) -> str:
    """
    Build standardised filename from extracted invoice data.
    Convention: {type}_{counterparty}_{date}_{invoice_number}.pdf
    """
    inv_type = extracted.get("invoice_type", "unknown")
    counterparty = (
        extracted.get("supplier_name")
        or extracted.get("customer_name")
        or "unknown"
    )
    date = extracted.get("invoice_date", "nodate")
    inv_number = extracted.get("invoice_number", "noinv")

    return (
        f"{inv_type}"
        f"_{_clean(counterparty, max_len=30)}"
        f"_{_clean_date(date)}"
        f"_{_clean(inv_number, max_len=20)}"
        f".pdf"
    )

# ── Background watch thread ────────────────────────────────────────────────────

_watch_thread: threading.Thread | None = None
_stop_event = threading.Event()


def is_running() -> bool:
    return _watch_thread is not None and _watch_thread.is_alive()


def start_watch() -> str:
    global _watch_thread
    if is_running():
        return "Agent already running."
    _stop_event.clear()
    _watch_thread = threading.Thread(target=_watch_loop, daemon=True)
    _watch_thread.start()
    return "Agent started."


def stop_watch() -> str:
    _stop_event.set()
    return "Agent stopped."


def _watch_loop():
    while not _stop_event.is_set():
        try:
            process_invoices()
        except Exception as e:
            print(f"[invoice_agent] watch loop error: {e}")
        _stop_event.wait(timeout=POLL_INTERVAL)


# ── Gmail helpers ──────────────────────────────────────────────────────────────

def connect_gmail() -> imaplib.IMAP4_SSL:
    """Connect to Gmail via IMAP and return the mail object."""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    return mail


def mark_as_read(mail: imaplib.IMAP4_SSL, message_id: bytes) -> None:
    """Mark an email as read."""
    mail.store(message_id, "+FLAGS", "\\Seen")


def ensure_gmail_label(mail: imaplib.IMAP4_SSL, label: str) -> None:
    """Create a Gmail label if it does not already exist."""
    try:
        status, _ = mail.create(label)
        if status not in ("OK", "NO"):
            print(f"[invoice_agent] Warning: could not create label '{label}'")
    except Exception as e:
        print(f"[invoice_agent] Warning: could not ensure label '{label}': {e}")


def add_gmail_label(mail: imaplib.IMAP4_SSL, message_id: bytes, label: str) -> None:
    """Apply a Gmail label to a message."""
    try:
        ensure_gmail_label(mail, label)
        mail.store(message_id, "+X-GM-LABELS", f"({label})")
    except Exception as e:
        print(f"[invoice_agent] Warning: could not label message with '{label}': {e}")


def fetch_invoice_emails(mail: imaplib.IMAP4_SSL) -> list[tuple]:
    """Fetch unread emails that have PDF attachments."""
    mail.select("INBOX")
    _, message_ids = mail.search(None, "UNSEEN")
    ids = message_ids[0].split() if message_ids[0] else []

    invoice_emails = []
    for mid in ids:
        _, msg_data = mail.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        has_pdf = any(
            part.get_content_type() == "application/pdf"
            for part in msg.walk()
        )
        if has_pdf:
            invoice_emails.append((mid, msg))
    return invoice_emails


# ── PDF + Claude extraction ────────────────────────────────────────────────────

def extract_pdf_text(msg) -> str:
    """Extract text from all PDF attachments in an email."""
    texts = []
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            pdf_bytes = part.get_payload(decode=True)
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
    return "\n".join(texts)


def extract_invoice_data(pdf_text: str) -> dict:
    """Use Claude to extract structured invoice data from PDF text."""
    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"""Extract the following fields from this invoice text and return as JSON:
- invoice_number
- date
- vendor_name
- total_amount
- line_items (array of objects with: description, quantity, unit_price, total)
- payment_terms
- due_date

Invoice text:
{pdf_text}

Return only valid JSON, no other text."""
        }]
    )
    response_text = response.content[0].text.strip()
    response_text = re.sub(r"^```json\s*", "", response_text)
    response_text = re.sub(r"\s*```$", "", response_text)
    return json.loads(response_text)


# ── ChromaDB storage ───────────────────────────────────────────────────────────

def _get_collection():
    """Return (or create) the invoices collection, using the Railway volume path."""
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return chroma_client.get_or_create_collection("invoices")


def store_invoice(collection, invoice_data: dict, email_subject: str, sender: str) -> str:
    """Store invoice data in ChromaDB and return the doc_id."""
    doc_id = str(uuid.uuid4())
    document_text = f"""
Invoice Number: {invoice_data.get('invoice_number', 'N/A')}
Date: {invoice_data.get('date', 'N/A')}
Vendor: {invoice_data.get('vendor_name', 'N/A')}
Total Amount: {invoice_data.get('total_amount', 'N/A')}
Payment Terms: {invoice_data.get('payment_terms', 'N/A')}
Due Date: {invoice_data.get('due_date', 'N/A')}
Line Items: {json.dumps(invoice_data.get('line_items', []))}
""".strip()

    collection.add(
        documents=[document_text],
        metadatas=[{
            "invoice_number": str(invoice_data.get("invoice_number", "")),
            "vendor_name":    str(invoice_data.get("vendor_name", "")),
            "total_amount":   str(invoice_data.get("total_amount", "")),
            "date":           str(invoice_data.get("date", "")),
            "email_subject":  str(email_subject),
            "sender":         str(sender),
        }],
        ids=[doc_id],
    )
    return doc_id


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_invoices() -> list[dict]:
    """Fetch, extract, and store all new invoice emails. Returns processed list."""
    collection = _get_collection()
    mail = connect_gmail()
    invoice_emails = fetch_invoice_emails(mail)
    print(f"[invoice_agent] Found {len(invoice_emails)} invoice email(s)")

    processed = []
    for mid, msg in invoice_emails:
        subject = msg.get("Subject", "No Subject")
        sender  = msg.get("From", "Unknown")
        try:
            pdf_text = extract_pdf_text(msg)
            if not pdf_text:
                continue
            invoice_data = extract_invoice_data(pdf_text)
            doc_id = store_invoice(collection, invoice_data, subject, sender)
            add_gmail_label(mail, mid, INVOICE_LABEL)
            mark_as_read(mail, mid)
            processed.append({
                "doc_id":         doc_id,
                "invoice_number": invoice_data.get("invoice_number"),
                "vendor":         invoice_data.get("vendor_name"),
                "amount":         invoice_data.get("total_amount"),
            })
        except Exception as e:
            print(f"[invoice_agent] Error processing email '{subject}': {e}")

    mail.logout()
    return processed


def _lookup_supplier_email(supplier: str) -> str:
    return SUPPLIER_EMAILS.get(supplier, "")


def _build_procurement_draft(row: dict) -> dict:
    supplier = row["supplier"] or "Supplier"
    product = row["product_name"]
    qty = row["quantity_kg"]
    reorder_at = row["reorder_at"]
    unit = row["unit"] or "kg"
    suggested_qty = max(reorder_at * 2 - qty, reorder_at)
    supplier_email = _lookup_supplier_email(supplier)
    subject = f"Reorder Suggestion - {product}"
    body = (
        f"Hello {supplier},\n\n"
        f"We would like to place a reorder for {product}.\n"
        f"Our current stock is {qty} {unit}, with a reorder threshold of {reorder_at} {unit}.\n"
        f"Please confirm availability, lead time, and pricing for {suggested_qty:.0f} {unit}.\n\n"
        f"Best regards,\n"
        f"California Nutraceuticals"
    )
    draft_id = str(uuid.uuid4())[:8]
    draft = {
        "id": draft_id,
        "product_name": product,
        "supplier": supplier,
        "supplier_email": supplier_email,
        "subject": subject,
        "body": body,
    }
    _draft_store[draft_id] = draft
    return draft


def get_pending_drafts() -> list[dict]:
    """Return current in-memory procurement drafts."""
    return list(_draft_store.values())


def send_procurement_draft(draft_id: str) -> str:
    """Send a previously drafted procurement email."""
    from email_feedback_agent import send_email

    draft = _draft_store.get(draft_id)
    if not draft:
        return (
            f"Draft '{draft_id}' was not found. Drafts are kept in memory, "
            "so please generate the draft again before sending."
        )
    if not draft.get("supplier_email"):
        return (
            f"Draft '{draft_id}' has no supplier email address. "
            "Add a supplier email mapping before sending."
        )

    send_email(draft["supplier_email"], draft["subject"], draft["body"])
    del _draft_store[draft_id]
    return (
        f"Sent procurement email for {draft['product_name']} to "
        f"{draft['supplier']} <{draft['supplier_email']}>."
    )


def discard_procurement_draft(draft_id: str) -> str:
    """Discard a previously drafted procurement email."""
    draft = _draft_store.pop(draft_id, None)
    if not draft:
        return f"Draft '{draft_id}' was not found."
    return f"Discarded draft for {draft['product_name']}."


# ── run_agent — called by agent_control command input ─────────────────────────

def run_agent(command: str) -> str:
    """
    Dispatch a plain-text command from the agent control page.
    Returns a human-readable result string.
    """
    from database import get_connection, _execute  # local import avoids circular dep
    from email_feedback_agent import fetch_procurement_replies, parse_reply

    cmd = command.lower().strip()

    # ── check low stock ──
    if "low stock" in cmd:
        try:
            conn = get_connection()
            rows = _execute(
                conn,
                "SELECT product_name, quantity_kg, reorder_at FROM stock WHERE quantity_kg < reorder_at"
            ).fetchall()
            conn.close()
            if not rows:
                return "No low-stock items."
            lines = [f"• {r['product_name']}: {r['quantity_kg']} kg (reorder at {r['reorder_at']} kg)" for r in rows]
            return "Low stock items:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error checking stock: {e}"

    # ── check gmail / run invoice agent ──
    elif "gmail" in cmd or "invoice" in cmd:
        try:
            results = process_invoices()
            if not results:
                return "No new invoice emails found."
            lines = [f"• {r['invoice_number']} from {r['vendor']} — {r['amount']}" for r in results]
            return f"Processed {len(results)} invoice(s):\n" + "\n".join(lines)
        except Exception as e:
            return f"Error checking Gmail: {e}"

    # ── draft procurement email ──
    elif "draft" in cmd and ("procurement email" in cmd or "reorder email" in cmd):
        try:
            conn = get_connection()

            product_name = None
            match = re.search(r"(?:for|about)\s+(.+)$", command, re.IGNORECASE)
            if match:
                product_name = match.group(1).strip().rstrip(".!?")

            if product_name:
                row = _execute(
                    conn,
                    """
                    SELECT product_name, supplier, quantity_kg, reorder_at, unit
                    FROM stock
                    WHERE LOWER(product_name) = LOWER(?)
                    """,
                    (product_name,),
                ).fetchone()
                if not row:
                    conn.close()
                    return f"Could not find a stock item named '{product_name}'."
                rows = [row]
            else:
                rows = _execute(
                    conn,
                    """
                    SELECT product_name, supplier, quantity_kg, reorder_at, unit
                    FROM stock
                    WHERE quantity_kg < reorder_at
                    ORDER BY quantity_kg ASC
                    """
                ).fetchall()
                if not rows:
                    conn.close()
                    return "No low-stock items found to draft procurement emails for."

            conn.close()

            drafts = []
            for row in rows:
                draft = _build_procurement_draft(row)
                email_line = draft["supplier_email"] or "[missing supplier email]"
                drafts.append(
                    f"Draft ID: {draft['id']}\n"
                    f"To: {email_line}\n"
                    f"Subject: {draft['subject']}\n\n"
                    f"{draft['body']}"
                )

            return (
                "Drafted procurement emails for review. Nothing has been sent.\n"
                "To send one, run: send procurement email <draft-id>\n\n"
                + ("\n\n" + ("-" * 72) + "\n\n").join(drafts)
            )
        except Exception as e:
            return f"Error drafting procurement email: {e}"

    # ── send approved procurement email ──
    elif cmd.startswith("send procurement email"):
        try:
            match = re.search(r"send procurement email\s+([a-zA-Z0-9\-]+)$", command.strip(), re.IGNORECASE)
            if not match:
                return "Please specify a draft ID, for example: send procurement email abc12345"
            return send_procurement_draft(match.group(1))
        except Exception as e:
            return f"Error sending procurement email: {e}"

    # ── check procurement replies ──
    elif "procurement" in cmd or "repl" in cmd:
        try:
            replies = fetch_procurement_replies()
            if not replies:
                return "No procurement replies found."

            conn = get_connection()
            lines = []
            for r in replies:
                parsed = parse_reply(r["body"])
                action = parsed["action"]
                run_id = r["run_id"]

                detail = ""
                if parsed.get("reason"):
                    detail += f" · Reason: {parsed['reason']}"
                if parsed.get("supplier"):
                    detail += f" · Supplier: {parsed['supplier']}"
                if parsed.get("quantity"):
                    detail += f" · Qty: {parsed['quantity']}"
                lines.append(f"RUN_ID {run_id}: {action}{detail}")

                if action in ("REJECT", "CHANGE", "INVALID"):
                    _execute(
                        conn,
                        "INSERT INTO agent_flags (reason, details) VALUES (?, ?)",
                        (
                            f"Procurement reply: {action} [RUN_ID={run_id}]",
                            f"Supplier: {parsed.get('supplier')} · Reason: {parsed.get('reason')}",
                        ),
                    )
            conn.commit()
            conn.close()
            return "\n".join(lines)
        except Exception as e:
            return f"Error checking procurement replies: {e}"

    return (
        f"Unknown command: '{command}'. Try: check low stock, check gmail, "
        "check procurement replies, draft procurement emails, send procurement email <draft-id>."
    )


if __name__ == "__main__":
    results = process_invoices()
    print(f"Processed {len(results)} invoice(s):")
    for r in results:
        print(f"  - {r['invoice_number']} from {r['vendor']} for {r['amount']}")
