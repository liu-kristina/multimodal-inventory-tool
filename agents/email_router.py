"""
email_router.py

Gmail label-based email routing.

Replaces subject-anchor-based fetching with label-folder fetching so each
email type lives in a dedicated Gmail label and is routed to the correct agent.

Supported labels -> handlers:
    sales/invoice        -> invoice_agent  (document_type forced to "sales")
    purchase/receipt     -> invoice_agent  (document_type forced to "purchase")
    procurement/quote    -> quote parser   (_process_quote_reply in cli.py)
    procurement/approval -> approval flow  (_process_approval_reply in cli.py)
"""

from __future__ import annotations

import email as email_lib
import imaplib
import re
from email.header import decode_header

_RUN_ID_PATTERN = re.compile(r"\[RUN_ID=([^\]]+)\]")

# Actions that indicate an internal approval reply, not a supplier quote.
# Mirrors _VALID_ACTIONS in email_feedback_agent.py — kept local to avoid
# importing that module here (it pulls in invoice_agent / pdfplumber).
_APPROVAL_ACTIONS = frozenset({
    "APPROVE", "REJECT", "APPROVE ANYWAY", "STOP PURCHASE", "PROVIDE NEW QUOTE", "CHANGE",
})

# Matches email signature / quoted-history markers (same pattern as email_feedback_agent.py)
_BODY_STOP_RE = re.compile(r"^(--|__|\-\-\-|On .+ wrote:|From:|>)", re.IGNORECASE)


def _detect_approval_action(body: str) -> str | None:
    """Return the uppercased first meaningful line if it is an approval action, else None."""
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _BODY_STOP_RE.match(stripped):
            break
        action = stripped.upper()
        return action if action in _APPROVAL_ACTIONS else None
    return None

SUPPORTED_LABELS: list[str] = [
    "sales/invoice",
    "purchase/receipt",
    "procurement/quote",
    "procurement/approval",
]


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_unread_by_label(mail: imaplib.IMAP4_SSL, label: str) -> list[dict]:
    """
    Select a Gmail IMAP label folder and return all unread messages as dicts.
    Returns [] if the label does not exist or is empty.

    Returned dict keys:
        mid        – raw bytes message-ID used by mail.store / mark_as_read
        message_id – str version of mid (used for DB dedup)
        msg        – email.message.Message object (for PDF extraction)
        subject    – str
        sender     – str
        body       – plain-text body str
        run_id     – str extracted from [RUN_ID=...] in subject, or None
    """
    status, _ = mail.select(f'"{label}"')
    if status != "OK":
        print(f"[ROUTING] Label '{label}' not found in Gmail — skipping")
        return []

    _, message_ids = mail.search(None, "UNSEEN")
    ids = message_ids[0].split() if message_ids[0] else []
    if not ids:
        return []

    results = []
    for mid in ids:
        _, msg_data = mail.fetch(mid, "(RFC822)")
        msg = email_lib.message_from_bytes(msg_data[0][1])

        raw_subject = decode_header(msg.get("Subject", ""))[0][0]
        subject = raw_subject.decode() if isinstance(raw_subject, bytes) else (raw_subject or "")
        sender  = msg.get("From", "")

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    body = payload.decode("utf-8", errors="ignore") if payload else ""
                    break
        else:
            payload = msg.get_payload(decode=True)
            body = payload.decode("utf-8", errors="ignore") if payload else ""

        run_id_match = _RUN_ID_PATTERN.search(subject)
        run_id     = run_id_match.group(1) if run_id_match else None
        message_id = mid.decode() if isinstance(mid, bytes) else str(mid)

        results.append({
            "mid":        mid,
            "message_id": message_id,
            "msg":        msg,
            "subject":    subject,
            "sender":     sender,
            "body":       body,
            "run_id":     run_id,
        })

    return results


# ── Route ──────────────────────────────────────────────────────────────────────

def route_email_by_label(
    label: str,
    message: dict,
    mark_done_fn=None,
) -> None:
    """
    Dispatch one message to the correct handler based on its Gmail label.

    Content-based routing takes priority: if the email body parses as an
    approval action (APPROVE, REJECT, etc.) the message is sent to the
    approval handler regardless of its Gmail label. This handles the common
    case where an approval reply inherits the procurement/quote thread label.

    mark_done_fn(message_id: str) is passed to procurement handlers so they
    can mark the message processed and read inside their own flow.
    For invoice labels, the caller (run_label_routing) handles mark-as-read.
    """
    _noop = lambda _: None
    mark_done = mark_done_fn or _noop

    # Content-based routing priority — check body before label dispatch.
    # Handles the case where an approval reply inherits the procurement/quote
    # Gmail label from the thread, which would otherwise misroute it.
    action = _detect_approval_action(message.get("body", ""))
    if action:
        print(f"[ROUTING] body action detected: {action} -> approval handler")
        try:
            from cli import _process_approval_reply
            _process_approval_reply(message, mark_done)
        except ImportError as exc:
            print(f"[ROUTING] procurement/approval: handler unavailable ({exc})")
        return

    if label == "sales/invoice":
        print(
            f"[ROUTING] label=sales/invoice -> invoice_agent (sales)"
            f"  subject={message.get('subject', '')!r}"
        )
        _route_invoice(message, document_type="sales")

    elif label == "purchase/receipt":
        print(
            f"[ROUTING] label=purchase/receipt -> invoice_agent (purchase)"
            f"  subject={message.get('subject', '')!r}"
        )
        _route_invoice(message, document_type="purchase")

    elif label == "procurement/quote":
        print(
            f"[ROUTING] label=procurement/quote -> quote parser"
            f"  run_id={message.get('run_id')}"
        )
        try:
            from cli import _process_quote_reply
            _process_quote_reply(message, mark_done)
        except ImportError as exc:
            print(f"[ROUTING] procurement/quote: handler unavailable ({exc})")

    elif label == "procurement/approval":
        print(
            f"[ROUTING] label=procurement/approval -> approval handler"
            f"  run_id={message.get('run_id')}"
        )
        try:
            from cli import _process_approval_reply
            _process_approval_reply(message, mark_done)
        except ImportError as exc:
            print(f"[ROUTING] procurement/approval: handler unavailable ({exc})")

    else:
        print(f"[ROUTING] label={label!r} — no handler registered, skipping")


def _route_invoice(message: dict, document_type: str) -> None:
    """
    Process a single invoice email via invoice_agent.
    Forces document_type from the label instead of relying on LLM classification.
    Then runs the inventory pipeline on the extracted data.
    """
    try:
        from agents.invoice_agent import process_invoice_message
    except ImportError:
        print(f"[ROUTING] invoice: invoice_agent unavailable (simulation mode) — skipping")
        return
    try:
        from scripts.run_pipeline import run_pipeline_from_gmail_invoices
    except ImportError:
        run_pipeline_from_gmail_invoices = None

    try:
        result = process_invoice_message(message, forced_document_type=document_type)
    except Exception as exc:
        print(f"[ROUTING] invoice: extraction error ({exc!r}) — skipping")
        result = None
    if result and result.get("invoice_data"):
        print(
            f"[ROUTING] invoice extracted: #{result.get('invoice_number')} "
            f"from {result.get('vendor')} — ${result.get('amount', 0):.2f}"
        )
        if run_pipeline_from_gmail_invoices:
            run_pipeline_from_gmail_invoices(result["invoice_data"])
    else:
        print(f"[ROUTING] invoice: no data extracted from {message.get('subject', '')!r}")


# ── Main entry ─────────────────────────────────────────────────────────────────

def run_label_routing() -> None:
    """
    Connect to Gmail, iterate every supported label, and route unread messages.

    - Procurement labels: mark_done is called inside the handler (DB + read flag).
    - Invoice labels: marked read by this function after routing returns.
    """
    from database import mark_message_processed
    from agents.invoice_agent import connect_gmail, mark_as_read, add_gmail_label, INVOICE_LABEL

    mail = connect_gmail()

    def _make_mark_done(current_label: str):
        def _mark_done(message_id: str) -> None:
            mark_message_processed(message_id, label=current_label)
            mail.select(f'"{current_label}"')
            mid_bytes = message_id.encode() if isinstance(message_id, str) else message_id
            mark_as_read(mail, mid_bytes)
        return _mark_done

    for label in SUPPORTED_LABELS:
        messages = fetch_unread_by_label(mail, label)
        if not messages:
            continue

        print(f"[ROUTING] {len(messages)} unread message(s) in label '{label}'")
        mark_done = _make_mark_done(label)

        for msg_dict in messages:
            route_email_by_label(label, msg_dict, mark_done_fn=mark_done)

            # Invoice handlers don't call mark_done; handle here
            if label in ("sales/invoice", "purchase/receipt"):
                mail.select(f'"{label}"')
                add_gmail_label(mail, msg_dict["mid"], INVOICE_LABEL)
                mark_as_read(mail, msg_dict["mid"])
                mark_message_processed(msg_dict["message_id"], label=label)

    mail.logout()


# ── Simulate (test without Gmail) ──────────────────────────────────────────────

def simulate_label_routing(label: str, mock_message: dict) -> None:
    """
    Route a mock message without a real Gmail connection.
    Uses a no-op mark_done so no DB writes occur for test messages.
    """
    route_email_by_label(label, mock_message, mark_done_fn=lambda _: None)