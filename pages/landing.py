"""
pages/landing.py  —  Hermes AI Procurement public landing page
Registers at path="/" and replaces the old home page as the entry point.

The existing app pages (chat, inventory, agent-control, about, upload) are
accessible after clicking "View Demo" or "Customer Login".
"""

import dash
from dash import html, dcc
import dash_bootstrap_components as dbc

dash.register_page(__name__, path="/", title="Hermes AI Procurement")

# ── Logo (embedded from uploaded asset path) ──────────────────────────────────
LOGO_PATH = "/assets/hermes_logo.png"   # put the logo PNG in your /assets/ folder

# ── Layout ────────────────────────────────────────────────────────────────────

layout = html.Div([

    html.Div(className="hermes-landing", children=[

        # ── NAV ──────────────────────────────────────────────────────────────
        html.Nav(className="h-nav", children=[
            html.A(className="h-nav-brand", href="/", children=[
                html.Img(src=LOGO_PATH, alt="Hermes logo", style={"height": "40px"}),
                html.Div(className="h-nav-brand-text", children=[
                    html.Div("HERMES", className="name"),
                    html.Div("AI Procurement", className="sub"),
                ]),
            ]),
            html.Ul(className="h-nav-links", children=[
                html.Li(html.A("About", href="#about")),
                html.Li(html.A("Platform", href="#platform")),
                html.Li(html.A("Demo", href="#demo")),
            ]),
            html.Div(className="h-nav-cta", children=[
                html.A("Customer Login", href="#login", className="btn-h-outline"),
                html.A("Request Access", href="#demo",  className="btn-h-primary"),
            ]),
        ]),

        # ── HERO ─────────────────────────────────────────────────────────────
        html.Section(className="h-hero", children=[
            html.Div(className="h-hero-grid"),
            html.Div(className="h-hero-glow"),

            html.Div(className="h-hero-badge", children=[
                html.Div(className="h-pulse"),
                "AI-Powered Procurement Intelligence",
            ]),

            html.H1(children=[
                "Smarter Procurement, ", html.Br(),
                html.Em("Powered by AI"),
            ]),

            html.P(
                "Hermes AI Procurement automates supplier sourcing, invoice processing, "
                "and inventory intelligence — so your team spends less time on admin "
                "and more time making great decisions.",
                className="h-hero-sub",
            ),

            html.Div(className="h-hero-actions", children=[
                html.A("View Live Demo", href="/home", className="btn-h-hero"),
                html.A("Learn More", href="#about",   className="btn-h-ghost"),
            ]),

            html.Div(className="h-hero-stats", children=[
                html.Div(children=[
                    html.Div("90%", className="h-stat-num"),
                    html.Div("Faster invoice processing", className="h-stat-lbl"),
                ]),
                html.Div(className="h-stat-div"),
                html.Div(children=[
                    html.Div("100%", className="h-stat-num"),
                    html.Div("Automated data extraction", className="h-stat-lbl"),
                ]),
                html.Div(className="h-stat-div"),
                html.Div(children=[
                    html.Div("Real-time", className="h-stat-num"),
                    html.Div("Inventory & lead-time visibility", className="h-stat-lbl"),
                ]),
            ]),
        ]),

        # ── ABOUT ─────────────────────────────────────────────────────────────
        html.Section(id="about", className="h-about", children=[
            html.Div(className="h-about-grid", children=[

                html.Div(children=[
                    html.P("About us", className="h-section-label"),
                    html.H2("Procurement on autopilot", className="h-section-title"),
                    html.P(
                        "Procurement teams spend too much time on manual work — chasing invoices, "
                        "copying data between systems, and drafting the same supplier emails over and over. "
                        "Hermes eliminates that busywork so your team can focus on decisions, not admin.",
                        className="h-section-body",
                        style={"marginBottom": "16px"},
                    ),
                    html.P(
                        "From the moment an invoice lands to the moment a reorder is sent, "
                        "Hermes automates the entire flow: extracting data from PDFs, keeping inventory "
                        "current, flagging what needs attention, and sending supplier emails for approval — "
                        "approve or reject directly from your inbox or Slack, without touching the platform.",
                        className="h-section-body",
                    ),
                ]),

                html.Div(className="h-cards", children=[
                    html.Div(className="h-card", children=[
                        html.Div("🧠", className="h-card-icon"),
                        html.H4("AI-Native"),
                        html.P("Claude AI reasons over your invoices to answer complex sourcing questions instantly."),
                    ]),
                    html.Div(className="h-card", children=[
                        html.Div("📄", className="h-card-icon"),
                        html.H4("PDF Intelligence"),
                        html.P("Supplier and customer invoices are automatically extracted, indexed, and made searchable."),
                    ]),
                    html.Div(className="h-card", children=[
                        html.Div("📦", className="h-card-icon"),
                        html.H4("Live Inventory"),
                        html.P("Real-time stock levels with low-stock alerts and configurable reorder thresholds."),
                    ]),
                    html.Div(className="h-card", children=[
                        html.Div("✉️", className="h-card-icon"),
                        html.H4("Email & Slack Native"),
                        html.P("The procurement agent sends reorder emails and Slack notifications — approve or reject without ever opening the app."),
                    ]),
                ]),
            ]),
        ]),

        # ── PLATFORM / TOOL ───────────────────────────────────────────────────
        html.Section(id="platform", className="h-tool", children=[
            html.Div(className="h-tool-inner", children=[

                html.Div(className="h-tool-header", children=[
                    html.P("The platform", className="h-section-label"),
                    html.H2("Everything your procurement team needs", className="h-section-title"),
                    html.P(
                        "One connected workspace — from invoice upload to supplier Q&A to "
                        "automated reorder drafts.",
                        className="h-section-body",
                        style={"maxWidth": "100%"},
                    ),
                ]),

                html.Div(className="h-steps", children=[
                    html.Div(className="h-step", children=[
                        html.Div("01", className="h-step-bg"),
                        html.Div("💬", className="h-step-icon"),
                        html.H3("Invoice Chat"),
                        html.P("Ask questions about your supplier history, pricing trends, "
                               "and lead times."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("02", className="h-step-bg"),
                        html.Div("📊", className="h-step-icon"),
                        html.H3("Inventory Dashboard"),
                        html.P("Live stock levels across all products. Color-coded with low-stock alerts."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("03", className="h-step-bg"),
                        html.Div("✉️", className="h-step-icon"),
                        html.H3("Email & Slack Native Agent"),
                        html.P("The agent monitors low stock, drafts supplier reorder emails, and sends "
                               "them directly. Your team approves or rejects from their inbox or from Slack."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("04", className="h-step-bg"),
                        html.Div("🤖", className="h-step-icon"),
                        html.H3("Agent Control"),
                        html.P("Review pending drafts, track approval history, monitor agent activity, "
                               "and run procurement commands from a central dashboard."),
                    ]),
                ]),
            ]),
        ]),

        # ── DEMO CTA ──────────────────────────────────────────────────────────
        html.Section(id="demo", className="h-demo", children=[
            html.Div(className="h-demo-inner", children=[
                html.Div(children=[
                    html.P("See it in action", className="h-section-label"),
                    html.H2("Explore the live demo", className="h-section-title"),
                    html.P(
                        "Walk through a fully functional demo built around California Nutraceuticals — "
                        "a fictional raw material distributor.",
                        className="h-section-body",
                    ),
                    html.P("No login required · For illustrative purposes only", className="h-demo-note"),
                ]),
                html.Div(children=[
                    html.A("Open Demo →", href="/home", className="btn-h-hero",
                           style={"fontSize": "16px", "padding": "15px 36px"}),
                ]),
            ]),
        ]),

        # ── TEAM ──────────────────────────────────────────────────────────────
        html.Section(className="h-about", style={"paddingTop": "80px", "paddingBottom": "80px"}, children=[
            html.Div(style={"maxWidth": "1140px", "margin": "0 auto"}, children=[
                html.P("The team", className="h-section-label", style={"textAlign": "center"}),
                html.H2("Built by", className="h-section-title",
                        style={"textAlign": "center", "maxWidth": "100%", "marginBottom": "48px"}),

                html.Div(style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(auto-fit, minmax(220px, 1fr))",
                    "gap": "24px",
                    "maxWidth": "780px",
                    "margin": "0 auto",
                }, children=[
                    # Ying
                    html.Div(style={
                        "background": "rgba(255,255,255,0.04)",
                        "border": "0.5px solid rgba(255,255,255,0.08)",
                        "borderRadius": "14px", "padding": "32px 24px", "textAlign": "center",
                    }, children=[
                        html.Img(src="/assets/Ying.png", style={
                            "width": "80px", "height": "80px", "borderRadius": "50%",
                            "objectFit": "cover", "marginBottom": "14px",
                            "border": "2px solid rgba(58,171,255,0.3)",
                        }),
                        html.H4("Ying Huang", style={"fontSize": "16px", "marginBottom": "4px"}),
                        html.P("github.com/yh51", style={"fontSize": "12px", "color": "#8ba5c4", "marginBottom": "14px"}),
                        html.Img(src="/assets/YingQR.png", style={"width": "72px", "height": "72px", "opacity": "0.85"}),
                        html.P("LinkedIn", style={"fontSize": "11px", "color": "#8ba5c4", "marginTop": "6px"}),
                    ]),
                    # Kristina
                    html.Div(style={
                        "background": "rgba(255,255,255,0.04)",
                        "border": "0.5px solid rgba(255,255,255,0.08)",
                        "borderRadius": "14px", "padding": "32px 24px", "textAlign": "center",
                    }, children=[
                        html.Img(src="/assets/Kristina.png", style={
                            "width": "80px", "height": "80px", "borderRadius": "50%",
                            "objectFit": "cover", "marginBottom": "14px",
                            "border": "2px solid rgba(58,171,255,0.3)",
                        }),
                        html.H4("Kristina Liang", style={"fontSize": "16px", "marginBottom": "4px"}),
                        html.P("github.com/liu-kristina", style={"fontSize": "12px", "color": "#8ba5c4", "marginBottom": "14px"}),
                        html.Img(src="/assets/KristinaQR.png", style={"width": "72px", "height": "72px", "opacity": "0.85"}),
                        html.P("LinkedIn", style={"fontSize": "11px", "color": "#8ba5c4", "marginTop": "6px"}),
                    ]),
                    # Moxi
                    html.Div(style={
                        "background": "rgba(255,255,255,0.04)",
                        "border": "0.5px solid rgba(255,255,255,0.08)",
                        "borderRadius": "14px", "padding": "32px 24px", "textAlign": "center",
                    }, children=[
                        html.Img(src="/assets/Moxi.png", style={
                            "width": "80px", "height": "80px", "borderRadius": "50%",
                            "objectFit": "cover", "marginBottom": "14px",
                            "border": "2px solid rgba(58,171,255,0.3)",
                        }),
                        html.H4("Moxi Liang", style={"fontSize": "16px", "marginBottom": "4px"}),
                        html.P("github.com/moxixmx533-ux", style={"fontSize": "12px", "color": "#8ba5c4", "marginBottom": "14px"}),
                        html.Img(src="/assets/MoxiQR.png", style={"width": "72px", "height": "72px", "opacity": "0.85"}),
                        html.P("LinkedIn", style={"fontSize": "11px", "color": "#8ba5c4", "marginTop": "6px"}),
                    ]),
                ]),
            ]),
        ]),

        # ── CUSTOMER LOGIN ─────────────────────────────────────────────────────
        html.Section(id="login", className="h-login", children=[
            html.Div(className="h-login-inner", children=[
                html.P("Customer portal", className="h-section-label", style={"textAlign": "center"}),
                html.H2("Sign in to your account", className="h-section-title",
                        style={"textAlign": "center", "maxWidth": "100%"}),
                html.P("Access your company's procurement dashboard.",
                       className="h-section-body",
                       style={"margin": "0 auto", "textAlign": "center", "maxWidth": "100%"}),

                html.Div(className="h-login-card", children=[
                    html.Label("Email address"),
                    dcc.Input(
                        id="login-email",
                        type="email",
                        placeholder="you@yourcompany.com",
                        className="h-input",
                    ),
                    html.Label("Password"),
                    dcc.Input(
                        id="login-password",
                        type="password",
                        placeholder="••••••••",
                        className="h-input",
                    ),
                    html.A("Sign In", href="/home", className="btn-h-login"),
                    html.P("──── or ────", className="h-login-divider"),
                    html.A("Explore Demo (no login)", href="/home",
                           className="btn-h-outline",
                           style={"display": "block", "textAlign": "center", "padding": "10px"}),
                    html.P(
                        "Don't have an account? Contact your Hermes account manager to request access.",
                        className="h-login-note",
                    ),
                ]),
            ]),
        ]),

        # ── FOOTER ────────────────────────────────────────────────────────────
        html.Footer(className="h-footer", children=[
            html.Div(children=[
                html.Div("HERMES", className="h-footer-brand"),
                html.Small("AI Procurement"),
            ], className="h-footer-brand"),
        ]),

    ]),
], style={"margin": "0", "padding": "0"})
