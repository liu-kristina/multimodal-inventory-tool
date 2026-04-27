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
from invoice_agent import is_running, run_agent, start_watch, stop_watch

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
    state  = get_agent_state()
    logs   = get_run_log()
    flags  = get_flags(resolved=False)
    stats  = get_stats()
    active = is_running()

    label_style = {
        "fontSize": "11px",
        "fontWeight": "500",
        "letterSpacing": "0.08em",
        "textTransform": "uppercase",
        "color": "var(--bs-secondary)",
        "marginBottom": "8px",
    }

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

    return dbc.Container([

        html.H4("Agent control", className="mb-3 mt-3"),

        # ── Status card ──
        dbc.Card([
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H6("Invoice agent", className="mb-0"),
                        html.Small(
                            "Monitors Gmail · updates stock · drafts and sends procurement email",
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
                    dbc.Button("Draft procurement email", size="sm", outline=True,
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

    ], fluid=True)


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
        "btn-draft-email": "draft procurement email",
        "btn-replies":     "check procurement replies",
    }.get(ctx, "")


@callback(
    Output("cmd-output", "children"),
    Input("cmd-btn", "n_clicks"),
    State("cmd-input", "value"),
    prevent_initial_call=True,
)
def run_command(n, command):
    if not command:
        return "Please enter a command."
    result = run_agent(command)
    log_run(f"Command: {command[:60]}")
    return result


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
