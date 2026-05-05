import dash
from dash import html
import dash_bootstrap_components as dbc

# Register page
dash.register_page(__name__, path="/about", title="About us", name="About Us", order=5)


# ── App introduction section ────────────────────────────────────────────────────

intro_section = dbc.Card(
    dbc.CardBody([
        html.P(
            "Powered by Claude (Anthropic) for AI reasoning, OpenAI text-embedding-3-small for semantic search, ChromaDB as the vector store, Dash for the interface, and SQLite for structured data.",
            className="text-muted mb-3",
            style={'fontSize': '13px', 'fontStyle': 'italic'}
        ),
        dbc.Alert([
            html.Strong("Privacy notice: "),
            "Invoice data entered in this app is processed by third-party APIs. Please review their privacy policies: ",
            html.A("Anthropic", href="https://www.anthropic.com/privacy", target="_blank", className="alert-link"),
            " · ",
            html.A("OpenAI", href="https://openai.com/policies/privacy-policy", target="_blank", className="alert-link"),
            ". Do not upload confidential documents in this demo environment.",
        ], color="warning", className="mb-0", style={'fontSize': '13px'}),
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



# ── Layout ──────────────────────────────────────────────────────────────────────

layout = html.Div([
    html.H4("About the app", className="mb-1 fw-bold"),
     intro_section,
    html.H4("The team", className="mb-1 fw-bold mt-2"),
    html.P("Connect with us on LinkedIn", className="text-muted mb-4"),
    team_section,
    description_card,
])
