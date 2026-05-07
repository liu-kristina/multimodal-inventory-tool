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

# ── Inline CSS injected via a <style> tag ─────────────────────────────────────

STYLES = """
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap');

body { background: #0d1b2e !important; }

.hermes-landing {
  font-family: 'DM Sans', sans-serif;
  background: #0d1b2e;
  color: #e8f0fa;
  min-height: 100vh;
}

/* ── TOP NAV ─────────────────────────────────────── */
.h-nav {
  position: sticky; top: 0; z-index: 200;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 5vw;
  height: 66px;
  background: rgba(13,27,46,0.95);
  backdrop-filter: blur(14px);
  border-bottom: 0.5px solid rgba(255,255,255,0.07);
}
.h-nav-brand {
  display: flex; align-items: center; gap: 11px;
  text-decoration: none;
}
.h-nav-brand img { height: 38px; width: auto; }
.h-nav-brand-text .name {
  font-family: 'Syne', sans-serif;
  font-size: 17px; font-weight: 700;
  color: #fff; letter-spacing: -0.3px; line-height: 1.1;
}
.h-nav-brand-text .sub {
  font-size: 10px; font-weight: 400;
  color: #3aabff; letter-spacing: 1px; text-transform: uppercase;
}
.h-nav-links {
  display: flex; align-items: center; gap: 4px;
  list-style: none; margin: 0; padding: 0;
}
.h-nav-links a {
  color: #8ba5c4; text-decoration: none;
  font-size: 14px; padding: 6px 14px;
  border-radius: 6px;
  transition: color .18s, background .18s;
}
.h-nav-links a:hover { color: #fff; background: rgba(255,255,255,0.06); }
.h-nav-cta { display: flex; gap: 10px; align-items: center; }

.btn-h-outline {
  background: transparent;
  border: 1px solid rgba(58,171,255,0.45);
  color: #3aabff; padding: 7px 18px;
  border-radius: 6px; font-size: 14px;
  font-family: 'DM Sans', sans-serif;
  cursor: pointer; text-decoration: none;
  transition: background .18s, border-color .18s;
  white-space: nowrap;
}
.btn-h-outline:hover { background: rgba(58,171,255,0.1); border-color: #3aabff; color: #3aabff; }

.btn-h-primary {
  background: #1a6fc4; border: none;
  color: #fff; padding: 7px 20px;
  border-radius: 6px; font-size: 14px;
  font-family: 'DM Sans', sans-serif; font-weight: 500;
  cursor: pointer; text-decoration: none;
  transition: background .18s, transform .12s;
  white-space: nowrap;
}
.btn-h-primary:hover { background: #2f8de8; transform: translateY(-1px); color: #fff; }

/* ── HERO ────────────────────────────────────────── */
.h-hero {
  min-height: 88vh;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  text-align: center;
  padding: 80px 5vw 60px;
  position: relative; overflow: hidden;
}
.h-hero-grid {
  position: absolute; inset: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(58,171,255,0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(58,171,255,0.035) 1px, transparent 1px);
  background-size: 52px 52px;
}
.h-hero-glow {
  position: absolute; top: 10%; left: 50%; transform: translateX(-50%);
  width: 680px; height: 480px; pointer-events: none;
  background: radial-gradient(ellipse, rgba(26,111,196,0.2) 0%, transparent 70%);
}
.h-hero-badge {
  display: inline-flex; align-items: center; gap: 8px;
  background: rgba(46,204,113,0.1);
  border: 1px solid rgba(46,204,113,0.28);
  color: #2ecc71; padding: 5px 14px;
  border-radius: 99px; font-size: 12px; font-weight: 500;
  margin-bottom: 28px; position: relative; z-index: 1;
}
.h-pulse {
  width: 7px; height: 7px;
  background: #2ecc71; border-radius: 50%;
  animation: hpulse 2s infinite;
}
@keyframes hpulse {
  0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(1.5)}
}
.h-hero h1 {
  font-family: 'Syne', sans-serif;
  font-size: clamp(36px, 5vw, 68px);
  font-weight: 800; line-height: 1.07; letter-spacing: -2px;
  color: #fff; max-width: 820px; margin-bottom: 22px;
  position: relative; z-index: 1;
}
.h-hero h1 em {
  font-style: normal;
  background: linear-gradient(130deg, #3aabff, #2f8de8);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.h-hero-sub {
  font-size: clamp(15px, 1.5vw, 19px); font-weight: 300;
  color: #8ba5c4; max-width: 520px; line-height: 1.65;
  margin-bottom: 42px; position: relative; z-index: 1;
}
.h-hero-actions {
  display: flex; gap: 14px; flex-wrap: wrap;
  align-items: center; justify-content: center;
  position: relative; z-index: 1; margin-bottom: 64px;
}
.btn-h-hero {
  background: #1a6fc4; color: #fff; border: none;
  padding: 13px 32px; border-radius: 8px;
  font-size: 15px; font-family: 'DM Sans', sans-serif; font-weight: 500;
  cursor: pointer; text-decoration: none;
  transition: background .18s, transform .12s;
}
.btn-h-hero:hover { background: #2f8de8; transform: translateY(-1px); color: #fff; }
.btn-h-ghost {
  background: transparent; color: #e8f0fa;
  border: 1px solid rgba(255,255,255,0.14);
  padding: 13px 26px; border-radius: 8px;
  font-size: 15px; font-family: 'DM Sans', sans-serif;
  cursor: pointer; text-decoration: none;
  transition: border-color .18s, background .18s;
}
.btn-h-ghost:hover { border-color: rgba(255,255,255,0.32); background: rgba(255,255,255,0.04); color: #e8f0fa; }
.h-hero-stats {
  display: flex; gap: 48px; justify-content: center;
  align-items: center; flex-wrap: wrap;
  position: relative; z-index: 1;
}
.h-stat-num {
  font-family: 'Syne', sans-serif;
  font-size: 28px; font-weight: 700; color: #fff;
}
.h-stat-lbl { font-size: 12px; color: #8ba5c4; margin-top: 2px; }
.h-stat-div { width: 1px; height: 40px; background: rgba(255,255,255,0.1); }

/* ── ABOUT SECTION ───────────────────────────────── */
.h-about {
  background: #0f2035;
  padding: 96px 5vw;
  border-top: 0.5px solid rgba(255,255,255,0.06);
}
.h-about-grid {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 80px; align-items: center;
  max-width: 1140px; margin: 0 auto;
}
@media(max-width:800px){.h-about-grid{grid-template-columns:1fr;gap:44px}}
.h-section-label {
  font-size: 11px; font-weight: 500; letter-spacing: 1.5px;
  text-transform: uppercase; color: #3aabff; margin-bottom: 12px;
}
.h-section-title {
  font-family: 'Syne', sans-serif;
  font-size: clamp(26px, 3vw, 42px);
  font-weight: 700; line-height: 1.13; letter-spacing: -1px;
  color: #fff; margin-bottom: 14px;
}
.h-section-body {
  font-size: 15px; color: #8ba5c4;
  line-height: 1.72; font-weight: 300; max-width: 480px;
}
.h-cards {
  display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
}
.h-card {
  background: rgba(255,255,255,0.038);
  border: 0.5px solid rgba(255,255,255,0.075);
  border-radius: 12px; padding: 20px 18px;
  transition: border-color .2s, background .2s;
}
.h-card:hover { border-color: rgba(58,171,255,0.28); background: rgba(58,171,255,0.045); }
.h-card-icon { font-size: 20px; margin-bottom: 10px; }
.h-card h4 {
  font-family: 'Syne', sans-serif;
  font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 6px;
}
.h-card p { font-size: 12px; color: #8ba5c4; line-height: 1.55; font-weight: 300; margin: 0; }

/* ── TOOL SECTION ────────────────────────────────── */
.h-tool {
  padding: 96px 5vw;
  background: #0d1b2e;
  border-top: 0.5px solid rgba(255,255,255,0.05);
}
.h-tool-inner { max-width: 1140px; margin: 0 auto; }
.h-tool-header { text-align: center; max-width: 640px; margin: 0 auto 56px; }
.h-tool-header .h-section-title,
.h-tool-header .h-section-body { max-width: 100%; }
.h-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 20px;
}
.h-step {
  background: rgba(255,255,255,0.028);
  border: 0.5px solid rgba(255,255,255,0.065);
  border-radius: 14px; padding: 30px 24px;
  position: relative; overflow: hidden;
  transition: border-color .2s;
}
.h-step:hover { border-color: rgba(58,171,255,0.22); }
.h-step-bg {
  font-family: 'Syne', sans-serif;
  font-size: 52px; font-weight: 800;
  color: rgba(255,255,255,0.04);
  position: absolute; top: 12px; right: 16px; line-height: 1;
  pointer-events: none;
}
.h-step-icon {
  width: 40px; height: 40px; border-radius: 10px;
  background: rgba(26,111,196,0.18);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; margin-bottom: 16px;
}
.h-step h3 {
  font-family: 'Syne', sans-serif;
  font-size: 15px; font-weight: 600; color: #fff; margin-bottom: 8px;
}
.h-step p { font-size: 12px; color: #8ba5c4; line-height: 1.6; font-weight: 300; margin: 0; }

/* ── DEMO BANNER ─────────────────────────────────── */
.h-demo {
  padding: 80px 5vw;
  background: linear-gradient(135deg, #0e2542, #152640, #0d1b2e);
  border-top: 0.5px solid rgba(255,255,255,0.06);
  border-bottom: 0.5px solid rgba(255,255,255,0.06);
}
.h-demo-inner {
  max-width: 900px; margin: 0 auto;
  display: flex; align-items: center;
  justify-content: space-between; gap: 40px; flex-wrap: wrap;
}
.h-demo-inner .h-section-body { margin-bottom: 0; }
.h-demo-note { font-size: 12px; color: #8ba5c4; margin-top: 8px; }

/* ── LOGIN SECTION ───────────────────────────────── */
.h-login {
  padding: 96px 5vw;
  background: #0f2035;
  border-top: 0.5px solid rgba(255,255,255,0.06);
}
.h-login-inner { max-width: 440px; margin: 0 auto; text-align: center; }
.h-login-card {
  background: rgba(255,255,255,0.04);
  border: 0.5px solid rgba(255,255,255,0.1);
  border-radius: 16px; padding: 40px 36px; margin-top: 36px;
}
.h-login-card label {
  display: block; text-align: left;
  font-size: 13px; color: #8ba5c4; margin-bottom: 6px; margin-top: 18px;
}
.h-input {
  width: 100%; padding: 10px 14px;
  background: rgba(255,255,255,0.06);
  border: 0.5px solid rgba(255,255,255,0.12);
  border-radius: 7px; color: #e8f0fa;
  font-size: 14px; font-family: 'DM Sans', sans-serif;
  outline: none; transition: border-color .18s;
}
.h-input:focus { border-color: rgba(58,171,255,0.5); }
.btn-h-login {
  width: 100%; margin-top: 24px;
  background: #1a6fc4; color: #fff; border: none;
  padding: 11px; border-radius: 7px;
  font-size: 14px; font-family: 'DM Sans', sans-serif; font-weight: 500;
  cursor: pointer; text-decoration: none; display: block; text-align: center;
  transition: background .18s;
}
.btn-h-login:hover { background: #2f8de8; color: #fff; }
.h-login-divider { margin: 20px 0; color: #8ba5c4; font-size: 12px; }
.h-login-note { font-size: 12px; color: #8ba5c4; margin-top: 16px; line-height: 1.5; }

/* ── FOOTER ──────────────────────────────────────── */
.h-footer {
  background: #0a1625;
  padding: 36px 5vw;
  border-top: 0.5px solid rgba(255,255,255,0.06);
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 16px;
}
.h-footer-brand {
  font-family: 'Syne', sans-serif;
  font-size: 15px; font-weight: 700; color: #fff;
}
.h-footer-brand small {
  display: block; font-family: 'DM Sans', sans-serif;
  font-size: 11px; font-weight: 300; color: #8ba5c4; margin-top: 2px;
}
.h-footer-note { font-size: 12px; color: #8ba5c4; }
"""

# ── Logo (embedded from uploaded asset path) ──────────────────────────────────
LOGO_PATH = "/assets/hermes_logo.png"   # put the logo PNG in your /assets/ folder

# ── Layout ────────────────────────────────────────────────────────────────────

layout = html.Div([

    # Inject styles (html.Style doesn't exist in Dash — use script tag via dangerouslySetInnerHTML)
    html.Div(dangerouslySetInnerHTML={"__html": f"<style>{STYLES}</style>"}),

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
                    html.H2("Built for raw material procurement teams", className="h-section-title"),
                    html.P(
                        "Hermes AI Procurement was built to solve a real problem: procurement teams "
                        "in nutraceuticals, pharma, and specialty chemicals spend countless hours "
                        "manually processing supplier invoices, chasing lead times, and managing "
                        "reorder decisions from spreadsheets. "
                        "We built an AI-native platform that handles all of it — from PDF extraction "
                        "to natural-language Q&A over your entire invoice history.",
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
                        html.P("Upload any supplier or customer invoice — Hermes extracts entities and indexes it automatically."),
                    ]),
                    html.Div(className="h-card", children=[
                        html.Div("📦", className="h-card-icon"),
                        html.H4("Live Inventory"),
                        html.P("Real-time stock levels with low-stock alerts and configurable reorder thresholds."),
                    ]),
                    html.Div(className="h-card", children=[
                        html.Div("✉️", className="h-card-icon"),
                        html.H4("Reorder Drafts"),
                        html.P("Generate professional supplier reorder emails from a single command."),
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
                        html.P("Ask plain-English questions about your supplier history, pricing trends, "
                               "and lead times. Powered by RAG over your full invoice database."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("02", className="h-step-bg"),
                        html.Div("📊", className="h-step-icon"),
                        html.H3("Inventory Dashboard"),
                        html.P("Live stock levels across all SKUs. Color-coded low-stock alerts and "
                               "one-click manual updates keep your data accurate."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("03", className="h-step-bg"),
                        html.Div("📤", className="h-step-icon"),
                        html.H3("Invoice Upload"),
                        html.P("Drop in any PDF. The agent extracts supplier, product, price, and "
                               "shipping data — and adds it to your searchable knowledge base instantly."),
                    ]),
                    html.Div(className="h-step", children=[
                        html.Div("04", className="h-step-bg"),
                        html.Div("🤖", className="h-step-icon"),
                        html.H3("Agent Control"),
                        html.P("Toggle automation on/off, approve pending reorder drafts, run "
                               "procurement commands, and review the full action history."),
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
                        "a fictional raw material distributor with real invoice data, live inventory, "
                        "and a working AI chat interface.",
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
                html.Small("AI Procurement · All data shown is fictional and for demo purposes only."),
            ], className="h-footer-brand"),
            html.P("Built with Dash · Anthropic Claude · ChromaDB · SQLite",
                   className="h-footer-note"),
        ]),

    ]),
], style={"margin": "0", "padding": "0"})