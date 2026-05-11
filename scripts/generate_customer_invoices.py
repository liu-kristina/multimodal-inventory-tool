from faker import Faker
from fpdf import FPDF
import random
import os
from datetime import timedelta

fake = Faker()
output_dir = "customer_invoices"
os.makedirs(output_dir, exist_ok=True)

# Products California Nutraceuticals sells, with retail markup over supplier cost
PRODUCTS = [
    ("Collagen Powder", "kg",     95,  160),
    ("Shark Cartilage Powder", "kg",   130,  200),
    ("Bovine Gelatin Type A", "kg",    65,  100),
    ("Fish Collagen Peptides", "kg",  150,  240),
    ("Hydrolyzed Marine Collagen", "kg", 170, 280),
    ("Bovine Cartilage Extract", "kg",  110,  180),
    ("Plant Extract - Ginseng Root", "kg", 200, 350),
    ("Plant Extract - Turmeric", "kg",   55,  100),
    ("Plant Extract - Ashwagandha", "kg", 90,  160),
    ("Plant Extract - Elderberry", "kg", 120,  200),
    ("Plant Extract - Echinacea", "kg",  100,  170),
    ("Hyaluronic Acid Powder", "kg",    420,  700),
    ("Chondroitin Sulfate", "kg",       180,  300),
    ("Glucosamine HCl", "kg",           120,  200),
    ("Collagen Peptides Type I", "kg",  140,  240),
]

# American customers — supplement brands, health food distributors, manufacturers
CUSTOMERS = [
    {
        "name": "Pacific Health Supplements LLC",
        "address": "4820 Industrial Blvd, Suite 100",
        "city": "San Diego, CA 92121",
        "contact": "Rachel Kim",
        "email": "rkim@pacifichealthsupp.com",
        "phone": "+1-858-555-0143",
        "type": "Supplement manufacturer",
        "preferred_products": [
            "Collagen Powder", "Fish Collagen Peptides",
            "Collagen Peptides Type I", "Hyaluronic Acid Powder"
        ],
    },
    {
        "name": "NutriCore Brands Inc",
        "address": "1100 Commerce Way",
        "city": "Austin, TX 78744",
        "contact": "James Okafor",
        "email": "jokafor@nutricorebrands.com",
        "phone": "+1-512-555-0287",
        "type": "Private label brand",
        "preferred_products": [
            "Plant Extract - Turmeric", "Plant Extract - Ashwagandha",
            "Plant Extract - Elderberry", "Plant Extract - Echinacea"
        ],
    },
    {
        "name": "Cascade Natural Foods Co",
        "address": "3350 SE Powell Blvd",
        "city": "Portland, OR 97202",
        "contact": "Amanda Torres",
        "email": "atorres@cascadenaturalfoods.com",
        "phone": "+1-503-555-0391",
        "type": "Health food distributor",
        "preferred_products": [
            "Bovine Gelatin Type A", "Plant Extract - Ginseng Root",
            "Plant Extract - Turmeric", "Glucosamine HCl"
        ],
    },
    {
        "name": "Apex Sports Nutrition LLC",
        "address": "2200 N Scottsdale Rd, Suite 300",
        "city": "Scottsdale, AZ 85257",
        "contact": "Derek Nguyen",
        "email": "dnguyen@apexsportsnutrition.com",
        "phone": "+1-480-555-0512",
        "type": "Sports nutrition brand",
        "preferred_products": [
            "Collagen Powder", "Bovine Cartilage Extract",
            "Chondroitin Sulfate", "Glucosamine HCl"
        ],
    },
    {
        "name": "Greenleaf Wellness Distributors",
        "address": "890 Market Street, Floor 5",
        "city": "San Francisco, CA 94102",
        "contact": "Priya Patel",
        "email": "ppatel@greenleafwellness.com",
        "phone": "+1-415-555-0634",
        "type": "Wellness distributor",
        "preferred_products": [
            "Plant Extract - Ashwagandha", "Plant Extract - Elderberry",
            "Hyaluronic Acid Powder", "Fish Collagen Peptides"
        ],
    },
    {
        "name": "Rocky Mountain Nutraceuticals",
        "address": "5500 Arapahoe Ave, Unit B",
        "city": "Boulder, CO 80303",
        "contact": "Sarah Whitfield",
        "email": "swhitfield@rockymtnnutra.com",
        "phone": "+1-720-555-0776",
        "type": "Supplement manufacturer",
        "preferred_products": [
            "Plant Extract - Ginseng Root", "Hydrolyzed Marine Collagen",
            "Collagen Peptides Type I", "Plant Extract - Echinacea"
        ],
    },
    {
        "name": "Sunrise Pharma Ingredients Inc",
        "address": "12400 Research Blvd, Suite 210",
        "city": "Dallas, TX 75244",
        "contact": "Michael Brooks",
        "email": "mbrooks@sunrisepharmingredients.com",
        "phone": "+1-972-555-0821",
        "type": "Pharmaceutical ingredient supplier",
        "preferred_products": [
            "Shark Cartilage Powder", "Bovine Cartilage Extract",
            "Chondroitin Sulfate", "Hyaluronic Acid Powder"
        ],
    },
    {
        "name": "Great Lakes Health Foods LLC",
        "address": "7200 W Grand Ave",
        "city": "Chicago, IL 60707",
        "contact": "Laura Chen",
        "email": "lchen@greatlakeshealthfoods.com",
        "phone": "+1-312-555-0944",
        "type": "Health food distributor",
        "preferred_products": [
            "Collagen Powder", "Plant Extract - Turmeric",
            "Glucosamine HCl", "Bovine Gelatin Type A"
        ],
    },
]

PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "Due on receipt"]
SHIPPING_METHODS = [
    "FedEx Ground", "UPS Ground", "FedEx 2-Day",
    "UPS Next Day Air", "LTL Freight"
]
SHIPPING_DAYS = {
    "FedEx Ground":     (3, 7),
    "UPS Ground":       (3, 7),
    "FedEx 2-Day":      (2, 3),
    "UPS Next Day Air": (1, 2),
    "LTL Freight":      (5, 10),
}

SELLER = {
    "name": "California Nutraceuticals Inc.",
    "address": "3901 Westwood Blvd, Suite 210",
    "city": "Los Angeles, CA 90034",
    "contact": "Sales Department",
    "email": "sales@canutraceuticals.com",
    "phone": "+1-310-555-0192",
    "tax_id": "EIN: 95-4821037",
}


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


def draw_single_col_rows(pdf, rows, label_w=55, value_w=115):
    for label, value in rows:
        pdf.set_x(20)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 6, label, ln=False)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(value_w, 6, value, ln=True)


def generate_customer_invoice(invoice_number):
    customer = random.choice(CUSTOMERS)
    shipping_method = random.choice(SHIPPING_METHODS)
    payment_terms = random.choice(PAYMENT_TERMS)
    invoice_date = fake.date_between(start_date="-2y", end_date="today")
    due_days = {"Net 30": 30, "Net 45": 45, "Net 60": 60, "Due on receipt": 0}
    due_date = invoice_date + timedelta(days=due_days[payment_terms])

    min_ship, max_ship = SHIPPING_DAYS[shipping_method]
    ship_lag = random.randint(1, 3)
    shipment_date = invoice_date + timedelta(days=ship_lag)
    transit_days = random.randint(min_ship, max_ship)
    delivery_date = shipment_date + timedelta(days=transit_days)

    # Pick products — bias toward customer's preferred products
    num_items = random.randint(1, 4)
    preferred = [p for p in PRODUCTS if p[0] in customer["preferred_products"]]
    other = [p for p in PRODUCTS if p[0] not in customer["preferred_products"]]
    pool = preferred * 3 + other  # weight preferred 3x
    selected = random.sample(pool, min(num_items, len(pool)))
    # deduplicate by product name
    seen = set()
    line_items_raw = []
    for p in selected:
        if p[0] not in seen:
            seen.add(p[0])
            line_items_raw.append(p)
    selected = line_items_raw[:num_items]

    line_items = []
    for product_name, unit, min_price, max_price in selected:
        quantity = random.randint(5, 150)
        unit_price = round(random.uniform(min_price, max_price), 2)
        line_total = round(quantity * unit_price, 2)
        line_items.append({
            "product": product_name,
            "unit": unit,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": line_total,
        })

    subtotal = round(sum(i["total"] for i in line_items), 2)
    shipping_cost = round(random.uniform(50, 400), 2)
    tax_rate = 0.0  # B2B sales typically tax-exempt with resale cert
    grand_total = round(subtotal + shipping_cost, 2)
    po_number = "PO-" + fake.bothify("########").upper()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    # ── Title ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 10, "SALES INVOICE", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, SELLER["name"], ln=True, align="C")
    pdf.ln(2)
    pdf.set_draw_color(180, 180, 180)
    pdf.set_line_width(0.4)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)

    # ── Seller / Buyer columns ─────────────────────────────────────────
    col_w = 82
    gap = 6

    draw_section_header(pdf, "SOLD BY")
    y_after_header = pdf.get_y()

    seller_lines = [
        SELLER["name"],
        SELLER["address"],
        SELLER["city"],
        "Contact: " + SELLER["contact"],
        "Email: " + SELLER["email"],
        "Tel: " + SELLER["phone"],
        SELLER["tax_id"],
    ]
    buyer_lines = [
        customer["name"],
        customer["address"],
        customer["city"],
        "Contact: " + customer["contact"],
        "Email: " + customer["email"],
        "Tel: " + customer["phone"],
        "Type: " + customer["type"],
    ]

    saved_y = pdf.get_y()
    pdf.set_xy(20 + col_w + gap, y_after_header - 6)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(col_w, 6, "BILL TO / SHIP TO", ln=True)
    pdf.set_text_color(30, 30, 30)

    pdf.set_xy(20, saved_y)
    pdf.set_font("Helvetica", "", 10)
    for i in range(max(len(seller_lines), len(buyer_lines))):
        pdf.set_x(20)
        pdf.cell(col_w, 5,
                 seller_lines[i] if i < len(seller_lines) else "", ln=False)
        pdf.set_x(20 + col_w + gap)
        pdf.cell(col_w, 5,
                 buyer_lines[i] if i < len(buyer_lines) else "", ln=True)

    pdf.ln(4)
    draw_divider(pdf)

    # ── Invoice details ────────────────────────────────────────────────
    draw_section_header(pdf, "INVOICE DETAILS")
    draw_single_col_rows(pdf, [
        ("Invoice No:", "CUST-" + str(invoice_number).zfill(4)),
        ("PO Number:", po_number),
        ("Invoice Date:", invoice_date.strftime("%d %b %Y")),
        ("Payment Due:", due_date.strftime("%d %b %Y")),
        ("Payment Terms:", payment_terms),
        ("Currency:", "USD"),
    ], label_w=55, value_w=115)

    pdf.ln(2)
    draw_divider(pdf)

    # ── Shipping details ───────────────────────────────────────────────
    draw_section_header(pdf, "SHIPPING DETAILS")
    draw_single_col_rows(pdf, [
        ("Shipping Method:", shipping_method),
        ("Ship From:", SELLER["address"] + ", " + SELLER["city"]),
        ("Ship To:", customer["address"] + ", " + customer["city"]),
        ("Shipment Date:", shipment_date.strftime("%d %b %Y")),
        ("Expected Delivery:", delivery_date.strftime("%d %b %Y")),
        ("Transit Time:", str(transit_days) + " business days"),
    ], label_w=55, value_w=115)

    pdf.ln(2)
    draw_divider(pdf)

    # ── Line items ─────────────────────────────────────────────────────
    draw_section_header(pdf, "ITEMS ORDERED")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    col_widths = [78, 16, 14, 32, 30]
    for header, w in zip(
            ["Product Description", "Qty", "Unit", "Unit Price", "Amount"],
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
                 "USD " + str(item["unit_price"]), align="R", ln=False)
        pdf.cell(col_widths[4], 6,
                 "USD " + "{:,.2f}".format(item["total"]), align="R", ln=True)

    pdf.ln(2)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(3)

    # ── Totals ─────────────────────────────────────────────────────────
    for i, (label, value) in enumerate([
        ("Subtotal:", "USD " + "{:,.2f}".format(subtotal)),
        ("Shipping:", "USD " + "{:,.2f}".format(shipping_cost)),
        ("Tax:", "USD 0.00"),
        ("Total Due:", "USD " + "{:,.2f}".format(grand_total)),
    ]):
        pdf.set_x(120)
        if i == 3:
            pdf.set_font("Helvetica", "B", 11)
            pdf.line(120, pdf.get_y(), 190, pdf.get_y())
            pdf.ln(1)
            pdf.set_x(120)
        else:
            pdf.set_font("Helvetica", "", 10)
        pdf.cell(40, 6, label, align="R", ln=False)
        pdf.cell(30, 6, value, align="R", ln=True)

    # Tax exempt note on its own full-width line below totals
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.set_x(20)
    pdf.cell(0, 6, "* Tax exempt - resale certificate on file", ln=True)
    pdf.set_text_color(30, 30, 30)

    pdf.ln(5)
    draw_divider(pdf)

    # ── Payment instructions ───────────────────────────────────────────
    draw_section_header(pdf, "PAYMENT INSTRUCTIONS")
    pdf.set_font("Helvetica", "", 10)
    for line in [
        "Bank Name: First Pacific Commerce Bank",
        "Account Name: California Nutraceuticals Inc.",
        "Account Number: 4401-8823-0012",
        "Routing Number: 122400724",
        "Remittance Email: " + SELLER["email"],
    ]:
        pdf.cell(0, 5, line, ln=True)

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 5,
        "Thank you for your business. Please reference the invoice number on "
        "your payment. For questions regarding this invoice, contact our sales "
        "department at " + SELLER["email"] + " or " + SELLER["phone"] + ".")

    safe_name = (customer["name"]
                 .replace(" ", "_").replace(".", "").replace(",", ""))
    filename = (output_dir + "/customer_invoice_" +
                str(invoice_number).zfill(4) + "_" + safe_name + ".pdf")
    pdf.output(filename)
    return filename, customer["name"], line_items[0]["product"], grand_total


if __name__ == "__main__":
    print("California Nutraceuticals Inc. — Customer Invoice Generator")
    print("=" * 72)
    print("  {:>4}  {:<38} {:<28} {:>10}".format(
        "#", "Customer", "Primary Product", "Total"))
    print("-" * 72)
    count = 50
    for i in range(1, count + 1):
        filename, customer, product, total = generate_customer_invoice(i)
        print("  {:>4}  {:<38} {:<28} ${:>9,.0f}".format(
            i, customer[:38], product[:28], total))
    print("=" * 72)
    print("Done. {} customer invoices saved to ./{}/".format(count, output_dir))