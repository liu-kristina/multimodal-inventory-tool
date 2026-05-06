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
import ast

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

def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0

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


def parse_invoice_json(text: str) -> dict:
    """Parse Claude's response into a dict, tolerating markdown fences and non-strict JSON."""
    # strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    # extract first { ... last }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass
    # repair duplicated leading quote on keys: ,""key": → ,"key":
    text = re.sub(r'([,{]\s*)""([A-Za-z_][A-Za-z0-9_]*"\s*:)', r'\1"\2', text)
    # fallback: quote unquoted keys
    repaired = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # fallback: multiple top-level objects missing outer array brackets
    wrapped = f"[{text}]"
    try:
        return json.loads(wrapped)
    except json.JSONDecodeError:
        pass
    wrapped_repaired = f"[{repaired}]"
    try:
        return json.loads(wrapped_repaired)
    except json.JSONDecodeError:
        pass
    with open("debug_last_model_output.txt", "w", encoding="utf-8") as f:
        f.write(text)
    raise ValueError(
        f"Could not parse model output as JSON. Length={len(text)}\n"
        f"First 1000 chars:\n{text[:1000]}\n\n"
        f"Last 1000 chars:\n{text[-1000:]}"
    )


_INVOICE_BOUNDARY = re.compile(
    r'(?=(?:Invoice\s*(?:Number|#|No\.?)|INVOICE|CUST-))',
    re.IGNORECASE,
)


def _split_invoice_chunks(text: str) -> list[str]:
    """Split text at invoice boundaries. Returns [text] unchanged if none found."""
    parts = _INVOICE_BOUNDARY.split(text)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks if len(chunks) > 1 else [text]


def _call_claude(chunk: str) -> list:
    """Send one chunk to Claude and return a flat list of invoice dicts."""
    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=8096,
        messages=[{
            "role": "user",
            "content": f"""Extract invoice data from the text below and return a JSON array.

Our company name is: "California Nutraceuticals Inc."

Each element must have these fields:
- invoice_number
- date
- vendor_name: the external company or counterparty (NOT our company unless it is a sales invoice)
- total_amount
- line_items (array of objects with: description, quantity, unit_price, total)
- payment_terms
- due_date
- document_type: one of "purchase" or "sales"

Task:

Determine the document_type based on the roles of the parties in the invoice.

Steps:
1. Identify the seller (the entity issuing the invoice).
2. Identify the buyer (the entity receiving the invoice).
3. Compare both with our company name: "California Nutraceuticals Inc."

Decision:
- If our company is the seller → document_type = "sales"
- If our company is the buyer → document_type = "purchase"
- If uncertain → default to "purchase"

Guidelines:
- Use fields such as "sold by", "bill from", "vendor", "supplier", "remit to" to identify the seller.
- Use fields such as "bill to", "ship to", "sold to" to identify the buyer.
- Do NOT rely on invoice number patterns.
- Focus on understanding roles, not matching keywords.

Important:
- vendor_name should not be "California Nutraceuticals Inc." for purchase invoices.

Output rules:
- Always return a JSON array, even for a single invoice
- No markdown, no explanation — only raw JSON

Invoice text:
{chunk}"""
        }]
    )
    parsed = parse_invoice_json(response.content[0].text.strip())
    return parsed if isinstance(parsed, list) else [parsed]


def extract_invoice_data(pdf_text: str) -> list:
    """Extract invoice data from PDF text, chunking by invoice boundary to avoid truncation."""
    chunks = _split_invoice_chunks(pdf_text)
    results = []
    for chunk in chunks:
        results.extend(_call_claude(chunk))
    return results


# ── ChromaDB storage ───────────────────────────────────────────────────────────

def _get_collection():
    """Return (or create) the invoices collection, using the Railway volume path."""
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return chroma_client.get_or_create_collection("invoices")


def store_invoice(collection, invoice_data: dict, email_subject: str, sender: str) -> str:
    """Store invoice data in ChromaDB and return the doc_id."""
    if isinstance(invoice_data, list):
        first_invoice = invoice_data[0] if invoice_data else {}
        all_invoices = invoice_data
    else:
        first_invoice = invoice_data
        all_invoices = [invoice_data]

    doc_id = str(uuid.uuid4())
    document_text = f"""
Invoice Number: {first_invoice.get('invoice_number', 'N/A')}
Date: {first_invoice.get('date', 'N/A')}
Vendor: {first_invoice.get('vendor_name', 'N/A')}
Total Amount: {first_invoice.get('total_amount', 'N/A')}
Payment Terms: {first_invoice.get('payment_terms', 'N/A')}
Due Date: {first_invoice.get('due_date', 'N/A')}
Line Items: {json.dumps([inv.get("line_items", []) for inv in all_invoices])}
""".strip()

    collection.add(
        documents=[document_text],
        metadatas=[{
            "invoice_number": str(first_invoice.get("invoice_number", "")),
            "vendor_name":    str(first_invoice.get("vendor_name", "")),
            "total_amount":   str(first_invoice.get("total_amount", "")),
            "date":           str(first_invoice.get("date", "")),
            "email_subject":  str(email_subject),
            "sender":         str(sender),
        }],
        ids=[doc_id],
    )
    return doc_id


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_invoice_message(message_dict: dict, forced_document_type: str | None = None) -> dict | None:
    """
    Process a single pre-fetched email message dict through the extraction pipeline.

    message_dict must contain: msg (email.message), subject, sender
    forced_document_type: when set, overrides the LLM-classified document_type.
      "sales"    → inventory decrease  (we are the seller)
      "purchase" → inventory increase  (we are the buyer)
    Returns a result dict or None on failure / no PDF.
    """
    msg     = message_dict.get("msg")
    subject = message_dict.get("subject", "")
    sender  = message_dict.get("sender", "")

    if msg is None:
        print(f"[invoice_agent] process_invoice_message: msg is None for subject={subject!r}")
        return None

    try:
        collection = _get_collection()
        pdf_text = extract_pdf_text(msg)
        if not pdf_text:
            return None

        invoice_data = extract_invoice_data(pdf_text)
        if isinstance(invoice_data, dict):
            invoice_data = [invoice_data]

        if forced_document_type:
            for inv in invoice_data:
                inv["document_type"] = forced_document_type

        if not invoice_data:
            return None

        doc_id = store_invoice(collection, invoice_data, subject, sender)
        return {
            "doc_id":         doc_id,
            "invoice_number": invoice_data[0].get("invoice_number") if invoice_data else None,
            "vendor":         invoice_data[0].get("vendor_name") if invoice_data else None,
            "amount":         sum(_to_float(inv.get("total_amount")) for inv in invoice_data),
            "invoice_data":   invoice_data,
        }
    except Exception as e:
        print(f"[invoice_agent] Error processing '{subject}': {e}")
        return None


def process_invoices() -> list[dict]:
    """Fetch, extract, and store all new invoice emails. Returns processed list."""
    collection = _get_collection()
    mail = connect_gmail()
    invoice_emails = fetch_invoice_emails(mail)
    print(f"[invoice_agent] Found {len(invoice_emails)} invoice email(s)")

    processed = []
    seen_invoice_numbers = set()
    for mid, msg in invoice_emails:
        subject = msg.get("Subject", "No Subject")
        sender  = msg.get("From", "Unknown")
        try:
            pdf_text = extract_pdf_text(msg)
            if not pdf_text:
                continue
            # print("RAW EXTRACTED:", pdf_text)
            invoice_data = extract_invoice_data(pdf_text)
            if isinstance(invoice_data, dict):
                invoice_data = [invoice_data]

            unique_invoice_data = []
            for inv in invoice_data:
                invoice_number = inv.get("invoice_number")
                if invoice_number and invoice_number in seen_invoice_numbers:
                    print(f"[invoice_agent] Skipping duplicate invoice: {invoice_number}")
                    continue
                if invoice_number:
                    seen_invoice_numbers.add(invoice_number)
                unique_invoice_data.append(inv)

            invoice_data = unique_invoice_data

            if not invoice_data:
                continue

            doc_id = store_invoice(collection, invoice_data, subject, sender)
            add_gmail_label(mail, mid, INVOICE_LABEL)
            mark_as_read(mail, mid)
            processed.append({
                "doc_id":         doc_id,
                "invoice_number": invoice_data[0].get("invoice_number") if invoice_data else None,
                "vendor":         invoice_data[0].get("vendor_name") if invoice_data else None,
                "amount":         sum(_to_float(inv.get("total_amount")) for inv in invoice_data),
                "invoice_data":   invoice_data,
            })
        except Exception as e:
            print(f"[invoice_agent] Error processing email '{subject}': {e}")
            processed.append({
                "doc_id": None,
                "invoice_number": None,
                "vendor": sender,
                "amount": 0,
                "invoice_data": [],
                "status": "failed_parse",
                "error": str(e),
            })
            continue

    mail.logout()
    return processed

# ── run_agent — called by agent_control command input ─────────────────────────

def run_agent(command: str) -> str:
    """
    Dispatch a plain-text command from the agent control page.
    Returns a human-readable result string.
    """
    from agents.procurement_agent import run_procurement_command

    cmd = command.lower().strip()

    # ── check gmail / run invoice agent ──
    if "gmail" in cmd or "invoice" in cmd:
        try:
            results = process_invoices()
            if not results:
                return "No new invoice emails found."
            lines = [f"• {r['invoice_number']} from {r['vendor']} — {r['amount']}" for r in results]
            return f"Processed {len(results)} invoice(s):\n" + "\n".join(lines)
        except Exception as e:
            return f"Error checking Gmail: {e}"

    procurement_result = run_procurement_command(command)
    if procurement_result:
        return procurement_result

    return (
        f"Unknown command: '{command}'. Try: check low stock, check gmail, "
        "check procurement replies, draft procurement emails, send procurement email <draft-id>."
    )


if __name__ == "__main__":
    results = process_invoices()
    print(f"Processed {len(results)} invoice(s):")
    for r in results:
        print(f"  - {r['invoice_number']} from {r['vendor']} for {r['amount']}")
