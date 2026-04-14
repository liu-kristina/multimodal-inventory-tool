"""
pages/upload.py - Invoice upload page
Accepts a PDF, extracts entities, generates embedding, adds to ChromaDB.
"""

import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import base64
import tempfile
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.pdf_extractor import extract_invoice
from pipeline.generate_embeddings import get_embedding, CHROMA_DIR, COLLECTION_NAME
import chromadb

dash.register_page(__name__, path="/upload", title="Upload Invoice")

# ── Layout ─────────────────────────────────────────────────────────────────────

layout = html.Div(
    [
        html.H4("Upload Invoice", className="mb-1 fw-bold"),
        html.P(
            "Upload a supplier or customer invoice PDF to add it to the knowledge base.",
            className="text-muted mb-4",
        ),

        # Upload area
        dcc.Upload(
            id="upload-pdf",
            children=html.Div(
                [
                    html.P("Drag and drop a PDF here, or", className="mb-1"),
                    dbc.Button("Browse Files", color="primary", outline=True, size="sm"),
                ],
                className="text-center py-4",
            ),
            style={
                "border": "2px dashed #dee2e6",
                "borderRadius": "8px",
                "backgroundColor": "#f8f9fa",
                "cursor": "pointer",
                "marginBottom": "1.5rem",
            },
            accept=".pdf",
            multiple=False,
        ),

        # Status output
        html.Div(id="upload-status"),

        # Recent uploads log
        html.H6("Extracted Fields Preview", className="fw-bold mt-4 mb-2"),
        html.Div(id="upload-preview", className="text-muted fst-italic",
                 children="Upload an invoice to see extracted fields here."),
    ],
    style={"maxWidth": "700px"},
)


# ── Callbacks ──────────────────────────────────────────────────────────────────

@callback(
    Output("upload-status",  "children"),
    Output("upload-preview", "children"),
    Input("upload-pdf",      "contents"),
    State("upload-pdf",      "filename"),
    prevent_initial_call=True,
)
def process_upload(contents, filename):
    if contents is None:
        return "", "Upload an invoice to see extracted fields here."

    if not filename.endswith(".pdf"):
        return dbc.Alert("Please upload a PDF file.", color="warning"), ""

    # Decode base64 PDF content
    content_type, content_string = contents.split(",")
    pdf_bytes = base64.b64decode(content_string)

    # Save to temp file for processing
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        # Step 1 — extract entities
        extracted = extract_invoice(tmp_path)
        extracted["filename"] = filename

        # Step 2 — generate embedding
        from pipeline.generate_embeddings import build_text
        text      = build_text(extracted)
        embedding = get_embedding(text)

        # Step 3 — add to ChromaDB
        chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection    = chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        inv_type = extracted.get("invoice_type", "unknown")
        metadata = {
            "filename":       filename,
            "invoice_type":   inv_type,
            "invoice_number": extracted.get("invoice_number", ""),
            "invoice_date":   extracted.get("invoice_date", ""),
            "grand_total":    float(extracted.get("grand_total", 0)),
        }
        if inv_type == "supplier":
            metadata["supplier_name"]    = extracted.get("supplier_name", "")
            metadata["shipping_method"]  = extracted.get("shipping_method", "")
            metadata["typical_lead_time"] = extracted.get("typical_lead_time", "")
        elif inv_type == "customer":
            metadata["customer_name"]  = extracted.get("customer_name", "")
            metadata["customer_type"]  = extracted.get("customer_type", "")
            metadata["shipping_method"] = extracted.get("shipping_method", "")

        doc_id = extracted.get("invoice_number") or filename
        collection.upsert(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[doc_id],
        )

        # Build preview table
        items = extracted.get("line_items", [])
        preview = html.Div([
            dbc.Table(
                [
                    html.Tbody([
                        html.Tr([html.Td("Type", className="text-muted fw-bold"),
                                 html.Td(inv_type.title())]),
                        html.Tr([html.Td("Invoice No", className="text-muted fw-bold"),
                                 html.Td(extracted.get("invoice_number", "—"))]),
                        html.Tr([html.Td("Date", className="text-muted fw-bold"),
                                 html.Td(extracted.get("invoice_date", "—"))]),
                        html.Tr([html.Td(
                            "Supplier" if inv_type == "supplier" else "Customer",
                            className="text-muted fw-bold"),
                            html.Td(extracted.get("supplier_name") or
                                    extracted.get("customer_name") or "—")]),
                        html.Tr([html.Td("Products", className="text-muted fw-bold"),
                                 html.Td(", ".join(i["product"] for i in items) or "—")]),
                        html.Tr([html.Td("Grand Total", className="text-muted fw-bold"),
                                 html.Td(f"${extracted.get('grand_total', 0):,.2f}")]),
                    ])
                ],
                bordered=False, size="sm", style={"maxWidth": "500px"}
            )
        ])

        status = dbc.Alert(
            [
                html.Strong(f"{filename} uploaded successfully. "),
                f"Invoice {extracted.get('invoice_number', '')} added to knowledge base.",
            ],
            color="success",
        )
        return status, preview

    except Exception as e:
        status = dbc.Alert(f"Error processing invoice: {str(e)}", color="danger")
        return status, ""

    finally:
        os.unlink(tmp_path)
