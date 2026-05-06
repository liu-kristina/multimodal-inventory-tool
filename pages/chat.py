"""
pages/chat.py - Invoice chatbot page
"""

import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash.register_page(__name__, path="/chat", title="Chat")

# ── Suggested questions ────────────────────────────────────────────────────────

SUGGESTIONS = [
    "Who supplies shark cartilage powder?",
    "What is the lead time from Jiaxing Natural Products?",
    "Which customers buy collagen from us?",
    "What is the best price we have paid for collagen powder?",
    "Draft a reorder email for shark cartilage powder",
]

# ── Layout ─────────────────────────────────────────────────────────────────────

layout = html.Div(
    [
        # Page header
        html.H4("Invoice Assistant", className="mb-1 fw-bold"),
        html.P(
            "Ask questions about suppliers, customers, pricing and lead times.",
            className="text-muted mb-4",
        ),

        # Suggested questions
        html.Div(
            [
                html.Small("Try asking:", className="text-muted d-block mb-2"),
                html.Div(
                    [
                        dbc.Button(
                            q,
                            id={"type": "suggestion", "index": i},
                            color="light",
                            size="sm",
                            className="me-2 mb-2 border",
                        )
                        for i, q in enumerate(SUGGESTIONS)
                    ]
                ),
            ],
            className="mb-4",
        ),

        # Chat history
        html.Div(
            id="chat-history",
            style={
                "height": "420px",
                "overflowY": "auto",
                "border": "1px solid #dee2e6",
                "borderRadius": "8px",
                "padding": "1rem",
                "backgroundColor": "#f8f9fa",
                "marginBottom": "1rem",
            },
            children=[
                html.Div(
                    "Hello! Ask me anything about your invoice history — "
                    "suppliers, customers, pricing, lead times, or I can "
                    "draft reorder emails for you.",
                    className="text-muted fst-italic",
                )
            ],
        ),

        # Input row
        dbc.InputGroup(
            [
                dbc.Input(
                    id="chat-input",
                    placeholder="Ask a question about your invoices...",
                    type="text",
                    debounce=False,
                    n_submit=0,
                ),
                dbc.Button(
                    "Ask",
                    id="chat-submit",
                    color="primary",
                    n_clicks=0,
                ),
            ]
        ),

        # Loading indicator
        dcc.Loading(
            id="chat-loading",
            type="dot",
            children=html.Div(id="chat-loading-output"),
        ),

        # Store for chat history
        dcc.Store(id="chat-store", data=[]),
    ],
    style={"maxWidth": "800px"},
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_message(role: str, text: str):
    """Render a single chat message bubble."""
    is_user = role == "user"
    return html.Div(
        [
            html.Small(
                "You" if is_user else "Assistant",
                className="text-muted d-block mb-1",
                style={"textAlign": "right" if is_user else "left"},
            ),
            html.Div(
                text,
                style={
                    "backgroundColor": "#0d6efd" if is_user else "#ffffff",
                    "color": "white" if is_user else "#212529",
                    "borderRadius": "12px",
                    "padding": "10px 14px",
                    "display": "inline-block",
                    "maxWidth": "85%",
                    "border": "none" if is_user else "1px solid #dee2e6",
                    "whiteSpace": "pre-wrap",
                },
            ),
        ],
        style={
            "display": "flex",
            "flexDirection": "column",
            "alignItems": "flex-end" if is_user else "flex-start",
            "marginBottom": "12px",
        },
    )


def render_history(messages: list):
    """Render full chat history from stored messages."""
    if not messages:
        return html.Div(
            "Hello! Ask me anything about your invoice history.",
            className="text-muted fst-italic",
        )
    return [make_message(m["role"], m["text"]) for m in messages]


# ── Callbacks ──────────────────────────────────────────────────────────────────

@callback(
    Output("chat-input", "value", allow_duplicate=True),
    Input({"type": "suggestion", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def fill_suggestion(n_clicks):
    """Fill input when a suggestion button is clicked."""
    ctx = dash.callback_context
    if not ctx.triggered or not any(n_clicks):
        return dash.no_update
    triggered = ctx.triggered[0]["prop_id"]
    import json
    idx = json.loads(triggered.split(".")[0])["index"]
    return SUGGESTIONS[idx]


@callback(
    Output("chat-store",   "data"),
    Output("chat-history", "children"),
    Output("chat-input",   "value"),
    Input("chat-submit",   "n_clicks"),
    Input("chat-input",    "n_submit"),
    State("chat-input",    "value"),
    State("chat-store",    "data"),
    prevent_initial_call=True,
)
def handle_message(n_clicks, n_submit, query, history):
    """Handle user message — call RAG pipeline and update chat."""
    if not query or not query.strip():
        return history, render_history(history), ""

    query = query.strip()

    # Add user message
    history = history + [{"role": "user", "text": query}]

    # Call RAG pipeline
    try:
        from pipeline.rag_query import ask
        answer = ask(query)
    except Exception as e:
        answer = f"Sorry, something went wrong: {str(e)}"

    # Add assistant response
    history = history + [{"role": "assistant", "text": answer}]

    return history, render_history(history), ""