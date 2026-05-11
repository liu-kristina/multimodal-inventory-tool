"""
slack_notifier.py — Slim procurement approval reminder for Slack.

Approval emails remain the source of truth.  Slack is a secondary
notification channel only — no approval buttons, no reply parsing,
no database access.

Configuration (all via env vars — no hardcoded values):
    SLACK_BOT_TOKEN          xoxb-... token for the Hermes Slack app
    SLACK_APPROVAL_CHANNEL   Channel ID to post reminders (e.g. C0ABC1234)

If either env var is missing the reminder is silently skipped with a log line.
If the Slack API call fails the procurement workflow is NOT interrupted.
"""

import os

from dotenv import load_dotenv
load_dotenv()


def format_quantity(quantity) -> str | None:
    """Return a display string for quantity, or None to suppress the line.

    None          -> None
    130 / 130.0   -> "130 kg"
    "130"         -> "130 kg"
    "130 kg"      -> "130 kg"
    any other str -> original string (no crash)
    """
    if quantity is None:
        return None
    if isinstance(quantity, (int, float)):
        return f"{quantity:.0f} kg"
    # string branch
    stripped = str(quantity).strip()
    try:
        return f"{float(stripped):.0f} kg"
    except ValueError:
        pass
    # e.g. "130 kg" or "130.5 kg" — try parsing the numeric prefix
    parts = stripped.split(None, 1)
    if parts:
        try:
            num = float(parts[0])
            unit = parts[1] if len(parts) > 1 else "kg"
            return f"{num:.0f} {unit}"
        except ValueError:
            pass
    return stripped  # non-numeric string: pass through unchanged


def send_approval_reminder(
    product_name: str,
    supplier: str,
    run_id: str,
    quantity: float | None = None,
    decision: str | None = None,
) -> None:
    """Post a one-way procurement approval reminder to SLACK_APPROVAL_CHANNEL.

    Never raises — logs a clear message and returns on any failure so the
    caller's procurement workflow is unaffected.

    Args:
        product_name: the product that needs approval
        supplier:     the supplier associated with the recommendation
        run_id:       the approval draft ID (= email RUN_ID the approver replies to)
        quantity:     suggested order quantity in kg (optional)
        decision:     recommendation decision action string (optional)
    """
    channel   = os.environ.get("SLACK_APPROVAL_CHANNEL")
    bot_token = os.environ.get("SLACK_BOT_TOKEN")

    if not channel:
        print("[SLACK NOTIFY] SLACK_APPROVAL_CHANNEL not set; skipping approval reminder.")
        return
    if not bot_token:
        print("[SLACK NOTIFY] SLACK_BOT_TOKEN not set; skipping approval reminder.")
        return

    qty_str   = format_quantity(quantity)
    qty_line  = f"*Quantity:* {qty_str}\n" if qty_str is not None else ""
    decision_line = f"*Decision:* {decision}\n"        if decision  is not None else ""

    message = (
        ":bell: *Procurement approval needed*\n\n"
        "A new procurement recommendation is waiting for your approval.\n\n"
        f"*Product:* {product_name}\n"
        f"*Supplier:* {supplier}\n"
        f"{qty_line}"
        f"{decision_line}"
        f"*Run ID:* {run_id}\n\n"
        "Please check your email and *reply by email* with "
        "APPROVE / REJECT / APPROVE ANYWAY / STOP PURCHASE."
    )

    print(f"[SLACK NOTIFY] posting to channel={channel!r} run_id={run_id!r}")
    try:
        from slack_sdk import WebClient
        client = WebClient(token=bot_token)
        resp = client.chat_postMessage(channel=channel, text=message)
        print(f"[SLACK NOTIFY] Slack API success ts={resp.get('ts')!r}")
    except ImportError:
        print("[SLACK NOTIFY] slack_sdk not installed; skipping approval reminder.")
    except Exception as exc:
        print(f"[SLACK NOTIFY] Slack API failure: {exc!r}")
