"""
pages/inventory.py - Inventory dashboard page
"""

import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import json
import os

dash.register_page(__name__, path="/inventory", title="Inventory")

# ── Dummy inventory data ───────────────────────────────────────────────────────
# Replace this with your SQLite database in week 3

INVENTORY = [
    {"product": "Collagen Powder",            "stock": 450,  "reorder_at": 100, "unit": "kg"},
    {"product": "Shark Cartilage Powder",     "stock": 80,   "reorder_at": 150, "unit": "kg"},
    {"product": "Fish Collagen Peptides",     "stock": 210,  "reorder_at": 100, "unit": "kg"},
    {"product": "Hydrolyzed Marine Collagen", "stock": 55,   "reorder_at": 100, "unit": "kg"},
    {"product": "Bovine Gelatin Type A",      "stock": 320,  "reorder_at": 80,  "unit": "kg"},
    {"product": "Bovine Cartilage Extract",   "stock": 40,   "reorder_at": 80,  "unit": "kg"},
    {"product": "Plant Extract - Turmeric",   "stock": 180,  "reorder_at": 60,  "unit": "kg"},
    {"product": "Plant Extract - Ashwagandha","stock": 95,   "reorder_at": 60,  "unit": "kg"},
    {"product": "Plant Extract - Elderberry", "stock": 30,   "reorder_at": 60,  "unit": "kg"},
    {"product": "Hyaluronic Acid Powder",     "stock": 120,  "reorder_at": 50,  "unit": "kg"},
    {"product": "Chondroitin Sulfate",        "stock": 200,  "reorder_at": 80,  "unit": "kg"},
    {"product": "Glucosamine HCl",            "stock": 170,  "reorder_at": 80,  "unit": "kg"},
]


def get_low_stock():
    return [p for p in INVENTORY if p["stock"] <= p["reorder_at"]]


def make_chart():
    """Build stock level bar chart colored by status."""
    products = [p["product"].replace("Plant Extract - ", "") for p in INVENTORY]
    stocks   = [p["stock"] for p in INVENTORY]
    colors   = [
        "#dc3545" if p["stock"] <= p["reorder_at"]
        else "#fd7e14" if p["stock"] <= p["reorder_at"] * 1.5
        else "#198754"
        for p in INVENTORY
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
    low_stock   = get_low_stock()
    total       = len(INVENTORY)
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
                        for p in INVENTORY
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
