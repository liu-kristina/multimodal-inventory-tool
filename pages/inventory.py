"""
pages/inventory.py - Inventory dashboard page
"""

import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from database import get_connection, _execute, _use_postgres

dash.register_page(__name__, path="/inventory", title="Inventory")

# ── Database ───────────────────────────────────────────────────────────────────

def get_inventory():
    conn = get_connection()
    try:
        rows = _execute(conn, "SELECT * FROM stock ORDER BY product_name").fetchall()
    finally:
        conn.close()
    return [
        {
            "product": row["product_name"],
            "stock": row["quantity_kg"],
            "reorder_at": row["reorder_at"],
            "unit": row["unit"],
        }
        for row in rows
    ]


def get_low_stock():
    return [p for p in get_inventory() if p["stock"] <= p["reorder_at"]]


def get_pending_products():
    """Return products flagged by the invoice agent as unknown."""
    try:
        conn = get_connection()
        rows = _execute(conn, """
            SELECT * FROM pending_products WHERE status = 'pending' ORDER BY created_at DESC
        """).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def approve_pending_product(product_name: str):
    """Move a pending product into the products table and mark as approved."""
    conn = get_connection()
    try:
        if _use_postgres:
            _execute(conn, """
                INSERT INTO products (product_name) VALUES (?)
                ON CONFLICT (product_name) DO NOTHING
            """, (product_name,))
        else:
            _execute(conn, "INSERT OR IGNORE INTO products (product_name) VALUES (?)", (product_name,))
        _execute(conn, "UPDATE pending_products SET status = 'approved' WHERE product_name = ?", (product_name,))
        conn.commit()
    finally:
        conn.close()


def dismiss_pending_product(product_name: str):
    """Dismiss a pending product without adding it."""
    conn = get_connection()
    try:
        _execute(conn, "UPDATE pending_products SET status = 'dismissed' WHERE product_name = ?", (product_name,))
        conn.commit()
    finally:
        conn.close()

def setup_db():
    """
    One-time setup: adds missing columns and sets reorder thresholds.
    Run manually from terminal when needed:
        python3 -c "from pages.inventory import setup_db; setup_db()"
    """
    conn = get_connection()
    try:
        _execute(conn, "ALTER TABLE stock ADD COLUMN reorder_at REAL DEFAULT 0")
    except Exception:
        pass  # column already exists
    try:
        _execute(conn, "ALTER TABLE stock ADD COLUMN unit TEXT DEFAULT 'kg'")
    except Exception:
        pass  # column already exists

    reorder_thresholds = [
        ("Collagen Powder",             100),
        ("Shark Cartilage Powder",      150),
        ("Fish Collagen Peptides",      100),
        ("Hydrolyzed Marine Collagen",  100),
        ("Bovine Gelatin Type A",       80),
        ("Bovine Cartilage Extract",    80),
        ("Plant Extract - Turmeric",    60),
        ("Plant Extract - Ashwagandha", 60),
        ("Plant Extract - Elderberry",  60),
        ("Hyaluronic Acid Powder",      50),
        ("Chondroitin Sulfate",         80),
        ("Glucosamine HCl",             80),
    ]
    for product, reorder in reorder_thresholds:
        _execute(conn, "UPDATE stock SET reorder_at = ? WHERE product_name = ?", (reorder, product))
    conn.commit()
    conn.close()
    print("Done")
    
def make_chart():
    """Build stock level bar chart colored by status."""
    inventory = get_inventory()
    products = [p["product"].replace("Plant Extract - ", "") for p in inventory]
    stocks   = [p["stock"] for p in inventory]
    colors   = [
        "#dc3545" if p["stock"] <= p["reorder_at"]
        else "#fd7e14" if p["stock"] <= p["reorder_at"] * 1.5
        else "#198754"
        for p in inventory
    ]

    fig = go.Figure(go.Bar(
        x=stocks,
        y=products,
        orientation="h",
        marker_color=colors,
        text=[f"{s} kg" for s in stocks],
        textposition="outside",
    ))

    fig.update_layout(
        margin=dict(l=0, r=60, t=10, b=10),
        height=420,
        xaxis_title="Stock (kg)",
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=False,
        font=dict(size=12),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
    return fig


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout():
    inventory   = get_inventory()
    low_stock   = get_low_stock()
    total       = len(inventory)
    low_count   = len(low_stock)
    healthy     = total - low_count

    return html.Div(
        [
            html.H4("Inventory Dashboard", className="mb-1 fw-bold"),
            html.P("Current stock levels across all products.", className="text-muted mb-4"),

            # Summary cards
            dbc.Row(
                [
                    dbc.Col(dbc.Card([
                        html.Div("Total Products", className="text-muted", style={"fontSize": "13px"}),
                        html.H3(total, className="mb-0 fw-bold"),
                    ], body=True, className="text-center"), width=4),
                    dbc.Col(dbc.Card([
                        html.Div("Healthy Stock", className="text-muted", style={"fontSize": "13px"}),
                        html.H3(healthy, className="mb-0 fw-bold text-success"),
                    ], body=True, className="text-center"), width=4),
                    dbc.Col(dbc.Card([
                        html.Div("Low Stock / Reorder", className="text-muted", style={"fontSize": "13px"}),
                        html.H3(low_count, className="mb-0 fw-bold text-danger"),
                    ], body=True, className="text-center"), width=4),
                ],
                className="mb-4",
            ),

            # Low stock alerts
            html.Div(
                [
                    dbc.Alert(
                        [
                            html.Strong(p["product"]),
                            f" — {p['stock']} kg remaining "
                            f"(reorder at {p['reorder_at']} kg)",
                        ],
                        color="danger",
                        className="mb-2 py-2",
                    )
                    for p in low_stock
                ] if low_stock else
                dbc.Alert("All products are above reorder thresholds.", color="success"),
                className="mb-4",
            ),

            # Pending products section
            html.Div(id="pending-products-section"),

            # Stock chart
            html.H6("Stock Levels", className="fw-bold mb-2"),
            dcc.Graph(figure=make_chart(), config={"displayModeBar": False}),

            # Product table
            html.H6("All Products", className="fw-bold mt-4 mb-2"),
            dbc.Table(
                [
                    html.Thead(html.Tr([
                        html.Th("Product"),
                        html.Th("Stock (kg)", style={"textAlign": "right"}),
                        html.Th("Reorder At", style={"textAlign": "right"}),
                        html.Th("Status"),
                    ])),
                    html.Tbody([
                        html.Tr([
                            html.Td(p["product"]),
                            html.Td(p["stock"], style={"textAlign": "right"}),
                            html.Td(p["reorder_at"], style={"textAlign": "right"}),
                            html.Td(
                                dbc.Badge(
                                    "Reorder" if p["stock"] <= p["reorder_at"] else "OK",
                                    color="danger" if p["stock"] <= p["reorder_at"] else "success",
                                    pill=True,
                                )
                            ),
                        ])
                        for p in inventory
                    ]),
                ],
                bordered=False,
                hover=True,
                responsive=True,
                striped=True,
                size="sm",
            ),
        ],
        style={"maxWidth": "900px"},
    )


# ── Callbacks ──────────────────────────────────────────────────────────────────

@callback(
    Output("pending-products-section", "children"),
    Input("pending-products-section", "id"),  # triggers on page load
)
def render_pending_products(_):
    """Render the pending products review section."""
    pending = get_pending_products()
    if not pending:
        return html.Div()

    return html.Div([
        html.H6("New Products — Pending Review", className="fw-bold mt-4 mb-2 text-warning"),
        html.P(
            "The invoice agent found these products that aren't in your products list. "
            "Approve to add them, or dismiss to ignore.",
            className="text-muted small mb-3",
        ),
        html.Div([
            dbc.Card(
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col(html.Span(p["product_name"], className="fw-semibold"), width=6),
                        dbc.Col(html.Small(p["created_at"], className="text-muted"), width=3),
                        dbc.Col([
                            dbc.Button(
                                "Approve",
                                id={"type": "approve-product", "index": p["product_name"]},
                                color="success",
                                size="sm",
                                className="me-2",
                            ),
                            dbc.Button(
                                "Dismiss",
                                id={"type": "dismiss-product", "index": p["product_name"]},
                                color="secondary",
                                size="sm",
                                outline=True,
                            ),
                        ], width=3, className="text-end"),
                    ], align="center"),
                ]),
                className="mb-2",
            )
            for p in pending
        ]),
    ], className="mb-4")


@callback(
    Output("pending-products-section", "children", allow_duplicate=True),
    Input({"type": "approve-product", "index": dash.ALL}, "n_clicks"),
    Input({"type": "dismiss-product", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_pending_action(approve_clicks, dismiss_clicks):
    """Handle approve or dismiss button clicks."""
    ctx = dash.callback_context
    if not ctx.triggered or not any(approve_clicks + dismiss_clicks):
        return dash.no_update

    triggered = ctx.triggered[0]["prop_id"]
    import json as _json
    btn = _json.loads(triggered.split(".")[0])
    product_name = btn["index"]
    action = btn["type"]

    if action == "approve-product":
        approve_pending_product(product_name)
    elif action == "dismiss-product":
        dismiss_pending_product(product_name)

    # Re-render the section
    return render_pending_products(None)
