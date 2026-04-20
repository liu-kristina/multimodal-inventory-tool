"""
California Nutraceuticals Inc. - Invoice Intelligence App
Main entry point for the Dash application.

Run:
    python app.py

Then open: http://localhost:8050
"""

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc
from database import init_db, seed_initial_inventory, seed_products


init_db()
seed_initial_inventory()
seed_products()

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
)

app.title = "California Nutraceuticals"

sidebar = dbc.Nav(
    [
        html.Div(
            [
                html.H5("California", className="mb-0 fw-bold"),
                html.H5("Nutraceuticals", className="mb-0 fw-bold"),
                html.Small("Invoice Intelligence", className="text-muted"),
            ],
            className="px-3 py-4 border-bottom",
        ),
        dbc.NavLink(
            "Chat",
            href="/",
            active="exact",
            className="px-3 py-2",
        ),
        dbc.NavLink(
            "Inventory",
            href="/inventory",
            active="exact",
            className="px-3 py-2",
        ),
        dbc.NavLink(
            "Agent Control",  
            href="/agent-control",  
            active="exact", 
            className="px-3 py-2"),

    ],
    vertical=True,
    pills=True,
    className="bg-light border-end",
    style={"width": "220px", "minHeight": "100vh", "position": "fixed"},
)

app.layout = html.Div(
    [
        sidebar,
        html.Div(
            dash.page_container,
            style={"marginLeft": "220px", "padding": "2rem"},
        ),
    ]
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=8050)
