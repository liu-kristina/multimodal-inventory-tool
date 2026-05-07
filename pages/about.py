import dash
from dash import html
import dash_bootstrap_components as dbc

dash.register_page(__name__, path="/about", title="About", name="About", order=5)


# ── Tech stack line ────────────────────────────────────────────────────────────

tech_stack_line = html.P([
    "Powered by ",
    html.A("Claude (Anthropic)", href="https://www.anthropic.com", target="_blank"),
    " for AI reasoning · local embeddings (sentence-transformers) for semantic search · "
    "ChromaDB as the vector store · Dash for the interface · SQL for structured data.",
], className="text-muted mb-4", style={'fontSize': '13px', 'fontStyle': 'italic'})


# ── Privacy notice ─────────────────────────────────────────────────────────────

privacy_notice = dbc.Alert([
    html.Strong("Privacy notice: "),
    "Invoice data submitted through this app is processed by the ",
    html.A("Claude API (Anthropic)", href="https://www.anthropic.com/privacy",
           target="_blank", className="alert-link"),
    ". Semantic search runs on a local embedding model — invoice text is never "
    "sent to a third-party embedding service.",
], color="info", className="mb-4", style={'fontSize': '13px'})


# ── Business context card ──────────────────────────────────────────────────────

business_context_card = dbc.Card(
    dbc.CardBody([
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
        html.H6("California Nutraceuticals", className="fw-bold mb-2"),
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
    ]),
    className="shadow-sm mb-4"
)


# ── Easy Learning card ─────────────────────────────────────────────────────────

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


# ── Layout ─────────────────────────────────────────────────────────────────────

layout = html.Div([
    html.H4("Business context", className="mb-3 fw-bold"),
    business_context_card,
    privacy_notice,
    tech_stack_line,
    description_card,
])
