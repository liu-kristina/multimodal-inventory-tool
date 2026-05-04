import dash
from pathlib import Path

# Import packages
from dash import Dash, html, dcc
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
from src.dashboard.components.title_section import create_title_section


# Initialize the app
dash.register_page(__name__, path='/about_us', title="About us", name="About Us", order=5)

img_src = Path("pages", "assets")



title_section = create_title_section("The team behind the app", 
                                     "Connect with us on LinkedIn")

# Team members section
def create_team_member_section(name, image, qr_code, github_handle):
    return html.Div([
        html.Img(src=dash.get_asset_url(image), style={'width': '200px', 'height': '200px', 'margin': '20px'}),
        html.H4(name),
        html.P(github_handle, style={'margin-top': '5px'}),
        html.Br(),
        html.Img(src=dash.get_asset_url(qr_code), style={'width': '120px', 'height': 'auto', 'margin': '20px'}),
    ], style={'display': 'inline-block', 'text-align': 'center', 'font-size': '20px'})

images_section = html.Div([
    create_team_member_section('Ying Huang', 'Ying.png', 'KristinaQR.png', 'github.com/yh51'),
    create_team_member_section('Kristina Liang', 'Kristina.png', 'KristinaQR.png', 'github.com/liu-kristina'),
    create_team_member_section('Moxi Liang', 'Moxi.png', 'MoxiQR.png', 'github.com/moxixmx533-ux'),

], style={'text-align': 'center', 'margin-top': '20px'})

# App description section
description_box = html.Div([
    dcc.Markdown("""
        This application was developed as the capstone project  
        for the NLP and GenAI program from Easy Learning.
    """, style={'text-align': 'center', 'font-size': '18px'}),
    html.Img(src=dash.get_asset_url('easylearningai.png'), 
             style={
                 'width': '300px', 
                 'height': 'auto', 
                 'display': 'block', 
                 'margin': '0 auto 10px'}),  # above, sides, below 
], style={
    'background-color': '#e5e5e5', 
    'color': '#004ad8',
    'border-radius': '10px',  # Rounded corners
    'padding': '15px',  # Padding around the content
    'margin-top': '20px',  # Spacing from above
    'max-width': '600px',  # Keep the box compact
    'margin-left': 'auto',
    'margin-right': 'auto'
})

# Footer image
footer_image = html.Div([
    html.Img(src=dash.get_asset_url('4_footer.png'), style={'width': '100%', 'height': '40px','margin-top': '30px'})
])

# Define layout
layout = html.Div([
    title_section,
    images_section,
    description_box,
    footer_image
])

# Run the app
if __name__ == '__main__':
   None