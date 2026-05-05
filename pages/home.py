import dash
from dash import html
import dash_bootstrap_components as dbc

dash.register_page(__name__, path="/", title="Home", name="Home", order=0)


# ── Stat card ────────────────────────────────────────────────────────────────

def stat_card(label, value, sub, color=None):
    value_style = {'fontSize': '24px', 'fontWeight': '500', 'margin': '0'}
    if color:
        value_style['color'] = color
    return dbc.Card(
        dbc.CardBody([
            html.P(label, className="text-muted mb-1", style={'fontSize': '12px'}),
            html.P(value, style=value_style),
            html.P(sub, className="text-muted mb-0", style={'fontSize': '11px'}),
        ]),
        className="bg-light border-0",
    )


# ── Feature card ─────────────────────────────────────────────────────────────

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


# ── Layout ───────────────────────────────────────────────────────────────────

layout = dbc.Container([

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

    # Stats row
    dbc.Row([
        dbc.Col(stat_card("Invoices indexed", "247", "supplier + customer"), md=3),
        dbc.Col(stat_card("Products tracked", "15", "across all suppliers"), md=3),
        dbc.Col(stat_card("Low stock alerts", "3", "below reorder threshold", color="var(--bs-warning)"), md=3),
        dbc.Col(stat_card("Active customers", "12", "in invoice history"), md=3),
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
            title="Invoice Agent",
            description=(
                "Upload a supplier or customer PDF. The agent extracts entities, "
                "stores the data, and can draft a reorder email on your behalf."
            ),
            link_label="Upload an Invoice →",
            href="/invoice-agent",
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
