"""
rename_invoices.py — One-time migration script
Renames all existing PDFs in data/invoices/ and data/customer_invoices/
to the new naming convention:
    {type}_{counterparty}_{date}_{invoice_number}.pdf

Run once from the project root:
    python rename_invoices.py

Safe to run — previews changes first and asks for confirmation.
Does not delete anything — only renames.
"""

import os
import sys
import json
import shutil
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline.pdf_extractor import extract_invoice

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
INVOICE_DIRS = [
    os.path.join(BASE_DIR, "data", "invoices"),
    os.path.join(BASE_DIR, "data", "customer_invoices"),
]


def clean(s: str, max_len: int = 30) -> str:
    """Remove special chars, replace spaces with hyphens, cap length."""
    s = str(s).strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s[:max_len].strip("-")


def clean_date(date_str: str) -> str:
    """Sanitize date string without capping — preserves full year e.g. 28-Jan-2024."""
    s = str(date_str).strip()
    s = re.sub(r"[^\w-]", "", s)   # strip spaces, commas, dots — keep hyphens
    return s


def build_filename(extracted: dict) -> str:
    """Build standardised filename from extracted invoice data."""
    inv_type     = extracted.get("invoice_type", "unknown")
    counterparty = (extracted.get("supplier_name") or
                    extracted.get("customer_name") or "unknown")
    date         = extracted.get("invoice_date", "nodate")
    inv_number   = extracted.get("invoice_number", "noinv")

    date         = clean_date(date)
    counterparty = clean(counterparty, max_len=30)
    inv_number   = clean(inv_number, max_len=20)

    return f"{inv_type}_{counterparty}_{date}_{inv_number}.pdf"


def preview_renames(invoice_dir: str) -> list:
    """Return list of (old_path, new_path) tuples for all PDFs in folder."""
    pdfs = sorted(Path(invoice_dir).glob("*.pdf"))
    if not pdfs:
        return []

    print(f"\nScanning {invoice_dir}...")
    print(f"Found {len(pdfs)} PDFs\n")

    renames = []
    for pdf in pdfs:
        print(f"  Extracting: {pdf.name}")
        try:
            extracted = extract_invoice(str(pdf))
            new_name  = build_filename(extracted)
            new_path  = pdf.parent / new_name

            if pdf.name == new_name:
                print(f"    → already correct, skipping")
                continue

            # Handle duplicates
            if new_path.exists() and new_path != pdf:
                stem, ext = os.path.splitext(new_name)
                new_name  = f"{stem}_dup{ext}"
                new_path  = pdf.parent / new_name

            renames.append((str(pdf), str(new_path), pdf.name, new_name))
            print(f"    → {new_name}")

        except Exception as e:
            print(f"    [ERROR] Could not extract: {e} — skipping")

    return renames


def do_renames(renames: list):
    """Execute the renames."""
    success = 0
    errors  = 0
    for old_path, new_path, old_name, new_name in renames:
        try:
            os.rename(old_path, new_path)
            success += 1
        except Exception as e:
            print(f"  [ERROR] Could not rename {old_name}: {e}")
            errors += 1
    print(f"\nDone. {success} renamed, {errors} errors.")


if __name__ == "__main__":
    print("=" * 60)
    print("Invoice rename migration")
    print("=" * 60)

    all_renames = []
    for folder in INVOICE_DIRS:
        if os.path.exists(folder):
            all_renames.extend(preview_renames(folder))
        else:
            print(f"\nFolder not found, skipping: {folder}")

    if not all_renames:
        print("\nNo files to rename.")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Ready to rename {len(all_renames)} file(s).")
    print(f"{'='*60}")
    confirm = input("\nProceed? (yes/no): ").strip().lower()

    if confirm == "yes":
        do_renames(all_renames)
    else:
        print("Cancelled — no files changed.")
