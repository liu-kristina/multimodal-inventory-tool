import dash
from dash import html, Input, Output, callback
import dash_bootstrap_components as dbc
from database import get_connection, _execute, _use_postgres

dash.register_page(__name__, path="/home", title="Home", name="Home", order=0)


# ── Live stat queries ─────────────────────────────────────────────────────────

def fetch_stats():
    """Pull all homepage stats from the database in one connection."""
    try:
        conn = get_connection()

        # Total invoices indexed
        cur = _execute(conn, "SELECT COUNT(*) as n FROM invoices")
        row = cur.fetchone()
        invoice_count = row["n"] if _use_postgres else row[0]

        # Products tracked (rows in stock table)
        cur = _execute(conn, "SELECT COUNT(*) as n FROM stock")
        row = cur.fetchone()
        product_count = row["n"] if _use_postgres else row[0]

        # Low stock alerts (quantity below reorder threshold)
        cur = _execute(conn, "SELECT COUNT(*) as n FROM stock WHERE quantity_kg < reorder_at")
        row = cur.fetchone()
        low_stock_count = row["n"] if _use_postgres else row[0]

        # Active customers (distinct customer counterparties)
        cur = _execute(conn, """
            SELECT COUNT(DISTINCT counterparty_name) as n
            FROM invoices
            WHERE invoice_type = 'customer'
        """)
        row = cur.fetchone()
        customer_count = row["n"] if _use_postgres else row[0]

        conn.close()
        return invoice_count, product_count, low_stock_count, customer_count
    except Exception:
        return "—", "—", "—", "—"


# ── Stat card ─────────────────────────────────────────────────────────────────

def stat_card(label, value_id, sub, color=None):
    value_style = {'fontSize': '24px', 'fontWeight': '500', 'margin': '0'}
    if color:
        value_style['color'] = color
    return dbc.Card(
        dbc.CardBody([
            html.P(label, className="text-muted mb-1", style={'fontSize': '12px'}),
            html.P("—", id=value_id, style=value_style),
            html.P(sub, className="text-muted mb-0", style={'fontSize': '11px'}),
        ]),
        className="bg-light border-0",
    )


# ── Feature card ──────────────────────────────────────────────────────────────

def feature_card(icon, title, description, link_label, href):
    return dbc.Card(
        dbc.CardBody([
            html.Div(icon, className="mb-3", style={'fontSize': '22px'}),
            html.H6(title, className="fw-semibold mb-2"),
            html.P(description, className="text-muted mb-3", style={'fontSize': '13px', 'lineHeight': '1.55'}),
            html.A(link_label, href=href, className="text-primary", style={'fontSize': '13px'}),
        ]),
        className="h-100 shadow-sm",
    )


# ── Layout ────────────────────────────────────────────────────────────────────

layout = dbc.Container([

    # Invisible trigger for on-load callback
    html.Div(id="home-load-trigger"),

    # Hero
    dbc.Row([
        dbc.Col([
            dbc.Badge(
                "Demo · For illustrative purposes only",
                color="success",
                pill=True,
                className="mb-3",
                style={'fontSize': '11px'},
            ),
            html.H2("California Nutraceuticals Invoice Intelligence", className="fw-semibold mb-2"),
            html.P(
                "An AI-powered procurement tool for raw material distributors. "
                "Ask questions about suppliers, track inventory, and process invoices — all from one place.",
                className="text-muted mb-0",
                style={'maxWidth': '600px', 'fontSize': '15px', 'lineHeight': '1.65'},
            ),
        ])
    ], className="mb-4"),

    html.Hr(className="my-4"),

    # Stats row — values populated by callback on load
    dbc.Row([
        dbc.Col(stat_card("Invoices indexed",  "stat-invoices",  "supplier + customer"), md=3),
        dbc.Col(stat_card("Products tracked",  "stat-products",  "across all suppliers"), md=3),
        dbc.Col(stat_card("Low stock alerts",  "stat-low-stock", "below reorder threshold", color="var(--bs-warning)"), md=3),
        dbc.Col(stat_card("Active customers",  "stat-customers", "in invoice history"), md=3),
    ], className="g-3 mb-4"),

    html.Hr(className="my-4"),

    # Section header
    html.P(
        "WHAT THIS APP DOES",
        className="text-muted mb-3",
        style={'fontSize': '11px', 'letterSpacing': '0.07em', 'fontWeight': '600'},
    ),

    # Feature cards
    dbc.Row([
        dbc.Col(feature_card(
            icon="💬",
            title="Invoice Chat",
            description=(
                "Ask natural language questions across your entire invoice history. "
                "Find suppliers, compare pricing, and check lead times instantly."
            ),
            link_label="Open Invoice Chat →",
            href="/chat",
        ), md=3),
        dbc.Col(feature_card(
            icon="📦",
            title="Inventory Dashboard",
            description=(
                "Live stock levels with color-coded alerts. See what's running low, "
                "set reorder thresholds, and update quantities manually."
            ),
            link_label="View Inventory →",
            href="/inventory",
        ), md=3),
        dbc.Col(feature_card(
            icon="🤖",
            title="Agents",
            description=(
                "Two AI agents working together: the Invoice Agent monitors Gmail "
                "for incoming invoice PDFs and indexes them automatically. "
                "The Procurement Agent detects low stock, drafts supplier reorder "
                "emails for approval, and processes supplier replies."
            ),
            link_label="Open Agent Control →",
            href="/agent-control",
        ), md=3),
        dbc.Col(feature_card(
            icon="👤",
            title="About & Team",
            description=(
                "Learn about the business context behind this tool, the team that "
                "built it, and the technology powering it."
            ),
            link_label="Learn More →",
            href="/about",
        ), md=3),
    ], className="g-3 mb-4"),

    html.Hr(className="my-4"),

    # Disclaimer
    html.P(
        "All companies and data shown are fictional and created for demonstration purposes only.",
        className="text-muted",
        style={'fontSize': '12px'},
    ),

], fluid=True, className="py-4 px-4")


# ── Callback — populate stats on page load ────────────────────────────────────

@callback(
    Output("stat-invoices",  "children"),
    Output("stat-products",  "children"),
    Output("stat-low-stock", "children"),
    Output("stat-customers", "children"),
    Input("home-load-trigger", "children"),
)
def update_stats(_):
    invoices, products, low_stock, customers = fetch_stats()
    return invoices, products, low_stock, customers