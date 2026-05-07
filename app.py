"""
app.py — Hermes AI Procurement
Run:  python app.py  →  http://localhost:8050
"""

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
)

app.title = "Hermes AI Procurement"

# ── Sidebar (shown only when NOT on the landing page) ─────────────────────────

sidebar = dbc.Nav(
    [
        html.Div(
            [
                html.H6("HERMES", className="mb-0 fw-bold", style={"letterSpacing": "1px"}),
                html.Small("AI Procurement", className="text-muted"),
            ],
            className="px-3 py-3 border-bottom",
        ),
        dbc.NavLink("Home",           href="/home",           active="exact", className="px-3 py-2"),
        dbc.NavLink("Chat",           href="/chat",           active="exact", className="px-3 py-2"),
        dbc.NavLink("Inventory",      href="/inventory",      active="exact", className="px-3 py-2"),
        dbc.NavLink("Upload Invoice", href="/upload",         active="exact", className="px-3 py-2"),
        dbc.NavLink("Agent Control",  href="/agent-control",  active="exact", className="px-3 py-2"),
        dbc.NavLink("About",          href="/about",          active="exact", className="px-3 py-2"),
        html.Hr(className="my-2"),
        dbc.NavLink(
            [html.Small("← Back to landing", className="text-muted")],
            href="/",
            className="px-3 py-1",
        ),
    ],
    id="app-sidebar",
    vertical=True,
    pills=True,
    className="bg-light border-end",
    style={"width": "220px", "minHeight": "100vh", "position": "fixed", "display": "none"},
)

# ── Root layout ───────────────────────────────────────────────────────────────

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    sidebar,
    html.Div(dash.page_container, id="page-content"),
])

# ── Show/hide sidebar based on current route ──────────────────────────────────

@app.callback(
    Output("app-sidebar", "style"),
    Output("page-content", "style"),
    Input("url", "pathname"),
)
def toggle_sidebar(pathname):
    # Landing page: no sidebar, full-width, no padding
    if pathname == "/" or pathname is None:
        sidebar_style = {
            "width": "220px", "minHeight": "100vh",
            "position": "fixed", "display": "none",
        }
        content_style = {"marginLeft": "0", "padding": "0"}
    else:
        # All other pages: show sidebar
        sidebar_style = {
            "width": "220px", "minHeight": "100vh",
            "position": "fixed", "display": "block",
        }
        content_style = {"marginLeft": "220px", "padding": "2rem"}

    return sidebar_style, content_style


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)

