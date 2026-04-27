"""
procurement_agent.py

Separate procurement workflow for low-stock review, draft generation,
approval, sending, and procurement reply checks.
"""

from __future__ import annotations

import re
import uuid

from dotenv import load_dotenv

load_dotenv()

from database import _execute, _use_postgres, get_connection

SUPPLIER_EMAILS = {
    "Pacific Rim BioMaterials Co.": "jchen@pacificrimbiomaterials.com",
    "Jiaxing Natural Products Ltd": "lwang@jiaxingnatural.com",
    "Shanghai BioSupply International": "mzhou@shibiosupply.com",
    "Zhejiang Green Botanicals Corp": "sliu@zjgreenbotanicals.com",
    "Guangzhou Nutra Raw Materials Inc": "dliang@gznutraraw.com",
    "Jiaxing Supplier": "lwang@jiaxingnatural.com",
    "Alt Distributor": "sliu@zjgreenbotanicals.com",
}


def _lookup_supplier_email(supplier: str) -> str:
    return SUPPLIER_EMAILS.get(supplier, "")


def get_low_stock_items() -> list[dict]:
    conn = get_connection()
    try:
        rows = _execute(
            conn,
            """
            SELECT product_name, supplier, quantity_kg, reorder_at, unit
            FROM stock
            WHERE quantity_kg < reorder_at
            ORDER BY quantity_kg ASC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _draft_from_row(row: dict) -> dict:
    supplier = row["supplier"] or "Supplier"
    product = row["product_name"]
    qty = float(row["quantity_kg"] or 0)
    reorder_at = float(row["reorder_at"] or 0)
    unit = row["unit"] or "kg"
    suggested_qty = max(reorder_at * 2 - qty, reorder_at)
    supplier_email = _lookup_supplier_email(supplier)
    urgency = "high" if qty <= 0 else "medium"
    reason = (
        f"Current stock ({qty} {unit}) is below reorder threshold "
        f"({reorder_at} {unit})."
    )
    subject = f"Reorder Suggestion - {product}"
    body = (
        f"Hello {supplier},\n\n"
        f"We would like to place a reorder for {product}.\n"
        f"Our current stock is {qty} {unit}, with a reorder threshold of {reorder_at} {unit}.\n"
        f"Please confirm availability, lead time, and pricing for {suggested_qty:.0f} {unit}.\n\n"
        f"Best regards,\n"
        f"California Nutraceuticals"
    )
    return {
        "product_name": product,
        "supplier": supplier,
        "supplier_email": supplier_email,
        "current_stock_kg": qty,
        "reorder_at_kg": reorder_at,
        "suggested_order_qty": suggested_qty,
        "urgency": urgency,
        "reason": reason,
        "subject": subject,
        "body": body,
    }


def _save_draft(draft: dict) -> dict:
    conn = get_connection()
    try:
        if _use_postgres:
            row = _execute(
                conn,
                """
                INSERT INTO procurement_recommendations
                    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg,
                     suggested_order_qty, urgency, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval')
                RETURNING id
                """,
                (
                    draft["product_name"],
                    draft["supplier"],
                    draft["supplier_email"],
                    draft["current_stock_kg"],
                    draft["reorder_at_kg"],
                    draft["suggested_order_qty"],
                    draft["urgency"],
                    draft["reason"],
                ),
            ).fetchone()
            recommendation_id = row["id"]
        else:
            rec_cur = _execute(
                conn,
                """
                INSERT INTO procurement_recommendations
                    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg,
                     suggested_order_qty, urgency, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval')
                """,
                (
                    draft["product_name"],
                    draft["supplier"],
                    draft["supplier_email"],
                    draft["current_stock_kg"],
                    draft["reorder_at_kg"],
                    draft["suggested_order_qty"],
                    draft["urgency"],
                    draft["reason"],
                ),
            )
            recommendation_id = rec_cur.lastrowid

        draft_id = str(uuid.uuid4())[:8]
        _execute(
            conn,
            """
            INSERT INTO procurement_email_drafts
                (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                draft_id,
                recommendation_id,
                draft["product_name"],
                draft["supplier"],
                draft["supplier_email"],
                draft["subject"],
                draft["body"],
            ),
        )
        conn.commit()
        saved = dict(draft)
        saved["id"] = draft_id
        saved["recommendation_id"] = recommendation_id
        return saved
    finally:
        conn.close()


def draft_procurement_emails(product_name: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
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
            rows = [dict(row)] if row else []
        else:
            rows = get_low_stock_items()
    finally:
        conn.close()
    return [_save_draft(_draft_from_row(row)) for row in rows]


def get_pending_drafts() -> list[dict]:
    conn = get_connection()
    try:
        rows = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, subject, body
            FROM procurement_email_drafts
            WHERE status = 'draft'
            ORDER BY created_at DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def send_procurement_draft(draft_id: str) -> str:
    from email_feedback_agent import send_email

    conn = get_connection()
    try:
        row = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, subject, body
            FROM procurement_email_drafts
            WHERE id = ? AND status = 'draft'
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return f"Draft '{draft_id}' was not found."
        draft = dict(row)
        if not draft.get("supplier_email"):
            return (
                f"Draft '{draft_id}' has no supplier email address. "
                "Add a supplier email mapping before sending."
            )

        send_email(draft["supplier_email"], draft["subject"], draft["body"])
        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft_id,),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'sent', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft["recommendation_id"],),
        )
        conn.commit()
        return (
            f"Sent procurement email for {draft['product_name']} to "
            f"{draft['supplier']} <{draft['supplier_email']}>."
        )
    finally:
        conn.close()


def discard_procurement_draft(draft_id: str) -> str:
    conn = get_connection()
    try:
        row = _execute(
            conn,
            "SELECT id, recommendation_id, product_name FROM procurement_email_drafts WHERE id = ? AND status = 'draft'",
            (draft_id,),
        ).fetchone()
        if not row:
            return f"Draft '{draft_id}' was not found."
        draft = dict(row)
        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'discarded' WHERE id = ?",
            (draft_id,),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'discarded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft["recommendation_id"],),
        )
        conn.commit()
        return f"Discarded draft for {draft['product_name']}."
    finally:
        conn.close()


def check_procurement_replies() -> str:
    from email_feedback_agent import fetch_procurement_replies, parse_reply

    replies = fetch_procurement_replies()
    if not replies:
        return "No procurement replies found."

    conn = get_connection()
    try:
        lines = []
        for r in replies:
            parsed = parse_reply(r["body"])
            action = parsed["action"]
            run_id = r["run_id"]

            _execute(
                conn,
                """
                INSERT INTO procurement_replies
                    (draft_id, sender, subject, raw_body, parsed_action, parsed_supplier, parsed_quantity, parsed_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    r.get("sender"),
                    r.get("subject"),
                    r.get("body"),
                    action,
                    parsed.get("supplier"),
                    parsed.get("quantity"),
                    parsed.get("reason"),
                ),
            )

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
        return "\n".join(lines)
    finally:
        conn.close()


def run_procurement_command(command: str) -> str:
    cmd = command.lower().strip()

    if "low stock" in cmd:
        try:
            rows = get_low_stock_items()
            if not rows:
                return "No low-stock items."
            lines = [
                f"• {r['product_name']}: {r['quantity_kg']} kg (reorder at {r['reorder_at']} kg)"
                for r in rows
            ]
            return "Low stock items:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error checking stock: {e}"

    if "draft" in cmd and ("procurement email" in cmd or "reorder email" in cmd):
        try:
            product_name = None
            match = re.search(r"(?:for|about)\s+(.+)$", command, re.IGNORECASE)
            if match:
                product_name = match.group(1).strip().rstrip(".!?")

            drafts = draft_procurement_emails(product_name=product_name)
            if product_name and not drafts:
                return f"Could not find a stock item named '{product_name}'."
            if not drafts:
                return "No low-stock items found to draft procurement emails for."

            rendered = []
            for draft in drafts:
                rendered.append(
                    f"Draft ID: {draft['id']}\n"
                    f"To: {draft['supplier_email'] or '[missing supplier email]'}\n"
                    f"Subject: {draft['subject']}\n\n"
                    f"{draft['body']}"
                )
            return (
                "Drafted procurement emails for review. Nothing has been sent.\n"
                "Use the approval buttons in Agent Control or run: send procurement email <draft-id>\n\n"
                + ("\n\n" + ("-" * 72) + "\n\n").join(rendered)
            )
        except Exception as e:
            return f"Error drafting procurement email: {e}"

    if cmd.startswith("send procurement email"):
        try:
            match = re.search(r"send procurement email\s+([a-zA-Z0-9\-]+)$", command.strip(), re.IGNORECASE)
            if not match:
                return "Please specify a draft ID, for example: send procurement email abc12345"
            return send_procurement_draft(match.group(1))
        except Exception as e:
            return f"Error sending procurement email: {e}"

    if "procurement" in cmd or "repl" in cmd:
        try:
            return check_procurement_replies()
        except Exception as e:
            return f"Error checking procurement replies: {e}"

    return ""
