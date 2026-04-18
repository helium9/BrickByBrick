import json
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from pypdf import PdfReader, PdfWriter
from io import BytesIO

# ---------------- CONFIG ----------------
INPUT_PDF = "./Form_M_English.pdf"
OUTPUT_PDF = "./filled_form_final.pdf"
JSON_FILE = "userdata.json"

BASE_FONT = "Helvetica"

# ---------------- HELPER ----------------
def fit_text_to_underscore(c, text, x, y, underscore_len=25,
                          min_size=5, max_size=10):
    """
    Fit text inside underscore width.
    underscore_len = number of '_' characters visually present
    """

    # Calculate max width using underscores
    underscore_str = "_" * underscore_len
    max_width = stringWidth(underscore_str, BASE_FONT, max_size)

    # Start small and grow
    font_size = min_size
    while font_size < max_size:
        width = stringWidth(text, BASE_FONT, font_size)
        if width > max_width:
            break
        font_size += 0.5

    font_size -= 0.5  # step back

    c.setFont(BASE_FONT, font_size)

    # Slight downward shift to align with line
    c.drawString(x, y - 2, text)


def draw_multiline_fit(c, lines, x, y_start, line_gap=13, underscore_len=35):
    """Draw multiple lines aligned to dotted lines"""
    for i, line in enumerate(lines):
        fit_text_to_underscore(
            c,
            line.strip(),
            x,
            y_start - (i * line_gap),
            underscore_len=underscore_len
        )

# ---------------- LOAD DATA ----------------
with open(JSON_FILE) as f:
    data = json.load(f)

name = data["personal_details"]["full_name"]
date = "18/04/2026"

addr = data["address_details"]
address_lines = [
    f"{addr['house_no']} {addr['street']}",
    addr["city"],
    f"{addr['state']} - {addr['pincode']}"
]

# ---------------- CREATE OVERLAY ----------------
packet = BytesIO()
c = canvas.Canvas(packet, pagesize=letter)

# ---------------- FIELD MAPPING ----------------
# NOTE: Coordinates tuned for your Form (page 1)

# Date (short underscore)
fit_text_to_underscore(c, date, 410, 710, underscore_len=12)

# Constituency (long underscore)
fit_text_to_underscore(c, "Bhopal Constituency", 110, 640, underscore_len=30)

# Polling station
fit_text_to_underscore(c, "Bhopal Central Booth", 150, 555, underscore_len=28)

# City
fit_text_to_underscore(c, "Bhopal", 270, 530, underscore_len=20)

# Address block (dotted lines)
draw_multiline_fit(
    c,
    address_lines,
    x=120,
    y_start=285,
    line_gap=13,
    underscore_len=35
)

# Signature section
fit_text_to_underscore(c, name, 360, 115, underscore_len=25)

# Name at bottom
fit_text_to_underscore(c, name, 360, 85, underscore_len=30)

c.save()

# ---------------- MERGE ----------------
packet.seek(0)
overlay_pdf = PdfReader(packet)
original_pdf = PdfReader(INPUT_PDF)

writer = PdfWriter()

for i in range(len(original_pdf.pages)):
    page = original_pdf.pages[i]
    if i < len(overlay_pdf.pages):
        page.merge_page(overlay_pdf.pages[i])
    writer.add_page(page)

# ---------------- SAVE ----------------
with open(OUTPUT_PDF, "wb") as f:
    writer.write(f)

print(f"✅ Filled PDF saved at: {OUTPUT_PDF}")
