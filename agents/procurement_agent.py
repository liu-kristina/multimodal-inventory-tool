"""
procurement_agent.py

Separate procurement workflow for low-stock review, draft generation,
approval, sending, and procurement reply checks.
"""

from __future__ import annotations

import os
import re
import uuid

from dotenv import load_dotenv

load_dotenv()

from database import _execute, _insert_id, _use_postgres, get_connection

SUPPLIER_EMAILS = {
    "Pacific Rim BioMaterials Co.": "jchen@pacificrimbiomaterials.com",
    "Jiaxing Natural Products Ltd": "lwang@jiaxingnatural.com",
    "Shanghai BioSupply International": "mzhou@shibiosupply.com",
    "Zhejiang Green Botanicals Corp": "sliu@zjgreenbotanicals.com",
    "Guangzhou Nutra Raw Materials Inc": "dliang@gznutraraw.com",
    "Jiaxing Supplier": "lwang@jiaxingnatural.com",
    "Alt Distributor": "sliu@zjgreenbotanicals.com",
    # Demo supplier routes RFQ emails to the configured approval/test inbox.
    "Demo Supplier": os.getenv("USER_APPROVAL_EMAIL") or os.getenv("GMAIL_ADDRESS", ""),
}



def _lookup_supplier_email(supplier: str) -> str:
    return SUPPLIER_EMAILS.get(supplier, "")


def resolve_supplier_email(conn, product_name: str, supplier: str) -> str:
    """Return the best-known email for a supplier/product pair.

    Resolution priority:
    1. Latest non-empty supplier_email in procurement_recommendations (same product + supplier)
    2. Latest non-empty supplier_email in procurement_email_drafts (same product + supplier)
    3. product_supplier_alternates catalog (covers user-registered suppliers via CHANGE action)
    4. Static SUPPLIER_EMAILS mapping (includes Demo Supplier → env-var inbox)
    Returns '' when no email can be found; callers must handle that case explicitly.
    """
    for table, order_col in [
        ("procurement_recommendations", "id"),
        ("procurement_email_drafts",    "created_at"),
    ]:
        row = _execute(conn,
            f"SELECT supplier_email FROM {table} "
            f"WHERE product_name = ? AND supplier = ? AND supplier_email != '' "
            f"ORDER BY {order_col} DESC LIMIT 1",
            (product_name, supplier),
        ).fetchone()
        if row and row["supplier_email"]:
            return row["supplier_email"]
    # alt_row = _execute(conn,
    #     "SELECT supplier_email FROM product_supplier_alternates "
    #     "WHERE product_name = ? AND supplier = ? AND supplier_email IS NOT NULL AND supplier_email != '' "
    #     "ORDER BY priority ASC LIMIT 1",
    #     (product_name, supplier),
    # ).fetchone()
    # if alt_row and alt_row["supplier_email"]:
    #     return alt_row["supplier_email"]
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


def get_primary_supplier_for_product(conn, product_name: str) -> dict | None:
    """
    Return the primary (default) supplier for a product from the stock table.

    The stock table is the authoritative source for which supplier a product is
    normally sourced from.  Alternative suppliers only enter the picture when the
    primary path fails (out-of-stock, human REJECT, etc.).

    Returns {"supplier": str, "supplier_email": str} or None if not found.
    """
    row = _execute(
        conn,
        "SELECT supplier FROM stock WHERE LOWER(product_name) = LOWER(?)",
        (product_name,),
    ).fetchone()
    if not row or not row["supplier"]:
        return None
    supplier = row["supplier"]
    return {
        "supplier":       supplier,
        "supplier_email": _lookup_supplier_email(supplier),
    }


def _draft_from_row(row: dict) -> dict:
    supplier = row["supplier"] or "Supplier"
    product = row["product_name"]
    qty = float(row["quantity_kg"] or 0)
    reorder_at = float(row["reorder_at"] or 0)
    unit = row["unit"] or "kg"
    suggested_qty = max(reorder_at * 2 - qty, reorder_at)
    conn = get_connection()
    try:
        supplier_email = resolve_supplier_email(conn, product, supplier)
    finally:
        conn.close()
    urgency = "high" if qty <= 0 else "medium"
    reason = (
        f"Current stock ({qty} {unit}) is below reorder threshold "
        f"({reorder_at} {unit})."
    )
    subject = f"Reorder Suggestion - {product}"
    body = (
        f"Hello {supplier},\n\n"
        f"Could you please provide a quote for:\n\n"
        f"Product:  {product}\n"
        f"Quantity: {suggested_qty:.0f} {unit}\n\n"
        f"Including:\n"
        f"- unit price\n"
        f"- availability\n"
        f"- lead time\n"
        f"- shipping cost, if applicable\n"
        f"- quote validity period\n\n"
        f"Please reference RUN_ID={{run_id}} in your reply.\n\n"
        f"This is a request for quote (RFQ) only.\n\n"
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rfq_draft')
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
            recommendation_id = _insert_id(
                conn,
                """
                INSERT INTO procurement_recommendations
                    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg,
                     suggested_order_qty, urgency, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rfq_draft')
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

        draft_id = str(uuid.uuid4())[:8]
        body = draft["body"].replace("{run_id}", draft_id)
        # Embed RUN_ID in the stored subject so it matches what is sent to suppliers.
        subject_with_id = f"{draft['subject']} [RUN_ID={draft_id}]"
        _execute(
            conn,
            """
            INSERT INTO procurement_email_drafts
                (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'rfq_draft')
            """,
            (
                draft_id,
                recommendation_id,
                draft["product_name"],
                draft["supplier"],
                draft["supplier_email"],
                subject_with_id,
                body,
            ),
        )
        conn.commit()
        saved = dict(draft)
        saved["id"] = draft_id
        saved["recommendation_id"] = recommendation_id
        return saved
    finally:
        conn.close()


_TERMINAL_DRAFT_STATUSES = ("rejected", "stopped", "order_sent", "discarded")


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

        filtered = []
        for row in rows:
            pn = row["product_name"]
            existing = _execute(
                conn,
                "SELECT id FROM procurement_email_drafts "
                "WHERE product_name = ? AND status NOT IN "
                "('rejected','stopped','order_sent','discarded')",
                (pn,),
            ).fetchone()
            if existing:
                print(f"[PROCUREMENT] Active draft already exists for {pn}, skipping duplicate")
            else:
                filtered.append(row)
    finally:
        conn.close()

    drafts = []
    for row in filtered:
        supplier = row.get("supplier") or "unknown"
        print(f"[PROCUREMENT STRATEGY] Trying primary supplier first: {supplier}")
        drafts.append(_save_draft(_draft_from_row(row)))
    return drafts


def get_pending_drafts() -> list[dict]:
    conn = get_connection()
    try:
        rows = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, subject, body
            FROM procurement_email_drafts
            WHERE status = 'rfq_draft'
            ORDER BY created_at DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def send_procurement_draft(draft_id: str) -> str:
    from agents.email_feedback_agent import send_email, build_recommendation_subject

    conn = get_connection()
    try:
        row = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, subject, body
            FROM procurement_email_drafts
            WHERE id = ? AND status IN ('rfq_draft', 'draft')
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return f"Draft '{draft_id}' was not found."
        draft = dict(row)

        if not draft.get("supplier_email"):
            resolved = resolve_supplier_email(conn, draft["product_name"], draft["supplier"])
            if not resolved:
                return (
                    f"NO_EMAIL: Draft '{draft_id}' — supplier '{draft['supplier']}' has no "
                    "known email address. Add it to SUPPLIER_EMAILS or ensure a prior "
                    "draft/recommendation recorded it."
                )
            draft["supplier_email"] = resolved
            _execute(conn,
                "UPDATE procurement_email_drafts SET supplier_email = ? WHERE id = ?",
                (resolved, draft_id),
            )
            conn.commit()

        subject = build_recommendation_subject(draft["product_name"], draft_id)
        print(f"[PROCUREMENT] Sending RFQ to {draft['supplier_email']}")
        try:
            send_email(draft["supplier_email"], subject, draft["body"])
        except Exception as e:
            print(f"[PROCUREMENT] Failed to send RFQ: {e}")
            return f"Failed to send RFQ for '{draft['product_name']}': {e}"

        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'rfq_sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft_id,),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'rfq_sent', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
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
            "SELECT id, recommendation_id, product_name FROM procurement_email_drafts WHERE id = ? AND status = 'rfq_draft'",
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


def create_order_approval_draft(recommendation_id: int) -> str:
    conn = get_connection()
    try:
        row = _execute(
            conn,
            "SELECT product_name, supplier, supplier_email, suggested_order_qty, reason "
            "FROM procurement_recommendations WHERE id = ?",
            (recommendation_id,),
        ).fetchone()
        if not row:
            return f"Recommendation {recommendation_id} not found."
        rec = dict(row)

        product = rec["product_name"]
        supplier = rec["supplier"]
        qty = rec["suggested_order_qty"]

        subject = f"Order Approval Required - {product}"
        body = (
            f"Hello {supplier},\n\n"
            f"We would like to place an order for:\n\n"
            f"Product: {product}\n"
            f"Quantity: {qty:.0f} kg\n\n"
            f"Please confirm receipt of this order and expected delivery date.\n\n"
            f"Best regards,\n"
            f"California Nutraceuticals"
        )

        draft_id = str(uuid.uuid4())[:8]
        _execute(
            conn,
            """
            INSERT INTO procurement_email_drafts
                (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'order_approval_draft')
            """,
            (draft_id, recommendation_id, product, supplier, rec.get("supplier_email") or "", subject, body),
        )
        conn.commit()
        return draft_id
    finally:
        conn.close()


def send_order_approval_draft(draft_id: str) -> str:
    from agents.email_feedback_agent import send_email

    to = os.getenv("USER_APPROVAL_EMAIL") or os.getenv("TEST_EMAIL")
    if not to:
        return (
            "No recipient configured. "
            "Set USER_APPROVAL_EMAIL or TEST_EMAIL before sending approval emails."
        )

    conn = get_connection()
    try:
        row = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, subject, body
            FROM procurement_email_drafts
            WHERE id = ? AND status = 'order_approval_draft'
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return f"Approval draft '{draft_id}' was not found."
        draft = dict(row)

        rec_row = _execute(
            conn,
            "SELECT suggested_order_qty, reason FROM procurement_recommendations WHERE id = ?",
            (draft["recommendation_id"],),
        ).fetchone()
        rec = dict(rec_row) if rec_row else {}
        qty = rec.get("suggested_order_qty", 0)
        reason = rec.get("reason", "")

        internal_body = (
            f"A procurement recommendation has been generated for your approval.\n\n"
            f"Product:  {draft['product_name']}\n"
            f"Supplier: {draft['supplier']}\n"
            f"Quantity: {qty:.0f} kg\n"
            f"Reason:   {reason}\n\n"
            f"Please reply with one of the following:\n\n"
            f"  APPROVE\n\n"
            f"  REJECT\n"
            f"  Reason: <your reason>\n\n"
            f"  CHANGE\n"
            f"  Supplier: <new supplier name>\n"
            f"  Email: <supplier@example.com>\n"
            f"  Quantity: <new quantity (optional)>\n"
            f"  Reason: <your reason (optional)>\n\n"
            f"--- Supplier Order Email Preview ---\n"
            f"To: {draft['supplier_email']}\n"
            f"Subject: Purchase Order - {draft['product_name']}\n\n"
            f"{draft['body']}"
        )

        subject = f"{draft['subject']} [RUN_ID={draft_id}]"
        send_email(to, subject, internal_body)

        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'order_approval_sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft_id,),
        )
        conn.commit()
        return f"Sent order approval email for {draft['product_name']} to {to}."
    finally:
        conn.close()


def send_supplier_order_email(draft_id: str) -> str:
    from agents.email_feedback_agent import send_email

    conn = get_connection()
    try:
        row = _execute(
            conn,
            """
            SELECT id, recommendation_id, product_name, supplier, supplier_email, body
            FROM procurement_email_drafts
            WHERE id = ? AND status IN ('order_approval_sent', 'approved')
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return f"Order draft '{draft_id}' not found or not in a sendable state."
        draft = dict(row)

        if not draft.get("supplier_email"):
            return f"Draft '{draft_id}' has no supplier email address."

        subject = f"Purchase Order - {draft['product_name']} [RUN_ID={draft_id}]"
        send_email(draft["supplier_email"], subject, draft["body"])

        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'order_sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft_id,),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'order_sent', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft["recommendation_id"],),
        )
        conn.commit()
        return (
            f"Sent supplier order email for {draft['product_name']} to "
            f"{draft['supplier']} <{draft['supplier_email']}>."
        )
    finally:
        conn.close()


def check_procurement_replies() -> str:
    from agents.email_feedback_agent import fetch_procurement_replies, parse_reply

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


def extract_quote_fields(text: str) -> dict:
    """Extract structured fields from a supplier quote reply body."""
    fields: dict = {
        "unit_price": None,
        "availability": None,
        "lead_time": None,
        "shipping_cost": None,
        "quote_validity": None,
    }

    # unit price
    m = re.search(r"unit\s*price[:\s]*\$?([\d,]+\.?\d*)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\$\s*([\d,]+\.?\d*)\s*/?\s*kg", text, re.IGNORECASE)
    if not m:
        m = re.search(r"price[:\s]+(?:USD\s*)?\$?([\d,]+\.?\d*)", text, re.IGNORECASE)
    if m:
        try:
            fields["unit_price"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # availability
    if re.search(r"\bout\s*of\s*stock\b", text, re.IGNORECASE):
        fields["availability"] = "out of stock"
    elif re.search(r"\bnot\s+available\b", text, re.IGNORECASE):
        fields["availability"] = "not available"
    elif re.search(r"\bin\s*stock\b", text, re.IGNORECASE):
        fields["availability"] = "in stock"
    elif re.search(r"\bavailable\b", text, re.IGNORECASE):
        fields["availability"] = "available"

    # lead time — "lead time: N days", "delivery/delivered/ships in N days/weeks"
    m = re.search(
        r"lead\s*time[:\s]+([\d]+\s*(?:business\s*)?(?:days?|weeks?|months?))",
        text, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"(?:deliver(?:ed|y)?|ships?)\s+in\s+([\d]+\s*(?:business\s*)?(?:days?|weeks?))",
            text, re.IGNORECASE,
        )
    if m:
        fields["lead_time"] = m.group(1).strip()

    # shipping cost — requires explicit cost/fee/charge label or ": $X"; never matches "delivery in N weeks"
    m = re.search(
        r"(?:shipping|freight)(?:\s+(?:cost|fee|charge|price))?[:\s]+\$?\s*([\d,]+\.?\d*)",
        text, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"delivery\s+(?:cost|fee|charge)[:\s]+\$?\s*([\d,]+\.?\d*)",
            text, re.IGNORECASE,
        )
    if not m:
        m = re.search(r"\$\s*([\d,]+\.?\d*)\s*(?:shipping|freight)", text, re.IGNORECASE)
    if m:
        try:
            fields["shipping_cost"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # quote validity — "valid for 14 days", "quote valid for 2 weeks"
    # Using (?:\s+for)? instead of [^:]* to avoid greedy consumption of the leading digit
    m = re.search(
        r"\bvalid(?:ity)?(?:\s+for)?[:\s]+([\d]+\s*(?:days?|weeks?))",
        text, re.IGNORECASE,
    )
    if m:
        fields["quote_validity"] = m.group(1).strip()

    return fields


def _parse_memory_fields_from_reason(reason: str) -> dict:
    """Extract structured quote fields from a formatted recommendation reason string."""
    result = {
        "unit_price": None,
        "lead_time": None,
        "shipping_cost": None,
        "estimated_total_cost": None,
        "availability": None,
        "recommendation_action": None,
    }
    if not reason:
        return result

    m = re.search(r"Unit price: \$([\d,]+\.?\d*)", reason)
    if m:
        try:
            result["unit_price"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = re.search(r"Lead time: ([\d]+\s*\w+)\.", reason)
    if m:
        result["lead_time"] = m.group(1).strip()

    m = re.search(r"includes \$([\d,]+\.?\d*) shipping", reason)
    if m:
        try:
            result["shipping_cost"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = re.search(r"Estimated total cost: \$([\d,]+\.?\d*)", reason)
    if m:
        try:
            result["estimated_total_cost"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = re.search(r"- Availability: ([^.]+)\.", reason)
    if m:
        result["availability"] = m.group(1).strip()

    m = re.search(r"Decision: (LOW_RISK_RECOMMEND|NEEDS_HUMAN_REVIEW|REJECT)", reason)
    if m:
        result["recommendation_action"] = m.group(1)

    return result


def get_supplier_memory_score(conn, product_name: str, supplier: str) -> dict:
    """
    Return a score and breakdown for a supplier based on past procurement decisions.

    Scoring weights:
        APPROVE           +1    (confirmed good supplier)
        APPROVE_ANYWAY     0    (override — no alternative; neutral signal)
        REJECT            -2    (active rejection signal)
        STOP_PURCHASE     -3    (strongest negative — purchase halted)
        CHANGE            -1    (user redirected away from this supplier)
        PROVIDE_NEW_QUOTE  0    (neutral; supplier gets another evaluation chance)
    """
    rows = _execute(
        conn,
        "SELECT user_action, user_reason FROM procurement_memory "
        "WHERE product_name = ? AND supplier = ? ORDER BY created_at ASC",
        (product_name, supplier),
    ).fetchall()

    _weights = {
        "APPROVE":            1.0,
        "APPROVE_ANYWAY":     0.0,
        "REJECT":            -2.0,
        "STOP_PURCHASE":     -3.0,
        "CHANGE":            -1.0,
        "PROVIDE_NEW_QUOTE":  0.0,
    }

    score          = 0.0
    approval_count = 0
    reject_count   = 0
    override_count = 0
    stop_count     = 0
    change_count   = 0
    recent_reasons: list[str] = []

    for row in rows:
        action = (row["user_action"] or "").upper()
        score += _weights.get(action, 0.0)
        if action == "APPROVE":
            approval_count += 1
        elif action == "APPROVE_ANYWAY":
            override_count += 1
        elif action == "REJECT":
            reject_count += 1
            if row["user_reason"]:
                recent_reasons.append(row["user_reason"])
        elif action == "STOP_PURCHASE":
            stop_count += 1
        elif action == "CHANGE":
            change_count += 1

    return {
        "supplier_score":        round(score, 2),
        "approval_count":        approval_count,
        "reject_count":          reject_count,
        "override_count":        override_count,
        "stop_count":            stop_count,
        "change_count":          change_count,
        "recent_reject_reasons": recent_reasons[-3:],
    }


def build_recommendation_reason(
    product_name: str,
    supplier: str,
    quantity: float,
    quote_fields: dict,
    conn=None,
) -> str:
    """Build recommendation reason lines applying the procurement decision policy."""
    lines = []
    risk_flags = []

    unit_price    = quote_fields.get("unit_price")
    availability  = quote_fields.get("availability")
    lead_time     = quote_fields.get("lead_time")
    shipping_cost = quote_fields.get("shipping_cost")

    lines.append("- Single known supplier; no competing quote available.")
    lines.append("- Supplier responded to RFQ.")
    lines.append("- Stock is below reorder threshold.")

    if availability is None:
        risk_flags.append("Availability not stated in supplier reply.")
    elif availability.lower() in ("out of stock", "not available"):
        risk_flags.append(f"Supplier reports availability as '{availability}'.")
    else:
        lines.append(f"- Availability: {availability}.")

    if lead_time is None:
        risk_flags.append("Lead time not stated in supplier reply.")
    else:
        lines.append(f"- Lead time: {lead_time}.")

    if unit_price is None:
        risk_flags.append("Unit price not found in supplier reply; cost estimate unavailable.")
    else:
        lines.append(f"- Unit price: ${unit_price:.2f}.")
        shipping_val = shipping_cost if shipping_cost is not None else 0.0
        total_cost = unit_price * quantity + shipping_val
        cost_note = f"- Estimated total cost: ${total_cost:,.2f}"
        if shipping_cost is not None:
            cost_note += f" (includes ${shipping_cost:.2f} shipping)."
        else:
            cost_note += " (shipping cost not stated)."
        lines.append(cost_note)

    if risk_flags:
        lines.append("Risk flags:")
        for flag in risk_flags:
            lines.append(f"  ! {flag}")

    if conn is not None:
        ms    = get_supplier_memory_score(conn, product_name, supplier)
        total = (ms["approval_count"] + ms["reject_count"]
                 + ms["override_count"] + ms["stop_count"] + ms["change_count"])
        score = ms["supplier_score"]
        lines.append("Memory signal:")
        if total > 0:
            lines.append(f"  - Past approvals: {ms['approval_count']}")
            lines.append(f"  - Past rejections: {ms['reject_count']}")
            if ms["change_count"]:
                lines.append(f"  - Times redirected away: {ms['change_count']}")
            lines.append(f"  - Supplier memory score: {score:.1f}")
            if score < 0:
                lines.append("  - Memory note: this supplier has previous negative feedback.")
            else:
                lines.append("  - Memory note: this supplier has previous approvals.")
            if ms["recent_reject_reasons"]:
                lines.append(f"  - Recent rejection reasons: {'; '.join(ms['recent_reject_reasons'])}")
        else:
            lines.append("  - Memory note: no prior supplier feedback found.")

    return "\n".join(lines)


def decide_procurement_action(
    quote_fields: dict,
    quantity: float,
    max_auto_total: float = 1000.0,
) -> dict:
    """
    Apply procurement decision policy to a parsed supplier quote.

    Returns a dict with keys:
        action               – "LOW_RISK_RECOMMEND" | "NEEDS_HUMAN_REVIEW" | "REJECT"
        decision_reason      – human-readable explanation
        risk_flags           – list of risk/warning strings
        estimated_total_cost – float or None

    No action triggers an automatic order.  Human approval is always required
    before any order is sent.  LOW_RISK_RECOMMEND means the quote is complete
    and within threshold; NEEDS_HUMAN_REVIEW means missing fields or elevated
    cost; REJECT means the supplier cannot fulfil and no approval draft is sent.
    """
    unit_price    = quote_fields.get("unit_price")
    availability  = quote_fields.get("availability")
    lead_time     = quote_fields.get("lead_time")
    shipping_cost = quote_fields.get("shipping_cost")

    risk_flags: list[str] = []

    # Compute cost (None when unit_price missing)
    if unit_price is not None:
        estimated_total = unit_price * quantity + (shipping_cost or 0.0)
    else:
        estimated_total = None

    # Hard reject: supplier cannot fulfil
    avail_lower = (availability or "").lower()
    if avail_lower in ("out of stock", "not available"):
        return {
            "action": "REJECT",
            "decision_reason": f"Supplier reports availability as '{availability}'; cannot fulfil order.",
            "risk_flags": [f"Availability: {availability}"],
            "estimated_total_cost": None,
        }

    # Escalate: missing required fields
    if unit_price is None:
        risk_flags.append("Unit price not found in supplier reply.")
    if availability is None:
        risk_flags.append("Availability not stated in supplier reply.")
    if lead_time is None:
        risk_flags.append("Lead time not stated in supplier reply.")

    if risk_flags:
        return {
            "action": "NEEDS_HUMAN_REVIEW",
            "decision_reason": "Incomplete quote; human must review before approving.",
            "risk_flags": risk_flags,
            "estimated_total_cost": estimated_total,
        }

    # Escalate: cost exceeds review threshold
    if estimated_total is not None and estimated_total > max_auto_total:
        return {
            "action": "NEEDS_HUMAN_REVIEW",
            "decision_reason": "Estimated total exceeds review threshold; human approval required.",
            "risk_flags": [],
            "estimated_total_cost": estimated_total,
        }

    return {
        "action": "LOW_RISK_RECOMMEND",
        "decision_reason": (
            f"Quote is complete and estimated total ${estimated_total:,.2f} "
            f"is within threshold. Human approval still required before any order is sent."
        ),
        "risk_flags": [],
        "estimated_total_cost": estimated_total,
    }


def find_alternative_recommendation(conn, product_name: str, excluded_supplier: str) -> dict | None:
    """Return the newest non-excluded alternative rec for product_name.

    Includes rfq_sent so callers can detect 'already waiting for fallback quote'.
    Callers should check alt['status'] to decide the next action:
      - 'quote_received'        → create approval draft immediately
      - 'recommendation_created'→ create approval draft immediately
      - 'rfq_sent'              → RFQ already sent; wait for supplier reply
    """
    # TODO: when multiple alternatives exist, score each via get_supplier_memory_score() and
    # use supplier_score as a tie-breaker after: availability > estimated_total_cost > lead_time.
    row = _execute(
        conn,
        """
        SELECT id, supplier, supplier_email, suggested_order_qty, reason, status
        FROM procurement_recommendations
        WHERE product_name = ?
          AND supplier != ?
          AND status IN ('recommendation_created', 'quote_received', 'rfq_sent')
        ORDER BY created_at DESC LIMIT 1
        """,
        (product_name, excluded_supplier),
    ).fetchone()
    return dict(row) if row else None


def find_fallback_supplier_in_catalog(
    conn, product_name: str, excluded_supplier: str
) -> dict | None:
    """Return the highest-priority fallback supplier for product_name from the
    product_supplier_alternates catalog, excluding the rejected supplier.

    The catalog is seeded by demo-seed and can be extended for any product.
    Returns a dict with 'supplier' and 'supplier_email', or None.
    """
    row = _execute(
        conn,
        """
        SELECT supplier, supplier_email
        FROM product_supplier_alternates
        WHERE product_name = ? AND supplier != ?
        ORDER BY priority ASC
        LIMIT 1
        """,
        (product_name, excluded_supplier),
    ).fetchone()
    return dict(row) if row else None


def rank_fallback_suppliers(
    conn, product_name: str, excluded_supplier: str
) -> dict | None:
    """
    Rank all catalog alternate suppliers for product_name (excluding excluded_supplier)
    and return the best candidate with a transparent explanation.

    Ranking factors (higher total score = preferred):
      - Catalog priority:  1.0 / priority  (priority 1 → 1.0, priority 2 → 0.5, …)
      - Memory score:      past APPROVE/REJECT history scaled by 0.5, clamped to [-0.5, +0.5]
      - Email bonus:       +0.3 if supplier_email is present and non-empty

    Returns dict with keys:
      supplier, supplier_email, score, ranking_reason, candidates_considered
    Returns None if no candidates exist in the catalog.
    """
    rows = _execute(
        conn,
        """
        SELECT supplier, supplier_email, priority
        FROM product_supplier_alternates
        WHERE product_name = ? AND supplier != ?
        ORDER BY priority ASC
        """,
        (product_name, excluded_supplier),
    ).fetchall()

    if not rows:
        return None

    candidates = []
    for row in rows:
        sup      = row["supplier"]
        email    = (row["supplier_email"] or "").strip()
        priority = int(row["priority"] or 1)
        mem      = get_supplier_memory_score(conn, product_name, sup)
        mem_score = mem["supplier_score"]

        priority_score = 1.0 / priority
        memory_bonus   = max(-0.5, min(0.5, mem_score * 0.5))
        email_bonus    = 0.3 if email else 0.0
        total_score    = round(priority_score + memory_bonus + email_bonus, 3)

        candidates.append({
            "supplier":   sup,
            "email":      email,
            "priority":   priority,
            "mem_score":  mem_score,
            "approvals":  mem["approval_count"],
            "rejections": mem["reject_count"],
            "total_score": total_score,
        })

    # Sort by score descending; break ties by catalog priority ascending.
    candidates.sort(key=lambda c: (-c["total_score"], c["priority"]))
    best = candidates[0]

    # Build transparent ranking reason.
    n = len(candidates)
    if n == 1:
        intro = (
            f"{best['supplier']} is recommended as the fallback supplier for {product_name} "
            f"because it is the only approved alternate supplier in the catalog "
            f"after excluding {excluded_supplier}."
        )
    else:
        others = ", ".join(c["supplier"] for c in candidates[1:])
        intro = (
            f"{best['supplier']} is recommended as the highest-ranked fallback supplier "
            f"for {product_name} (score {best['total_score']:.3f}), selected over "
            f"{n - 1} other candidate(s): {others}."
        )

    factors = []
    if n > 1:
        factors.append(f"catalog priority {best['priority']} (highest priority among candidates)")
    if best["email"]:
        factors.append(f"valid supplier email ({best['email']})")
    else:
        factors.append("no supplier email on file — address will need to be confirmed")
    if best["approvals"] > 0:
        factors.append(
            f"positive procurement history "
            f"({best['approvals']} prior approval(s), memory score {best['mem_score']:+.1f})"
        )
    elif best["rejections"] > 0:
        factors.append(
            f"prior procurement history on record "
            f"({best['rejections']} rejection(s), memory score {best['mem_score']:+.1f})"
        )
    else:
        factors.append("no prior procurement history with this supplier")

    ranking_reason = intro + " Factors: " + "; ".join(factors) + "."

    return {
        "supplier":              best["supplier"],
        "supplier_email":        best["email"],
        "score":                 best["total_score"],
        "ranking_reason":        ranking_reason,
        "candidates_considered": candidates,
    }


def create_and_send_fallback_rfq(
    product_name: str,
    fallback_supplier: str,
    ranking_reason: str = "",
) -> str:
    """
    Create and send an RFQ to a fallback supplier after a primary quote is rejected.

    The fallback supplier has not been contacted — this function sends the first
    RFQ to them and sets rec status to rfq_sent.  If ranking_reason is provided
    it is prepended to the stored recommendation reason so the selection rationale
    is recorded in the database alongside the RFQ.

    Returns a human-readable result message.
    """
    conn = get_connection()
    try:
        stock_row = _execute(conn,
            "SELECT quantity_kg, reorder_at, unit FROM stock WHERE product_name = ?",
            (product_name,),
        ).fetchone()
    finally:
        conn.close()

    if not stock_row:
        return f"Stock row not found for {product_name} — cannot create fallback RFQ"

    fake_row = {
        "product_name": product_name,
        "supplier":     fallback_supplier,
        "quantity_kg":  float(stock_row["quantity_kg"]),
        "reorder_at":   float(stock_row["reorder_at"]),
        "unit":         stock_row["unit"] or "kg",
    }
    draft_data = _draft_from_row(fake_row)
    if ranking_reason:
        draft_data["reason"] = ranking_reason + "\n\n" + draft_data["reason"]
    saved = _save_draft(draft_data)
    result = send_procurement_draft(saved["id"])
    return result


def create_and_send_change_rfq(
    product_name: str,
    new_supplier: str,
    new_supplier_email: str,
    reason: str = "",
) -> str:
    """
    Upsert new_supplier into product_supplier_alternates for product_name,
    then send a fresh RFQ to them.

    Called when the human replies CHANGE (with Supplier + Email) to an approval
    email. No recommendation or order is created here — the system waits for a
    real quote reply, which the existing quote-parsing path then handles.

    Returns a human-readable result message.
    """
    conn = get_connection()
    try:
        if _use_postgres:
            _execute(conn,
                """
                INSERT INTO product_supplier_alternates
                    (product_name, supplier, supplier_email, priority)
                VALUES (?, ?, ?, 99)
                ON CONFLICT (product_name, supplier) DO UPDATE SET
                    supplier_email = excluded.supplier_email
                """,
                (product_name, new_supplier, new_supplier_email),
            )
        else:
            _execute(conn,
                """
                INSERT OR REPLACE INTO product_supplier_alternates
                    (product_name, supplier, supplier_email, priority)
                VALUES (?, ?, ?, 99)
                """,
                (product_name, new_supplier, new_supplier_email),
            )
        conn.commit()
        stock_row = _execute(conn,
            "SELECT quantity_kg, reorder_at, unit FROM stock WHERE product_name = ?",
            (product_name,),
        ).fetchone()
    finally:
        conn.close()

    if not stock_row:
        return f"Stock row not found for {product_name} — cannot create change RFQ"

    fake_row = {
        "product_name": product_name,
        "supplier":     new_supplier,
        "quantity_kg":  float(stock_row["quantity_kg"]),
        "reorder_at":   float(stock_row["reorder_at"]),
        "unit":         stock_row["unit"] or "kg",
    }
    draft_data = _draft_from_row(fake_row)
    draft_data["supplier_email"] = new_supplier_email  # use user-provided email directly
    human_note = (
        f"Human-requested supplier change: {reason}" if reason
        else "Human-requested supplier change."
    )
    draft_data["reason"] = human_note + "\n\n" + draft_data["reason"]
    saved = _save_draft(draft_data)
    return send_procurement_draft(saved["id"])


def send_no_alternative_notification(rec_id: int) -> str:
    """
    Create and immediately send a notification to the user explaining that no
    alternative supplier exists for the rejected recommendation.

    The email body presents three human-actionable options:
      APPROVE ANYWAY / PROVIDE NEW QUOTE / STOP PURCHASE
    """
    from agents.email_feedback_agent import send_email

    to = os.getenv("USER_APPROVAL_EMAIL") or os.getenv("TEST_EMAIL")
    if not to:
        return (
            "No recipient configured. "
            "Set USER_APPROVAL_EMAIL or TEST_EMAIL before sending notifications."
        )

    conn = get_connection()
    try:
        row = _execute(
            conn,
            "SELECT product_name, supplier, supplier_email, suggested_order_qty "
            "FROM procurement_recommendations WHERE id = ?",
            (rec_id,),
        ).fetchone()
        if not row:
            return f"Recommendation {rec_id} not found."
        rec = dict(row)

        product  = rec["product_name"]
        supplier = rec["supplier"]
        qty      = rec["suggested_order_qty"]

        draft_id = str(uuid.uuid4())[:8]
        subject  = f"Order Approval Required - {product} [RUN_ID={draft_id}]"
        body = (
            f"No alternative supplier recommendation is available for:\n\n"
            f"Product:  {product}\n"
            f"Supplier: {supplier} (previously recommended)\n"
            f"Quantity: {qty:.0f} kg\n\n"
            f"Stock is still below reorder threshold. Shortage risk remains.\n\n"
            f"Please reply with ONE of the following:\n\n"
            f"  APPROVE ANYWAY\n"
            f"  (Override the rejection and proceed with {supplier}.)\n\n"
            f"  PROVIDE NEW QUOTE\n"
            f"  Supplier: <new supplier name>\n"
            f"  (Forward or paste a new supplier quote so the agent can reprocess it.)\n\n"
            f"  STOP PURCHASE\n"
            f"  (Stop the purchase process for this item.)\n"
        )

        _execute(
            conn,
            """
            INSERT INTO procurement_email_drafts
                (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'no_alternative_notification')
            """,
            (draft_id, rec_id, product, supplier, rec.get("supplier_email") or "", subject, body),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations "
            "SET status = 'no_alternative_supplier', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (rec_id,),
        )
        conn.commit()

        send_email(to, subject, body)
        return f"Sent no-alternative notification for {product} to {to}. draft_id={draft_id}"
    finally:
        conn.close()


def _insert_new_recommendation(
    conn, *, product_name, supplier, supplier_email,
    current_stock_kg, reorder_at_kg, suggested_order_qty,
    urgency, reason, status, parent_rec_id,
) -> int:
    """Insert a new rec row. Falls back to embedding parent_rec_id in reason text if column absent."""
    params_with = (
        product_name, supplier, supplier_email or "",
        float(current_stock_kg or 0), float(reorder_at_kg or 0),
        float(suggested_order_qty or 0), urgency or "medium",
        reason, status, parent_rec_id,
    )
    params_without = (
        product_name, supplier, supplier_email or "",
        float(current_stock_kg or 0), float(reorder_at_kg or 0),
        float(suggested_order_qty or 0), urgency or "medium",
        f"[parent_rec_id={parent_rec_id}]\n{reason}", status,
    )
    sql_with = (
        "INSERT INTO procurement_recommendations "
        "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
        "suggested_order_qty, urgency, reason, status, parent_rec_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    sql_without = (
        "INSERT INTO procurement_recommendations "
        "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
        "suggested_order_qty, urgency, reason, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    try:
        if _use_postgres:
            row = _execute(conn, sql_with + " RETURNING id", params_with).fetchone()
            return row["id"]
        else:
            return _execute(conn, sql_with, params_with).lastrowid
    except Exception:
        if _use_postgres:
            row = _execute(conn, sql_without + " RETURNING id", params_without).fetchone()
            return row["id"]
        else:
            return _execute(conn, sql_without, params_without).lastrowid


def run_recommendation_from_quote(conn, rec_id: int, quote_fields: dict) -> dict:
    """
    Build a recommendation from a supplier quote, supersede the old rec, and
    create + send an approval draft.

    Steps:
      1. Read original recommendation (rec_id).
      2. Build reason + apply decide_procurement_action.
      3. Mark old rec status = 'superseded'.
      4. Insert new recommendation row (parent_rec_id = rec_id).
      5. If decision != REJECT, create and send an order approval draft.

    Does NOT close conn — caller is responsible.

    Returns dict:
      new_rec_id, approval_draft_id, send_result, decision,
      product_name, supplier, quantity, rec_reason
    """
    old_rec = _execute(conn,
        "SELECT product_name, supplier, supplier_email, suggested_order_qty, "
        "current_stock_kg, reorder_at_kg, urgency "
        "FROM procurement_recommendations WHERE id = ?",
        (rec_id,),
    ).fetchone()
    if not old_rec:
        raise ValueError(f"Recommendation {rec_id} not found.")
    old_rec = dict(old_rec)

    product_name = old_rec["product_name"]
    supplier     = old_rec["supplier"] or ""
    quantity     = float(old_rec["suggested_order_qty"] or 0)

    rec_reason = build_recommendation_reason(product_name, supplier, quantity, quote_fields, conn)
    decision   = decide_procurement_action(quote_fields, quantity)
    rec_reason += f"\nDecision: {decision['action']}"
    rec_reason += f"\nDecision reason: {decision['decision_reason']}"
    if decision["risk_flags"]:
        rec_reason += "\nRisk flags: " + "; ".join(decision["risk_flags"])

    new_status = "rejected" if decision["action"] == "REJECT" else "recommendation_created"

    _execute(conn,
        "UPDATE procurement_recommendations "
        "SET status = 'superseded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (rec_id,),
    )

    new_rec_id = _insert_new_recommendation(
        conn,
        product_name=product_name,
        supplier=supplier,
        supplier_email=old_rec.get("supplier_email") or "",
        current_stock_kg=float(old_rec.get("current_stock_kg") or 0),
        reorder_at_kg=float(old_rec.get("reorder_at_kg") or 0),
        suggested_order_qty=quantity,
        urgency=old_rec.get("urgency") or "medium",
        reason=rec_reason,
        status=new_status,
        parent_rec_id=rec_id,
    )
    conn.commit()
    print(f"[RECOMMENDATION] superseded old_rec={rec_id} new_rec={new_rec_id} status={new_status}")

    approval_draft_id = None
    send_result       = None
    if decision["action"] != "REJECT":
        approval_draft_id = create_order_approval_draft(new_rec_id)
        try:
            send_result = send_order_approval_draft(approval_draft_id)
        except Exception as exc:
            send_result = f"ERROR: {exc}"
        try:
            from agents.slack_notifier import send_approval_reminder
            send_approval_reminder(
                product_name=product_name,
                supplier=supplier,
                run_id=approval_draft_id,
                quantity=quantity,
                decision=decision["action"],
            )
        except Exception as exc:
            print(f"[SLACK NOTIFY] Unexpected error sending approval reminder: {exc}")

    return {
        "new_rec_id":        new_rec_id,
        "approval_draft_id": approval_draft_id,
        "send_result":       send_result,
        "decision":          decision,
        "product_name":      product_name,
        "supplier":          supplier,
        "quantity":          quantity,
        "rec_reason":        rec_reason,
    }


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


class ProcurementAgent:
    def __init__(self, *args, **kwargs):
        pass

    def run(self) -> dict:
        items = get_low_stock_items()
        drafts = draft_procurement_emails()

        return {
            "status": "completed",
            "total_items_checked": len(items),
            "recommendations_created": len(drafts),
            "drafts_created": len(drafts),
            "skipped_duplicates": 0,
            "errors": [],
            "drafts": drafts,
        }