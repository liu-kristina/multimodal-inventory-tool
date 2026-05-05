import dash
from dash import html
import dash_bootstrap_components as dbc

# Register page
dash.register_page(__name__, path="/about", title="About us", name="About Us", order=5)


# ── App introduction section ────────────────────────────────────────────────────

intro_section = dbc.Card(
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.H5("What this app does", className="fw-bold mb-3"),
                html.P(
                    "This is an AI-powered procurement tool "
                    "built for raw material distributors. It automates the extraction, storage, and "
                    "analysis of supplier and customer invoices — turning unstructured PDFs into "
                    "searchable, actionable data.",
                    className="text-muted mb-3",
                    style={'fontSize': '14px', 'lineHeight': '1.7'}
                ),
                dbc.Row([
                    dbc.Col([
                        html.Div([
                            html.Span("💬", style={'fontSize': '20px'}),
                            html.Div([
                                html.Strong("Invoice Chat", className="d-block", style={'fontSize': '14px'}),
                                html.Span("Ask questions about suppliers, pricing, and lead times using RAG over your invoice history.",
                                          className="text-muted", style={'fontSize': '13px'}),
                            ], className="ms-2")
                        ], className="d-flex align-items-start mb-3"),
                        html.Div([
                            html.Span("📦", style={'fontSize': '20px'}),
                            html.Div([
                                html.Strong("Inventory Dashboard", className="d-block", style={'fontSize': '14px'}),
                                html.Span("Live stock levels with low-stock alerts and reorder threshold tracking.",
                                          className="text-muted", style={'fontSize': '13px'}),
                            ], className="ms-2")
                        ], className="d-flex align-items-start mb-3"),
                    ], md=6),
                    dbc.Col([
                        html.Div([
                            html.Span("🤖", style={'fontSize': '20px'}),
                            html.Div([
                                html.Strong("Invoice Agent", className="d-block", style={'fontSize': '14px'}),
                                html.Span("An AI agent that processes PDFs, extracts entities, and drafts supplier reorder emails.",
                                          className="text-muted", style={'fontSize': '13px'}),
                            ], className="ms-2")
                        ], className="d-flex align-items-start mb-3"),
                        html.Div([
                            html.Span("📧", style={'fontSize': '20px'}),
                            html.Div([
                                html.Strong("Procurement Agent", className="d-block", style={'fontSize': '14px'}),
                                html.Span("Monitors low stock, drafts supplier reorder emails, manages approvals, and tracks procurement replies.",
                                          className="text-muted", style={'fontSize': '13px'}),
                            ], className="ms-2")
                        ], className="d-flex align-items-start mb-3"),
                        html.Div([
                            html.Span("🔍", style={'fontSize': '20px'}),
                            html.Div([
                                html.Strong("Smart Search", className="d-block", style={'fontSize': '14px'}),
                                html.Span("ChromaDB vector search over invoice documents for fast, context-aware retrieval.",
                                          className="text-muted", style={'fontSize': '13px'}),
                            ], className="ms-2")
                        ], className="d-flex align-items-start mb-3"),
                    ], md=6),
                ]),
                html.P(
                    "PPowered by Claude (Anthropic) for AI reasoning, OpenAI text-embedding-3-small for semantic search, ChromaDB as the vector store, Dash for the interface, and SQLite for structured data.",
                    className="text-muted mb-0",
                    style={'fontSize': '13px', 'fontStyle': 'italic'}
                ),
            ])
        ])
    ]),
    className="shadow-sm mb-4"
)

# ── Team member card ────────────────────────────────────────────────────────────

def create_team_member_card(name, image, qr_code, github_handle):
    return dbc.Col(
        dbc.Card([
            dbc.CardBody([
                html.Img(
                    src=dash.get_asset_url(image),
                    style={
                        'width': '160px',
                        'height': '160px',
                        'objectFit': 'cover',
                        'borderRadius': '50%',
                        'marginBottom': '12px',
                    }
                ),
                html.H5(name, className="mb-1 fw-bold"),
                html.P(
                    github_handle,
                    className="text-muted mb-3",
                    style={'fontSize': '13px'}
                ),
                html.Hr(className="my-2"),
                html.P("LinkedIn QR Code", className="text-muted mb-1", style={'fontSize': '12px'}),
                html.Img(
                    src=dash.get_asset_url(qr_code),
                    style={'width': '90px', 'height': '90px'}
                ),
            ], className="text-center py-4")
        ], className="h-100 shadow-sm border"),
        md=4,
        className="mb-4"
    )


# ── Team section ────────────────────────────────────────────────────────────────

team_section = dbc.Row([
    create_team_member_card('Ying Huang',     'Ying.png',     'YingQR.png', 'github.com/yh51'),
    create_team_member_card('Kristina Liang', 'Kristina.png', 'KristinaQR.png', 'github.com/liu-kristina'),
    create_team_member_card('Moxi Liang',     'Moxi.png',     'MoxiQR.png',     'github.com/moxixmx533-ux'),
], className="mt-3")


# ── Description card ────────────────────────────────────────────────────────────

description_card = dbc.Card(
    dbc.CardBody([
        html.P(
            "This application was developed as the capstone project for the NLP and GenAI program from Easy Learning.",
            className="text-center mb-3",
            style={'fontSize': '15px', 'color': '#004ad8'}
        ),
        html.Img(
            src=dash.get_asset_url('easylearningai.png'),
            style={'width': '220px', 'height': 'auto', 'display': 'block', 'margin': '0 auto'}
        ),
    ]),
    className="shadow-sm border-0 mx-auto mt-2 mb-4",
    style={'maxWidth': '560px', 'backgroundColor': '#e5e5e5', 'borderRadius': '10px'}
)



# ── Target customer section ──────────────────────────────────────────────────────

target_customer_section = dbc.Card(
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.H5("Raw material distributor", className="fw-bold mb-3"),
                html.P(
                    "Raw material distributors in the nutraceutical industry operate on speed and precision. "
                    "High invoice volumes, fragmented supplier and customer data, and disconnected inventory "
                    "systems create daily friction that directly threatens margins and fulfillment.",
                    className="text-muted mb-3",
                    style={'fontSize': '14px', 'lineHeight': '1.7'}
                ),
                html.P(
                    "The core challenges are threefold: matching the right suppliers to customer demand fast "
                    "enough to close sales, maintaining a single source of truth for inventory across multiple "
                    "systems, and managing the administrative burden of heavy paperwork without sacrificing accuracy.",
                    className="text-muted mb-3",
                    style={'fontSize': '14px', 'lineHeight': '1.7'}
                ),
                html.P(
                    "When these break down, so does profitability.",
                    className="fw-semibold mb-4",
                    style={'fontSize': '14px'}
                ),
                html.Hr(className="my-3"),
                html.H6("Example customer: California Nutraceuticals *", className="fw-bold mb-2"),
                html.P(
                    "A family-owned nutraceutical ingredients distributor with 5–20 employees, acting as the "
                    "exclusive North American importer for one or more overseas manufacturers (e.g., a collagen "
                    "and cartilage supplier in China). They sell to supplement brands, private label manufacturers, "
                    "and health food distributors across the US.",
                    className="text-muted mb-3",
                    style={'fontSize': '14px', 'lineHeight': '1.7'}
                ),
                html.P(
                    "* All companies referenced in this application, including California Nutraceuticals and its "
                    "suppliers and customers, are fictional and created for demo purposes only.",
                    className="text-muted fst-italic mb-0",
                    style={'fontSize': '12px'}
                ),
            ], md=7),
            dbc.Col([
                html.Img(
                    src="https://images.everydayhealth.com/images/womens-health/supplements-women-722x406.jpg",
                    style={
                        'width': '100%',
                        'borderRadius': '8px',
                        'objectFit': 'cover',
                        'maxHeight': '280px',
                    }
                ),
                html.P(
                    "Photo credit: everydayhealth.com",
                    className="text-muted mt-1 mb-0",
                    style={'fontSize': '11px'}
                ),
            ], md=5),
        ]),
    ]),
    className="shadow-sm mb-4"
)


# ── Layout ──────────────────────────────────────────────────────────────────────

layout = html.Div([
    html.H4("Business context", className="mb-3 fw-bold"),
    html.H5("Target customer", className="mb-3 text-muted fw-normal"),
    target_customer_section,
    html.H4("About the app", className="mb-1 fw-bold mt-4"),
    intro_section,
    html.H4("The team", className="mb-1 fw-bold mt-2"),
    html.P("Connect with us on LinkedIn", className="text-muted mb-4"),
    team_section,
    description_card,
])