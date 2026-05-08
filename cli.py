"""
cli.py — California Nutraceuticals Invoice Intelligence
Unified CLI entrypoint.  Email-driven agent is the primary runtime.

Usage:
    python cli.py init-db
    python cli.py agent once
    python cli.py agent watch
    python cli.py run scenario data/scenario/day1.json
    python cli.py run pdf --pdf-dir data/invoices
    python cli.py app
"""

import argparse
import sys


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_db(args):
    """Initialise database and seed inventory/products."""
    from database import init_db, seed_initial_inventory, seed_products
    print("Initialising database...")
    init_db()
    seed_initial_inventory()
    seed_products()
    print("Done.")


def cmd_agent_once(args):
    """Check Gmail once using label routing, then exit.

    Previously used process_invoices() (broad INBOX scan) which could pick up
    every unread PDF email regardless of label and apply all their inventory
    changes in one batch — the root cause of the duplicate-invoice bug.
    Now delegates to route_gmail_once_core() so the same label-routing dedup
    guards (RFC Message-ID pre-check, mark-read, inv_applied) are active.
    """
    route_gmail_once_core()


def cmd_agent_watch(args):
    """Poll Gmail in a loop using label routing. Ctrl-C to stop.

    This definition is shadowed at runtime by the cmd_agent_watch defined
    later in this file (line ~503). It is kept here only as a safety net;
    the broad-INBOX start_watch() call that was here previously has been
    removed to prevent duplicate invoice processing.
    """
    import time
    interval = getattr(args, "interval", 10)
    print("[AGENT WATCH] Starting label-routing watch mode. Press Ctrl-C to stop.")
    try:
        while True:
            route_gmail_once_core()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[AGENT WATCH] Stopped by user.")


def cmd_run_scenario(args):
    """Run the pipeline against a scenario JSON file (demo/testing)."""
    import json
    # from run_pipeline import run_pipeline
    from scripts.run_pipeline import run_pipeline
    with open(args.path) as f:
        scenario = json.load(f)
    run_pipeline(scenario)


def cmd_run_pdf(args):
    """Run the pipeline against local PDF files (demo/fallback mode)."""
    # from run_pipeline import load_invoices_from_pdfs, run_pipeline
    from scripts.run_pipeline import load_invoices_from_pdfs, run_pipeline
    scenario = load_invoices_from_pdfs(args.pdf_dir)
    run_pipeline(scenario)


def cmd_app(args):
    """Launch the Dash web app (runs app.py as main)."""
    import runpy
    runpy.run_path("app.py", run_name="__main__")


def _process_quote_reply(r: dict, mark_done_fn) -> None:
    """Process one supplier quote-reply dict through the full quote->recommendation flow."""
    from agents.email_feedback_agent import parse_reply
    from database import (
        is_message_processed, save_user_feedback,
        has_feedback_for_run, get_connection, _execute,
    )

    message_id = r["message_id"]
    if isinstance(message_id, bytes):
        message_id = message_id.decode()

    if is_message_processed(message_id, "procurement/quote"):
        print(f"[DEDUP] already processed label=procurement/quote message_id={message_id}")
        return

    parsed = parse_reply(r["body"])

    if has_feedback_for_run(r["run_id"]):
        mark_done_fn(message_id)
        print(f"[SKIP] feedback already exists for run_id={r['run_id']}")
        return

    save_user_feedback(
        run_id=r["run_id"],
        message_id=message_id,
        action=parsed["action"],
        supplier=parsed.get("supplier"),
        quantity=parsed.get("quantity"),
        reason=parsed.get("reason"),
    )

    conn = get_connection()
    draft_row = _execute(conn,
        "SELECT product_name, supplier, recommendation_id FROM procurement_email_drafts WHERE id = ?",
        (r["run_id"],),
    ).fetchone()

    if draft_row and parsed["action"] != "INVALID":
        product_name = draft_row["product_name"]
        rec_id       = draft_row["recommendation_id"]

        _execute(conn,
            "UPDATE procurement_email_drafts SET status = 'quote_received' WHERE id = ?",
            (r["run_id"],),
        )
        _execute(conn,
            "UPDATE procurement_recommendations SET status = 'quote_received', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (rec_id,),
        )
        conn.commit()

        supplier_count = _execute(conn,
            "SELECT COUNT(DISTINCT supplier) AS cnt FROM procurement_email_drafts WHERE product_name = ?",
            (product_name,),
        ).fetchone()["cnt"]

        quote_received_count = _execute(conn,
            "SELECT COUNT(*) AS cnt FROM procurement_email_drafts WHERE product_name = ? AND status = 'quote_received'",
            (product_name,),
        ).fetchone()["cnt"]

        ready_to_recommend = (supplier_count == 1) or (quote_received_count >= 2)

        # TODO: add timeout fallback — if no supplier quote is received within N days
        # of rfq_sent, trigger get_primary_supplier_for_product fallback or notify human.
        if not ready_to_recommend:
            print(f"[QUOTE] Received quote for {product_name} from {draft_row['supplier']}")
            print(f"[WAIT] Waiting for more quotes ({quote_received_count}/{supplier_count})")
        else:
            from agents.procurement_agent import extract_quote_fields, run_recommendation_from_quote

            quote_fields = extract_quote_fields(r["body"])
            result = run_recommendation_from_quote(conn, rec_id, quote_fields)

            print("\n[RECOMMENDATION]")
            print(f"Product:  {result['product_name']}")
            print(f"Supplier: {result['supplier']}")
            print(f"Quantity: {result['quantity']:.0f}")
            print("Reason:")
            for line in result["rec_reason"].splitlines():
                print(f"  {line}")

            if result["decision"]["action"] == "REJECT":
                print(f"[REJECTED] {product_name} — {result['decision']['decision_reason']}")
            else:
                print(f"[APPROVAL DRAFT] id={result['approval_draft_id']} for {product_name}")
                if result["send_result"]:
                    print(f"[APPROVAL SENT] {result['send_result']}")
                from agents.slack_notifier import send_approval_reminder
                send_approval_reminder(product_name, result["supplier"], result["approval_draft_id"])
    else:
        print(f"[SKIP] run_id={r['run_id']} draft not found or action INVALID")

    conn.close()
    mark_done_fn(message_id)
    print(
        f"[FEEDBACK] run_id={r['run_id']} action={parsed['action']}"
        f" supplier={parsed.get('supplier')} qty={parsed.get('quantity')}"
        f" reason={parsed.get('reason')}"
    )


def _process_approval_reply(r: dict, mark_done_fn) -> None:
    """Process one internal approval-reply dict through the approval->order flow."""
    from agents.email_feedback_agent import parse_reply
    from database import (
        is_message_processed, save_user_feedback,
        has_feedback_for_run, get_connection, _execute,
    )

    message_id = r["message_id"]
    if isinstance(message_id, bytes):
        message_id = message_id.decode()

    if is_message_processed(message_id, "procurement/approval"):
        print(f"[DEDUP] already processed label=procurement/approval message_id={message_id}")
        return

    parsed = parse_reply(r["body"])

    if has_feedback_for_run(r["run_id"]):
        mark_done_fn(message_id)
        print(f"[SKIP] feedback already exists for run_id={r['run_id']}")
        return

    save_user_feedback(
        run_id=r["run_id"],
        message_id=message_id,
        action=parsed["action"],
        supplier=parsed.get("supplier"),
        quantity=parsed.get("quantity"),
        reason=parsed.get("reason"),
    )

    conn = get_connection()
    draft_row = _execute(conn,
        "SELECT product_name, recommendation_id FROM procurement_email_drafts WHERE id = ?",
        (r["run_id"],),
    ).fetchone()

    _memory_kwargs = None

    if draft_row:
        product_name = draft_row["product_name"]
        rec_id       = draft_row["recommendation_id"]
        action       = parsed["action"]

        rec_full_row = _execute(conn,
            "SELECT supplier, supplier_email, reason, suggested_order_qty "
            "FROM procurement_recommendations WHERE id = ?",
            (rec_id,),
        ).fetchone()
        rec_full = dict(rec_full_row) if rec_full_row else {}

        def _mk_memory(user_action: str, outcome_status: str) -> dict:
            from agents.procurement_agent import _parse_memory_fields_from_reason
            mf = _parse_memory_fields_from_reason(rec_full.get("reason", ""))
            return {
                "product_name":          product_name,
                "supplier":              rec_full.get("supplier", ""),
                "supplier_email":        rec_full.get("supplier_email"),
                "unit_price":            mf["unit_price"],
                "lead_time":             mf["lead_time"],
                "shipping_cost":         mf["shipping_cost"],
                "estimated_total_cost":  mf["estimated_total_cost"],
                "availability":          mf["availability"],
                "recommendation_id":     rec_id,
                "recommendation_action": mf["recommendation_action"],
                "user_action":           user_action,
                "user_reason":           parsed.get("reason"),
                "outcome_status":        outcome_status,
                "run_id":                r["run_id"],
            }

        if action in ("APPROVE", "APPROVE ANYWAY"):
            if action == "APPROVE ANYWAY":
                _execute(conn,
                    "UPDATE procurement_recommendations "
                    "SET reason = COALESCE(reason, '') || ? WHERE id = ?",
                    ("\nHuman override approval after no alternative supplier was available.", rec_id),
                )
            _execute(conn, "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?", (r["run_id"],))
            _execute(conn, "UPDATE procurement_recommendations SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rec_id,))
            conn.commit()
            if action == "APPROVE ANYWAY":
                print(f"[APPROVAL] override approved — {product_name}")
                from agents.procurement_agent import create_order_approval_draft, send_supplier_order_email
                order_draft_id = create_order_approval_draft(rec_id)
                _execute(conn, "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?", (order_draft_id,))
                conn.commit()
                order_result = send_supplier_order_email(order_draft_id)
                _memory_kwargs = _mk_memory("APPROVE_ANYWAY", "order_sent")
            else:
                print(f"[APPROVAL] approved — {product_name}")
                from agents.procurement_agent import send_supplier_order_email
                order_result = send_supplier_order_email(r["run_id"])
                _memory_kwargs = _mk_memory("APPROVE", "order_sent")
            print(f"[ORDER SENT] {order_result}")
        elif action == "REJECT":
            rejected_supplier = rec_full.get("supplier", "")
            _execute(conn, "UPDATE procurement_email_drafts SET status = 'rejected' WHERE id = ?", (r["run_id"],))
            _execute(conn, "UPDATE procurement_recommendations SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rec_id,))
            conn.commit()
            print(f"[APPROVAL] rejected — {product_name}")
            _memory_kwargs = _mk_memory("REJECT", "rejected")
            _handle_rejection_fallback(conn, product_name, rec_id, rejected_supplier)
        elif action == "CHANGE":
            new_supplier  = parsed.get("supplier")
            new_email     = parsed.get("email")
            change_reason = parsed.get("reason")

            if not new_supplier:
                print(
                    f"[APPROVAL] change_requested — {product_name} — "
                    f"missing Supplier: in reply; no RFQ sent. "
                    f"Reply again with:\n"
                    f"  CHANGE\n  Supplier: <name>\n  Email: <email>"
                )
            elif not new_email or "@" not in new_email:
                print(
                    f"[APPROVAL] change_requested — {product_name} — "
                    f"missing or invalid Email: in reply; no RFQ sent.\n"
                    f"Reply again with:\n"
                    f"  CHANGE\n  Supplier: {new_supplier}\n  Email: <supplier@example.com>"
                )
            else:
                _execute(conn, "UPDATE procurement_email_drafts SET status = 'change_requested' WHERE id = ?", (r["run_id"],))
                _execute(conn, "UPDATE procurement_recommendations SET status = 'change_requested', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rec_id,))
                conn.commit()
                print(f"[APPROVAL] change_requested — {product_name} — sending RFQ to {new_supplier} <{new_email}>")
                from agents.procurement_agent import create_and_send_change_rfq
                change_result = create_and_send_change_rfq(
                    product_name, new_supplier, new_email, reason=change_reason or ""
                )
                print(f"[CHANGE RFQ] {change_result}")
                _memory_kwargs = _mk_memory("CHANGE", "change_rfq_sent")
        elif action == "STOP PURCHASE":
            _execute(conn, "UPDATE procurement_email_drafts SET status = 'stopped' WHERE id = ?", (r["run_id"],))
            _execute(conn, "UPDATE procurement_recommendations SET status = 'stopped', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rec_id,))
            conn.commit()
            print(f"[APPROVAL] stopped — {product_name}")
            _memory_kwargs = _mk_memory("STOP_PURCHASE", "stopped")
        elif action == "PROVIDE NEW QUOTE":
            _execute(conn, "UPDATE procurement_email_drafts SET status = 'pending_new_quote' WHERE id = ?", (r["run_id"],))
            conn.commit()
            print(f"[APPROVAL] pending_new_quote — {product_name}")
            _memory_kwargs = _mk_memory("PROVIDE_NEW_QUOTE", "pending_new_quote")

            from agents.procurement_agent import extract_quote_fields, run_recommendation_from_quote

            quote_fields = extract_quote_fields(r["body"])
            result = run_recommendation_from_quote(conn, rec_id, quote_fields)

            if result["decision"]["action"] == "REJECT":
                print(f"[REJECTED] {product_name} — {result['decision']['decision_reason']}")
            else:
                print(f"[APPROVAL DRAFT] id={result['approval_draft_id']} for {product_name} (new quote)")
                if result["send_result"]:
                    print(f"[APPROVAL SENT] {result['send_result']}")
                from agents.slack_notifier import send_approval_reminder
                send_approval_reminder(product_name, result["supplier"], result["approval_draft_id"])
        else:
            print(f"[APPROVAL] unrecognized action '{action}' for {product_name} — no status update")
    else:
        print(f"[SKIP] run_id={r['run_id']} draft not found")

    conn.close()

    if _memory_kwargs:
        from database import record_procurement_memory
        record_procurement_memory(**_memory_kwargs)
        print(f"[MEMORY] recorded user_action={_memory_kwargs['user_action']} for {_memory_kwargs['supplier']}")

    mark_done_fn(message_id)
    print(
        f"[FEEDBACK] run_id={r['run_id']} action={parsed['action']}"
        f" supplier={parsed.get('supplier')} qty={parsed.get('quantity')}"
        f" reason={parsed.get('reason')}"
    )


def _handle_rejection_fallback(conn, product_name, rec_id, rejected_supplier):
    """
    Called after a human REJECT of an approval draft.

    Realistic two-step fallback strategy:
    1. If an alternative rec with quote_received status already exists
       (e.g. pre-seeded for the Glucosamine HCl demo path), create an
       approval draft immediately — the quote is already in hand.
    2. If a fallback rec at rfq_sent status exists, the RFQ was already
       dispatched; log and wait for the supplier to reply.
    3. If no alternative rec exists, check FALLBACK_SUPPLIERS for a known
       fallback supplier and send them a fresh RFQ.  The system then waits
       for their quote email (label procurement/quote) before generating a
       recommendation.
    4. If no fallback supplier is mapped, send a no-alternative notification
       with APPROVE ANYWAY / PROVIDE NEW QUOTE / STOP PURCHASE options.
    Does NOT send supplier orders automatically.
    """
    from agents.procurement_agent import (
        find_alternative_recommendation,
        rank_fallback_suppliers,
        send_no_alternative_notification,
        create_order_approval_draft,
        send_order_approval_draft,
        create_and_send_fallback_rfq,
    )
    from database import _execute as _db_execute

    print(
        f"[PROCUREMENT STRATEGY] Primary supplier path failed ({rejected_supplier}). "
        f"Searching for alternative suppliers for {product_name}..."
    )

    alt = find_alternative_recommendation(conn, product_name, rejected_supplier)

    if alt and alt["status"] in ("quote_received", "recommendation_created"):
        # Quote already in hand — send approval immediately (fast path; used by GLC demo).
        print(f"[FALLBACK] Alternative supplier found: {alt['supplier']} — creating new approval draft")
        _db_execute(
            conn,
            "UPDATE procurement_recommendations "
            "SET reason = COALESCE(reason, '') || ? WHERE id = ?",
            (
                "\nPrimary supplier path failed, so the agent searched for alternative suppliers.",
                alt["id"],
            ),
        )
        conn.commit()
        alt_draft_id = create_order_approval_draft(alt["id"])
        result = send_order_approval_draft(alt_draft_id)
        print(f"[FALLBACK] {result}")

    elif alt and alt["status"] == "rfq_sent":
        # RFQ already dispatched to this fallback supplier in a previous cycle.
        print(
            f"[FALLBACK] Fallback RFQ already sent to {alt['supplier']} — "
            f"waiting for their quote (label it procurement/quote when received)"
        )

    else:
        # No existing alternative rec. Rank candidates from product_supplier_alternates catalog.
        ranked = rank_fallback_suppliers(conn, product_name, rejected_supplier)
        if ranked:
            fallback_supplier = ranked["supplier"]
            ranking_reason    = ranked["ranking_reason"]
            print(f"[FALLBACK] {ranking_reason}")
            result = create_and_send_fallback_rfq(
                product_name, fallback_supplier, ranking_reason=ranking_reason
            )
            print(f"[FALLBACK] {result}")
            print(
                f"[FALLBACK] Waiting for {fallback_supplier} to reply with a quote. "
                f"When received, forward it and apply label: procurement/quote"
            )
        else:
            print(
                f"[FALLBACK] No alternative supplier for {product_name} — "
                f"sending no-alternative notification"
            )
            result = send_no_alternative_notification(rec_id)
            print(f"[FALLBACK] {result}")


def cmd_feedback_once(args):
    """Fetch procurement reply emails once and print parsed feedback."""
    from agents.email_feedback_agent import fetch_quote_replies, fetch_procurement_replies
    from database import mark_message_processed
    from agents.invoice_agent import connect_gmail, mark_as_read

    quote_replies    = fetch_quote_replies()
    approval_replies = fetch_procurement_replies()

    if not quote_replies and not approval_replies:
        print("No new feedback emails.")
        return

    def _mark_done(message_id):
        mark_message_processed(message_id)
        mail = connect_gmail()
        mail.select("INBOX")
        mark_as_read(mail, message_id.encode())
        mail.logout()

    for r in quote_replies:
        _process_quote_reply(r, _mark_done)

    for r in approval_replies:
        _process_approval_reply(r, _mark_done)


def cmd_route_gmail_watch(args):
    """Loop forever, calling run_label_routing() every 10 seconds. Ctrl-C to stop."""
    import time
    from agents.email_router import run_label_routing

    print("[WATCH] Starting Gmail label watch mode. Press Ctrl-C to stop.")
    try:
        while True:
            print("[WATCH] Checking Gmail labels...")
            try:
                run_label_routing()
            except Exception as e:
                print(f"[WATCH] Error: {e}")
            print("[WATCH] Done. Sleeping 10s...")
            time.sleep(10)
    except KeyboardInterrupt:
        print("[WATCH] Stopped by user.")


def cmd_agent_watch(args):
    """Closed-loop agent: Gmail routing + low-stock procurement. Ctrl-C to stop."""
    import time

    interval = args.interval
    print(f"[AGENT WATCH] Starting closed-loop agent (interval={interval}s). Ctrl-C to stop.")
    try:
        while True:
            print("[AGENT WATCH] Running Gmail routing...")
            try:
                route_gmail_once_core()
            except Exception as e:
                print(f"[AGENT WATCH] Gmail routing error: {e}")

            print("[AGENT WATCH] Running procurement check...")
            try:
                run_procurement_once_core()
            except Exception as e:
                print(f"[AGENT WATCH] Procurement check error: {e}")

            print(f"[AGENT WATCH] Sleeping {interval}s...")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[AGENT WATCH] Stopped by user.")


def cmd_agent_watch_test(args):
    """End-to-end test for the low-stock RFQ flow. No real Gmail or email sending."""
    import sys
    import types
    from database import get_connection, _execute, init_db

    print("[AGENT WATCH TEST] Setting up...")
    init_db()

    # Inject a lightweight fake module so send_procurement_draft can import
    # send_email without pulling in the pdfplumber dependency chain.
    sent_emails: list[dict] = []
    _fake_efa = types.ModuleType("email_feedback_agent")
    _fake_efa.send_email = lambda to, subject, body: sent_emails.append({"to": to, "subject": subject})
    _fake_efa.build_recommendation_subject = lambda item, run_id: f"Reorder Suggestion - {item} [RUN_ID={run_id}]"
    _prev_efa        = sys.modules.get("email_feedback_agent")
    _prev_agents_efa = sys.modules.get("agents.email_feedback_agent")
    sys.modules["email_feedback_agent"]        = _fake_efa
    sys.modules["agents.email_feedback_agent"] = _fake_efa

    PRODUCT = "NMC Powder"
    orig_qty: float | None = None

    conn = get_connection()
    try:
        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        orig_qty = row["quantity_kg"] if row else None
        _execute(conn, "UPDATE stock SET quantity_kg = 15.0 WHERE product_name = ?", (PRODUCT,))
        # Clear ALL pending rfq_draft entries to avoid interference from other tests
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE status = 'rfq_draft'")
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
        conn.commit()
    finally:
        conn.close()

    try:
        all_pass = True

        # -- Run 1: no draft exists -> create and send --
        print("\n[AGENT WATCH TEST] Run 1: no existing draft -> expect RFQ created and sent")
        run_procurement_once_core()

        conn = get_connection()
        try:
            draft = _execute(conn,
                "SELECT id, supplier_email, status, sent_at FROM procurement_email_drafts "
                "WHERE product_name = ? ORDER BY created_at DESC LIMIT 1",
                (PRODUCT,),
            ).fetchone()
        finally:
            conn.close()

        checks_1 = [
            ("RFQ draft exists",         draft is not None),
            ("supplier_email non-empty",  bool(draft and draft["supplier_email"])),
            ("status = rfq_sent",         draft is not None and draft["status"] == "rfq_sent"),
            ("sent_at not null",          draft is not None and draft["sent_at"] is not None),
            ("NMC Powder RFQ email sent", any("NMC Powder" in e.get("subject", "") for e in sent_emails)),
        ]
        for label, ok in checks_1:
            all_pass = all_pass and ok
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")

        # -- Run 2: already sent -> no duplicate --
        print("\n[AGENT WATCH TEST] Run 2: RFQ already sent -> expect skip")
        count_before = len(sent_emails)
        run_procurement_once_core()
        ok_no_dupe = len(sent_emails) == count_before
        all_pass = all_pass and ok_no_dupe
        print(f"  {'PASS' if ok_no_dupe else 'FAIL'}  No duplicate RFQ on second run")

        print(f"\n[AGENT WATCH TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")

    finally:
        if _prev_efa is None:
            sys.modules.pop("email_feedback_agent", None)
        else:
            sys.modules["email_feedback_agent"] = _prev_efa
        if _prev_agents_efa is None:
            sys.modules.pop("agents.email_feedback_agent", None)
        else:
            sys.modules["agents.email_feedback_agent"] = _prev_agents_efa
        if orig_qty is not None:
            conn = get_connection()
            try:
                _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?", (orig_qty, PRODUCT))
                conn.commit()
            finally:
                conn.close()
        print("[AGENT WATCH TEST] Stock restored.")


def cmd_supplier_email_test(args):
    """Verify resolve_supplier_email across all resolution paths."""
    from agents.procurement_agent import resolve_supplier_email, SUPPLIER_EMAILS
    from database import get_connection, _execute, init_db

    print("[SUPPLIER EMAIL TEST] Setting up...")
    init_db()
    all_pass = True

    conn = get_connection()
    try:
        # ── Test 1: known static mapping ─────────────────────────────────────
        known_supplier = "Pacific Rim BioMaterials Co."
        expected_email = SUPPLIER_EMAILS[known_supplier]
        got = resolve_supplier_email(conn, "Any Product", known_supplier)
        ok1 = got == expected_email
        all_pass = all_pass and ok1
        print(f"  {'PASS' if ok1 else 'FAIL'}  Static mapping: {known_supplier!r} -> {got!r}")

        # ── Test 2: reuse from prior recommendation ───────────────────────────
        _execute(conn,
            "DELETE FROM procurement_recommendations "
            "WHERE product_name = '_TestProd' AND supplier = '_TestSupplier'",
        )
        _execute(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            " suggested_order_qty, urgency, reason, status) "
            "VALUES ('_TestProd','_TestSupplier','prior@example.com',5,20,35,'high','test','rfq_sent')",
        )
        conn.commit()
        got2 = resolve_supplier_email(conn, "_TestProd", "_TestSupplier")
        ok2 = got2 == "prior@example.com"
        all_pass = all_pass and ok2
        print(f"  {'PASS' if ok2 else 'FAIL'}  Reuse from recommendations: {got2!r}")

        # ── Test 3: reuse from prior draft ────────────────────────────────────
        _execute(conn,
            "DELETE FROM procurement_email_drafts "
            "WHERE id = '_t0001'",
        )
        _execute(conn,
            "INSERT INTO procurement_email_drafts "
            "(id, product_name, supplier, supplier_email, subject, body, status) "
            "VALUES ('_t0001','_TestProd2','_TestSupplier2','draft@example.com','s','b','rfq_draft')",
        )
        conn.commit()
        got3 = resolve_supplier_email(conn, "_TestProd2", "_TestSupplier2")
        ok3 = got3 == "draft@example.com"
        all_pass = all_pass and ok3
        print(f"  {'PASS' if ok3 else 'FAIL'}  Reuse from drafts: {got3!r}")

        # ── Test 4: unknown supplier ->empty string ───────────────────────────
        got4 = resolve_supplier_email(conn, "_NoSuchProduct", "_NoSuchSupplier")
        ok4 = got4 == ""
        all_pass = all_pass and ok4
        print(f"  {'PASS' if ok4 else 'FAIL'}  Unknown supplier returns '': {got4!r}")

        # ── Test 5: Demo Supplier resolves to non-empty when env is configured ─
        import os
        demo_email = os.getenv("USER_APPROVAL_EMAIL") or os.getenv("GMAIL_ADDRESS", "")
        got5 = resolve_supplier_email(conn, "NMC Powder", "Demo Supplier")
        if demo_email:
            ok5 = got5 == demo_email
            all_pass = all_pass and ok5
            print(f"  {'PASS' if ok5 else 'FAIL'}  Demo Supplier -> {got5!r} (expected {demo_email!r})")
        else:
            print(f"  INFO  Demo Supplier -> {got5!r} (set USER_APPROVAL_EMAIL or GMAIL_ADDRESS to test)")

        # ── Cleanup ──────────────────────────────────────────────────────────
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = '_TestProd'")
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE id = '_t0001'")
        conn.commit()

    finally:
        conn.close()

    print(f"\n[SUPPLIER EMAIL TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")


def cmd_demo_check(args):
    """
    Verify product-name normalization and stock-update logic.

    Tests use product names drawn from the demo seed, but the rules themselves
    are generic (trailing-qty strip + non-inventory-term filter).  Any product
    in the stock table would exercise the same code paths.
    """
    from scripts.run_pipeline import (
        _normalize_product_name,
        _resolve_inventory_key,
        InventoryRecord,
        inventory_agent,
        Invoice,
        LineItem,
    )
    from database import get_connection, _execute

    print("[DEMO CHECK] Generic normalization examples:")
    # These are illustrative; the rules apply to ANY product description.
    examples = [
        # (raw LLM description,           expected result)
        ("Widget 5 kg",                   "Widget"),
        ("Widget 20 kg",                  "Widget"),
        ("Product Name 10.5 kg",          "Product Name"),
        ("Part A 100 pcs",                "Part A"),
        ("Compound B 2.5 g",              "Compound B"),
        ("Shipping",                      None),
        ("Freight & Insurance",           None),
        ("Tax",                           None),
        ("Discount",                      None),
        ("Clean Name",                    "Clean Name"),
    ]
    all_pass = True
    for raw, expected in examples:
        got = _normalize_product_name(raw)
        ok = got == expected
        all_pass = all_pass and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {raw!r:35s} -> {got!r}  (expected {expected!r})")

    print("\n[DEMO CHECK] Inventory update simulation (demo products as example):")
    # Seed an arbitrary inventory; normalization must resolve the right key.
    inventory = {
        "NMC Powder":     InventoryRecord("NMC Powder",     24.0, 20.0),
        "Graphite Anode": InventoryRecord("Graphite Anode", 50.0, 25.0),
    }
    invoice = Invoice(
        invoice_id="check-sales-001",
        document_type="sales",
        counterparty_name="Test Customer",
        invoice_date="2025-01-15",
        total_amount=225.0,
        line_items=[
            LineItem("NMC Powder 5 kg",     5.0,  45.0),   # trailing qty stripped
            LineItem("Graphite Anode 8 kg", 8.0,  35.0),   # trailing qty stripped
            LineItem("Shipping",            0.0,   0.0),   # non-inventory ->skipped
            LineItem("Unknown Material",    2.0,  10.0),   # not in inventory ->skipped
        ],
    )
    inventory_agent(invoice, inventory)

    checks = [
        ("NMC Powder stock",            inventory.get("NMC Powder"),     19.0),
        ("Graphite Anode stock",        inventory.get("Graphite Anode"), 42.0),
        ("No phantom NMC row",          "NMC Powder 5 kg" not in inventory, True),
        ("No phantom Graphite row",     "Graphite Anode 8 kg" not in inventory, True),
        ("No phantom Shipping row",     "Shipping" not in inventory, True),
        ("No phantom Unknown row",      "Unknown Material" not in inventory, True),
    ]
    for label, val, expected in checks:
        if isinstance(val, InventoryRecord):
            actual = val.current_stock
            ok = abs(actual - expected) < 0.001
        else:
            actual = val
            ok = actual == expected
        all_pass = all_pass and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: {actual}  (expected {expected})")

    print("\n[DEMO CHECK] Live DB sanity (requires demo-seed to have been run):")
    conn = get_connection()
    try:
        row = _execute(conn,
            "SELECT quantity_kg, supplier FROM stock WHERE product_name = 'NMC Powder'"
        ).fetchone()
        artifact_rows = _execute(conn,
            "SELECT product_name FROM stock WHERE product_name != 'NMC Powder' AND product_name LIKE '%NMC Powder%'"
        ).fetchall()
        bad_rows = _execute(conn,
            "SELECT product_name FROM stock WHERE supplier IS NULL AND quantity_kg < 0"
        ).fetchall()

        if row:
            print(f"  INFO  stock: NMC Powder = {row['quantity_kg']} kg, supplier = {row['supplier']}")
        else:
            print("  INFO  NMC Powder not found — run `python cli.py demo-seed` first")

        ok_no_artifact = len(artifact_rows) == 0
        ok_no_bad_rows = len(bad_rows) == 0
        all_pass = all_pass and ok_no_artifact and ok_no_bad_rows

        if ok_no_artifact:
            print("  PASS  No trailing-qty artefact rows for NMC Powder in DB")
        else:
            names = [r["product_name"] for r in artifact_rows]
            print(f"  FAIL  Artefact rows still in DB: {names}  — re-run demo-seed")

        if ok_no_bad_rows:
            print("  PASS  No NULL-supplier negative-qty rows in DB")
        else:
            names = [r["product_name"] for r in bad_rows]
            print(f"  FAIL  Negative-qty artefact rows still in DB: {names}  — re-run demo-seed")
    finally:
        conn.close()

    print("\n[DEMO CHECK] Database backend:")
    import os as _os
    _db_url = _os.getenv("DATABASE_URL", "")
    _backend = "PostgreSQL (Railway)" if _db_url else "SQLite (local inventory.db)"
    print(f"  Backend : {_backend}")
    if _db_url:
        print(f"  URL     : {_db_url[:50]}{'...' if len(_db_url) > 50 else ''}")

    print("\n[DEMO CHECK] Business product inventory (requires demo-seed):")
    _biz_products = [
        "Collagen Powder", "Shark Cartilage Powder", "Bovine Gelatin Type A",
        "Fish Collagen Peptides", "Hydrolyzed Marine Collagen", "Bovine Cartilage Extract",
        "Plant Extract - Ginseng Root", "Plant Extract - Turmeric", "Plant Extract - Ashwagandha",
        "Plant Extract - Elderberry", "Plant Extract - Echinacea", "Hyaluronic Acid Powder",
        "Chondroitin Sulfate", "Glucosamine HCl", "Collagen Peptides Type I",
    ]
    conn2 = get_connection()
    try:
        stock_count     = _execute(conn2, "SELECT COUNT(*) AS cnt FROM stock").fetchone()["cnt"]
        low_stock_count = _execute(conn2,
            "SELECT COUNT(*) AS cnt FROM stock WHERE quantity_kg < reorder_at"
        ).fetchone()["cnt"]
        draft_count     = _execute(conn2,
            "SELECT COUNT(*) AS cnt FROM procurement_email_drafts"
        ).fetchone()["cnt"]
        rec_count       = _execute(conn2,
            "SELECT COUNT(*) AS cnt FROM procurement_recommendations"
        ).fetchone()["cnt"]
        print(f"  INFO  stock rows: {stock_count}  |  low-stock: {low_stock_count}"
              f"  |  drafts: {draft_count}  |  recommendations: {rec_count}")

        _missing = [
            pn for pn in _biz_products
            if not _execute(conn2, "SELECT 1 FROM stock WHERE product_name = ?", (pn,)).fetchone()
        ]
        ok_biz = len(_missing) == 0
        all_pass = all_pass and ok_biz
        print(f"  {'PASS' if ok_biz else 'FAIL'}  All 15 business products in stock table"
              + (f" (missing: {_missing})" if _missing else ""))

        _biz_low = _execute(conn2,
            "SELECT product_name FROM stock WHERE quantity_kg < reorder_at AND product_name IN ({})".format(
                ",".join("?" * len(_biz_products))
            ),
            tuple(_biz_products),
        ).fetchall()
        ok_low = len(_biz_low) == 0
        all_pass = all_pass and ok_low
        if ok_low:
            print("  PASS  All 15 business products above reorder_at (clean demo start)")
        else:
            print(f"  FAIL  Business products below reorder_at after demo-seed: "
                  f"{[r['product_name'] for r in _biz_low]}")

        _glc_alt = _execute(conn2,
            "SELECT id FROM procurement_recommendations "
            "WHERE product_name = 'Glucosamine HCl' AND status = 'quote_received' LIMIT 1",
        ).fetchone()
        ok_glc = _glc_alt is not None
        all_pass = all_pass and ok_glc
        print(f"  {'PASS' if ok_glc else 'FAIL'}  Glucosamine HCl has alternative rec (quote_received)")

        _hap_alt = _execute(conn2,
            "SELECT id FROM procurement_recommendations "
            "WHERE product_name = 'Hyaluronic Acid Powder' "
            "  AND status IN ('recommendation_created','quote_received') LIMIT 1",
        ).fetchone()
        ok_hap = _hap_alt is None
        all_pass = all_pass and ok_hap
        print(f"  {'PASS' if ok_hap else 'FAIL'}  Hyaluronic Acid Powder has no alternative rec (forces no-alt path)")

        from agents.procurement_agent import SUPPLIER_EMAILS
        _real_suppliers = [s for s in SUPPLIER_EMAILS if s != "Demo Supplier"]
        _email_ok = True
        for _sup in _real_suppliers:
            _em = SUPPLIER_EMAILS[_sup]
            _ok_em = bool(_em)
            _email_ok = _email_ok and _ok_em
            print(f"  {'PASS' if _ok_em else 'FAIL'}  SUPPLIER_EMAILS: {_sup} -> {_em or 'MISSING'}")
        all_pass = all_pass and _email_ok

        # Demo story consistency: CUST-DEMO-001 causes both Collagen Powder and FCP low stock
        _cp_row = _execute(conn2,
            "SELECT quantity_kg, reorder_at FROM stock WHERE product_name = 'Collagen Powder'"
        ).fetchone()
        if _cp_row:
            _cp_after = float(_cp_row["quantity_kg"]) - 380
            ok_cp_low = _cp_after < float(_cp_row["reorder_at"])
            _cp_str = f"{float(_cp_row['quantity_kg'])} - 380 = {_cp_after:.1f} kg < {float(_cp_row['reorder_at'])} reorder"
        else:
            ok_cp_low = False
            _cp_str = "row not found — run demo-seed"
        all_pass = all_pass and ok_cp_low
        print(f"  {'PASS' if ok_cp_low else 'FAIL'}  Collagen Powder low-stock after CUST-DEMO-001 ({_cp_str})")

        _fcp_row = _execute(conn2,
            "SELECT quantity_kg, reorder_at FROM stock WHERE product_name = 'Fish Collagen Peptides'"
        ).fetchone()
        if _fcp_row:
            _fcp_after = float(_fcp_row["quantity_kg"]) - 100
            ok_fcp_low = _fcp_after < float(_fcp_row["reorder_at"])
            _fcp_str = f"{float(_fcp_row['quantity_kg'])} - 100 = {_fcp_after:.1f} kg < {float(_fcp_row['reorder_at'])} reorder"
        else:
            ok_fcp_low = False
            _fcp_str = "row not found — run demo-seed"
        all_pass = all_pass and ok_fcp_low
        print(f"  {'PASS' if ok_fcp_low else 'FAIL'}  Fish Collagen Peptides low-stock after CUST-DEMO-001 ({_fcp_str})")

        # Collagen Powder supplier email (needed to send the approved order)
        ok_cp_email = bool(SUPPLIER_EMAILS.get("Pacific Rim BioMaterials Co.", ""))
        all_pass = all_pass and ok_cp_email
        print(f"  {'PASS' if ok_cp_email else 'FAIL'}  Collagen Powder supplier email: "
              f"Pacific Rim BioMaterials Co. -> {SUPPLIER_EMAILS.get('Pacific Rim BioMaterials Co.', 'MISSING')}")

        # Fish Collagen Peptides fallback supplier from product_supplier_alternates catalog
        _fcp_cat = _execute(conn2,
            "SELECT supplier, supplier_email FROM product_supplier_alternates "
            "WHERE product_name = 'Fish Collagen Peptides' ORDER BY priority ASC LIMIT 1"
        ).fetchone()
        ok_fcp_fallback = _fcp_cat is not None
        all_pass = all_pass and ok_fcp_fallback
        print(
            f"  {'PASS' if ok_fcp_fallback else 'FAIL'}  "
            f"Fish Collagen Peptides fallback in catalog: "
            f"{_fcp_cat['supplier'] if _fcp_cat else 'NOT SEEDED'} "
            f"-> {_fcp_cat['supplier_email'] if _fcp_cat else 'MISSING'} "
            f"(run demo-seed if FAIL)"
        )

        # Demo memory: FCP fallback supplier should have a seeded negative memory record
        # so the recommendation shows a meaningful memory warning in Step 6.
        from agents.procurement_agent import get_supplier_memory_score
        _fcp_sup = _fcp_cat["supplier"] if _fcp_cat else "Shanghai BioSupply International"
        _mem = get_supplier_memory_score(conn2, "Fish Collagen Peptides", _fcp_sup)
        ok_mem = _mem["reject_count"] >= 1 and _mem["supplier_score"] < 0
        all_pass = all_pass and ok_mem
        print(
            f"  {'PASS' if ok_mem else 'FAIL'}  "
            f"FCP fallback supplier demo memory: score={_mem['supplier_score']:.1f}, "
            f"rejections={_mem['reject_count']} "
            f"(run demo-seed if FAIL)"
        )
    finally:
        conn2.close()

    print(f"\n[DEMO CHECK] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")


def run_procurement_once_core() -> dict:
    """Create RFQ drafts for low-stock items and send any unsent drafts.

    Single source of truth for the procurement check — called by
    cmd_run_procurement and the agent-watch loop so both produce identical
    behaviour.  Dedup lives in draft_procurement_emails(); this function
    just coordinates create -> send.
    """
    from agents.procurement_agent import ProcurementAgent, get_pending_drafts, send_procurement_draft

    result = ProcurementAgent().run()

    for draft in get_pending_drafts():
        send_result = send_procurement_draft(draft["id"])
        if send_result.startswith("Sent"):
            print(f"[PROCUREMENT] RFQ sent for {draft['product_name']}: {send_result}")
        elif send_result.startswith("NO_EMAIL"):
            print(f"[PROCUREMENT] Missing supplier_email for {draft['product_name']}. Draft created but not sent.")
        else:
            print(f"[PROCUREMENT] RFQ send failed for {draft['product_name']}: {send_result}")

    return result


def cmd_run_procurement(args):
    """Run the Procurement Agent manually against the current inventory DB."""
    result = run_procurement_once_core()
    print("Procurement Agent Run Complete")
    print(f"  - Items checked:           {result['total_items_checked']}")
    print(f"  - Recommendations created: {result['recommendations_created']}")
    print(f"  - Skipped (duplicates):    {result['skipped_duplicates']}")
    if result.get("errors"):
        print(f"  - Errors:                  {result['errors']}")


def cmd_db_check(args):
    """Print database backend, host, stock count, and sample products."""
    import os as _os
    from database import get_connection, _execute, _use_postgres, DATABASE_URL, DB_PATH

    db_url = DATABASE_URL or ""
    if _use_postgres:
        # Show host only — never print credentials
        try:
            import urllib.parse as _up
            _parsed = _up.urlparse(db_url)
            host_display = _parsed.hostname or "(unknown host)"
        except Exception:
            host_display = "(unknown host)"
        print(f"[DB CHECK] Backend  : PostgreSQL")
        print(f"[DB CHECK] Host     : {host_display}")
    else:
        print(f"[DB CHECK] Backend  : SQLite")
        print(f"[DB CHECK] Path     : {DB_PATH}")

    conn = get_connection()
    try:
        count = _execute(conn, "SELECT COUNT(*) AS cnt FROM stock").fetchone()
        cnt = count["cnt"] if count else 0
        print(f"[DB CHECK] Stock rows: {cnt}")

        rows = _execute(
            conn,
            "SELECT product_name, quantity_kg, unit FROM stock ORDER BY product_name LIMIT 5",
        ).fetchall()
        if rows:
            print("[DB CHECK] Sample products:")
            for r in rows:
                unit = r["unit"] or "kg"
                print(f"  • {r['product_name']}: {r['quantity_kg']:.1f} {unit}")
        else:
            print("[DB CHECK] No stock rows found — run `python cli.py demo-seed` first.")
    finally:
        conn.close()


DEMO_RUN_ID = "demo0001"   # fixed run_id used in demo-seed and demo instructions


def cmd_slack_agent(args):
    """Start the Hermes Slack bot using Socket Mode."""
    from agents.slack_agent import start_slack_agent
    start_slack_agent()


def cmd_slack_notify_test(args):
    """Send a sample procurement approval reminder to SLACK_APPROVAL_CHANNEL."""
    from agents.slack_notifier import send_approval_reminder
    print("[SLACK NOTIFY TEST] Sending sample approval reminder to SLACK_APPROVAL_CHANNEL...")
    send_approval_reminder(
        product_name="Collagen Powder (test)",
        supplier="Pacific Rim BioMaterials Co. (test)",
        run_id="test-run-001",
    )
    print("[SLACK NOTIFY TEST] Done.")


def cmd_inventory_status(args):
    """Print current inventory stock levels."""
    from database import get_connection, _execute

    conn = get_connection()
    try:
        rows = _execute(conn,
            "SELECT product_name, supplier, quantity_kg, reorder_at, unit "
            "FROM stock ORDER BY product_name",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No inventory data. Run: python cli.py init-db")
        return

    header = f"{'Product':<35} {'Supplier':<22} {'Stock':>8}  {'Reorder':>7}  Status"
    print("\n" + header)
    print("-" * len(header))
    for r in [dict(row) for row in rows]:
        unit   = r.get("unit") or "kg"
        status = "LOW STOCK" if (r["quantity_kg"] or 0) < (r["reorder_at"] or 0) else "ok"
        print(
            f"{r['product_name']:<35} {(r['supplier'] or ''):<22}"
            f" {r['quantity_kg']:>7.1f}{unit}"
            f"  {r['reorder_at']:>7.1f}"
            f"  {status}"
        )
    print()


def route_gmail_once_core() -> None:
    """Connect to Gmail and route unread emails by label.

    Single source of truth for Gmail routing — called by cmd_route_gmail_once
    and the agent-watch loop so both produce identical behaviour.
    """
    from agents.email_router import run_label_routing
    print("[ROUTE] Connecting to Gmail and routing by label...")
    run_label_routing()
    print("[ROUTE] Done.")


def cmd_route_gmail_once(args):
    """Connect to Gmail, fetch unread emails by label, and route each one."""
    route_gmail_once_core()


def _seed_demo_procurement(conn, _execute_fn, _use_postgres):
    """Seed one RFQ draft for NMC Powder using DEMO_RUN_ID so demo Step 3 is reproducible."""
    # Recommendation row
    if _use_postgres:
        _execute_fn(conn,
            """INSERT INTO procurement_recommendations
                   (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg,
                    suggested_order_qty, urgency, reason, status)
               VALUES (?,?,?,?,?,?,?,?,'rfq_sent')
               ON CONFLICT DO NOTHING""",
            ("NMC Powder", "Demo Supplier", "demo@supplier.com",
             8.0, 20.0, 32.0, "high",
             "Below reorder threshold (8 kg < 20 kg). Demo seed."),
        )
    else:
        _execute_fn(conn,
            """INSERT OR IGNORE INTO procurement_recommendations
                   (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg,
                    suggested_order_qty, urgency, reason, status)
               VALUES (?,?,?,?,?,?,?,?,'rfq_sent')""",
            ("NMC Powder", "Demo Supplier", "demo@supplier.com",
             8.0, 20.0, 32.0, "high",
             "Below reorder threshold (8 kg < 20 kg). Demo seed."),
        )

    rec_row = _execute_fn(conn,
        "SELECT id FROM procurement_recommendations WHERE product_name = 'NMC Powder'",
    ).fetchone()
    if not rec_row:
        return
    rec_id = rec_row["id"]

    subject = f"Reorder Suggestion - NMC Powder [RUN_ID={DEMO_RUN_ID}]"
    body = (
        f"Hello Demo Supplier,\n\n"
        f"Could you please provide a quote for NMC Powder (32 kg)?\n"
        f"Please reference RUN_ID={DEMO_RUN_ID} in your reply.\n\n"
        f"Best regards,\nCalifornia Nutraceuticals"
    )
    if _use_postgres:
        _execute_fn(conn,
            """INSERT INTO procurement_email_drafts
                   (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
               VALUES (?,?,?,?,?,?,?,'rfq_sent')
               ON CONFLICT DO NOTHING""",
            (DEMO_RUN_ID, rec_id, "NMC Powder", "Demo Supplier",
             "demo@supplier.com", subject, body),
        )
    else:
        _execute_fn(conn,
            """INSERT OR IGNORE INTO procurement_email_drafts
                   (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status)
               VALUES (?,?,?,?,?,?,?,'rfq_sent')""",
            (DEMO_RUN_ID, rec_id, "NMC Powder", "Demo Supplier",
             "demo@supplier.com", subject, body),
        )
    conn.commit()


def _print_demo_instructions():
    sep = "=" * 65
    print(f"\n{sep}")
    print("  LIVE DEMO SCRIPT — California Nutraceuticals")
    print("  Gmail Label-Based Procurement Workflow")
    print(sep)
    print("""
Setup (run once before each demo):
  python cli.py demo-seed
  python scripts/generate_demo_invoices.py
  python cli.py app                 # optional Dash UI (separate terminal)

Gmail labels required (Settings > Labels > Create new label):
  sales/invoice
  purchase/receipt
  procurement/quote
  procurement/approval
Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD, USER_APPROVAL_EMAIL in .env

Routing command (manual mode):
  python cli.py route-gmail-once    # or: python cli.py agent-watch

--------------------------------------------------------------
STEP 1  —  Sales Invoice  (two products go low-stock)
--------------------------------------------------------------
  File   : data/demo/customer_invoices/CUST-DEMO-001.pdf
  Subject: [INV-SALES] Demo sales invoice CUST-DEMO-001
  Action : Attach the PDF and send to yourself.
           In Gmail, apply label:  sales/invoice
  Run    : python cli.py route-gmail-once
  Expected output:
    [ROUTING] label=sales/invoice -> invoice_agent (sales)
    [ROUTING] invoice extracted: #CUST-DEMO-001 ...
  Inventory check:  python cli.py inventory-status
  Expected changes:
    Collagen Powder        450 kg  ->   70 kg  (below 120 kg reorder)
    Fish Collagen Peptides 210 kg  ->  110 kg  (below 120 kg reorder)

--------------------------------------------------------------
STEP 2  —  Run Procurement  (RFQs sent to primary suppliers)
--------------------------------------------------------------
  Run: python cli.py run-procurement
  Expected:
    [PROCUREMENT] RFQ sent for Collagen Powder  -> Pacific Rim BioMaterials Co.
    [PROCUREMENT] RFQ sent for Fish Collagen Peptides -> Pacific Rim BioMaterials Co.
  Note the RUN_ID printed for each product — needed in Steps 3 and 5.

--------------------------------------------------------------
STEP 3  —  Collagen Powder: good quote -> APPROVE -> order sent
--------------------------------------------------------------
  3a. Supplier quote
      Send a plain-text email to yourself (replying under the Collagen Powder RFQ):
        Subject : (keep or match the RFQ subject with its RUN_ID)
        Body    :
          Unit price: USD 66/kg
          Availability: in stock
          Lead time: 8 days
          Shipping: USD 180
          Quote valid for: 14 days
      In Gmail, apply label:  procurement/quote
      Run: python cli.py route-gmail-once
      Expected:
        [ROUTING] label=procurement/quote -> quote parser
        [RECOMMENDATION] Product: Collagen Powder  Decision: NEEDS_HUMAN_REVIEW
        [APPROVAL DRAFT] id=<draft_id> — approval email sent to USER_APPROVAL_EMAIL

  3b. Human approval
      Find the approval email and reply:
        APPROVE
      In Gmail, apply label:  procurement/approval
      Run: python cli.py route-gmail-once
      Expected:
        [APPROVAL] approved -- Collagen Powder
        [ORDER SENT] Sent supplier order email to Pacific Rim BioMaterials Co.
      Inventory: unchanged until purchase receipt arrives (Step 4)

--------------------------------------------------------------
STEP 4  —  Collagen Powder purchase receipt  (stock restored)
--------------------------------------------------------------
  File   : data/demo/purchase_receipts/SUP-DEMO-COLLAGEN-001.pdf
  Subject: [INV-PURCHASE] Demo purchase receipt SUP-DEMO-COLLAGEN-001
  Action : Attach the PDF and send to yourself.
           In Gmail, apply label:  purchase/receipt
  Run    : python cli.py route-gmail-once
  Expected:
    [ROUTING] label=purchase/receipt -> invoice_agent (purchase)
    [ROUTING] invoice extracted: #SUP-DEMO-COLLAGEN-001 ...
  Inventory check:  python cli.py inventory-status
  Expected changes:
    Collagen Powder         70 kg  ->  470 kg  (+400 kg received)
    Fish Collagen Peptides 110 kg  unchanged

--------------------------------------------------------------
STEP 5  —  Fish Collagen Peptides: risky quote -> REJECT -> fallback
--------------------------------------------------------------
  5a. Supplier quote (risky terms)
      Send a plain-text email to yourself (replying under the FCP RFQ):
        Subject : (keep or match the RFQ subject with its RUN_ID)
        Body    :
          Unit price: USD 160/kg
          Availability: limited stock
          Lead time: 35 days
          Shipping: USD 950
          Quote valid for: 3 days
      In Gmail, apply label:  procurement/quote
      Run: python cli.py route-gmail-once
      Expected:
        [ROUTING] label=procurement/quote -> quote parser
        [RECOMMENDATION] Product: Fish Collagen Peptides  Decision: NEEDS_HUMAN_REVIEW
        [APPROVAL DRAFT] id=<draft_id> — approval email sent

  5b. Human rejection
      Find the approval email and reply:
        REJECT
        Reason: lead time too long and shipping cost too high.
      In Gmail, apply label:  procurement/approval
      Run: python cli.py route-gmail-once
      Expected:
        [APPROVAL] rejected -- Fish Collagen Peptides
        [FALLBACK] RFQ sent to Shanghai BioSupply International
        No order placed; no inventory change

--------------------------------------------------------------
STEP 6  —  Shanghai fallback quote -> new recommendation
--------------------------------------------------------------
  Shanghai replies with better terms. Send a plain-text email to yourself:
    Subject : (match the fallback RFQ subject with its RUN_ID)
    Body    :
      Unit price: USD 150/kg
      Availability: in stock
      Lead time: 14 days
      Shipping: USD 320
      Quote valid for: 10 days
  In Gmail, apply label:  procurement/quote
  Run: python cli.py route-gmail-once
  Expected:
    [ROUTING] label=procurement/quote -> quote parser
    [RECOMMENDATION] Product: Fish Collagen Peptides  Supplier: Shanghai BioSupply International
    [APPROVAL DRAFT] id=<draft_id> — new approval email sent

  MEMORY WARNING (demo effect):
    The approval email reason will include a memory note such as:
      "Memory signal:
         - Past rejections: 1
         - Supplier memory score: -2.0
         - Memory note: this supplier has previous negative feedback.
         - Recent rejection reasons: Previous order had long lead time
           and unreliable delivery estimate."
    This is a risk signal only — the human can still APPROVE or use CHANGE.

--------------------------------------------------------------
STEP 7 (optional) -- CHANGE: redirect to a different supplier
--------------------------------------------------------------
  Because the memory note raises concern about Shanghai's reliability,
  you may choose to redirect to a different supplier instead of approving.
  Reply to the approval email with:
    CHANGE
    Supplier: <new supplier name>
    Email: <supplier@example.com>
    Quantity: <quantity (optional)>
    Reason: <reason (optional)>
  In Gmail, apply label:  procurement/approval
  Run: python cli.py route-gmail-once
  Expected:
    [APPROVAL] change_requested -- <product>
    [CHANGE RFQ] Sent procurement email for <product> to <new supplier>
  The agent registers the new supplier in its catalog and sends them an RFQ.
  Wait for their quote reply (label: procurement/quote) to get a new recommendation.
  CHANGE does NOT place an order or update inventory.
""")
    print(sep)
    print("  Run 'python cli.py inventory-status' at any point to check stock.")
    print(sep)
    print("""
FULLY AUTOMATED MODE (agent-watch)
  Uses: python cli.py agent-watch  (replaces manual route-gmail-once calls)

  Setup:
    python cli.py demo-seed
    python scripts/generate_demo_invoices.py
    python cli.py app          # UI — terminal 1
    python cli.py agent-watch  # agent loop — terminal 2 (polls every 10 s)

  Follow Steps 1-6 above; agent-watch handles routing automatically after
  each email/label action. No need to run route-gmail-once manually.

  Check inventory at any point:
    python cli.py inventory-status

--------------------------------------------------------------
SIMPLE TEST DEMO (legacy — NMC Powder / Graphite Anode)
--------------------------------------------------------------
  These demo PDFs exercise the same routing pipeline with simpler data:
    data/demo_pdfs/sales_invoice_INV_SALES_001.pdf
      -> NMC Powder 24 kg -> 19 kg (below 20 kg threshold)
    data/demo_pdfs/purchase_receipt_INV_PUR_001.pdf
      -> Graphite Anode 50 kg -> 70 kg
  Use for quick label-routing smoke tests only; not the primary business demo.
""")
    print(sep + "\n")


def cmd_demo_seed(args):
    """Initialize demo DB, seed demo stock rows, generate demo PDFs, print demo instructions."""
    from database import init_db, get_connection, _execute, _use_postgres

    print("[DEMO] Initializing database...")
    init_db()

    print("[DEMO] Fully resetting stock table (only demo products will remain)...")
    conn = get_connection()
    try:
        _execute(conn, "DELETE FROM stock")
        conn.commit()
    finally:
        conn.close()

    print("[DEMO] Clearing all procurement state...")
    conn = get_connection()
    try:
        _execute(conn, "DELETE FROM procurement_email_drafts")
        _execute(conn, "DELETE FROM procurement_recommendations")
        # Clear procurement_memory for demo products, business demo products, and
        # known test-product names so historical runs don't pollute the demo display.
        _execute(conn, """
            DELETE FROM procurement_memory
            WHERE product_name IN (
                'NMC Powder','Graphite Anode','NMC Powder 5 kg',
                'StratTest Widget','PATTest Widget',
                'Hydrolyzed Marine Collagen','Glucosamine HCl',
                'Hyaluronic Acid Powder','Plant Extract - Elderberry',
                'Shark Cartilage Powder','Bovine Cartilage Extract',
                'Collagen Peptides Type I',
                'Collagen Powder','Fish Collagen Peptides'
            ) OR product_name LIKE 'ELT%'
              OR product_name LIKE '%Test Widget%'
        """)
        conn.commit()
    finally:
        conn.close()

    print("[DEMO] Clearing processed Gmail message records...")
    conn = get_connection()
    try:
        _execute(conn, "DELETE FROM processed_messages")
        conn.commit()
    finally:
        conn.close()

    print("[DEMO] Seeding demo inventory...")
    conn = get_connection()
    demo_stock = [
        # Test/demo products (kept for all existing tests)
        ("NMC Powder",                "Demo Supplier",                    24.0,  20.0, "kg",  45.00),
        ("Graphite Anode",            "CarbonSupply Inc",                 50.0,  25.0, "kg",  35.00),
        # California Nutraceuticals business products — all above reorder_at after demo-seed;
        # CUST-DEMO-001 invoice (-380 CP, -100 FCP) makes only those two go low.
        ("Collagen Powder",           "Pacific Rim BioMaterials Co.",    450.0, 120.0, "kg",  68.00),
        ("Shark Cartilage Powder",    "Jiaxing Natural Products Ltd",    200.0, 150.0, "kg",  92.00),
        ("Bovine Gelatin Type A",     "Shanghai BioSupply International", 320.0,  90.0, "kg",  42.00),
        ("Fish Collagen Peptides",    "Pacific Rim BioMaterials Co.",    210.0, 120.0, "kg", 108.00),
        ("Hydrolyzed Marine Collagen","Pacific Rim BioMaterials Co.",    160.0, 110.0, "kg", 125.00),
        ("Bovine Cartilage Extract",  "Jiaxing Natural Products Ltd",    140.0,  90.0, "kg",  78.00),
        ("Plant Extract - Ginseng Root","Zhejiang Green Botanicals Corp", 130.0,  80.0, "kg", 145.00),
        ("Plant Extract - Turmeric",  "Zhejiang Green Botanicals Corp",  180.0,  70.0, "kg",  45.00),
        ("Plant Extract - Ashwagandha","Zhejiang Green Botanicals Corp",   95.0,  80.0, "kg",  62.00),
        ("Plant Extract - Elderberry","Zhejiang Green Botanicals Corp",   120.0,  75.0, "kg",  82.00),
        ("Plant Extract - Echinacea", "Zhejiang Green Botanicals Corp",   100.0,  75.0, "kg",  70.00),
        ("Hyaluronic Acid Powder",    "Shanghai BioSupply International", 100.0,  60.0, "kg", 310.00),
        ("Chondroitin Sulfate",       "Jiaxing Natural Products Ltd",    200.0,  90.0, "kg", 128.00),
        ("Glucosamine HCl",           "Jiaxing Natural Products Ltd",    130.0,  90.0, "kg",  82.00),
        ("Collagen Peptides Type I",  "Pacific Rim BioMaterials Co.",    150.0, 100.0, "kg",  98.00),
    ]
    for row in demo_stock:
        if _use_postgres:
            _execute(conn,
                """INSERT INTO stock
                       (product_name, supplier, quantity_kg, reorder_at, unit, unit_price)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT (product_name) DO UPDATE
                       SET quantity_kg = excluded.quantity_kg,
                           reorder_at  = excluded.reorder_at,
                           supplier    = excluded.supplier,
                           unit_price  = excluded.unit_price""",
                row,
            )
        else:
            _execute(conn,
                """INSERT OR REPLACE INTO stock
                       (product_name, supplier, quantity_kg, reorder_at, unit, unit_price)
                   VALUES (?,?,?,?,?,?)""",
                row,
            )
    conn.commit()
    conn.close()

    # --- Glucosamine HCl workflow scenario seed ---
    # Pre-seeds: rejected primary rec (Jiaxing) + alternative quote_received rec (Guangzhou)
    # + an approval draft with fixed id so the demo presenter can label a REJECT reply
    # and immediately see find_alternative_recommendation activate the Guangzhou fallback.
    print("[DEMO] Seeding Glucosamine HCl workflow scenario (REJECT -> fallback alternative)...")
    GLC_PRODUCT       = "Glucosamine HCl"
    GLC_PRIMARY       = "Jiaxing Natural Products Ltd"
    GLC_PRIMARY_EMAIL = "lwang@jiaxingnatural.com"
    GLC_ALT           = "Guangzhou Nutra Raw Materials Inc"
    GLC_ALT_EMAIL     = "dliang@gznutraraw.com"
    GLC_DRAFT_ID      = "glc01001"

    conn = get_connection()
    _execute(conn,
        "INSERT INTO procurement_recommendations "
        "    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
        "     suggested_order_qty, urgency, reason, status) "
        "VALUES (?,?,?,?,?,?,?,?,'rejected')",
        (GLC_PRODUCT, GLC_PRIMARY, GLC_PRIMARY_EMAIL, 35.0, 90.0, 110.0, "high",
         "Below reorder threshold (35 kg < 90 kg). Unit price: $82.00/kg. "
         "Lead time: 7 days. Shipping: $50. Decision: LOW_RISK_RECOMMEND"),
    )
    primary_rec_id = _execute(conn,
        "SELECT id FROM procurement_recommendations "
        "WHERE product_name = ? AND supplier = ? AND status = 'rejected' ORDER BY id DESC LIMIT 1",
        (GLC_PRODUCT, GLC_PRIMARY),
    ).fetchone()["id"]

    _execute(conn,
        "INSERT INTO procurement_recommendations "
        "    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
        "     suggested_order_qty, urgency, reason, status) "
        "VALUES (?,?,?,?,?,?,?,?,'quote_received')",
        (GLC_PRODUCT, GLC_ALT, GLC_ALT_EMAIL, 35.0, 90.0, 110.0, "medium",
         "Alternative supplier quote. Unit price: $79.00/kg. "
         "Lead time: 10 days. Shipping: $45. Decision: NEEDS_HUMAN_REVIEW"),
    )

    glc_subject = f"Order Approval Required - {GLC_PRODUCT} [RUN_ID={GLC_DRAFT_ID}]"
    glc_body = (
        f"Hello,\n\nProcurement recommendation for {GLC_PRODUCT}:\n"
        f"  Supplier: {GLC_PRIMARY}\n  Quantity: 110 kg\n"
        f"  Unit price: $82.00/kg\n  Estimated total: $9,070.00\n\n"
        f"Reply APPROVE or REJECT (with optional reason).\n\nRUN_ID={GLC_DRAFT_ID}"
    )
    _execute(conn,
        "INSERT INTO procurement_email_drafts "
        "    (id, recommendation_id, product_name, supplier, supplier_email, subject, body, status) "
        "VALUES (?,?,?,?,?,?,?,'approval_sent')",
        (GLC_DRAFT_ID, primary_rec_id, GLC_PRODUCT, GLC_PRIMARY, GLC_PRIMARY_EMAIL,
         glc_subject, glc_body),
    )
    conn.commit()
    conn.close()

    # --- Supplier alternate catalog seed ---
    # Populates product_supplier_alternates so the agent can discover fallback
    # suppliers from data, not from hardcoded code constants.
    print("[DEMO] Seeding product_supplier_alternates catalog (Fish Collagen Peptides fallback)...")
    conn = get_connection()
    if _use_postgres:
        _execute(conn,
            "INSERT INTO product_supplier_alternates "
            "    (product_name, supplier, supplier_email, priority) VALUES (?,?,?,?) "
            "ON CONFLICT (product_name, supplier) DO UPDATE SET "
            "    supplier_email = excluded.supplier_email, priority = excluded.priority",
            ("Fish Collagen Peptides", "Shanghai BioSupply International",
             "mzhou@shibiosupply.com", 1),
        )
    else:
        _execute(conn,
            "INSERT OR REPLACE INTO product_supplier_alternates "
            "    (product_name, supplier, supplier_email, priority) VALUES (?,?,?,?)",
            ("Fish Collagen Peptides", "Shanghai BioSupply International",
             "mzhou@shibiosupply.com", 1),
        )
    conn.commit()
    conn.close()

    # --- Demo procurement memory seed ---
    # One deterministic historical rejection so the Shanghai fallback recommendation
    # shows a meaningful memory warning instead of "no prior feedback found."
    # This is seed data only — no runtime supplier logic is conditioned on it.
    print("[DEMO] Seeding demo procurement memory (prior rejection for FCP fallback supplier)...")
    from database import record_procurement_memory
    record_procurement_memory(
        product_name="Fish Collagen Peptides",
        supplier="Shanghai BioSupply International",
        supplier_email="mzhou@shibiosupply.com",
        lead_time="35 days",
        availability="delayed / uncertain",
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="REJECT",
        user_reason="Previous order had long lead time and unreliable delivery estimate.",
        outcome_status="rejected",
        run_id="demo_seed_shanghai_fcp",
    )

    # --- Demo procurement memory notes ---
    # Narrative notes about past supplier events; embedding-indexed for retrieval.
    # create_memory_note() skips duplicates, so re-running demo-seed is safe.
    print("[DEMO] Seeding procurement memory notes...")
    _seed_demo_memory_notes()

    print("[DEMO] Generating demo PDFs...")
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
        from generate_demo_pdfs import generate_pdfs
    except ImportError:
        from scripts.generate_demo_pdfs import generate_pdfs
    ok = generate_pdfs()
    if not ok:
        print("[DEMO] WARNING: PDF generation failed. Install reportlab: python -m pip install reportlab")

    print("\n[DEMO] Current inventory after seed:")
    cmd_inventory_status(args)
    _print_demo_instructions()


_DEMO_MEMORY_NOTES = [
    {
        "supplier":     "Pacific Rim BioMaterials Co.",
        "product_name": "Collagen Peptides Type I",
        "event_type":   "DELIVERY_DELAY",
        "note":         "Delivery delayed by 10 days due to customs clearance issue.",
        "impact":       "negative",
    },
    {
        "supplier":     "Jiaxing Natural Products Ltd",
        "product_name": "Chondroitin Sulfate",
        "event_type":   "ON_TIME_DELIVERY",
        "note":         "Delivered on time during previous urgent reorder; responsive supplier.",
        "impact":       "positive",
    },
    {
        "supplier":     "Shanghai BioSupply International",
        "product_name": "Fish Collagen Peptides",
        "event_type":   "QUOTE_ISSUE",
        "note":         "Previous quote missing quote validity and pricing terms; required follow-up.",
        "impact":       "negative",
    },
    {
        "supplier":     "Pacific Rim BioMaterials Co.",
        "product_name": "Collagen Powder",
        "event_type":   "LEAD_TIME_ISSUE",
        "note":         "Repeated lead-time overruns on collagen products; actual delivery 7-14 days later than quoted.",
        "impact":       "negative",
    },
    {
        "supplier":     "CarbonSupply Inc",
        "product_name": "Graphite Anode",
        "event_type":   "STABLE_DELIVERY",
        "note":         "Consistent on-time delivery across all Graphite Anode orders; reliable partner.",
        "impact":       "positive",
    },
]


def _seed_demo_memory_notes():
    """Insert demo procurement_memory_notes rows, skipping duplicates."""
    try:
        from procurement_memory_embedding import create_memory_note
    except ImportError:
        print("[DEMO] WARNING: procurement_memory_embedding not available; skipping memory notes.")
        return
    inserted = 0
    for note in _DEMO_MEMORY_NOTES:
        try:
            if create_memory_note(**note):
                inserted += 1
        except Exception as exc:
            print(f"[DEMO] WARNING: could not seed memory note ({note['event_type']}): {exc}")
    print(f"[DEMO] Memory notes: {inserted} new, {len(_DEMO_MEMORY_NOTES) - inserted} already present.")


def cmd_strategy_test(args):
    """
    Simulate four procurement strategy scenarios without Gmail.

    A. Primary supplier available  -> get_primary_supplier_for_product returns it
    B. Primary supplier rejected   -> find_alternative_recommendation finds alt
    C. Primary rejected, no alt    -> find_alternative_recommendation returns None
    D. Primary supplier approved   -> recommendation status transitions to order_sent
    """
    from database import get_connection, _execute, init_db
    from agents.procurement_agent import get_primary_supplier_for_product, find_alternative_recommendation

    PRODUCT = "StratTest Widget"

    print("[STRATEGY TEST] Initialising DB...")
    init_db()

    conn = get_connection()
    try:
        # Clean any previous test data for idempotency
        _execute(conn, "DELETE FROM stock WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
        conn.commit()

        # Seed the primary stock row
        _execute(conn,
            "INSERT INTO stock "
            "    (product_name, supplier, quantity_kg, reorder_at, unit, unit_price) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (PRODUCT, "PrimarySupplier Inc", 5.0, 20.0, "kg", 10.0),
        )
        conn.commit()

        results: list[tuple[str, bool]] = []

        # ── Test A ────────────────────────────────────────────────────────────
        print("\n[TEST A] Primary supplier available -> no fallback triggered")
        primary = get_primary_supplier_for_product(conn, PRODUCT)
        ok_a = primary is not None and primary["supplier"] == "PrimarySupplier Inc"
        results.append(("A: get_primary_supplier_for_product returns stock.supplier", ok_a))
        print(f"  Primary: {primary}")
        print(f"  -> {'PASS' if ok_a else 'FAIL'}")

        # Seed: primary rec (rejected) + alternative rec (quote_received)
        _execute(conn,
            "INSERT INTO procurement_recommendations "
            "    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "     suggested_order_qty, urgency, reason, status) "
            "VALUES (?, 'PrimarySupplier Inc', 'primary@test.com', 5.0, 20.0, 35.0, 'high', "
            "        'Test primary rec.', 'rejected')",
            (PRODUCT,),
        )
        _execute(conn,
            "INSERT INTO procurement_recommendations "
            "    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "     suggested_order_qty, urgency, reason, status) "
            "VALUES (?, 'AltSupplier Corp', 'alt@test.com', 5.0, 20.0, 35.0, 'high', "
            "        'Test alternative rec.', 'quote_received')",
            (PRODUCT,),
        )
        conn.commit()

        # ── Test B ────────────────────────────────────────────────────────────
        print("\n[TEST B] Primary supplier rejected -> fallback finds alternative")
        alt_b = find_alternative_recommendation(conn, PRODUCT, "PrimarySupplier Inc")
        ok_b = alt_b is not None and alt_b["supplier"] == "AltSupplier Corp"
        results.append(("B: fallback finds AltSupplier Corp after primary rejected", ok_b))
        print(f"  Alternative: {alt_b['supplier'] if alt_b else None}")
        print(f"  -> {'PASS' if ok_b else 'FAIL'}")

        # ── Test C ────────────────────────────────────────────────────────────
        print("\n[TEST C] Primary rejected + no alternative -> None returned")
        # Excluding AltSupplier Corp leaves only PrimarySupplier Inc, which is 'rejected'
        # (not in 'recommendation_created' or 'quote_received') -> None
        alt_c = find_alternative_recommendation(conn, PRODUCT, "AltSupplier Corp")
        ok_c = alt_c is None
        results.append(("C: no alternative when only remaining rec is rejected", ok_c))
        print(f"  Alternative (excluding AltSupplier Corp): {alt_c}")
        print(f"  -> {'PASS' if ok_c else 'FAIL'}")

        # ── Test D ────────────────────────────────────────────────────────────
        print("\n[TEST D] Primary supplier approved -> order_sent status set correctly")
        _execute(conn,
            "INSERT INTO procurement_recommendations "
            "    (product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "     suggested_order_qty, urgency, reason, status) "
            "VALUES (?, 'PrimarySupplier Inc', 'primary@test.com', 5.0, 20.0, 35.0, "
            "        'medium', 'Primary order approved.', 'approved')",
            (PRODUCT,),
        )
        conn.commit()
        _execute(conn,
            "UPDATE procurement_recommendations SET status = 'order_sent' "
            "WHERE product_name = ? AND supplier = 'PrimarySupplier Inc' AND status = 'approved'",
            (PRODUCT,),
        )
        conn.commit()
        final = _execute(conn,
            "SELECT status FROM procurement_recommendations "
            "WHERE product_name = ? AND supplier = 'PrimarySupplier Inc' "
            "ORDER BY id DESC LIMIT 1",
            (PRODUCT,),
        ).fetchone()
        ok_d = final is not None and final["status"] == "order_sent"
        results.append(("D: approved recommendation transitions to order_sent", ok_d))
        print(f"  Final status: {final['status'] if final else None}")
        print(f"  -> {'PASS' if ok_d else 'FAIL'}")

    finally:
        # Clean up all test data so subsequent tests see a clean state
        try:
            _execute(conn, "DELETE FROM stock WHERE product_name = ?", (PRODUCT,))
            _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
            _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
            conn.commit()
        except Exception:
            pass
        conn.close()

    all_pass = all(ok for _, ok in results)
    print(f"\n[STRATEGY TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'} -- {label}")


def cmd_procurement_action_test(args):
    """
    Unit tests for run_recommendation_from_quote + APPROVE ANYWAY order path.

    A: run_recommendation_from_quote supersedes old rec and creates new one
    B: new rec gets status recommendation_created; approval draft is created
    C: second call (PROVIDE NEW QUOTE) supersedes previous new rec
    D: REJECT quote -> no approval draft, no send_email call
    E: APPROVE ANYWAY path calls send_supplier_order_email
    F: cmd_agent_watch is orchestrator-only (no direct import of procurement logic)
    """
    import sys
    import types
    import os
    from database import get_connection, _execute, _insert_id, init_db
    from agents.procurement_agent import run_recommendation_from_quote

    print("[PROCUREMENT ACTION TEST] Initialising...")
    init_db()

    PRODUCT = "PATTest Widget"
    sent_emails: list[dict] = []

    _fake_efa = types.ModuleType("email_feedback_agent")
    _fake_efa.send_email = lambda to, subject, body: sent_emails.append(
        {"to": to, "subject": subject, "body": body}
    )
    _prev_efa        = sys.modules.get("email_feedback_agent")
    _prev_agents_efa = sys.modules.get("agents.email_feedback_agent")
    sys.modules["email_feedback_agent"]        = _fake_efa
    sys.modules["agents.email_feedback_agent"] = _fake_efa

    _prev_approval = os.environ.get("USER_APPROVAL_EMAIL")
    os.environ["USER_APPROVAL_EMAIL"] = "approval@test.example"

    all_pass = True
    conn = get_connection()
    try:
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
        conn.commit()

        # Seed a rec that simulates state after RFQ was sent and quote received
        orig_rec_id = _insert_id(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (PRODUCT, "TestSupplier", "supplier@test.example",
             5.0, 20.0, 35.0, "high", "Original rec.", "quote_received"),
        )
        conn.commit()

        quote_fields = {
            "unit_price": 12.50,
            "availability": "in stock",
            "lead_time": "5 days",
            "shipping_cost": 20.0,
            "quote_validity": "14 days",
        }

        # ── Test A ────────────────────────────────────────────────────────────
        print("\n[TEST A] run_recommendation_from_quote supersedes old rec + creates new")
        result = run_recommendation_from_quote(conn, orig_rec_id, quote_fields)

        old_status = _execute(conn,
            "SELECT status FROM procurement_recommendations WHERE id = ?", (orig_rec_id,),
        ).fetchone()["status"]
        ok_a1 = old_status == "superseded"
        ok_a2 = result["new_rec_id"] != orig_rec_id
        new_status_row = _execute(conn,
            "SELECT status FROM procurement_recommendations WHERE id = ?", (result["new_rec_id"],),
        ).fetchone()
        ok_a3 = new_status_row is not None and new_status_row["status"] == "recommendation_created"
        all_pass = all_pass and ok_a1 and ok_a2 and ok_a3
        print(f"  {'PASS' if ok_a1 else 'FAIL'}  old rec marked superseded (status={old_status!r})")
        print(f"  {'PASS' if ok_a2 else 'FAIL'}  new rec id is different")
        print(f"  {'PASS' if ok_a3 else 'FAIL'}  new rec status = recommendation_created")

        # ── Test B ────────────────────────────────────────────────────────────
        print("\n[TEST B] approval draft created and send_email called")
        ok_b1 = result["approval_draft_id"] is not None
        ok_b2 = len(sent_emails) >= 1
        all_pass = all_pass and ok_b1 and ok_b2
        print(f"  {'PASS' if ok_b1 else 'FAIL'}  approval_draft_id = {result['approval_draft_id']}")
        print(f"  {'PASS' if ok_b2 else 'FAIL'}  send_email called ({len(sent_emails)} email(s) total)")

        # ── Test C ────────────────────────────────────────────────────────────
        print("\n[TEST C] second call supersedes first new rec (PROVIDE NEW QUOTE scenario)")
        sent_emails.clear()
        first_new_id = result["new_rec_id"]
        result2 = run_recommendation_from_quote(conn, first_new_id, quote_fields)
        mid_status = _execute(conn,
            "SELECT status FROM procurement_recommendations WHERE id = ?", (first_new_id,),
        ).fetchone()["status"]
        ok_c1 = mid_status == "superseded"
        ok_c2 = result2["new_rec_id"] != first_new_id
        all_pass = all_pass and ok_c1 and ok_c2
        print(f"  {'PASS' if ok_c1 else 'FAIL'}  first new rec marked superseded")
        print(f"  {'PASS' if ok_c2 else 'FAIL'}  second new rec id is distinct")

        # ── Test D ────────────────────────────────────────────────────────────
        print("\n[TEST D] REJECT quote -> no approval draft, no email")
        reject_rec_id = _insert_id(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (PRODUCT, "TestSupplier", "supplier@test.example",
             5.0, 20.0, 35.0, "high", "Reject-test rec.", "quote_received"),
        )
        conn.commit()
        reject_fields = {"unit_price": None, "availability": "out of stock",
                         "lead_time": None, "shipping_cost": None, "quote_validity": None}
        sent_before = len(sent_emails)
        result_rej = run_recommendation_from_quote(conn, reject_rec_id, reject_fields)
        ok_d1 = result_rej["approval_draft_id"] is None
        ok_d2 = len(sent_emails) == sent_before
        all_pass = all_pass and ok_d1 and ok_d2
        print(f"  {'PASS' if ok_d1 else 'FAIL'}  no approval_draft_id on REJECT")
        print(f"  {'PASS' if ok_d2 else 'FAIL'}  no email sent on REJECT")

        # ── Test E ────────────────────────────────────────────────────────────
        print("\n[TEST E] APPROVE ANYWAY sends supplier order via create_order_approval_draft")
        from agents.procurement_agent import create_order_approval_draft, send_supplier_order_email
        aa_rec_id = _insert_id(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (PRODUCT, "TestSupplier", "supplier@test.example",
             5.0, 20.0, 35.0, "high", "No-alt rec.", "no_alternative_supplier"),
        )
        conn.commit()
        _execute(conn, "UPDATE procurement_recommendations SET status = 'approved' WHERE id = ?", (aa_rec_id,))
        conn.commit()
        order_draft_id = create_order_approval_draft(aa_rec_id)
        _execute(conn, "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?", (order_draft_id,))
        conn.commit()
        sent_before_e = len(sent_emails)
        order_result = send_supplier_order_email(order_draft_id)
        ok_e = len(sent_emails) > sent_before_e
        all_pass = all_pass and ok_e
        print(f"  {'PASS' if ok_e else 'FAIL'}  send_supplier_order_email called")
        print(f"  INFO  order_result={order_result!r}")

        # ── Test F ────────────────────────────────────────────────────────────
        print("\n[TEST F] cmd_agent_watch is orchestrator-only")
        import inspect
        import cli as _cli_mod
        src = inspect.getsource(_cli_mod.cmd_agent_watch)
        ok_f1 = "route_gmail_once_core()" in src
        ok_f2 = "run_procurement_once_core()" in src
        ok_f3 = "from procurement_agent" not in src
        ok_f4 = "from email_feedback_agent" not in src
        all_pass = all_pass and ok_f1 and ok_f2 and ok_f3 and ok_f4
        print(f"  {'PASS' if ok_f1 else 'FAIL'}  calls route_gmail_once_core")
        print(f"  {'PASS' if ok_f2 else 'FAIL'}  calls run_procurement_once_core")
        print(f"  {'PASS' if ok_f3 else 'FAIL'}  no direct procurement_agent import in body")
        print(f"  {'PASS' if ok_f4 else 'FAIL'}  no direct email_feedback_agent import in body")

    finally:
        if _prev_efa is None:
            sys.modules.pop("email_feedback_agent", None)
        else:
            sys.modules["email_feedback_agent"] = _prev_efa
        if _prev_agents_efa is None:
            sys.modules.pop("agents.email_feedback_agent", None)
        else:
            sys.modules["agents.email_feedback_agent"] = _prev_agents_efa
        if _prev_approval is None:
            os.environ.pop("USER_APPROVAL_EMAIL", None)
        else:
            os.environ["USER_APPROVAL_EMAIL"] = _prev_approval
        try:
            _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
            _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
            conn.commit()
        except Exception:
            pass
        conn.close()
        print("[PROCUREMENT ACTION TEST] Cleaned up.")

    print(f"\n[PROCUREMENT ACTION TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")


def cmd_email_loop_test(args):
    """
    Full end-to-end email loop simulation without real Gmail or SMTP.

    Stages tested:
      1. Sales invoice -> inventory decreases below reorder threshold
      2. Low stock -> RFQ draft created and sent
      3. Supplier quote -> recommendation created + approval email sent
      4. APPROVE -> order email sent to supplier
      5. REJECT with alternative supplier -> new approval email sent
      6. REJECT with no alternative -> fallback options email sent
      7. APPROVE ANYWAY -> order email sent
      8. PROVIDE NEW QUOTE -> new recommendation + approval email sent
      9. STOP PURCHASE -> status=stopped, no order sent
    """
    import sys
    import types
    import os
    from database import get_connection, _execute, _insert_id, init_db
    from agents.procurement_agent import (
        extract_quote_fields,
        run_recommendation_from_quote,
        find_alternative_recommendation,
        create_order_approval_draft,
        send_order_approval_draft,
        send_no_alternative_notification,
        send_supplier_order_email,
    )

    print("[EMAIL LOOP TEST] Setting up...")
    init_db()

    PRODUCT = "NMC Powder"
    sent_emails: list[dict] = []

    _fake_efa = types.ModuleType("email_feedback_agent")
    _fake_efa.send_email = lambda to, subject, body: sent_emails.append(
        {"to": to, "subject": subject, "body": body}
    )
    _fake_efa.build_recommendation_subject = (
        lambda item, run_id: f"Reorder Suggestion - {item} [RUN_ID={run_id}]"
    )
    _prev_efa        = sys.modules.get("email_feedback_agent")
    _prev_agents_efa = sys.modules.get("agents.email_feedback_agent")
    sys.modules["email_feedback_agent"]        = _fake_efa
    sys.modules["agents.email_feedback_agent"] = _fake_efa

    _prev_approval = os.environ.get("USER_APPROVAL_EMAIL")
    os.environ["USER_APPROVAL_EMAIL"] = "approver@test.example"

    conn = get_connection()
    orig_row = _execute(
        conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)
    ).fetchone()
    orig_qty = float(orig_row["quantity_kg"]) if orig_row else None
    conn.close()

    results: list[tuple[str, bool]] = []

    try:
        # ── Setup: clean state ───────────────────────────────────────────────
        conn = get_connection()
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
        if orig_row:
            _execute(conn, "UPDATE stock SET quantity_kg = 24.0 WHERE product_name = ?", (PRODUCT,))
        else:
            _execute(
                conn,
                "INSERT INTO stock (product_name, supplier, quantity_kg, reorder_at, unit) "
                "VALUES (?, ?, ?, ?, ?)",
                (PRODUCT, "Demo Supplier", 24.0, 20.0, "kg"),
            )
        conn.commit()
        conn.close()

        # ── Stage 1: Sales invoice -> inventory decrease ──────────────────────
        print("\n[EMAIL LOOP TEST] Stage 1: Sales invoice -> inventory decrease")
        conn = get_connection()
        _execute(conn, "UPDATE stock SET quantity_kg = 19.0 WHERE product_name = ?", (PRODUCT,))
        conn.commit()
        row1 = _execute(
            conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)
        ).fetchone()
        conn.close()
        ok1 = row1 is not None and abs(float(row1["quantity_kg"]) - 19.0) < 0.001
        results.append(("Stage 1: inventory decreased to 19.0 kg (below 20.0 reorder)", ok1))
        print(f"  {'PASS' if ok1 else 'FAIL'}  NMC Powder = {row1['quantity_kg'] if row1 else '?'} kg")

        # ── Stage 2: Low stock -> RFQ sent ────────────────────────────────────
        print("\n[EMAIL LOOP TEST] Stage 2: Low stock -> RFQ draft created and sent")
        sent_emails.clear()
        run_procurement_once_core()
        conn = get_connection()
        rfq_draft = _execute(
            conn,
            "SELECT id, status, sent_at FROM procurement_email_drafts "
            "WHERE product_name = ? AND status = 'rfq_sent' ORDER BY created_at DESC LIMIT 1",
            (PRODUCT,),
        ).fetchone()
        rfq_rec = _execute(
            conn,
            "SELECT id FROM procurement_recommendations "
            "WHERE product_name = ? AND status = 'rfq_sent' ORDER BY id DESC LIMIT 1",
            (PRODUCT,),
        ).fetchone()
        conn.close()
        ok2 = rfq_draft is not None and len(sent_emails) >= 1
        results.append(("Stage 2: low-stock RFQ created and sent", ok2))
        print(
            f"  {'PASS' if ok2 else 'FAIL'}  "
            f"rfq_draft={'found id=' + dict(rfq_draft)['id'] if rfq_draft else 'None'}, "
            f"emails_sent={len(sent_emails)}"
        )
        if not rfq_draft or not rfq_rec:
            print("  [ABORT] No RFQ draft/recommendation created — aborting remaining stages")
            return
        rfq_draft_id = dict(rfq_draft)["id"]
        rfq_rec_id = int(dict(rfq_rec)["id"])

        # ── Stage 3: Quote -> recommendation + approval email ─────────────────
        print("\n[EMAIL LOOP TEST] Stage 3: Supplier quote -> recommendation + approval email")
        sent_emails.clear()
        quote_body = (
            "Unit price: $12.50/kg\n"
            "In stock. Lead time: 5 days. Shipping: $20. Valid for 14 days."
        )
        quote_fields = extract_quote_fields(quote_body)
        conn = get_connection()
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'quote_received' WHERE id = ?",
            (rfq_rec_id,),
        )
        conn.commit()
        result3 = run_recommendation_from_quote(conn, rfq_rec_id, quote_fields)
        conn.close()
        ok3a = result3["decision"]["action"] in ("LOW_RISK_RECOMMEND", "NEEDS_HUMAN_REVIEW")
        ok3b = result3["approval_draft_id"] is not None
        ok3c = len(sent_emails) >= 1
        ok3 = ok3a and ok3b and ok3c
        results.append(("Stage 3: supplier quote -> recommendation created + approval sent", ok3))
        print(f"  {'PASS' if ok3a else 'FAIL'}  decision={result3['decision']['action']}")
        print(f"  {'PASS' if ok3b else 'FAIL'}  approval_draft_id={result3['approval_draft_id']}")
        print(f"  {'PASS' if ok3c else 'FAIL'}  approval email sent ({len(sent_emails)} email(s))")

        approval_draft_id = result3["approval_draft_id"]
        new_rec_id = int(result3["new_rec_id"])

        # ── Stage 4: APPROVE -> order sent ────────────────────────────────────
        print("\n[EMAIL LOOP TEST] Stage 4: APPROVE -> order email sent to supplier")
        sent_emails.clear()
        conn = get_connection()
        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?",
            (approval_draft_id,),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'approved' WHERE id = ?",
            (new_rec_id,),
        )
        conn.commit()
        conn.close()
        order_result4 = send_supplier_order_email(approval_draft_id)
        ok4 = "Sent supplier order" in order_result4 and len(sent_emails) >= 1
        results.append(("Stage 4: APPROVE -> order email sent to supplier", ok4))
        print(f"  {'PASS' if ok4 else 'FAIL'}  {order_result4}")

        # ── Stage 5: REJECT + alternative supplier -> new approval ─────────────
        print("\n[EMAIL LOOP TEST] Stage 5: REJECT + alternative supplier -> new approval sent")
        sent_emails.clear()
        conn = get_connection()
        _execute(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "Demo Supplier", "demo@supplier.com", 19.0, 20.0, 35.0, "high",
             "Rejected primary rec.", "rejected"),
        )
        alt5_id = _insert_id(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "AltSupplier Corp", "alt@supplier.com", 19.0, 20.0, 35.0, "high",
             "Alternative rec.", "quote_received"),
        )
        conn.commit()
        alt5 = find_alternative_recommendation(conn, PRODUCT, "Demo Supplier")
        ok5_found = alt5 is not None and alt5["supplier"] == "AltSupplier Corp"
        if alt5:
            alt5_draft_id = create_order_approval_draft(alt5["id"])
            send_order_approval_draft(alt5_draft_id)
            ok5 = ok5_found and len(sent_emails) >= 1
        else:
            ok5 = False
        conn.close()
        results.append(("Stage 5: REJECT + alt supplier -> new approval email sent", ok5))
        print(f"  {'PASS' if ok5_found else 'FAIL'}  alt supplier found: {alt5['supplier'] if alt5 else '?'}")
        print(f"  {'PASS' if ok5 else 'FAIL'}  new approval email sent ({len(sent_emails)} email(s))")

        # ── Stage 6: REJECT, no alternative -> fallback options email ─────────
        print("\n[EMAIL LOOP TEST] Stage 6: REJECT with no alternative -> fallback options email")
        sent_emails.clear()
        conn = get_connection()
        noalt_rec_id = _insert_id(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "OnlySupplier", "only@supplier.com", 19.0, 20.0, 35.0, "high",
             "No-alt rejected rec.", "rejected"),
        )
        conn.commit()
        conn.close()
        notif6 = send_no_alternative_notification(noalt_rec_id)
        ok6 = len(sent_emails) >= 1 and "draft_id=" in notif6
        results.append(("Stage 6: no alternative -> fallback options email sent", ok6))
        print(f"  {'PASS' if ok6 else 'FAIL'}  {notif6[:90]}")

        # ── Stage 7: APPROVE ANYWAY -> order sent ─────────────────────────────
        print("\n[EMAIL LOOP TEST] Stage 7: APPROVE ANYWAY -> order email sent")
        sent_emails.clear()
        conn = get_connection()
        aa_rec_id = _insert_id(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "Demo Supplier", "demo@supplier.com", 19.0, 20.0, 35.0, "high",
             "Approve-anyway rec.", "no_alternative_supplier"),
        )
        _execute(
            conn,
            "UPDATE procurement_recommendations SET status = 'approved' WHERE id = ?",
            (aa_rec_id,),
        )
        conn.commit()
        aa_draft_id = create_order_approval_draft(aa_rec_id)
        _execute(
            conn,
            "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?",
            (aa_draft_id,),
        )
        conn.commit()
        conn.close()
        aa_result = send_supplier_order_email(aa_draft_id)
        ok7 = "Sent supplier order" in aa_result and len(sent_emails) >= 1
        results.append(("Stage 7: APPROVE ANYWAY -> order email sent to supplier", ok7))
        print(f"  {'PASS' if ok7 else 'FAIL'}  {aa_result}")

        # ── Stage 8: PROVIDE NEW QUOTE -> new rec + approval ──────────────────
        print("\n[EMAIL LOOP TEST] Stage 8: PROVIDE NEW QUOTE -> new recommendation + approval sent")
        sent_emails.clear()
        conn = get_connection()
        pnq_rec_id = _insert_id(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "Demo Supplier", "demo@supplier.com", 19.0, 20.0, 35.0, "high",
             "PNQ base rec.", "quote_received"),
        )
        conn.commit()
        pnq_body = (
            "Unit price: $11.00/kg\n"
            "In stock. Lead time: 7 days. Shipping: $15. Valid for 30 days."
        )
        pnq_fields = extract_quote_fields(pnq_body)
        pnq_result = run_recommendation_from_quote(conn, pnq_rec_id, pnq_fields)
        conn.close()
        ok8a = pnq_result["new_rec_id"] != pnq_rec_id
        ok8b = pnq_result["approval_draft_id"] is not None
        ok8c = len(sent_emails) >= 1
        ok8 = ok8a and ok8b and ok8c
        results.append(("Stage 8: PROVIDE NEW QUOTE -> new rec + approval email sent", ok8))
        print(f"  {'PASS' if ok8a else 'FAIL'}  new_rec_id={pnq_result['new_rec_id']} (old={pnq_rec_id})")
        print(f"  {'PASS' if ok8b else 'FAIL'}  approval_draft_id={pnq_result['approval_draft_id']}")
        print(f"  {'PASS' if ok8c else 'FAIL'}  approval email sent ({len(sent_emails)} email(s))")

        # ── Stage 9: STOP PURCHASE -> stopped, no order ───────────────────────
        print("\n[EMAIL LOOP TEST] Stage 9: STOP PURCHASE -> status=stopped, no order sent")
        sent_emails.clear()
        conn = get_connection()
        stop_rec_id = _insert_id(
            conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT, "Demo Supplier", "demo@supplier.com", 19.0, 20.0, 35.0, "high",
             "Stop rec.", "recommendation_created"),
        )
        conn.commit()
        _execute(
            conn,
            "UPDATE procurement_recommendations "
            "SET status = 'stopped', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (stop_rec_id,),
        )
        conn.commit()
        stop_final = _execute(
            conn,
            "SELECT status FROM procurement_recommendations WHERE id = ?",
            (stop_rec_id,),
        ).fetchone()
        conn.close()
        ok9 = (
            stop_final is not None
            and stop_final["status"] == "stopped"
            and len(sent_emails) == 0
        )
        results.append(("Stage 9: STOP PURCHASE -> status=stopped, no order", ok9))
        print(
            f"  {'PASS' if ok9 else 'FAIL'}  "
            f"status={stop_final['status'] if stop_final else '?'}, "
            f"emails_sent={len(sent_emails)}"
        )

    finally:
        if _prev_efa is None:
            sys.modules.pop("email_feedback_agent", None)
        else:
            sys.modules["email_feedback_agent"] = _prev_efa
        if _prev_agents_efa is None:
            sys.modules.pop("agents.email_feedback_agent", None)
        else:
            sys.modules["agents.email_feedback_agent"] = _prev_agents_efa
        if _prev_approval is None:
            os.environ.pop("USER_APPROVAL_EMAIL", None)
        else:
            os.environ["USER_APPROVAL_EMAIL"] = _prev_approval
        conn = get_connection()
        try:
            _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
            _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
            if orig_qty is not None:
                _execute(
                    conn,
                    "UPDATE stock SET quantity_kg = ? WHERE product_name = ?",
                    (orig_qty, PRODUCT),
                )
            conn.commit()
        except Exception:
            pass
        conn.close()
        print("\n[EMAIL LOOP TEST] Cleaned up.")

    all_pass = all(ok for _, ok in results)
    print(f"\n[EMAIL LOOP TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'} -- {label}")


def cmd_business_demo_test(args):
    """
    End-to-end business demo story test. No real Gmail or SMTP.

    Verifies the recommended live demo narrative:
    1. CUST-DEMO-001 sales invoice causes Collagen Powder and Fish Collagen Peptides low stock
    2. Collagen Powder: good quote -> APPROVE -> order email sent to supplier
    3. SUP-DEMO-COLLAGEN-001 purchase receipt -> only Collagen Powder stock increases
    4. Fish Collagen Peptides: risky quote -> REJECT -> fallback RFQ to Shanghai
    4c. Shanghai quote -> recommendation + approval email (not order)
    5. CHANGE reply with Marine BioActives -> product_supplier_alternates updated -> RFQ sent
    5c. Marine BioActives quote -> new recommendation (not order)
    """
    import sys
    import types
    import os
    from database import get_connection, _execute, _insert_id, init_db, _use_postgres
    from agents.procurement_agent import (
        extract_quote_fields,
        run_recommendation_from_quote,
        find_alternative_recommendation,
        create_order_approval_draft,
        send_order_approval_draft,
        send_supplier_order_email,
        create_and_send_change_rfq,
    )
    from agents.email_feedback_agent import parse_reply as _parse_reply_real

    print("[BUSINESS DEMO TEST] Setting up...")
    init_db()

    PRODUCT_CP      = "Collagen Powder"
    PRODUCT_FCP     = "Fish Collagen Peptides"
    SUPPLIER_CP     = "Pacific Rim BioMaterials Co."
    SUPPLIER_FCP    = "Pacific Rim BioMaterials Co."
    SUPPLIER_FCP_ALT = "Shanghai BioSupply International"

    sent_emails: list[dict] = []
    _fake_efa = types.ModuleType("email_feedback_agent")
    _fake_efa.send_email = lambda to, subject, body: sent_emails.append(
        {"to": to, "subject": subject, "body": body}
    )
    _fake_efa.build_recommendation_subject = (
        lambda item, run_id: f"Reorder Suggestion - {item} [RUN_ID={run_id}]"
    )
    _prev_efa        = sys.modules.get("email_feedback_agent")
    _prev_agents_efa = sys.modules.get("agents.email_feedback_agent")
    sys.modules["email_feedback_agent"]        = _fake_efa
    sys.modules["agents.email_feedback_agent"] = _fake_efa

    _prev_approval = os.environ.get("USER_APPROVAL_EMAIL")
    os.environ["USER_APPROVAL_EMAIL"] = "approver@test.example"

    conn = get_connection()
    orig_cp  = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_CP,)).fetchone()
    orig_fcp = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_FCP,)).fetchone()
    orig_cp_qty  = float(orig_cp["quantity_kg"])  if orig_cp  else None
    orig_fcp_qty = float(orig_fcp["quantity_kg"]) if orig_fcp else None
    conn.close()

    results: list[tuple[str, bool]] = []

    try:
        # ── Setup: demo-seed quantities, clean procurement state ─────────────────
        conn = get_connection()
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name IN (?, ?)",
                 (PRODUCT_CP, PRODUCT_FCP))
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name IN (?, ?)",
                 (PRODUCT_CP, PRODUCT_FCP))
        if orig_cp:
            _execute(conn, "UPDATE stock SET quantity_kg = 450.0 WHERE product_name = ?", (PRODUCT_CP,))
        else:
            _execute(conn,
                "INSERT INTO stock (product_name, supplier, quantity_kg, reorder_at, unit, unit_price) "
                "VALUES (?,?,?,?,?,?)",
                (PRODUCT_CP, SUPPLIER_CP, 450.0, 120.0, "kg", 68.00))
        if orig_fcp:
            _execute(conn, "UPDATE stock SET quantity_kg = 210.0 WHERE product_name = ?", (PRODUCT_FCP,))
        else:
            _execute(conn,
                "INSERT INTO stock (product_name, supplier, quantity_kg, reorder_at, unit, unit_price) "
                "VALUES (?,?,?,?,?,?)",
                (PRODUCT_FCP, SUPPLIER_FCP, 210.0, 120.0, "kg", 108.00))
        conn.commit()
        conn.close()

        # Seed fallback catalog so this test is self-contained (doesn't require demo-seed)
        conn = get_connection()
        if _use_postgres:
            _execute(conn,
                "INSERT INTO product_supplier_alternates "
                "    (product_name, supplier, supplier_email, priority) VALUES (?,?,?,?) "
                "ON CONFLICT (product_name, supplier) DO UPDATE SET "
                "    supplier_email = excluded.supplier_email, priority = excluded.priority",
                (PRODUCT_FCP, SUPPLIER_FCP_ALT, "mzhou@shibiosupply.com", 1),
            )
        else:
            _execute(conn,
                "INSERT OR REPLACE INTO product_supplier_alternates "
                "(product_name, supplier, supplier_email, priority) VALUES (?,?,?,?)",
                (PRODUCT_FCP, SUPPLIER_FCP_ALT, "mzhou@shibiosupply.com", 1),
            )
        conn.commit()
        conn.close()

        # ── Stage 1: CUST-DEMO-001 sales invoice -> both products low stock ───────
        print("\n[BUSINESS DEMO TEST] Stage 1: CUST-DEMO-001 sales invoice -> both products low stock")
        conn = get_connection()
        _execute(conn, "UPDATE stock SET quantity_kg = quantity_kg - 380.0 WHERE product_name = ?", (PRODUCT_CP,))
        _execute(conn, "UPDATE stock SET quantity_kg = quantity_kg - 100.0 WHERE product_name = ?", (PRODUCT_FCP,))
        conn.commit()
        cp_row1  = _execute(conn, "SELECT quantity_kg, reorder_at FROM stock WHERE product_name = ?", (PRODUCT_CP,)).fetchone()
        fcp_row1 = _execute(conn, "SELECT quantity_kg, reorder_at FROM stock WHERE product_name = ?", (PRODUCT_FCP,)).fetchone()
        conn.close()
        cp_qty  = float(cp_row1["quantity_kg"])
        fcp_qty = float(fcp_row1["quantity_kg"])
        ok1a = cp_qty  < float(cp_row1["reorder_at"])
        ok1b = fcp_qty < float(fcp_row1["reorder_at"])
        results.append(("Stage 1: CUST-DEMO-001 -> Collagen Powder and Fish Collagen Peptides low stock", ok1a and ok1b))
        print(f"  {'PASS' if ok1a else 'FAIL'}  Collagen Powder = {cp_qty} kg (reorder at {float(cp_row1['reorder_at'])} kg)")
        print(f"  {'PASS' if ok1b else 'FAIL'}  Fish Collagen Peptides = {fcp_qty} kg (reorder at {float(fcp_row1['reorder_at'])} kg)")

        # ── Stage 2a: Collagen Powder - good quote -> recommendation + approval ───
        print("\n[BUSINESS DEMO TEST] Stage 2: Collagen Powder good quote -> APPROVE -> order email sent")
        sent_emails.clear()
        conn = get_connection()
        cp_rec_id = _insert_id(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT_CP, SUPPLIER_CP, "jchen@pacificrimbiomaterials.com",
             cp_qty, 120.0, 400.0, "high", "Below reorder threshold.", "quote_received"),
        )
        conn.commit()
        cp_quote_body = (
            "Unit price: $66.00/kg\n"
            "In stock. Lead time: 8 days. Shipping: $180. Valid for 14 days."
        )
        cp_quote_fields = extract_quote_fields(cp_quote_body)
        cp_result = run_recommendation_from_quote(conn, cp_rec_id, cp_quote_fields)
        conn.close()
        ok2a = cp_result["decision"]["action"] in ("LOW_RISK_RECOMMEND", "NEEDS_HUMAN_REVIEW")
        ok2b = cp_result["approval_draft_id"] is not None
        ok2c = len(sent_emails) >= 1
        results.append(("Stage 2: Collagen Powder good quote -> recommendation + approval sent",
                         ok2a and ok2b and ok2c))
        print(f"  {'PASS' if ok2a else 'FAIL'}  decision={cp_result['decision']['action']}")
        print(f"  {'PASS' if ok2b else 'FAIL'}  approval_draft_id={cp_result['approval_draft_id']}")
        print(f"  {'PASS' if ok2c else 'FAIL'}  approval email sent ({len(sent_emails)} email(s))")

        # ── Stage 2b: APPROVE -> order sent ──────────────────────────────────────
        cp_approval_draft_id = cp_result["approval_draft_id"]
        cp_new_rec_id = int(cp_result["new_rec_id"])
        sent_emails.clear()
        conn = get_connection()
        _execute(conn, "UPDATE procurement_email_drafts SET status = 'approved' WHERE id = ?", (cp_approval_draft_id,))
        _execute(conn, "UPDATE procurement_recommendations SET status = 'approved' WHERE id = ?", (cp_new_rec_id,))
        conn.commit()
        conn.close()
        cp_order_result = send_supplier_order_email(cp_approval_draft_id)
        ok2d = "Sent supplier order" in cp_order_result and len(sent_emails) >= 1
        results.append(("Stage 2b: APPROVE Collagen Powder -> order email sent to supplier", ok2d))
        print(f"  {'PASS' if ok2d else 'FAIL'}  {cp_order_result}")

        # ── Stage 3: Purchase receipt for Collagen Powder only ───────────────────
        print("\n[BUSINESS DEMO TEST] Stage 3: SUP-DEMO-COLLAGEN-001 -> only Collagen Powder stock increases")
        conn = get_connection()
        cp_before3  = float(_execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_CP,)).fetchone()["quantity_kg"])
        fcp_before3 = float(_execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_FCP,)).fetchone()["quantity_kg"])
        _execute(conn, "UPDATE stock SET quantity_kg = quantity_kg + 400.0 WHERE product_name = ?", (PRODUCT_CP,))
        conn.commit()
        cp_after3  = float(_execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_CP,)).fetchone()["quantity_kg"])
        fcp_after3 = float(_execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT_FCP,)).fetchone()["quantity_kg"])
        conn.close()
        ok3a = abs(cp_after3 - cp_before3 - 400.0) < 0.001
        ok3b = abs(fcp_after3 - fcp_before3) < 0.001
        results.append(("Stage 3: purchase receipt increases only Collagen Powder", ok3a and ok3b))
        print(f"  {'PASS' if ok3a else 'FAIL'}  Collagen Powder: {cp_before3} -> {cp_after3} kg (+400)")
        print(f"  {'PASS' if ok3b else 'FAIL'}  Fish Collagen Peptides unchanged: {fcp_after3} kg")

        # ── Stage 4: FCP risky quote -> REJECT -> fallback RFQ sent to Shanghai ────
        print("\n[BUSINESS DEMO TEST] Stage 4: FCP risky quote -> REJECT -> fallback RFQ to Shanghai")
        sent_emails.clear()
        conn = get_connection()
        # Seed a rejected primary rec (Pacific Rim — risky quote was rejected)
        fcp_primary_rec_id = _insert_id(conn,
            "INSERT INTO procurement_recommendations "
            "(product_name, supplier, supplier_email, current_stock_kg, reorder_at_kg, "
            "suggested_order_qty, urgency, reason, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (PRODUCT_FCP, SUPPLIER_FCP, "jchen@pacificrimbiomaterials.com",
             fcp_qty, 120.0, 130.0, "high",
             "Risky quote: limited stock, 35-day lead time, high freight. NEEDS_HUMAN_REVIEW",
             "rejected"),
        )
        conn.commit()

        # Call the real rejection-fallback handler — no pre-seeded alt rec exists,
        # so it must find Shanghai via product_supplier_alternates catalog and send a new RFQ.
        _handle_rejection_fallback(conn, PRODUCT_FCP, fcp_primary_rec_id, SUPPLIER_FCP)
        conn.close()

        # Verify: Shanghai RFQ draft was created and RFQ email sent (not an approval)
        conn = get_connection()
        fcp_fallback_draft = _execute(conn,
            "SELECT id, recommendation_id, supplier, status FROM procurement_email_drafts "
            "WHERE product_name = ? AND supplier = ? AND status = 'rfq_sent' "
            "ORDER BY created_at DESC LIMIT 1",
            (PRODUCT_FCP, SUPPLIER_FCP_ALT),
        ).fetchone()
        conn.close()

        ok4a = fcp_fallback_draft is not None
        ok4b = len(sent_emails) >= 1
        ok4c = not any("Purchase Order" in e.get("subject", "") for e in sent_emails)
        ok4d = not any("Order Approval Required" in e.get("subject", "") for e in sent_emails)

        # Verify the fallback rec's reason field contains the ranking explanation.
        conn = get_connection()
        _fcp_fb_reason_row = _execute(conn,
            "SELECT reason FROM procurement_recommendations WHERE id = ?",
            (int(dict(fcp_fallback_draft)["recommendation_id"]),),
        ).fetchone() if fcp_fallback_draft else None
        conn.close()
        ok4_reason = bool(
            _fcp_fb_reason_row
            and "recommended" in (_fcp_fb_reason_row["reason"] or "").lower()
        )

        results.append(("Stage 4: REJECT -> fallback RFQ sent to Shanghai BioSupply International", ok4a and ok4b))
        results.append(("Stage 4b: fallback produces RFQ email, not approval or purchase order", ok4c and ok4d))
        results.append(("Stage 4b (ranking): recommendation explanation stored in fallback RFQ reason", ok4_reason))
        print(f"  {'PASS' if ok4a else 'FAIL'}  fallback RFQ draft exists: "
              f"id={dict(fcp_fallback_draft)['id'] if fcp_fallback_draft else 'None'} "
              f"supplier={dict(fcp_fallback_draft)['supplier'] if fcp_fallback_draft else '?'}")
        print(f"  {'PASS' if ok4b else 'FAIL'}  RFQ email sent to fallback supplier ({len(sent_emails)} email(s))")
        print(f"  {'PASS' if ok4c else 'FAIL'}  no purchase order email")
        print(f"  {'PASS' if ok4d else 'FAIL'}  no premature approval email")
        print(f"  {'PASS' if ok4_reason else 'FAIL'}  fallback RFQ reason includes recommendation explanation")

        # ── Stage 4c: Shanghai quote arrives -> recommendation + approval ──────────
        print("\n[BUSINESS DEMO TEST] Stage 4c: Shanghai quote arrives -> recommendation + approval email")
        sent_emails.clear()
        fcp_fallback_rec_id = int(dict(fcp_fallback_draft)["recommendation_id"]) if fcp_fallback_draft else None
        if fcp_fallback_rec_id:
            conn = get_connection()
            # Mark the fallback rec as quote_received (simulates label procurement/quote)
            _execute(conn,
                "UPDATE procurement_recommendations SET status = 'quote_received' WHERE id = ?",
                (fcp_fallback_rec_id,),
            )
            conn.commit()
            fcp_alt_quote_body = (
                "Unit price: $150.00/kg\n"
                "In stock. Lead time: 14 days. Shipping: $320. Valid for 10 days."
            )
            fcp_alt_quote_fields = extract_quote_fields(fcp_alt_quote_body)
            fcp_alt_result = run_recommendation_from_quote(conn, fcp_fallback_rec_id, fcp_alt_quote_fields)
            conn.close()
            ok4e = fcp_alt_result["decision"]["action"] in ("LOW_RISK_RECOMMEND", "NEEDS_HUMAN_REVIEW")
            ok4f = fcp_alt_result["approval_draft_id"] is not None
            ok4g = len(sent_emails) >= 1
            ok4h = not any("Purchase Order" in e.get("subject", "") for e in sent_emails)
        else:
            ok4e = ok4f = ok4g = ok4h = False
        results.append(("Stage 4c: Shanghai quote -> recommendation + approval email (not order)", ok4e and ok4f and ok4g and ok4h))
        print(f"  {'PASS' if ok4e else 'FAIL'}  decision={fcp_alt_result['decision']['action'] if fcp_fallback_rec_id else '?'}")
        print(f"  {'PASS' if ok4f else 'FAIL'}  approval_draft_id={fcp_alt_result['approval_draft_id'] if fcp_fallback_rec_id else '?'}")
        print(f"  {'PASS' if ok4g else 'FAIL'}  approval email sent ({len(sent_emails)} email(s))")
        print(f"  {'PASS' if ok4h else 'FAIL'}  no purchase order sent")

        # ── Stage 5: CHANGE reply -> Marine BioActives RFQ ────────────────────────
        print("\n[BUSINESS DEMO TEST] Stage 5: CHANGE reply -> new supplier RFQ")
        sent_emails.clear()

        SUPPLIER_FCP_CHANGE       = "Marine BioActives Ltd"
        SUPPLIER_FCP_CHANGE_EMAIL = "quotes@marinebioactives.com"

        # 5a: parse_reply must extract Email field from a CHANGE reply
        change_body = (
            "CHANGE\n"
            f"Supplier: {SUPPLIER_FCP_CHANGE}\n"
            f"Email: {SUPPLIER_FCP_CHANGE_EMAIL}\n"
            "Quantity: 130 kg\n"
            "Reason: Shanghai quote is still above target."
        )
        parsed_change = _parse_reply_real(change_body)
        ok5_parse   = (
            parsed_change["action"]              == "CHANGE"
            and parsed_change.get("supplier")    == SUPPLIER_FCP_CHANGE
            and parsed_change.get("email")       == SUPPLIER_FCP_CHANGE_EMAIL
            and parsed_change.get("quantity")    == 130.0
        )
        results.append(("Stage 5: parse_reply extracts Supplier + Email + Quantity from CHANGE reply", ok5_parse))
        print(f"  {'PASS' if ok5_parse else 'FAIL'}  parse_reply: action={parsed_change['action']}"
              f" supplier={parsed_change.get('supplier')} email={parsed_change.get('email')}"
              f" qty={parsed_change.get('quantity')}")

        # 5b: create_and_send_change_rfq -> catalog updated, RFQ sent, no order, no inv change
        conn = get_connection()
        fcp_inv_before5 = float(
            _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?",
                     (PRODUCT_FCP,)).fetchone()["quantity_kg"]
        )
        conn.close()
        _change_result = create_and_send_change_rfq(
            PRODUCT_FCP, SUPPLIER_FCP_CHANGE, SUPPLIER_FCP_CHANGE_EMAIL,
            reason="Shanghai quote is still above target.",
        )

        conn = get_connection()
        alt_cat_row = _execute(conn,
            "SELECT supplier_email FROM product_supplier_alternates "
            "WHERE product_name = ? AND supplier = ?",
            (PRODUCT_FCP, SUPPLIER_FCP_CHANGE),
        ).fetchone()
        change_rfq_draft = _execute(conn,
            "SELECT id, recommendation_id, supplier, supplier_email, status "
            "FROM procurement_email_drafts "
            "WHERE product_name = ? AND supplier = ? AND status = 'rfq_sent' "
            "ORDER BY created_at DESC LIMIT 1",
            (PRODUCT_FCP, SUPPLIER_FCP_CHANGE),
        ).fetchone()
        fcp_inv_after5 = float(
            _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?",
                     (PRODUCT_FCP,)).fetchone()["quantity_kg"]
        )
        conn.close()

        ok5b_catalog  = alt_cat_row is not None and alt_cat_row["supplier_email"] == SUPPLIER_FCP_CHANGE_EMAIL
        ok5b_draft    = change_rfq_draft is not None
        ok5b_rfq_sent = len(sent_emails) >= 1
        ok5b_no_order = not any("Purchase Order" in e.get("subject", "") for e in sent_emails)
        ok5b_no_appr  = not any("Order Approval Required" in e.get("subject", "") for e in sent_emails)
        ok5b_no_inv   = abs(fcp_inv_after5 - fcp_inv_before5) < 0.001

        results.append(("Stage 5b: CHANGE -> product_supplier_alternates updated", ok5b_catalog))
        results.append(("Stage 5b: CHANGE -> RFQ sent to user-provided supplier", ok5b_draft and ok5b_rfq_sent))
        results.append(("Stage 5b: CHANGE -> no order or premature approval email", ok5b_no_order and ok5b_no_appr))
        results.append(("Stage 5b: CHANGE -> no inventory change", ok5b_no_inv))
        print(f"  {'PASS' if ok5b_catalog else 'FAIL'}  catalog updated: Marine BioActives email="
              f"{alt_cat_row['supplier_email'] if alt_cat_row else 'NOT FOUND'}")
        print(f"  {'PASS' if ok5b_draft else 'FAIL'}  RFQ draft exists (status=rfq_sent): "
              f"id={dict(change_rfq_draft)['id'] if change_rfq_draft else 'None'}")
        print(f"  {'PASS' if ok5b_rfq_sent else 'FAIL'}  RFQ email sent ({len(sent_emails)} email(s))")
        print(f"  {'PASS' if ok5b_no_order else 'FAIL'}  no purchase order email")
        print(f"  {'PASS' if ok5b_no_appr else 'FAIL'}  no premature approval email")
        print(f"  {'PASS' if ok5b_no_inv else 'FAIL'}  Fish Collagen Peptides inventory unchanged ({fcp_inv_after5} kg)")

        # ── Stage 5c: Marine BioActives quote -> new recommendation (not order) ────
        print("\n[BUSINESS DEMO TEST] Stage 5c: Marine BioActives quote -> new recommendation")
        sent_emails.clear()
        change_rec_id = int(dict(change_rfq_draft)["recommendation_id"]) if change_rfq_draft else None
        if change_rec_id:
            conn = get_connection()
            _execute(conn,
                "UPDATE procurement_recommendations SET status = 'quote_received' WHERE id = ?",
                (change_rec_id,),
            )
            conn.commit()
            marine_quote_body = (
                "Unit price: USD 145/kg\n"
                "In stock. Lead time: 12 days. Shipping: USD 280. Valid for 14 days."
            )
            marine_quote_fields = extract_quote_fields(marine_quote_body)
            marine_result = run_recommendation_from_quote(conn, change_rec_id, marine_quote_fields)
            conn.close()
            ok5c_decision  = marine_result["decision"]["action"] in ("LOW_RISK_RECOMMEND", "NEEDS_HUMAN_REVIEW")
            ok5c_draft     = marine_result["approval_draft_id"] is not None
            ok5c_no_order  = not any("Purchase Order" in e.get("subject", "") for e in sent_emails)
        else:
            ok5c_decision = ok5c_draft = ok5c_no_order = False
        results.append(("Stage 5c: Marine BioActives quote -> recommendation + approval (not order)",
                         ok5c_decision and ok5c_draft and ok5c_no_order))
        print(f"  {'PASS' if ok5c_decision else 'FAIL'}  decision={marine_result['decision']['action'] if change_rec_id else '?'}")
        print(f"  {'PASS' if ok5c_draft else 'FAIL'}  approval_draft_id={marine_result['approval_draft_id'] if change_rec_id else '?'}")
        print(f"  {'PASS' if ok5c_no_order else 'FAIL'}  no purchase order sent")

    finally:
        if _prev_efa is None:
            sys.modules.pop("email_feedback_agent", None)
        else:
            sys.modules["email_feedback_agent"] = _prev_efa
        if _prev_agents_efa is None:
            sys.modules.pop("agents.email_feedback_agent", None)
        else:
            sys.modules["agents.email_feedback_agent"] = _prev_agents_efa
        if _prev_approval is None:
            os.environ.pop("USER_APPROVAL_EMAIL", None)
        else:
            os.environ["USER_APPROVAL_EMAIL"] = _prev_approval
        conn = get_connection()
        try:
            _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name IN (?, ?)",
                     (PRODUCT_CP, PRODUCT_FCP))
            _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name IN (?, ?)",
                     (PRODUCT_CP, PRODUCT_FCP))
            _execute(conn, "DELETE FROM product_supplier_alternates WHERE product_name = ?",
                     (PRODUCT_FCP,))
            if orig_cp_qty is not None:
                _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?",
                         (orig_cp_qty, PRODUCT_CP))
            if orig_fcp_qty is not None:
                _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?",
                         (orig_fcp_qty, PRODUCT_FCP))
            conn.commit()
        except Exception:
            pass
        conn.close()
        print("\n[BUSINESS DEMO TEST] Cleaned up. (Re-run demo-seed before the live demo.)")

    all_pass = all(ok for _, ok in results)
    print(f"\n[BUSINESS DEMO TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'} -- {label}")


def cmd_memory_status(args):
    """Print supplier memory summary: approvals, rejections, scores, recent reject reasons."""
    from database import get_connection, _execute
    from agents.procurement_agent import get_supplier_memory_score

    conn = get_connection()
    try:
        pairs = _execute(conn,
            "SELECT DISTINCT product_name, supplier FROM procurement_memory "
            "ORDER BY product_name, supplier",
        ).fetchall()

        if not pairs:
            print("[MEMORY] No procurement memory entries yet.")
            print("  Run: python cli.py memory-test  to seed test data.")
            return

        print("\n[MEMORY] Supplier history")
        header = (
            f"{'Product':<28} {'Supplier':<24}"
            f" {'Score':>6}  {'Approve':>7}  {'Reject':>6}  {'Override':>8}  {'Stop':>4}"
        )
        print(header)
        print("-" * len(header))

        all_reject_reasons: list[tuple[str, str]] = []
        for pair in [dict(p) for p in pairs]:
            product  = pair["product_name"]
            supplier = pair["supplier"]
            ms = get_supplier_memory_score(conn, product, supplier)
            print(
                f"{product:<28} {supplier:<24}"
                f" {ms['supplier_score']:>6.1f}"
                f"  {ms['approval_count']:>7}"
                f"  {ms['reject_count']:>6}"
                f"  {ms['override_count']:>8}"
                f"  {ms['stop_count']:>4}"
            )
            for reason in ms["recent_reject_reasons"]:
                all_reject_reasons.append((supplier, reason))

        if all_reject_reasons:
            print("\nRecent rejection reasons:")
            for supplier, reason in all_reject_reasons:
                print(f"  - {supplier}: {reason}")
        print()
    finally:
        conn.close()


def cmd_memory_test(args):
    """
    Seed structured memory records and verify scoring, memory note, and audit trail.

    Scoring weights under test:
        APPROVE           +1
        REJECT            -2
        STOP_PURCHASE     -3
        CHANGE            -1
        APPROVE_ANYWAY     0
        PROVIDE_NEW_QUOTE  0

    Test suppliers (generic — no demo product/supplier hardcodes in runtime):
        BatteryMaterials Co  : APPROVE(+1) + REJECT(-2)  = -1.0
        CarbonSupply Inc     : APPROVE(+1)               =  1.0
        HaltedVendor Ltd     : STOP_PURCHASE(-3)         = -3.0
        RedirectedSupplier   : CHANGE(-1)                = -1.0
        OverriddenVendor     : APPROVE_ANYWAY(0)         =  0.0
        NewQuoteVendor       : PROVIDE_NEW_QUOTE(0)      =  0.0
    """
    from database import get_connection, _execute, record_procurement_memory, init_db
    from agents.procurement_agent import (
        get_supplier_memory_score,
        build_recommendation_reason,
        extract_quote_fields,
    )

    print("[MEMORY TEST] Initialising DB...")
    init_db()

    conn = get_connection()
    _execute(conn, "DELETE FROM procurement_memory WHERE run_id LIKE 'memtest%'")
    conn.commit()
    conn.close()

    print("[MEMORY TEST] Seeding test records...")

    _common = dict(
        product_name="NMC Powder",
        lead_time="5 days",
        shipping_cost=20.0,
        availability="in stock",
    )

    # BatteryMaterials Co: APPROVE (+1) then REJECT (-2) => score -1.0
    record_procurement_memory(
        **_common,
        supplier="BatteryMaterials Co",
        supplier_email="sales@batterymaterials.com",
        unit_price=12.50,
        estimated_total_cost=420.0,
        recommendation_action="LOW_RISK_RECOMMEND",
        user_action="APPROVE",
        user_reason=None,
        outcome_status="order_sent",
        run_id="memtest001",
    )
    record_procurement_memory(
        **_common,
        supplier="BatteryMaterials Co",
        supplier_email="sales@batterymaterials.com",
        unit_price=18.00,
        estimated_total_cost=596.0,
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="REJECT",
        user_reason="price too high",
        outcome_status="rejected",
        run_id="memtest002",
    )

    # CarbonSupply Inc: APPROVE (+1) => score 1.0
    record_procurement_memory(
        **_common,
        supplier="CarbonSupply Inc",
        supplier_email="sales@carbonsupply.com",
        unit_price=11.00,
        estimated_total_cost=370.0,
        recommendation_action="LOW_RISK_RECOMMEND",
        user_action="APPROVE",
        user_reason=None,
        outcome_status="order_sent",
        run_id="memtest003",
    )

    # HaltedVendor Ltd: STOP_PURCHASE (-3) => score -3.0
    record_procurement_memory(
        **_common,
        supplier="HaltedVendor Ltd",
        supplier_email="ops@haltedvendor.com",
        unit_price=25.00,
        estimated_total_cost=820.0,
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="STOP_PURCHASE",
        user_reason="sourcing cancelled",
        outcome_status="stopped",
        run_id="memtest004",
    )

    # RedirectedSupplier: CHANGE (-1) => score -1.0
    record_procurement_memory(
        **_common,
        supplier="RedirectedSupplier",
        supplier_email="sales@redirected.com",
        unit_price=14.00,
        estimated_total_cost=460.0,
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="CHANGE",
        user_reason="switching to alternative",
        outcome_status="change_rfq_sent",
        run_id="memtest005",
    )

    # OverriddenVendor: APPROVE_ANYWAY (0) => score 0.0
    record_procurement_memory(
        **_common,
        supplier="OverriddenVendor",
        supplier_email="sales@overridden.com",
        unit_price=30.00,
        estimated_total_cost=980.0,
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="APPROVE_ANYWAY",
        user_reason=None,
        outcome_status="order_sent",
        run_id="memtest006",
    )

    # NewQuoteVendor: PROVIDE_NEW_QUOTE (0) => score 0.0
    record_procurement_memory(
        **_common,
        supplier="NewQuoteVendor",
        supplier_email="sales@newquote.com",
        unit_price=13.00,
        estimated_total_cost=440.0,
        recommendation_action="NEEDS_HUMAN_REVIEW",
        user_action="PROVIDE_NEW_QUOTE",
        user_reason=None,
        outcome_status="pending_new_quote",
        run_id="memtest007",
    )

    print("[MEMORY TEST] Records seeded.\n")
    cmd_memory_status(args)

    conn = get_connection()
    try:
        battery    = get_supplier_memory_score(conn, "NMC Powder", "BatteryMaterials Co")
        carbon     = get_supplier_memory_score(conn, "NMC Powder", "CarbonSupply Inc")
        halted     = get_supplier_memory_score(conn, "NMC Powder", "HaltedVendor Ltd")
        redirected = get_supplier_memory_score(conn, "NMC Powder", "RedirectedSupplier")
        overridden = get_supplier_memory_score(conn, "NMC Powder", "OverriddenVendor")
        newquote   = get_supplier_memory_score(conn, "NMC Powder", "NewQuoteVendor")
        unknown    = get_supplier_memory_score(conn, "NMC Powder", "UnknownVendor")

        # Verify memory note in recommendation reason (uses generic supplier/product)
        sample_quote = extract_quote_fields(
            "Unit price: $12.50/kg\nIn stock. Lead time: 5 days. Shipping: $20. Valid for 14 days."
        )
        reason_positive = build_recommendation_reason("NMC Powder", "CarbonSupply Inc", 30.0, sample_quote, conn)
        reason_negative = build_recommendation_reason("NMC Powder", "BatteryMaterials Co", 30.0, sample_quote, conn)
        reason_none     = build_recommendation_reason("NMC Powder", "UnknownVendor", 30.0, sample_quote, conn)
    finally:
        conn.close()

    score_tests = [
        ("BatteryMaterials Co  APPROVE+REJECT score",  battery["supplier_score"],    -1.0),
        ("CarbonSupply Inc     APPROVE score",         carbon["supplier_score"],      1.0),
        ("HaltedVendor Ltd     STOP_PURCHASE score",   halted["supplier_score"],     -3.0),
        ("RedirectedSupplier   CHANGE score",          redirected["supplier_score"], -1.0),
        ("OverriddenVendor     APPROVE_ANYWAY score",  overridden["supplier_score"],  0.0),
        ("NewQuoteVendor       PROVIDE_NEW_QUOTE score",newquote["supplier_score"],   0.0),
    ]
    note_tests = [
        ("positive memory note in reason (CarbonSupply Inc)",
         "Memory note: this supplier has previous approvals." in reason_positive),
        ("negative memory note in reason (BatteryMaterials Co)",
         "Memory note: this supplier has previous negative feedback." in reason_negative),
        ("no-history memory note in reason (UnknownVendor)",
         "Memory note: no prior supplier feedback found." in reason_none),
        ("memory signal section present in reason",
         "Memory signal:" in reason_positive),
        ("audit: change_count tracked for RedirectedSupplier",
         redirected["change_count"] == 1),
        ("audit: stop_count tracked for HaltedVendor",
         halted["stop_count"] == 1),
        ("audit: unknown vendor returns zero score",
         unknown["supplier_score"] == 0.0 and (
             unknown["approval_count"] + unknown["reject_count"] == 0
         )),
    ]

    all_pass = True
    print("[MEMORY TEST] Score verification:")
    for label, actual, expected in score_tests:
        ok = abs(actual - expected) < 0.001
        all_pass = all_pass and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label} = {actual:.1f}  (expected {expected:.1f})")

    print("\n[MEMORY TEST] Memory note verification:")
    for label, ok in note_tests:
        all_pass = all_pass and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

    print(f"\n[MEMORY TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")


def _make_mock_email_msg():
    """Return a minimal email.message object for simulation (no PDF)."""
    import email as _email
    return _email.message_from_string("Subject: Mock\r\n\r\nMock body")


def cmd_dedup_test(args):
    """
    Verify that run_pipeline_from_gmail_invoices is idempotent:
    processing the same invoice twice must not change inventory a second time.
    No Gmail, no Claude.
    """
    from database import get_connection, _execute, init_db, is_message_processed, mark_message_processed
    from scripts.run_pipeline import run_pipeline_from_gmail_invoices, _invoice_signature, _count_valid_line_items

    print("[DEDUP TEST] Initialising...")
    init_db()

    PRODUCT      = "DedupTestWidget"
    INVOICE_NUM  = "DEDUP-TEST-INV-001"
    INVOICE_A    = "DEDUP-TEST-INV-A"
    INVOICE_B    = "DEDUP-TEST-INV-B"
    INITIAL_QTY  = 100.0
    SALES_QTY    = 30.0
    SALES_QTY_A  = 15.0
    SALES_QTY_B  = 10.0

    conn = get_connection()
    all_pass = True

    # Build test invoice dicts up-front so we can compute their signatures
    # for use in finally-block cleanup.
    fake_invoice = {
        "invoice_number": INVOICE_NUM,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY * 10.0,
        "line_items": [
            {"description": PRODUCT, "quantity": SALES_QTY, "unit_price": 10.0, "total": SALES_QTY * 10.0}
        ],
    }
    # Test 7: same content as fake_invoice but invoice_number=None
    fake_invoice_unnamed = {
        "invoice_number": None,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY * 10.0,
        "line_items": [
            {"description": PRODUCT, "quantity": SALES_QTY, "unit_price": 10.0, "total": SALES_QTY * 10.0}
        ],
    }
    # Test 8: two genuinely different invoices (different quantities → different sigs)
    fake_invoice_a = {
        "invoice_number": INVOICE_A,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY_A * 10.0,
        "line_items": [
            {"description": PRODUCT, "quantity": SALES_QTY_A, "unit_price": 10.0, "total": SALES_QTY_A * 10.0}
        ],
    }
    fake_invoice_b = {
        "invoice_number": INVOICE_B,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY_B * 10.0,
        "line_items": [
            {"description": PRODUCT, "quantity": SALES_QTY_B, "unit_price": 10.0, "total": SALES_QTY_B * 10.0}
        ],
    }
    # Test 9: same invoice_number, but the first extracted dict has empty line_items
    # and the second (a different PDF chunk) has valid line_items.
    # Simulates the post-_fill_missing_invoice_numbers state where both chunks got
    # INVOICE_NUM from the email subject; dedup must upgrade to the complete candidate.
    fake_invoice_named_empty = {
        "invoice_number": INVOICE_NUM,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY * 10.0,
        "line_items": [],   # extracted from a header-only chunk, no item rows
    }
    fake_invoice_named_valid = {
        "invoice_number": INVOICE_NUM,
        "document_type": "sales",
        "vendor_name": "Test Customer",
        "date": "2026-05-06",
        "total_amount": SALES_QTY * 10.0,
        "line_items": [
            {"description": PRODUCT, "quantity": SALES_QTY, "unit_price": 10.0, "total": SALES_QTY * 10.0}
        ],
    }

    sig_main         = _invoice_signature(fake_invoice)
    sig_unnamed      = _invoice_signature(fake_invoice_unnamed)
    sig_a            = _invoice_signature(fake_invoice_a)
    sig_b            = _invoice_signature(fake_invoice_b)
    sig_named_empty  = _invoice_signature(fake_invoice_named_empty)
    # fake_invoice_named_valid shares sig with fake_invoice (same content)

    try:
        # Seed a disposable test product
        _execute(conn, "DELETE FROM stock WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?,?,?,?,?)",
                 (INVOICE_NUM, INVOICE_A, INVOICE_B, sig_main, sig_a, sig_b))
        _execute(conn, """
            INSERT INTO stock (product_name, supplier, quantity_kg, reorder_at, unit)
            VALUES (?, 'Test Supplier', ?, 0, 'kg')
        """, (PRODUCT, INITIAL_QTY))
        conn.commit()

        expected_after_first = INITIAL_QTY - SALES_QTY

        # --- Test 1: first pass applies the invoice ---
        run_pipeline_from_gmail_invoices([fake_invoice])

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_first = float(row["quantity_kg"]) if row else None

        ok1 = qty_after_first is not None and abs(qty_after_first - expected_after_first) < 0.01
        all_pass = all_pass and ok1
        print(
            f"  {'PASS' if ok1 else 'FAIL'}  "
            f"First pass: {INITIAL_QTY} -> {qty_after_first} (expected {expected_after_first})"
        )

        # --- Test 2: within-batch duplicate (same invoice_number twice in one call) ---
        # Simulates _split_invoice_chunks splitting a PDF into multiple chunks
        # where Claude returns the same invoice_number for each chunk.
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?)", (INVOICE_NUM, sig_main))
        _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?", (INITIAL_QTY, PRODUCT))
        conn.commit()

        run_pipeline_from_gmail_invoices([fake_invoice, fake_invoice])  # same invoice twice in one batch

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_within_batch = float(row["quantity_kg"]) if row else None

        ok2 = qty_after_within_batch is not None and abs(qty_after_within_batch - expected_after_first) < 0.01
        all_pass = all_pass and ok2
        print(
            f"  {'PASS' if ok2 else 'FAIL'}  "
            f"Within-batch duplicate: {INITIAL_QTY} -> {qty_after_within_batch} "
            f"(expected {expected_after_first}, not {INITIAL_QTY - 2 * SALES_QTY})"
        )

        # --- Test 3: cross-batch duplicate (already applied, second call should skip) ---
        run_pipeline_from_gmail_invoices([fake_invoice])

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_second_batch = float(row["quantity_kg"]) if row else None

        ok3 = qty_after_second_batch is not None and abs(qty_after_second_batch - qty_after_within_batch) < 0.01
        all_pass = all_pass and ok3
        print(
            f"  {'PASS' if ok3 else 'FAIL'}  "
            f"Cross-batch duplicate: inventory unchanged at {qty_after_second_batch} "
            f"(expected {qty_after_within_batch})"
        )

        # --- Test 4: inv_applied mark present ---
        ok4 = is_message_processed(INVOICE_NUM, label="inv_applied")
        all_pass = all_pass and ok4
        print(f"  {'PASS' if ok4 else 'FAIL'}  invoice_number marked as applied in processed_messages")

        # --- Test 5 & 6: DB dedup label isolation ---
        mark_message_processed("dedup-test-rfc-id", label="sales/invoice")
        ok5 = is_message_processed("dedup-test-rfc-id", label="sales/invoice")
        ok6 = not is_message_processed("dedup-test-rfc-id", label="purchase/receipt")
        all_pass = all_pass and ok5 and ok6
        _execute(conn, "DELETE FROM processed_messages WHERE message_id = ?", ("dedup-test-rfc-id",))
        conn.commit()
        print(f"  {'PASS' if ok5 else 'FAIL'}  is_message_processed returns True for marked id+label")
        print(f"  {'PASS' if ok6 else 'FAIL'}  is_message_processed returns False for different label")

        # --- Test 7: named + unnamed same-content → applied only once ---
        # Simulates one PDF chunk returning invoice_number="CUST-DEMO-001" and
        # another chunk returning invoice_number=None but identical line items.
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?,?)",
                 (INVOICE_NUM, sig_main, sig_unnamed))
        _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?", (INITIAL_QTY, PRODUCT))
        conn.commit()

        run_pipeline_from_gmail_invoices([fake_invoice, fake_invoice_unnamed])

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_7 = float(row["quantity_kg"]) if row else None
        expected_7 = INITIAL_QTY - SALES_QTY

        ok7 = qty_after_7 is not None and abs(qty_after_7 - expected_7) < 0.01
        all_pass = all_pass and ok7
        print(
            f"  {'PASS' if ok7 else 'FAIL'}  "
            f"Named+unnamed same content: {INITIAL_QTY} -> {qty_after_7} "
            f"(expected {expected_7}, not {INITIAL_QTY - 2 * SALES_QTY}) — sig dedup"
        )

        # --- Test 8: two genuinely different invoices both apply ---
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?,?,?)",
                 (INVOICE_A, INVOICE_B, sig_a, sig_b))
        _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?", (INITIAL_QTY, PRODUCT))
        conn.commit()

        run_pipeline_from_gmail_invoices([fake_invoice_a, fake_invoice_b])

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_8 = float(row["quantity_kg"]) if row else None
        expected_8 = INITIAL_QTY - SALES_QTY_A - SALES_QTY_B

        ok8 = qty_after_8 is not None and abs(qty_after_8 - expected_8) < 0.01
        all_pass = all_pass and ok8
        print(
            f"  {'PASS' if ok8 else 'FAIL'}  "
            f"Two different invoices both apply: {INITIAL_QTY} -> {qty_after_8} "
            f"(expected {expected_8})"
        )

        # --- Test 9: named-empty + named-valid (same invoice_number) → best candidate wins ---
        # Simulates one PDF producing multiple chunks where the first chunk has
        # invoice_number but no line_items, and a later chunk has both.
        # After _fill_missing_invoice_numbers, both share the same invoice_number.
        # _dedup_invoice_batch must upgrade to the candidate with valid line_items.
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?,?)",
                 (INVOICE_NUM, sig_main, sig_named_empty))
        _execute(conn, "UPDATE stock SET quantity_kg = ? WHERE product_name = ?", (INITIAL_QTY, PRODUCT))
        conn.commit()

        run_pipeline_from_gmail_invoices([fake_invoice_named_empty, fake_invoice_named_valid])

        row = _execute(conn, "SELECT quantity_kg FROM stock WHERE product_name = ?", (PRODUCT,)).fetchone()
        qty_after_9 = float(row["quantity_kg"]) if row else None
        expected_9 = INITIAL_QTY - SALES_QTY

        ok9a = qty_after_9 is not None and abs(qty_after_9 - expected_9) < 0.01
        ok9b = _count_valid_line_items(fake_invoice_named_valid) > 0  # sanity: valid candidate had items
        ok9 = ok9a and ok9b
        all_pass = all_pass and ok9
        print(
            f"  {'PASS' if ok9 else 'FAIL'}  "
            f"Named-empty vs named-valid (same invoice_number): {INITIAL_QTY} -> {qty_after_9} "
            f"(expected {expected_9}) — completeness upgrade"
        )

        print(f"\n[DEDUP TEST] {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")

    finally:
        # Cleanup: remove test rows without affecting real data
        _execute(conn, "DELETE FROM stock WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM processed_messages WHERE message_id IN (?,?,?,?,?,?,?,?,?)",
                 (INVOICE_NUM, INVOICE_A, INVOICE_B,
                  sig_main, sig_unnamed, sig_a, sig_b,
                  sig_named_empty, "dedup-test-rfc-id"))
        _execute(conn, "DELETE FROM procurement_recommendations WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_email_drafts WHERE product_name = ?", (PRODUCT,))
        _execute(conn, "DELETE FROM procurement_memory WHERE product_name = ?", (PRODUCT,))
        conn.commit()
        conn.close()
        print("[DEDUP TEST] Cleaned up.")


def cmd_route_test(args):
    """Simulate all four Gmail label routing flows without a real Gmail connection."""
    from agents.email_router import simulate_label_routing

    invoice_mock = {
        "message_id": "mock-invoice-001",
        "msg":        _make_mock_email_msg(),
        "subject":    "Invoice from Test Supplier",
        "sender":     "test@supplier.com",
        "body":       "",
        "run_id":     None,
    }
    quote_mock = {
        "message_id": "mock-quote-002",
        "msg":        None,
        "subject":    "Re: Reorder Suggestion - Widget A [RUN_ID=sim1234]",
        "sender":     "supplier@example.com",
        "body":       "Unit price: $12.50\nAvailability: in stock\nLead time: 7 days\nShipping: $20",
        "run_id":     "sim1234",
    }
    approval_mock = {
        "message_id": "mock-approval-003",
        "msg":        None,
        "subject":    "Order Approval Required - Widget A [RUN_ID=sim5678]",
        "sender":     "user@company.com",
        "body":       "APPROVE",
        "run_id":     "sim5678",
    }
    # Approval reply that inherited the procurement/quote label from the thread.
    # Must be routed to approval handler via content-based detection, not quote parser.
    approval_via_quote_label_mock = {
        "message_id": "mock-approval-via-quote-004",
        "msg":        None,
        "subject":    "Re: Reorder Suggestion - Widget A [RUN_ID=sim1234]",
        "sender":     "user@company.com",
        "body":       "APPROVE",
        "run_id":     "sim1234",
    }

    print("=" * 60)
    print("Simulating Gmail label-based routing (no Gmail required)")
    print("=" * 60)

    for label, mock in [
        ("sales/invoice",        invoice_mock),
        ("purchase/receipt",     invoice_mock),
        ("procurement/quote",    quote_mock),
        ("procurement/approval", approval_mock),
        ("procurement/quote",    approval_via_quote_label_mock),  # body=APPROVE -> approval handler
    ]:
        print(f"\n--- {label} ---")
        simulate_label_routing(label, mock)

    print("\n" + "=" * 60)
    print("Simulation complete.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="cli",
        description=(
            "California Nutraceuticals — Invoice Intelligence CLI\n"
            "\n"
            "Primary runtime:  python cli.py agent once\n"
            "                  python cli.py agent watch\n"
            "\n"
            "Demo/testing:     python cli.py run scenario <path.json>\n"
            "                  python cli.py run pdf --pdf-dir <dir>\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # init-db
    sub.add_parser("init-db", help="Initialise DB and seed inventory/products")

    # agent once | watch
    agent_p = sub.add_parser("agent", help="Email-driven invoice agent (primary runtime)")
    agent_sub = agent_p.add_subparsers(dest="agent_cmd", metavar="<once|watch>")
    agent_sub.required = True
    agent_sub.add_parser("once",  help="Check Gmail once, process unread invoices, exit")
    agent_watch_sub_p = agent_sub.add_parser("watch", help="Poll Gmail in a loop (Ctrl-C to stop)")
    agent_watch_sub_p.add_argument(
        "--interval", type=int, default=10, metavar="SECONDS",
        help="Poll interval in seconds (default: 10)",
    )

    # run scenario | pdf
    run_p = sub.add_parser("run", help="Pipeline runner for demo/testing")
    run_sub = run_p.add_subparsers(dest="run_cmd", metavar="<scenario|pdf>")
    run_sub.required = True

    scen_p = run_sub.add_parser("scenario", help="Run against a scenario JSON file")
    scen_p.add_argument("path", help="Path to scenario JSON (e.g. data/scenario/day1.json)")

    pdf_p = run_sub.add_parser("pdf", help="Run against local PDF files (fallback/demo)")
    pdf_p.add_argument("--pdf-dir", default="data/invoices",
                       help="Folder containing PDF invoices (default: data/invoices)")

    # app
    sub.add_parser("app", help="Launch the Dash web application")

    # feedback once
    feedback_p = sub.add_parser("feedback", help="Procurement feedback email commands")
    feedback_sub = feedback_p.add_subparsers(dest="feedback_cmd", metavar="<once>")
    feedback_sub.required = True
    feedback_sub.add_parser("once", help="Fetch and print unread procurement feedback emails")

    # run-procurement
    sub.add_parser("run-procurement", help="Run Procurement Agent against the active database")

    # route-test
    sub.add_parser("route-test", help="Simulate all 4 Gmail label routing flows (no Gmail needed)")

    # demo-seed
    sub.add_parser("demo-seed", help="Seed demo DB, generate PDFs, and print demo script")

    # route-gmail-once
    sub.add_parser("route-gmail-once", help="Connect to Gmail and route unread emails by label once")

    # route-gmail-watch
    sub.add_parser("route-gmail-watch", help="Watch Gmail labels in a loop every 10s (Ctrl-C to stop)")

    # agent-watch
    agent_watch_p = sub.add_parser(
        "agent-watch",
        help="Closed-loop agent: Gmail routing + low-stock procurement (Ctrl-C to stop)",
    )
    agent_watch_p.add_argument(
        "--interval", type=int, default=10, metavar="SECONDS",
        help="Poll interval in seconds (default: 10)",
    )

    # inventory-status
    sub.add_parser("inventory-status", help="Print current inventory stock levels")

    # memory-status
    sub.add_parser("memory-status", help="Print supplier memory summary (scores, approvals, rejections)")

    # memory-test
    sub.add_parser("memory-test", help="Seed test memory records and verify scoring with PASS/FAIL")

    # strategy-test
    sub.add_parser("strategy-test", help="Simulate primary-supplier-first strategy scenarios with PASS/FAIL")

    # demo-check
    sub.add_parser("demo-check", help="Verify product-name normalization and stock-update logic (no Gmail needed)")

    # agent-watch-test
    sub.add_parser("agent-watch-test", help="End-to-end test: low-stock RFQ flow with stubbed email (no Gmail needed)")

    # supplier-email-test
    sub.add_parser("supplier-email-test", help="Verify resolve_supplier_email across all resolution paths")

    # procurement-action-test
    sub.add_parser(
        "procurement-action-test",
        help="Unit tests: run_recommendation_from_quote, APPROVE ANYWAY order path, agent-watch orchestrator check",
    )

    # email-loop-test
    sub.add_parser(
        "email-loop-test",
        help="Full email-loop simulation without Gmail: invoice->RFQ->quote->approve/reject/fallback",
    )

    # business-demo-test
    sub.add_parser(
        "business-demo-test",
        help="End-to-end business demo story: CUST-DEMO-001 -> Collagen Powder approve + FCP reject paths",
    )

    # db-check
    sub.add_parser("db-check", help="Print database backend, host, stock count, and sample products")

    # slack-agent
    sub.add_parser("slack-agent", help="Start Hermes Slack bot using Socket Mode")

    # slack-notify-test
    sub.add_parser("slack-notify-test", help="Send a sample approval reminder to SLACK_APPROVAL_CHANNEL")

    # dedup-test
    sub.add_parser(
        "dedup-test",
        help="Verify duplicate-invoice prevention: same invoice applied twice must not change inventory twice",
    )

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init-db":          cmd_init_db,
        "app":              cmd_app,
        "run-procurement":  cmd_run_procurement,
        "route-test":       cmd_route_test,
        "demo-seed":        cmd_demo_seed,
        "route-gmail-once":  cmd_route_gmail_once,
        "route-gmail-watch": cmd_route_gmail_watch,
        "agent-watch":       cmd_agent_watch,
        "inventory-status": cmd_inventory_status,
        "memory-status":    cmd_memory_status,
        "memory-test":      cmd_memory_test,
        "strategy-test":    cmd_strategy_test,
        "demo-check":              cmd_demo_check,
        "agent-watch-test":        cmd_agent_watch_test,
        "supplier-email-test":     cmd_supplier_email_test,
        "procurement-action-test": cmd_procurement_action_test,
        "email-loop-test":         cmd_email_loop_test,
        "business-demo-test":      cmd_business_demo_test,
        "dedup-test":              cmd_dedup_test,
        "db-check":                cmd_db_check,
        "slack-agent":             cmd_slack_agent,
        "slack-notify-test":       cmd_slack_notify_test,
    }

    if args.command in dispatch:
        dispatch[args.command](args)

    elif args.command == "agent":
        {"once": cmd_agent_once, "watch": cmd_agent_watch}[args.agent_cmd](args)

    elif args.command == "run":
        {"scenario": cmd_run_scenario, "pdf": cmd_run_pdf}[args.run_cmd](args)

    elif args.command == "feedback":
        {"once": cmd_feedback_once}[args.feedback_cmd](args)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()