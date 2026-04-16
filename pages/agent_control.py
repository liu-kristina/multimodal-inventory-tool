import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection

dash.register_page(__name__, path="/agent-control", name="Agent Control")

def get_agent_state():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_state (
            id INTEGER PRIMARY KEY,
            active INTEGER DEFAULT 1,
            last_run TEXT,
            last_status TEXT
        )
    """)
    row = conn.execute("SELECT * FROM agent_state WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO agent_state (id, active) VALUES (1, 1)")
        conn.commit()
        row = conn.execute("SELECT * FROM agent_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)

def get_run_log(limit=10):
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            status TEXT DEFAULT 'ok',
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
    invoices = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    alerts = conn.execute(
        "SELECT COUNT(*) FROM stock WHERE quantity_kg < reorder_at"
    ).fetchone()[0]
    conn.close()
    return {"invoices": invoices, "alerts": alerts}

def layout():
    state = get_agent_state()
    logs = get_run_log()
    stats = get_stats()
    active = bool(state["active"])

    return dbc.Container([
        html.H4("Agent control", className="mb-3 mt-3"),

        # --- Status card ---
        dbc.Card([
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H6("Invoice agent", className="mb-0"),
                        html.Small("RAG over invoices · SQLite stock updates",
                                   className="text-muted"),
                    ]),
                    dbc.Col([
                        dbc.Switch(id="agent-toggle", value=active,
                                   label="Active" if active else "Inactive",
                                   className="float-end"),
                    ], width="auto"),
                ], align="center"),
                html.Hr(),
                dbc.Row([
                    dbc.Col(html.Small(
                        f"Last run: {state.get('last_run', 'never')}",
                        className="text-muted")),
                    dbc.Col(dbc.Badge(
                        "Healthy" if active else "Inactive",
                        color="success" if active else "secondary",
                        className="float-end")),
                ]),
            ])
        ], className="mb-3"),

        # --- Stats row ---
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["invoices"], className="mb-0"),
                html.Small("Invoices indexed", className="text-muted")
            ])), width=4),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.H4(stats["alerts"], className="mb-0"),
                html.Small("Stock alerts", className="text-muted")
            ])), width=4),
        ], className="mb-3 g-2"),

        # --- Command input ---
        html.H6("Send a command", className="text-uppercase text-muted mb-2",
                style={"fontSize": "11px", "letterSpacing": "0.08em"}),
        dbc.Card([
            dbc.CardBody([
                dbc.InputGroup([
                    dbc.Input(id="cmd-input",
                              placeholder="e.g. check low stock, re-index invoices…"),
                    dbc.Button("Run", id="cmd-btn", color="secondary"),
                ], className="mb-2"),
                html.Div([
                    dbc.Button("Check low stock", size="sm", outline=True,
                               color="secondary", id="btn-stock", className="me-1 mb-1"),
                    dbc.Button("Re-index invoices", size="sm", outline=True,
                               color="secondary", id="btn-reindex", className="me-1 mb-1"),
                    dbc.Button("Draft reorder email", size="sm", outline=True,
                               color="secondary", id="btn-email", className="mb-1"),
                ]),
                html.Div(id="cmd-output", className="mt-2 text-muted",
                         style={"fontSize": "13px"}),
            ])
        ], className="mb-3"),

        # --- Run log ---
        html.H6("Recent runs", className="text-uppercase text-muted mb-2",
                style={"fontSize": "11px", "letterSpacing": "0.08em"}),
        dbc.Card([
            dbc.CardBody([
                html.Div([
                    dbc.Row([
                        dbc.Col(html.Span("●", style={
                            "color": "#1D9E75" if r["status"] == "ok" else "#E24B4A",
                            "fontSize": "8px"
                        }), width="auto"),
                        dbc.Col([
                            html.P(r["message"], className="mb-0",
                                   style={"fontSize": "13px"}),
                            html.Small(r["created_at"], className="text-muted"),
                        ]),
                    ], align="start", className="mb-2")
                    for r in logs
                ] if logs else [html.Small("No runs yet.", className="text-muted")])
            ])
        ]),

        # Store for toggle state feedback
        html.Div(id="toggle-feedback"),

    ], fluid=True)


# --- Toggle callback ---
@callback(
    Output("toggle-feedback", "children"),
    Input("agent-toggle", "value"),
    prevent_initial_call=True
)
def toggle_agent(active):
    conn = get_connection()
    conn.execute("UPDATE agent_state SET active=? WHERE id=1", (1 if active else 0,))
    conn.commit()
    conn.close()
    return ""


# --- Quick button fills input ---
@callback(
    Output("cmd-input", "value"),
    Input("btn-stock", "n_clicks"),
    Input("btn-reindex", "n_clicks"),
    Input("btn-email", "n_clicks"),
    prevent_initial_call=True
)
def fill_command(s, r, e):
    ctx = dash.callback_context.triggered_id
    cmds = {
        "btn-stock": "Check which products are below reorder threshold",
        "btn-reindex": "Re-index all invoices in the data folder",
        "btn-email": "Draft a reorder email for the lowest stock item",
    }
    return cmds.get(ctx, "")


# --- Run command ---
@callback(
    Output("cmd-output", "children"),
    Input("cmd-btn", "n_clicks"),
    State("cmd-input", "value"),
    prevent_initial_call=True
)
def run_command(n, command):
    if not command:
        return "Please enter a command."
    # Check agent is active
    state = get_agent_state()
    if not state["active"]:
        return "Agent is currently inactive. Toggle it on to run commands."
    # Route to invoice_agent
    from invoice_agent import run_agent
    result = run_agent(command)
    # Log it
    conn = get_connection()
    conn.execute("INSERT INTO agent_log (message, status) VALUES (?, 'ok')",
                 (f"Command: {command[:60]}",))
    conn.execute("UPDATE agent_state SET last_run=datetime('now'), last_status='ok' WHERE id=1")
    conn.commit()
    conn.close()
    return result