"""
email_feedback_agent.py

Reads Gmail replies to procurement recommendation emails,
parses structured commands (APPROVE / REJECT / CHANGE),
and sends emails via Gmail SMTP.

Reuses connect_gmail() and mark_as_read() from invoice_agent.py.
"""

import email
import os
import re
import smtplib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from invoice_agent import connect_gmail, mark_as_read

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SMTP_SERVER        = "smtp.gmail.com"
SMTP_PORT          = 587

_SUBJECT_ANCHOR = "Reorder Suggestion"
_RUN_ID_PATTERN = re.compile(r"\[RUN_ID=([^\]]+)\]")
_VALID_ACTIONS  = {"APPROVE", "REJECT", "CHANGE"}
_FIELD_KEYS     = {"supplier", "quantity", "reason"}
_STOP_PATTERNS  = re.compile(
    r"^(--|__|\-\-\-|On .+ wrote:|From:|>)", re.IGNORECASE
)


# ── Send ───────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via Gmail SMTP."""
    msg = MIMEMultipart()
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to, msg.as_string())


def build_recommendation_subject(item: str, run_id: str) -> str:
    """
    Return a subject that embeds a correlation token so replies can be
    matched back to a specific procurement run.

    Example: "Reorder Suggestion – Widget A [RUN_ID=42]"
    """
    return f"{_SUBJECT_ANCHOR} – {item} [RUN_ID={run_id}]"


# ── Sender filtering ───────────────────────────────────────────────────────────

def get_allowed_emails() -> list[str]:
    raw = os.getenv("ALLOWED_USER_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def extract_email(sender: str) -> str:
    """
    Extract bare email address from a sender string.
    "John Doe <john@gmail.com>" → "john@gmail.com"
    """
    match = re.search(r"[\w\.\-]+@[\w\.\-]+", sender or "")
    return match.group(0).lower() if match else ""


def is_user_reply(sender: str) -> bool:
    """Return True only when sender is in the ALLOWED_USER_EMAILS list."""
    return extract_email(sender) in get_allowed_emails()


# ── Fetch replies ──────────────────────────────────────────────────────────────

def fetch_procurement_replies() -> list[dict]:
    """
    Search INBOX for unread procurement feedback emails:
      - subject contains "Reorder Suggestion"
      - subject contains "[RUN_ID=..."
      - sender is in ALLOWED_USER_EMAILS

    Marks matched emails as read.

    Returns list of dicts:
        { "run_id", "message_id", "subject", "sender", "body" }
    """
    mail = connect_gmail()
    mail.select("INBOX")

    _, message_ids = mail.search(None, "UNSEEN")
    ids = message_ids[0].split() if message_ids[0] else []

    results = []
    for mid in ids:
        _, msg_data = mail.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        raw_subject = decode_header(msg["Subject"])[0][0]
        subject = raw_subject.decode() if isinstance(raw_subject, bytes) else (raw_subject or "")
        sender  = msg.get("From", "")

        # All three guards must pass
        if _SUBJECT_ANCHOR.lower() not in subject.lower():
            continue
        run_id_match = _RUN_ID_PATTERN.search(subject)
        if not run_id_match:
            continue
        if not is_user_reply(sender):
            continue

        body = _extract_text_body(msg).strip()
        mark_as_read(mail, mid)

        results.append({
            "run_id":     run_id_match.group(1),
            "message_id": mid,
            "subject":    subject,
            "sender":     sender,
            "body":       body,
        })

    mail.logout()
    return results


def _extract_text_body(msg) -> str:
    """Return the plain-text body of an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                return payload.decode("utf-8", errors="ignore") if payload else ""
    else:
        payload = msg.get_payload(decode=True)
        return payload.decode("utf-8", errors="ignore") if payload else ""
    return ""


# ── Parse reply ────────────────────────────────────────────────────────────────

def parse_reply(body: str) -> dict:
    """
    Parse a structured procurement reply.

    Accepted formats (case-insensitive first line):
        approve

        reject
        Reason: price too high

        change
        Supplier: Supplier Beta
        Quantity: 100
        Reason: Alpha quality unstable

    Returns:
        {
            "action":   "APPROVE" | "REJECT" | "CHANGE" | "INVALID" | "UNKNOWN",
            "supplier": str | None,
            "quantity": float | None,
            "reason":   str | None,
        }
    """
    result: dict = {"action": "UNKNOWN", "supplier": None, "quantity": None, "reason": None}

    lines = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _STOP_PATTERNS.match(stripped):
            break
        lines.append(stripped)

    if not lines:
        return result

    action = lines[0].upper()
    result["action"] = action if action in _VALID_ACTIONS else "UNKNOWN"

    for line in lines[1:]:
        key, _, value = line.partition(":")
        key   = key.strip().lower()
        value = value.strip()
        if key not in _FIELD_KEYS or not value:
            continue
        if key == "supplier":
            result["supplier"] = value
        elif key == "quantity":
            try:
                result["quantity"] = float(value)
            except ValueError:
                pass
        elif key == "reason":
            result["reason"] = value

    # CHANGE requires at least supplier or quantity
    if result["action"] == "CHANGE" and result["supplier"] is None and result["quantity"] is None:
        result["action"] = "INVALID"

    return result
