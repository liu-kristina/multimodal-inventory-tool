from faker import Faker
from fpdf import FPDF
import random
import os
from datetime import timedelta

fake = Faker()
output_dir = "invoices"
import shutil
if os.path.exists(output_dir):
    shutil.rmtree(output_dir)
os.makedirs(output_dir)

PRODUCTS = [
    ("Collagen Powder", "kg", 45, 90),
    ("Shark Cartilage Powder", "kg", 60, 120),
    ("Bovine Gelatin Type A", "kg", 30, 55),
    ("Fish Collagen Peptides", "kg", 70, 140),
    ("Hydrolyzed Marine Collagen", "kg", 80, 160),
    ("Bovine Cartilage Extract", "kg", 50, 100),
    ("Plant Extract - Ginseng Root", "kg", 90, 200),
    ("Plant Extract - Turmeric", "kg", 25, 60),
    ("Plant Extract - Ashwagandha", "kg", 40, 85),
    ("Plant Extract - Elderberry", "kg", 55, 110),
    ("Plant Extract - Echinacea", "kg", 45, 95),
    ("Hyaluronic Acid Powder", "kg", 200, 400),
    ("Chondroitin Sulfate", "kg", 85, 170),
    ("Glucosamine HCl", "kg", 55, 110),
    ("Collagen Peptides Type I", "kg", 65, 130),
]

SHIPPING_METHODS = {
    "Sea freight - FCL": (25, 35, 7),
    "Sea freight - LCL": (30, 45, 10),
    "Air freight":       (5,  10, 3),
    "Express courier":   (3,  5,  2),
}

SUPPLIERS = [
    {
        "name": "Pacific Rim BioMaterials Co.",
        "address": "1482 Zhejiang Industrial Park, Hangzhou, China 310000",
        "contact": "James Chen",
        "email": "jchen@pacificrimbiomaterials.com",
        "phone": "+86-571-8823-4401",
        "port_of_loading": "Port of Shanghai",
        "preferred_shipping": ["Sea freight - FCL", "Sea freight - LCL"],
        "typical_lead_days": (28, 38),
        "specialties": [
            "Collagen Powder", "Fish Collagen Peptides",
            "Hydrolyzed Marine Collagen", "Collagen Peptides Type I"
        ],
    },
    {
        "name": "Jiaxing Natural Products Ltd",
        "address": "88 Export Processing Zone, Jiaxing, China 314000",
        "contact": "Lisa Wang",
        "email": "lwang@jiaxingnatural.com",
        "phone": "+86-573-8291-5567",
        "port_of_loading": "Port of Shanghai",
        "preferred_shipping": ["Sea freight - LCL", "Sea freight - FCL"],
        "typical_lead_days": (32, 45),
        "specialties": [
            "Shark Cartilage Powder", "Bovine Cartilage Extract",
            "Chondroitin Sulfate", "Glucosamine HCl"
        ],
    },
    {
        "name": "Shanghai BioSupply International",
        "address": "22F Pudong Science Tower, Shanghai, China 200120",
        "contact": "Michael Zhou",
        "email": "mzhou@shibiosupply.com",
        "phone": "+86-21-6834-9900",
        "port_of_loading": "Port of Shanghai",
        "preferred_shipping": ["Sea freight - FCL", "Air freight"],
        "typical_lead_days": (25, 35),
        "specialties": [
            "Bovine Gelatin Type A", "Hyaluronic Acid Powder",
            "Chondroitin Sulfate"
        ],
    },
    {
        "name": "Zhejiang Green Botanicals Corp",
        "address": "300 Botanical Research Ave, Wenzhou, China 325000",
        "contact": "Sarah Liu",
        "email": "sliu@zjgreenbotanicals.com",
        "phone": "+86-577-8810-2233",
        "port_of_loading": "Port of Ningbo",
        "preferred_shipping": ["Sea freight - LCL", "Air freight"],
        "typical_lead_days": (30, 42),
        "specialties": [
            "Plant Extract - Ginseng Root", "Plant Extract - Turmeric",
            "Plant Extract - Ashwagandha", "Plant Extract - Elderberry",
            "Plant Extract - Echinacea"
        ],
    },
    {
        "name": "Guangzhou Nutra Raw Materials Inc",
        "address": "Industrial Zone B, Guangzhou, China 510000",
        "contact": "David Liang",
        "email": "dliang@gznutraraw.com",
        "phone": "+86-20-8734-5521",
        "port_of_loading": "Port of Guangzhou",
        "preferred_shipping": ["Sea freight - FCL", "Sea freight - LCL"],
        "typical_lead_days": (30, 40),
        "specialties": [
            "Collagen Powder", "Bovine Gelatin Type A",
            "Glucosamine HCl", "Plant Extract - Turmeric"
        ],
    },
]

PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "Due on receipt"]
CURRENCIES = ["USD", "USD", "USD", "EUR"]
PORT_OF_DESTINATION = "Port of Los Angeles, CA, USA"

BUYER = {
    "name": "California Nutraceuticals Inc.",
    "address": "3901 Westwood Blvd, Suite 210",
    "city": "Los Angeles, CA 90034",
    "contact": "Procurement Dept",
    "email": "procurement@canutraceuticals.com",
    "phone": "+1-310-555-0192",
}


def find_supplier_for_product(product_name):
    for supplier in SUPPLIERS:
        if product_name in supplier["specialties"]:
            return supplier
    return random.choice(SUPPLIERS)


def calculate_delivery_dates(invoice_date, shipping_method, supplier):
    min_days, max_days, variance = SHIPPING_METHODS[shipping_method]
    base_transit = random.randint(min_days, max_days)
    ship_lag = random.randint(3, 7)
    shipment_date = invoice_date + timedelta(days=ship_lag)
    expected_delivery = shipment_date + timedelta(days=base_transit)
    delay = random.randint(-2, variance)
    actual_delivery = expected_delivery + timedelta(days=delay)
    actual_lead_days = (actual_delivery - invoice_date).days
    return shipment_date, expected_delivery, actual_delivery, actual_lead_days


def draw_divider(pdf):
    pdf.set_draw_color(180, 180, 180)
    pdf.set_line_width(0.4)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)


def draw_section_header(pdf, text):
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 6, text, ln=True)
    pdf.set_text_color(30, 30, 30)


def draw_two_col_rows(pdf, rows, label_w=52, value_w=40):
    """
    Draw rows as two side-by-side label/value pairs per line.
    label_w + value_w + gap must fit twice across the 170px usable width.
    With label_w=52, value_w=40, gap=8: (52+40+8)*2 = 200 -- too wide.
    Use label_w=48, value_w=34, gap=6: (48+34+6)*2 = 176 -- fits in 170.
    Caller passes explicit widths to keep it flexible.
    """
    gap = 6
    pdf.set_font("Helvetica", "", 10)
    for i in range(0, len(rows), 2):
        pdf.set_x(20)
        label, value = rows[i]
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 6, label, ln=False)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(value_w, 6, value, ln=False)
        if i + 1 < len(rows):
            pdf.set_x(20 + label_w + value_w + gap)
            label2, value2 = rows[i + 1]
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(label_w, 6, label2, ln=False)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(value_w, 6, value2, ln=True)
        else:
            pdf.ln()


def draw_single_col_rows(pdf, rows, label_w=55, value_w=115):
    """Full-width label + value pairs, one per line."""
    for label, value in rows:
        pdf.set_x(20)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 6, label, ln=False)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(value_w, 6, value, ln=True)


def generate_invoice(invoice_number):
    num_line_items = random.randint(1, 4)
    selected_products = random.sample(PRODUCTS, num_line_items)
    primary_product = selected_products[0][0]
    supplier = find_supplier_for_product(primary_product)
    currency = random.choice(CURRENCIES)
    payment_terms = random.choice(PAYMENT_TERMS)
    shipping_method = random.choice(supplier["preferred_shipping"])
    invoice_date = fake.date_between(start_date="-2y", end_date="today")
    due_date = fake.date_between(start_date=invoice_date, end_date="+60d")

    shipment_date, expected_delivery, actual_delivery, actual_lead_days = \
        calculate_delivery_dates(invoice_date, shipping_method, supplier)

    from datetime import date
    in_transit = actual_delivery > date.today()

    line_items = []
    for product_name, unit, min_price, max_price in selected_products:
        quantity = random.randint(10, 300)
        unit_price = round(random.uniform(min_price, max_price), 2)
        line_total = round(quantity * unit_price, 2)
        line_items.append({
            "product": product_name,
            "unit": unit,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": line_total,
        })

    subtotal = round(sum(item["total"] for item in line_items), 2)
    freight = round(random.uniform(200, 1200), 2)
    grand_total = round(subtotal + freight, 2)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    # ── Title ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 10, "COMMERCIAL INVOICE", ln=True, align="C")
    pdf.ln(2)
    draw_divider(pdf)

    # ── Seller / Buyer ─────────────────────────────────────────────────
    # Split supplier address into street + city/country so long
    # addresses never overflow into the buyer column
    sup_addr = supplier["address"]
    if ", China" in sup_addr:
        before_china = sup_addr.split(", China")[0]
        china_part   = "China" + sup_addr.split(", China")[1]
        parts = before_china.rsplit(", ", 1)
        sup_street_line = parts[0]
        sup_city_line   = (parts[1] + ", " if len(parts) == 2 else "") + china_part
    else:
        sup_street_line = sup_addr
        sup_city_line   = ""

    col_w = 85
    gap   = 5

    draw_section_header(pdf, "SELLER / EXPORTER")
    y_after_header = pdf.get_y()

    seller_lines = [
        supplier["name"],
        sup_street_line,
        sup_city_line,
        "Contact: " + supplier["contact"],
        "Email:   " + supplier["email"],
        "Tel:     " + supplier["phone"],
    ]
    buyer_lines = [
        BUYER["name"],
        BUYER["address"],
        BUYER["city"],
        "Contact: " + BUYER["contact"],
        "Email:   " + BUYER["email"],
        "Tel:     " + BUYER["phone"],
    ]

    # Print buyer header at same y-level as seller header
    saved_y = pdf.get_y()
    pdf.set_xy(20 + col_w + gap, y_after_header - 6)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(col_w, 6, "BUYER / IMPORTER", ln=True)
    pdf.set_text_color(30, 30, 30)

    pdf.set_xy(20, saved_y)
    pdf.set_font("Helvetica", "", 10)
    for i in range(max(len(seller_lines), len(buyer_lines))):
        pdf.set_x(20)
        s = seller_lines[i] if i < len(seller_lines) else ""
        b = buyer_lines[i]  if i < len(buyer_lines)  else ""
        pdf.cell(col_w, 5, s, ln=False)
        pdf.set_x(20 + col_w + gap)
        pdf.cell(col_w, 5, b, ln=True)

    pdf.ln(4)
    draw_divider(pdf)

    # ── Invoice details (2-col layout, label_w=44 value_w=40) ──────────
    # Two pairs per row, total per pair = 44+40 = 84, two pairs + gap = 84+6+84 = 174 ✓
    draw_section_header(pdf, "INVOICE DETAILS")
    draw_two_col_rows(pdf, [
        ("Invoice No:", "INV-" + str(invoice_number).zfill(4)),
        ("Invoice Date:", invoice_date.strftime("%d %b %Y")),
        ("Payment Due:", due_date.strftime("%d %b %Y")),
        ("Payment Terms:", payment_terms),
        ("Currency:", currency),
        ("", ""),
    ], label_w=44, value_w=40)

    pdf.ln(2)
    draw_divider(pdf)

    # ── Shipping & logistics (single-col, full width values) ───────────
    draw_section_header(pdf, "SHIPPING & LOGISTICS")

    typical_str = (str(supplier["typical_lead_days"][0]) + " - " +
                   str(supplier["typical_lead_days"][1]) + " days (this supplier)")

    if in_transit:
        actual_label = "Est. Delivery:"
        actual_value = actual_delivery.strftime("%d %b %Y") + "  (in transit)"
        lead_value   = ("~" + str((expected_delivery - invoice_date).days) +
                        " days estimated")
    else:
        actual_label = "Actual Delivery:"
        actual_value = actual_delivery.strftime("%d %b %Y")
        lead_value   = str(actual_lead_days) + " days  (invoice to delivery)"

    draw_single_col_rows(pdf, [
        ("Shipping Method:", shipping_method),
        ("Port of Loading:", supplier["port_of_loading"]),
        ("Port of Destination:", PORT_OF_DESTINATION),
        ("Shipment Date:", shipment_date.strftime("%d %b %Y")),
        ("Expected Delivery:", expected_delivery.strftime("%d %b %Y")),
        (actual_label, actual_value),
        ("Total Lead Time:", lead_value),
        ("Typical Lead Time:", typical_str),
    ], label_w=55, value_w=115)

    pdf.ln(2)
    draw_divider(pdf)

    # ── Line items ─────────────────────────────────────────────────────
    draw_section_header(pdf, "GOODS DESCRIPTION")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    col_widths = [78, 16, 14, 32, 30]
    for header, w in zip(["Product Description", "Qty", "Unit", "Unit Price", "Amount"],
                         col_widths):
        pdf.cell(w, 7, header, fill=True, align="C", ln=False)
    pdf.ln()
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())

    pdf.set_font("Helvetica", "", 10)
    for item in line_items:
        pdf.set_x(20)
        pdf.cell(col_widths[0], 6, item["product"], ln=False)
        pdf.cell(col_widths[1], 6, str(item["quantity"]), align="C", ln=False)
        pdf.cell(col_widths[2], 6, item["unit"], align="C", ln=False)
        pdf.cell(col_widths[3], 6,
                 currency + " " + str(item["unit_price"]), align="R", ln=False)
        pdf.cell(col_widths[4], 6,
                 currency + " " + "{:,.2f}".format(item["total"]), align="R", ln=True)

    pdf.ln(2)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(3)

    # ── Totals ─────────────────────────────────────────────────────────
    for i, (label, value) in enumerate([
        ("Subtotal:", currency + " " + "{:,.2f}".format(subtotal)),
        ("Freight & Insurance:", currency + " " + "{:,.2f}".format(freight)),
        ("Grand Total:", currency + " " + "{:,.2f}".format(grand_total)),
    ]):
        pdf.set_x(120)
        if i == 2:
            pdf.set_font("Helvetica", "B", 11)
            pdf.line(120, pdf.get_y(), 190, pdf.get_y())
            pdf.ln(1)
            pdf.set_x(120)
        else:
            pdf.set_font("Helvetica", "", 10)
        pdf.cell(40, 6, label, align="R", ln=False)
        pdf.cell(30, 6, value, align="R", ln=True)

    pdf.ln(5)
    draw_divider(pdf)

    # ── Banking ────────────────────────────────────────────────────────
    draw_section_header(pdf, "BANKING DETAILS")
    pdf.set_font("Helvetica", "", 10)
    for line in [
        "Bank Name: " + fake.company() + " Bank",
        "Account Name: " + supplier["name"],
        "Account Number: " + fake.bothify("####-####-####-####"),
        "SWIFT Code: " + fake.bothify("???BCNSH???").upper(),
    ]:
        pdf.cell(0, 5, line, ln=True)

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 5,
        "This commercial invoice is issued to California Nutraceuticals Inc. "
        "All goods are subject to quality inspection upon arrival at destination. "
        "Please retain this document for your accounting and customs records.")

    safe_name = (supplier["name"]
                 .replace(" ", "_").replace(".", "").replace(",", ""))
    filename = (output_dir + "/invoice_" + str(invoice_number).zfill(4) +
                "_" + safe_name + ".pdf")
    pdf.output(filename)
    return filename, supplier["name"], primary_product, grand_total, actual_lead_days, shipping_method


if __name__ == "__main__":
    print("California Nutraceuticals Inc. — Invoice Generator")
    print("=" * 72)
    print("  {:>4}  {:<36} {:<28} {:>10}  {:>3}  {}".format(
        "#", "Supplier", "Primary Product", "Total", "LT", "Shipping"))
    print("-" * 72)
    count = 50
    for i in range(1, count + 1):
        filename, supplier, product, total, lead_days, shipping = generate_invoice(i)
        short = (shipping.replace("Sea freight - ", "Sea ")
                         .replace("Express courier", "Express"))
        print("  {:>4}  {:<36} {:<28} ${:>9,.0f}  {:>3}d  {}".format(
            i, supplier[:36], product[:28], total, lead_days, short))
    print("=" * 72)
    print("Done. {} invoices saved to ./{}/".format(count, output_dir))
