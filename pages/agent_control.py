"""
pages/agent_control.py — Agent Control page
Toggle the invoice agent on/off, run commands, view logs.
"""

import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection
from invoice_agent import start_watch, stop_watch, is_running, run_agent, run_once

dash.register_page(__name__, path="/agent-control", name="Agent Control")


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_agent_state():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_state (
            id          INTEGER PRIMARY KEY,
            active      INTEGER DEFAULT 0,
            last_run    TEXT,
            last_status TEXT
        )
    """)
    row = conn.execute("SELECT * FROM agent_state WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO agent_state (id, active) VALUES (1, 0)")
        conn.commit()
        row = conn.execute("SELECT * FROM agent_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def get_run_log(limit=10):
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            message    TEXT,
            status     TEXT DEFAULT 'ok',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    rows = conn.execute(
        "SELECT * FROM agent_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    conn = get_connection()
    try:
        invoices = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    except Exception:
        invoices = 0
    try:
        alerts = conn.execute(
            "SELECT COUNT(*) FROM stock WHERE quantity_kg < reorder_at"
        ).fetchone()[0]
    except Exception:
        alerts = 0
    conn.close()
    return {"invoices": invoices, "alerts": alerts}


from datetime import datetime

def log_run(message, status="ok"):
    conn = get_connection()
    local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO agent_log (message, status, created_at) VALUES (?, ?, ?)",
        (message, status, local_time)
    )
    conn.execute(
        "UPDATE agent_state SET last_run=?, last_status=? WHERE id=1",
        (local_time, status)
    )
    conn.commit()
    conn.close()


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout():
    state  = get_agent_state()
    logs   = get_run_log()
    stats  = get_stats()
    active = is_running()  # use live thread state, not just DB

    label_style = {
        "fontSize": "11px",
        "fontWeight": "500",
        "letterSpacing": "0.08em",
        "textTransform": "uppercase",
        "color": "var(--bs-secondary)",
        "marginBottom": "8px",
    }

    return dbc.Container([

        html.H4("Agent control", className="mb-3 mt-3"),

        # ── Status card ──
        dbc.Card([
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H6("Invoice agent", className="mb-0"),
                        html.Small(
                            "Monitors Gmail · updates SQLite stock · embeds to ChromaDB",
                            className="text-muted"
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
                        className="text-muted"
                    )),
                    dbc.Col(
                        dbc.Badge(
                            "Running" if active else "Stopped",
                            color="success" if active else "secondary",
                            className="float-end",
                        )
                    ),
                ]),
            ])
        ], className="mb-3"),

        # ── Stats ──
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["invoices"], className="mb-0"),
                html.Small("Invoices indexed", className="text-muted"),
            ])), width=4),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["alerts"], className="mb-0"),
                html.Small("Stock alerts", className="text-muted"),
            ])), width=4),
        ], className="mb-3 g-2"),

        # ── Command input ──
        html.P("Send a command", style=label_style),
        dbc.Card([
            dbc.CardBody([
                dbc.InputGroup([
                    dbc.Input(
                        id="cmd-input",
                        placeholder="e.g. check low stock, check gmail…",
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
                ]),
                html.Div(id="cmd-output", className="mt-2",
                         style={"fontSize": "13px", "whiteSpace": "pre-wrap"}),
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
                            html.P(r["message"], className="mb-0",
                                   style={"fontSize": "13px"}),
                            html.Small(r["created_at"], className="text-muted"),
                        ]),
                    ], align="start", className="mb-2")
                    for r in logs
                ] if logs else [
                    html.Small("No runs yet.", className="text-muted")
                ])
            ])
        ]),

        # Feedback div (unused visually but needed for callbacks)
        html.Div(id="toggle-feedback"),

    ], fluid=True)


# ── Callbacks ──────────────────────────────────────────────────────────────────

@callback(
    Output("toggle-feedback", "children"),
    Input("agent-toggle", "value"),
    prevent_initial_call=True,
)
def toggle_agent(active):
    if active:
        msg = start_watch()
        status = "ok"
    else:
        msg = stop_watch()
        status = "ok"

    # Persist to DB
    conn = get_connection()
    conn.execute(
        "UPDATE agent_state SET active=? WHERE id=1", (1 if active else 0,)
    )
    conn.commit()
    conn.close()

    log_run(msg, status)
    return ""


@callback(
    Output("cmd-input", "value"),
    Input("btn-stock", "n_clicks"),
    Input("btn-gmail", "n_clicks"),
    prevent_initial_call=True,
)
def fill_command(s, g):
    ctx = dash.callback_context.triggered_id
    cmds = {
        "btn-stock": "check low stock",
        "btn-gmail": "check gmail",
    }
    return cmds.get(ctx, "")


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
    log_run(f"Command: {command[:60]}", "ok")
    return result
