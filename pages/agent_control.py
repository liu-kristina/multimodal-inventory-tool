"""
pages/agent_control.py — Agent Control page
Toggle the invoice agent, run commands, view logs and flags.
"""

import os
import sys
from datetime import datetime

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import _execute, _use_postgres, get_connection
from agents.invoice_agent import (
    is_running,
    run_agent,
    start_watch,
    stop_watch,
)
from agents.procurement_agent import (
    discard_procurement_draft,
    get_pending_drafts,
    send_procurement_draft,
)

dash.register_page(__name__, path="/agent-control", name="Agent Control")


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_agent_state() -> dict:
    conn = get_connection()
    _execute(conn, """
        CREATE TABLE IF NOT EXISTS agent_state (
            id          INTEGER PRIMARY KEY,
            active      INTEGER DEFAULT 0,
            last_run    TEXT,
            last_status TEXT
        )
    """)
    row = _execute(conn, "SELECT * FROM agent_state WHERE id=1").fetchone()
    if not row:
        _execute(conn, "INSERT INTO agent_state (id, active) VALUES (1, 0)")
        conn.commit()
        row = _execute(conn, "SELECT * FROM agent_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def get_run_log(limit: int = 10) -> list[dict]:
    conn = get_connection()
    if _use_postgres:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS agent_log (
                id         SERIAL PRIMARY KEY,
                message    TEXT,
                status     TEXT DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS agent_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message    TEXT,
                status     TEXT DEFAULT 'ok',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    rows = _execute(
        conn,
        "SELECT * FROM agent_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_flags(resolved: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        if _use_postgres:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS agent_flags (
                    id         SERIAL PRIMARY KEY,
                    reason     TEXT,
                    details    TEXT,
                    resolved   INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS agent_flags (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    reason     TEXT,
                    details    TEXT,
                    resolved   INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
        rows = _execute(
            conn,
            "SELECT * FROM agent_flags WHERE resolved=? ORDER BY created_at DESC",
            (1 if resolved else 0,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []


def get_stats() -> dict:
    conn = get_connection()
    try:
        invoices = _execute(conn, "SELECT COUNT(*) FROM invoices").fetchone()[0]
    except Exception:
        invoices = 0
    try:
        alerts = _execute(
            conn, "SELECT COUNT(*) FROM stock WHERE quantity_kg < reorder_at"
        ).fetchone()[0]
    except Exception:
        alerts = 0
    try:
        flags = _execute(
            conn, "SELECT COUNT(*) FROM agent_flags WHERE resolved=0"
        ).fetchone()[0]
    except Exception:
        flags = 0
    conn.close()
    return {"invoices": invoices, "alerts": alerts, "flags": flags}


def get_approval_history() -> dict:
    """
    Return procurement history split into pending and completed.
    Joins drafts with replies so we can show what action was taken.
    """
    conn = get_connection()
    try:
        # Pending: drafts that have been sent but not yet replied to
        pending = _execute(conn, """
            SELECT
                d.id          AS draft_id,
                d.product_name,
                d.supplier,
                d.supplier_email,
                d.status      AS draft_status,
                d.created_at,
                d.sent_at
            FROM procurement_email_drafts d
            WHERE d.status IN ('sent', 'draft')
              AND d.id NOT IN (
                  SELECT COALESCE(draft_id, '') FROM procurement_replies
              )
            ORDER BY d.created_at DESC
            LIMIT 50
        """).fetchall()

        # Completed: drafts that have a reply, or were discarded
        completed = _execute(conn, """
            SELECT
                d.id            AS draft_id,
                d.product_name,
                d.supplier,
                d.status        AS draft_status,
                d.created_at,
                d.sent_at,
                r.parsed_action AS reply_action,
                r.parsed_reason AS reply_reason,
                r.parsed_supplier AS reply_supplier,
                r.created_at    AS replied_at
            FROM procurement_email_drafts d
            LEFT JOIN procurement_replies r ON r.draft_id = d.id
            WHERE d.status = 'discarded'
               OR r.id IS NOT NULL
            ORDER BY COALESCE(r.created_at, d.created_at) DESC
            LIMIT 50
        """).fetchall()

        return {
            "pending":   [dict(r) for r in pending],
            "completed": [dict(r) for r in completed],
        }
    except Exception:
        return {"pending": [], "completed": []}
    finally:
        conn.close()


def log_run(message: str, status: str = "ok") -> None:
    conn = get_connection()
    local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _execute(
        conn,
        "INSERT INTO agent_log (message, status, created_at) VALUES (?, ?, ?)",
        (message, status, local_time),
    )
    _execute(
        conn,
        "UPDATE agent_state SET last_run=?, last_status=? WHERE id=1",
        (local_time, status),
    )
    conn.commit()
    conn.close()


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout():
    state    = get_agent_state()
    logs     = get_run_log()
    flags    = get_flags(resolved=False)
    stats    = get_stats()
    active   = is_running()
    drafts   = get_pending_drafts()
    approvals = get_approval_history()

    label_style = {
        "fontSize": "11px",
        "fontWeight": "500",
        "letterSpacing": "0.08em",
        "textTransform": "uppercase",
        "color": "var(--bs-secondary)",
        "marginBottom": "8px",
    }

    def _approval_row(r: dict, pending: bool):
        action = r.get("reply_action") or r.get("draft_status", "").upper()
        action_colors = {
            "APPROVE": "#1D9E75",
            "REJECT":  "#E24B4A",
            "CHANGE":  "#f0a500",
            "DISCARDED": "#adb5bd",
            "SENT":    "#0d6efd",
            "DRAFT":   "#6c757d",
        }
        badge_color = action_colors.get(action, "#6c757d")
        detail_parts = []
        if r.get("reply_reason"):
            detail_parts.append(f"Reason: {r['reply_reason']}")
        if r.get("reply_supplier"):
            detail_parts.append(f"New supplier: {r['reply_supplier']}")
        date_str = r.get("replied_at") or r.get("sent_at") or r.get("created_at") or ""
        return dbc.Row([
            dbc.Col(
                html.Span("●", style={"color": badge_color, "fontSize": "10px"}),
                width="auto",
            ),
            dbc.Col([
                html.P(r["product_name"], className="mb-0 fw-semibold", style={"fontSize": "13px"}),
                html.Small(f"Supplier: {r['supplier']}", className="text-muted d-block"),
                html.Small(", ".join(detail_parts), className="text-muted d-block") if detail_parts else None,
                html.Small(date_str, className="text-muted"),
            ]),
            dbc.Col(
                dbc.Badge(action, style={"backgroundColor": badge_color}, className="float-end"),
                width="auto",
            ),
        ], align="start", className="mb-3")

    def flag_card(f):
        return dbc.Row([
            dbc.Col(
                html.Span("●", style={"color": "#E24B4A", "fontSize": "10px"}),
                width="auto",
            ),
            dbc.Col([
                html.P(f["reason"], className="mb-0 fw-500", style={"fontSize": "13px"}),
                html.Small(f.get("details", ""), className="text-muted d-block"),
                html.Small(f["created_at"], className="text-muted"),
            ]),
            dbc.Col(
                dbc.Button(
                    "Resolve", size="sm", outline=True, color="secondary",
                    id={"type": "resolve-btn", "index": f["id"]},
                    className="float-end",
                ),
                width="auto",
            ),
        ], align="start", className="mb-3")

    def draft_card(d):
        recipient = d["supplier_email"] or "Missing supplier email"
        return dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.P(d["product_name"], className="mb-1 fw-semibold"),
                        html.Small(f"Supplier: {d['supplier']}", className="text-muted d-block"),
                        html.Small(f"To: {recipient}", className="text-muted d-block"),
                        html.Small(f"Draft ID: {d['id']}", className="text-muted d-block"),
                    ]),
                    dbc.Col([
                        dbc.Button(
                            "Send",
                            id={"type": "send-draft", "index": d["id"]},
                            color="success",
                            size="sm",
                            className="me-2",
                            disabled=not bool(d["supplier_email"]),
                        ),
                        dbc.Button(
                            "Discard",
                            id={"type": "discard-draft", "index": d["id"]},
                            color="secondary",
                            outline=True,
                            size="sm",
                        ),
                    ], width="auto", className="text-end"),
                ], align="start"),
                html.Hr(className="my-2"),
                html.Div([
                    html.Small(f"Subject: {d['subject']}", className="fw-semibold d-block mb-2"),
                    html.Pre(
                        d["body"],
                        className="mb-0",
                        style={
                            "whiteSpace": "pre-wrap",
                            "fontSize": "12px",
                            "fontFamily": "inherit",
                            "background": "transparent",
                        },
                    ),
                ]),
            ]),
            className="mb-2",
        )

    return dbc.Container([

        html.H4("Agent control", className="mb-3 mt-3"),

        # ── Status card ──
        dbc.Card([
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H6("Invoice agent", className="mb-0"),
                        html.Small(
                            "Monitors Gmail · updates stock · drafts procurement emails for review",
                            className="text-muted",
                        ),
                    ]),
                    dbc.Col([
                        dbc.Switch(
                            id="agent-toggle",
                            value=active,
                            label="Active" if active else "Inactive",
                            className="float-end",
                        ),
                    ], width="auto"),
                ], align="center"),
                html.Hr(),
                dbc.Row([
                    dbc.Col(html.Small(
                        f"Last run: {state.get('last_run') or 'never'}",
                        className="text-muted",
                    )),
                    dbc.Col(dbc.Badge(
                        "Running" if active else "Stopped",
                        color="success" if active else "secondary",
                        className="float-end",
                    )),
                ]),
            ])
        ], className="mb-3"),

        # ── Stats ──
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["invoices"], className="mb-0"),
                html.Small("Invoices indexed", className="text-muted"),
            ])), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["alerts"], className="mb-0"),
                html.Small("Stock alerts", className="text-muted"),
            ])), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(
                    stats["flags"],
                    className="mb-0",
                    style={"color": "#E24B4A"} if stats["flags"] > 0 else {},
                ),
                html.Small("Needs review", className="text-muted"),
            ])), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(
                    len(approvals["pending"]),
                    className="mb-0",
                    style={"color": "#f0a500"} if approvals["pending"] else {},
                ),
                html.Small("Awaiting reply", className="text-muted"),
            ])), width=3),
        ], className="mb-3 g-2"),

        # ── Flags for review ──
        html.P("Needs review", style=label_style),
        dbc.Card([
            dbc.CardBody([
                html.Div(
                    id="flags-container",
                    children=[flag_card(f) for f in flags] if flags else [
                        html.Small("No items flagged for review.", className="text-muted")
                    ],
                )
            ])
        ], className="mb-3"),

        # ── Command input ──
        html.P("Send a command", style=label_style),
        dbc.Card([
            dbc.CardBody([
                dbc.InputGroup([
                    dbc.Input(
                        id="cmd-input",
                        placeholder="e.g. check low stock, check gmail, check procurement replies…",
                    ),
                    dbc.Button("Run", id="cmd-btn", color="secondary"),
                ], className="mb-2"),
                html.Div([
                    dbc.Button("Check low stock", size="sm", outline=True,
                               color="secondary", id="btn-stock",
                               className="me-1 mb-1"),
                    dbc.Button("Check Gmail now", size="sm", outline=True,
                               color="secondary", id="btn-gmail",
                               className="me-1 mb-1"),
                    dbc.Button("Draft emails for low stock", size="sm", outline=True,
                               color="secondary", id="btn-draft-email",
                               className="me-1 mb-1"),
                    dbc.Button("Check procurement replies", size="sm", outline=True,
                               color="secondary", id="btn-replies",
                               className="me-1 mb-1"),
                ]),
                html.Div(
                    id="cmd-output",
                    className="mt-2",
                    style={"fontSize": "13px", "whiteSpace": "pre-wrap"},
                ),
            ])
        ], className="mb-3"),

        # ── Procurement drafts ──
        html.P("Pending drafts", style=label_style),
        dbc.Card([
            dbc.CardBody([
                html.Div(
                    id="drafts-container",
                    children=[draft_card(d) for d in drafts] if drafts else [
                        html.Small("No procurement drafts awaiting approval.", className="text-muted")
                    ],
                ),
                html.Small(
                    "Drafts are only sent when you click Send. They are kept in memory for the current app run.",
                    className="text-muted d-block mt-2",
                ),
            ])
        ], className="mb-3"),

        # ── Approval history ──
        html.P("Approval history", style=label_style),
        dbc.Card([
            dbc.CardBody([
                dbc.Tabs([
                    dbc.Tab(
                        label=f"Awaiting reply ({len(approvals['pending'])})",
                        tab_id="tab-pending",
                        children=[
                            html.Div(className="mt-3", children=(
                                [_approval_row(r, pending=True) for r in approvals["pending"]]
                                if approvals["pending"] else
                                [html.Small("No emails awaiting reply.", className="text-muted")]
                            ))
                        ],
                    ),
                    dbc.Tab(
                        label=f"Completed ({len(approvals['completed'])})",
                        tab_id="tab-completed",
                        children=[
                            html.Div(className="mt-3", children=(
                                [_approval_row(r, pending=False) for r in approvals["completed"]]
                                if approvals["completed"] else
                                [html.Small("No completed approvals yet.", className="text-muted")]
                            ))
                        ],
                    ),
                ], id="approval-tabs", active_tab="tab-pending"),
            ])
        ], className="mb-3"),

        # ── Run log ──
        html.P("Recent runs", style=label_style),
        dbc.Card([
            dbc.CardBody([
                html.Div([
                    dbc.Row([
                        dbc.Col(
                            html.Span("●", style={
                                "color": "#1D9E75" if r["status"] == "ok" else "#E24B4A",
                                "fontSize": "10px",
                            }),
                            width="auto",
                        ),
                        dbc.Col([
                            html.P(r["message"], className="mb-0", style={"fontSize": "13px"}),
                            html.Small(r["created_at"], className="text-muted"),
                        ]),
                    ], align="start", className="mb-2")
                    for r in logs
                ] if logs else [
                    html.Small("No runs yet.", className="text-muted")
                ])
            ])
        ]),

        html.Div(id="toggle-feedback"),
        html.Div(id="resolve-feedback"),
        html.Div(id="draft-feedback"),

    ], fluid=True)


# ── Approval history renderer ──────────────────────────────────────────────────

_ACTION_COLOR = {
    "APPROVE":         "#1D9E75",
    "APPROVE ANYWAY":  "#1D9E75",
    "REJECT":          "#E24B4A",
    "STOP PURCHASE":   "#E24B4A",
    "CHANGE":          "#f0a500",
    "PROVIDE NEW QUOTE": "#f0a500",
    "INVALID":         "#aaa",
    "UNKNOWN":         "#aaa",
}

_STATUS_COLOR = {
    "pending_approval": "#aaa",
    "sent":             "#f0a500",
    "approved":         "#1D9E75",
    "rejected":         "#E24B4A",
    "discarded":        "#aaa",
    "draft":            "#aaa",
}

def _render_approval_history(history: list) -> list:
    if not history:
        return [html.Small("No procurement history yet.", className="text-muted")]

    rows = []
    for h in history:
        action       = h.get("parsed_action") or ""
        draft_status = h.get("draft_status") or h.get("rec_status") or "unknown"
        action_color = _ACTION_COLOR.get(action, "#aaa")
        status_color = _STATUS_COLOR.get(draft_status, "#aaa")

        detail_parts = []
        if h.get("parsed_reason"):
            detail_parts.append(f"Reason: {h['parsed_reason']}")
        if h.get("parsed_supplier"):
            detail_parts.append(f"New supplier: {h['parsed_supplier']}")
        detail = " · ".join(detail_parts)

        rows.append(
            dbc.Row([
                dbc.Col(
                    html.Span("●", style={"color": action_color if action else status_color, "fontSize": "10px"}),
                    width="auto",
                ),
                dbc.Col([
                    html.P(
                        [
                            html.Span(h["product_name"], className="fw-semibold"),
                            html.Span(f" · {h['supplier']}", className="text-muted"),
                        ],
                        className="mb-0",
                        style={"fontSize": "13px"},
                    ),
                    html.Small([
                        dbc.Badge(
                            draft_status.replace("_", " ").title(),
                            color="light",
                            text_color="dark",
                            className="me-1",
                            style={"border": f"1px solid {status_color}"},
                        ),
                        html.Span(action, style={"color": action_color, "fontWeight": "600"}) if action else None,
                        html.Span(f"  {detail}", className="text-muted") if detail else None,
                    ], className="d-block"),
                    html.Small(
                        f"Created: {h['created_at']}" + (f" · Replied: {h['reply_at']}" if h.get("reply_at") else ""),
                        className="text-muted",
                    ),
                ]),
            ], align="start", className="mb-3")
        )
    return rows


# ── Callbacks ──────────────────────────────────────────────────────────────────

@callback(
    Output("toggle-feedback", "children"),
    Input("agent-toggle", "value"),
    prevent_initial_call=True,
)
def toggle_agent(active):
    msg = start_watch() if active else stop_watch()
    conn = get_connection()
    try:
        _execute(conn, "UPDATE agent_state SET active=? WHERE id=1", (1 if active else 0,))
        conn.commit()
    finally:
        conn.close()
    log_run(msg)
    return ""


@callback(
    Output("cmd-input", "value"),
    Input("btn-stock",   "n_clicks"),
    Input("btn-gmail",   "n_clicks"),
    Input("btn-draft-email", "n_clicks"),
    Input("btn-replies", "n_clicks"),
    prevent_initial_call=True,
)
def fill_command(s, g, d, r):
    ctx = dash.callback_context.triggered_id
    return {
        "btn-stock":       "check low stock",
        "btn-gmail":       "check gmail",
        "btn-draft-email": "draft procurement emails",
        "btn-replies":     "check procurement replies",
    }.get(ctx, "")


@callback(
    Output("cmd-output", "children"),
    Output("drafts-container", "children"),
    Input("cmd-btn", "n_clicks"),
    State("cmd-input", "value"),
    prevent_initial_call=True,
)
def run_command(n, command):
    def render_drafts():
        drafts = get_pending_drafts()
        if not drafts:
            return [html.Small("No procurement drafts awaiting approval.", className="text-muted")]
        cards = []
        for d in drafts:
            recipient = d["supplier_email"] or "Missing supplier email"
            cards.append(dbc.Card(
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.P(d["product_name"], className="mb-1 fw-semibold"),
                            html.Small(f"Supplier: {d['supplier']}", className="text-muted d-block"),
                            html.Small(f"To: {recipient}", className="text-muted d-block"),
                            html.Small(f"Draft ID: {d['id']}", className="text-muted d-block"),
                        ]),
                        dbc.Col([
                            dbc.Button(
                                "Send",
                                id={"type": "send-draft", "index": d["id"]},
                                color="success",
                                size="sm",
                                className="me-2",
                                disabled=not bool(d["supplier_email"]),
                            ),
                            dbc.Button(
                                "Discard",
                                id={"type": "discard-draft", "index": d["id"]},
                                color="secondary",
                                outline=True,
                                size="sm",
                            ),
                        ], width="auto", className="text-end"),
                    ], align="start"),
                    html.Hr(className="my-2"),
                    html.Div([
                        html.Small(f"Subject: {d['subject']}", className="fw-semibold d-block mb-2"),
                        html.Pre(
                            d["body"],
                            className="mb-0",
                            style={
                                "whiteSpace": "pre-wrap",
                                "fontSize": "12px",
                                "fontFamily": "inherit",
                                "background": "transparent",
                            },
                        ),
                    ]),
                ]),
                className="mb-2",
            ))
        return cards

    if not command:
        return "Please enter a command.", render_drafts()
    result = run_agent(command)
    log_run(f"Command: {command[:60]}")
    return result, render_drafts()


@callback(
    Output("draft-feedback", "children"),
    Output("drafts-container", "children", allow_duplicate=True),
    Input({"type": "send-draft", "index": dash.ALL}, "n_clicks"),
    Input({"type": "discard-draft", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_draft_action(send_clicks, discard_clicks):
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return "", dash.no_update

    draft_id = triggered["index"]
    action_type = triggered["type"]

    if action_type == "send-draft":
        message = send_procurement_draft(draft_id)
        log_run(f"Sent procurement draft {draft_id}")
    else:
        message = discard_procurement_draft(draft_id)
        log_run(f"Discarded procurement draft {draft_id}")

    drafts = get_pending_drafts()
    if not drafts:
        children = [html.Small("No procurement drafts awaiting approval.", className="text-muted")]
    else:
        children = []
        for d in drafts:
            recipient = d["supplier_email"] or "Missing supplier email"
            children.append(dbc.Card(
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.P(d["product_name"], className="mb-1 fw-semibold"),
                            html.Small(f"Supplier: {d['supplier']}", className="text-muted d-block"),
                            html.Small(f"To: {recipient}", className="text-muted d-block"),
                            html.Small(f"Draft ID: {d['id']}", className="text-muted d-block"),
                        ]),
                        dbc.Col([
                            dbc.Button(
                                "Send",
                                id={"type": "send-draft", "index": d["id"]},
                                color="success",
                                size="sm",
                                className="me-2",
                                disabled=not bool(d["supplier_email"]),
                            ),
                            dbc.Button(
                                "Discard",
                                id={"type": "discard-draft", "index": d["id"]},
                                color="secondary",
                                outline=True,
                                size="sm",
                            ),
                        ], width="auto", className="text-end"),
                    ], align="start"),
                    html.Hr(className="my-2"),
                    html.Div([
                        html.Small(f"Subject: {d['subject']}", className="fw-semibold d-block mb-2"),
                        html.Pre(
                            d["body"],
                            className="mb-0",
                            style={
                                "whiteSpace": "pre-wrap",
                                "fontSize": "12px",
                                "fontFamily": "inherit",
                                "background": "transparent",
                            },
                        ),
                    ]),
                ]),
                className="mb-2",
            ))
    return message, children


@callback(
    Output("resolve-feedback", "children"),
    Input({"type": "resolve-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def resolve_flag(n_clicks):
    if not any(n_clicks):
        return ""
    flag_id = dash.callback_context.triggered_id["index"]
    conn = get_connection()
    try:
        _execute(conn, "UPDATE agent_flags SET resolved=1 WHERE id=?", (flag_id,))
        conn.commit()
    finally:
        conn.close()
    return ""


@callback(
    Output("approval-history-container", "children"),
    Input("approval-refresh", "n_intervals"),
)
def refresh_approval_history(n):
    return _render_approval_history(get_approval_history())
