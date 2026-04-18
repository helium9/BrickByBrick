import json
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pypdf import PdfReader, PdfWriter
from io import BytesIO

# ---------------- CONFIG ----------------
INPUT_PDF = "./Form_M_English.pdf"
OUTPUT_PDF = "./filled_form_perfect.pdf"
JSON_FILE = "userdata.json"

FONT = "Helvetica"
FONT_SIZE = 6   # 🔥 small fixed font
LEFT_PADDING = 2  # 🔥 tiny gap from first underscore

# ---------------- HELPER ----------------
def write_on_line(c, text, x, y):
    """
    Write text starting just after first underscore
    """
    c.setFont(FONT, FONT_SIZE)
    c.drawString(x + LEFT_PADDING, y - 2, text)


def write_multiline(c, lines, x, y_start, gap=12):
    for i, line in enumerate(lines):
        write_on_line(c, line.strip(), x, y_start - (i * gap))


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

# ---------------- FIELD POSITIONS ----------------
# ⚠️ THESE X VALUES = START OF FIRST UNDERSCORE

# Date
write_on_line(c, date, 405, 710)

# Constituency
write_on_line(c, "Bhopal Constituency", 105, 640)

# Polling station
write_on_line(c, "Bhopal Central Booth", 145, 555)

# City
write_on_line(c, "Bhopal", 265, 530)

# Address block (aligned to dotted start)
write_multiline(
    c,
    address_lines,
    x=115,
    y_start=280,
    gap=12
)

# Signature
write_on_line(c, name, 355, 115)

# Name bottom
write_on_line(c, name, 355, 85)

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

print(f"✅ Perfectly aligned PDF saved at: {OUTPUT_PDF}")