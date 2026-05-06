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

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
USER_EMAIL         = os.getenv("USER_EMAIL", GMAIL_ADDRESS)   # who is allowed to send feedback
SMTP_SERVER        = "smtp.gmail.com"
SMTP_PORT          = 587

# At least one anchor must appear in the subject for a valid procurement reply
_SUBJECT_ANCHORS  = ("Reorder Suggestion", "RFQ", "Order Approval Required")
_QUOTE_ANCHORS    = ("Reorder Suggestion", "RFQ")
_APPROVAL_ANCHORS = ("Order Approval Required",)
_RUN_ID_PATTERN   = re.compile(r"\[RUN_ID=([^\]]+)\]")


# ── Send ───────────────────────────────────────────────────────────────────────
def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via Gmail SMTP."""
    test_email = os.getenv("TEST_EMAIL")
    if test_email:
        original_to = to
        to = test_email
        subject = "[TEST] " + subject
        body = f"Original recipient: {original_to}\n\n" + body

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
    return f"Reorder Suggestion - {item} [RUN_ID={run_id}]"


# ── Sender filtering ───────────────────────────────────────────────────────────

def get_allowed_emails():
    raw = os.getenv("ALLOWED_USER_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]

def extract_email(sender: str) -> str:
    """
    Extract email address from sender string.

    Examples:
    "John Doe <john@gmail.com>" → "john@gmail.com"
    "john@gmail.com" → "john@gmail.com"
    "john@gmail.com via something" → "john@gmail.com"
    """
    match = re.search(r'[\w\.-]+@[\w\.-]+', sender or "")
    return match.group(0).lower() if match else ""

def is_user_reply(sender: str) -> bool:
    """
    Return True only when the email came from the known user address.
    Keeps procurement feedback separated from supplier quote replies.
    """
    # return USER_EMAIL.lower() in sender.lower()
    sender_email = extract_email(sender)
    allowed = get_allowed_emails()
    return sender_email in allowed


# ── Fetch replies ──────────────────────────────────────────────────────────────

def _fetch_replies(anchors: tuple, require_user_reply: bool) -> list:
    from invoice_agent import connect_gmail
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

        sender = msg.get("From", "")

        if not any(a.lower() in subject.lower() for a in anchors):
            mail.store(mid, "-FLAGS", "\\Seen")
            continue

        run_id_match = _RUN_ID_PATTERN.search(subject)
        if not run_id_match:
            mail.store(mid, "-FLAGS", "\\Seen")
            continue

        if require_user_reply and not is_user_reply(sender):
            mail.store(mid, "-FLAGS", "\\Seen")
            continue

        run_id = run_id_match.group(1)
        body   = _extract_text_body(msg).strip()

        results.append({
            "run_id":     run_id,
            "message_id": mid,
            "subject":    subject,
            "sender":     sender,
            "body":       body,
        })

    mail.logout()
    return results


def fetch_quote_replies() -> list:
    """Fetch unread supplier RFQ replies. No sender filter — suppliers are allowed."""
    return _fetch_replies(_QUOTE_ANCHORS, require_user_reply=False)


def fetch_procurement_replies() -> list:
    """Fetch unread internal approval replies. Sender must be in ALLOWED_USER_EMAILS."""
    return _fetch_replies(_APPROVAL_ANCHORS, require_user_reply=True)


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

_VALID_ACTIONS = {"APPROVE", "REJECT", "CHANGE", "APPROVE ANYWAY", "STOP PURCHASE", "PROVIDE NEW QUOTE"}
# _FIELD_KEYS    = {"supplier", "email", "quantity", "reason"}
_FIELD_KEYS    = {"supplier", "quantity", "reason"}  
# Lines that signal the start of an email signature / quoted history
_STOP_PATTERNS = re.compile(
    r"^(--|__|\-\-\-|On .+ wrote:|From:|>)", re.IGNORECASE
)


def parse_reply(body: str) -> dict:
    """
    Parse a structured procurement reply.

    Accepted formats (case-insensitive first line):
        approve

        reject
        Reason: price too high

        change
        Supplier: Supplier Beta
        # Supplier: New Supplier Name
        # Email: supplier@example.com
        Quantity: 100
        Reason: redirecting to better supplier

    Robustness:
      - First-line action is case-insensitive.
      - Parsing stops at email signature / quoted-reply markers.
      - Extra unknown lines are silently ignored.
      - CHANGE with neither supplier nor quantity → action = "INVALID".

    Returns:
        {
            "action":   "APPROVE" | "REJECT" | "CHANGE" | "INVALID" | "UNKNOWN",
            "supplier": str | None,
            "email":    str | None,
            "quantity": float | None,
            "reason":   str | None,
        }
    """
    # result: dict = {"action": "UNKNOWN", "supplier": None, "email": None, "quantity": None, "reason": None}
    result: dict = {"action": "UNKNOWN", "supplier": None, "quantity": None, "reason": None}   

    lines = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        # Stop at signature / quoted-history markers
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
        # elif key == "email":
        #     result["email"] = value
        elif key == "quantity":
            try:
                result["quantity"] = float(value)
            except ValueError:
                pass
        elif key == "reason":
            result["reason"] = value

    # Validate CHANGE: at least supplier or quantity must be present
    if result["action"] == "CHANGE" and result["supplier"] is None and result["quantity"] is None:
        result["action"] = "INVALID"

    return result
