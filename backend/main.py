from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from databricks import sql
import requests
import uuid
import datetime
from dotenv import load_dotenv
import os
import json
import re
from urllib.parse import urlparse
import shutil
from pathlib import Path

load_dotenv()
app = FastAPI()

# 1. CORS is REQUIRED for Chrome Extensions to talk to localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Your Config (Replace these with your actual keys)
DATABRICKS_SERVER_HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

class ChatRequest(BaseModel):
    session_id: str 
    query: str
    url: str
    context: str

def get_db_connection():
    return sql.connect(
        server_hostname=DATABRICKS_SERVER_HOSTNAME,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN
    )

def get_chat_history(session_id, limit=5):
    """Fetches the last N messages for this session from Databricks"""
    query_str = """
        SELECT user_query, ai_response 
        FROM workspace.default.extension_chat_history 
        WHERE session_id = ? 
        ORDER BY timestamp DESC 
        LIMIT ?
    """
    history = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(query_str, (session_id, limit))
        rows = cursor.fetchall()
        
        # Databricks returns newest first (DESC). We need to reverse it 
        # so the LLM reads it in chronological order (oldest to newest)
        for row in reversed(rows):
            history.append({
                "user": row.user_query,
                "ai": row.ai_response
            })
            
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"⚠️ Could not fetch history: {e}")
    
    return history

def save_to_databricks(session_id, url, query, response):
    """Saves the new message to Databricks"""
    query_str = """
        INSERT INTO workspace.default.extension_chat_history 
        (session_id, url, user_query, ai_response, timestamp) 
        VALUES (?, ?, ?, ?, ?)
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(query_str, (session_id, url, query, response, datetime.datetime.now()))
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"❌ Databricks Insert Error: {e}")

def call_sarvam_ai(query, page_context, chat_history):
    """Calls Sarvam AI using page context AND previous conversation memory"""
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SARVAM_API_KEY}"
    }
    
    safe_context = page_context[:5000] 
    
    # 1. Start with the System Prompt
    messages = [
        {"role": "system", "content": f"You are a helpful assistant. Use the following webpage content to help answer questions: {safe_context}"}
    ]
    
    # 2. Inject the last 5 messages from Databricks history
    for exchange in chat_history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["ai"]})
        
    # 3. Add the brand new question
    messages.append({"role": "user", "content": query})

    payload = {
        "model": "sarvam-30b", # Or whichever model you settled on
        "messages": messages
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            print(f"❌ Sarvam API Error {response.status_code}: {response.text}")
            return "Error: Sarvam AI rejected the request."
    except Exception as e:
        return f"Error connecting to Sarvam: {e}"
    
class Profile:
    def __init__(self, user_details: dict):
        self.personal_details = user_details.get("personal_details", {})
        self.address_details = user_details.get("address_details", {})
        self.identity_documents = user_details.get("identity_documents", {})
        self.additional_info = user_details.get("additional_info", {})

    def __repr__(self):
        return (
            f"Profile(personal_details={self.personal_details}, "
            f"address_details={self.address_details}, "
            f"identity_documents={self.identity_documents}, "
            f"additional_info={self.additional_info})"
        )

def extract_json(text: str):
    if not text:
        return None

    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)

    try:
        return json.loads(text.strip())
    except:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidate = match.group()
        try:
            return json.loads(candidate)
        except:
            pass

    return None

class PDFProfileRequest(BaseModel):
    pdf_url: str
    
def fetch_pdf(pdf_url: str) -> str:
    """
    Returns local file path of PDF
    Handles:
    - file://
    - http/https
    """

    parsed = urlparse(pdf_url)

    if parsed.scheme == "file":
        return parsed.path  # local file

    elif parsed.scheme in ["http", "https"]:
        temp_path = f"/tmp/{uuid.uuid4()}.pdf"
        response = requests.get(pdf_url)

        if response.status_code != 200:
            raise Exception("Failed to download PDF")

        with open(temp_path, "wb") as f:
            f.write(response.content)

        return temp_path

    else:
        raise Exception("Unsupported URL scheme")
    
def fill_pdf(input_pdf_path: str, user_data: dict) -> str:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from pypdf import PdfReader, PdfWriter
    from io import BytesIO

    name = user_data["personal_details"]["full_name"]
    addr = user_data["address_details"]

    address_lines = [
        f"{addr['house_no']} {addr['street']}",
        addr["city"],
        f"{addr['state']} - {addr['pincode']}"
    ]

    FONT_SIZE = 7

    def write(c, text, x, y):
        c.setFont("Helvetica", FONT_SIZE)
        c.drawString(x + 2, y - 2, text)

    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=letter)

    # ---- WRITE FIELDS ----
    write(c, "18/04/2026", 405, 710)
    write(c, "Bhopal Constituency", 105, 640)
    write(c, "Bhopal Central Booth", 145, 555)
    write(c, "Bhopal", 265, 530)

    # Address
    y = 280
    for i, line in enumerate(address_lines):
        write(c, line, 115, y - i * 12)

    write(c, name, 355, 115)
    write(c, name, 355, 85)

    c.save()

    # Merge
    packet.seek(0)
    overlay = PdfReader(packet)
    original = PdfReader(input_pdf_path)

    writer = PdfWriter()

    for i in range(len(original.pages)):
        page = original.pages[i]
        if i < len(overlay.pages):
            page.merge_page(overlay.pages[i])
        writer.add_page(page)

    # ---- OUTPUT PATH ----
    downloads = str(Path.home() / "Downloads")

    base_name = os.path.basename(input_pdf_path).replace(".pdf", "")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    output_path = os.path.join(downloads, f"{base_name}-{timestamp}.pdf")

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    # 1. Get the last 5 messages for this specific user session
    past_history = get_chat_history(req.session_id, limit=5)
    
    # 2. Ask Sarvam, passing the history
    ai_answer = call_sarvam_ai(req.query, req.context, past_history)
    
    # 3. Save this new Q&A to Databricks
    save_to_databricks(req.session_id, req.url, req.query, ai_answer)
    
    return {"answer": ai_answer}

class ProfileRequest(BaseModel):
    required_data: list[str]

@app.post("/profile")
async def get_profile(request: ProfileRequest):
    print("🔥 /profile endpoint called")
    print(f"📋 Required fields requested: {request.required_data}")

    file_path = './userdata.json'

    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            user_profile = json.load(file)
        print(f"✅ User profile loaded from {file_path}")
    else:
        # TODO: Fetch user_profile from database
        user_profile = {}
        print(f"⚠️ {file_path} not found, using empty profile")

    profile = Profile(user_profile)
    print(f"Profile object created: {profile}")

    PROMPT = f"""
You are given a user profile JSON and a list of requested fields.

User Profile:
{json.dumps(user_profile)}

Required Fields:
{request.required_data}

Instructions:
- Map each requested field to the most relevant value from the user profile.
- The OUTPUT keys MUST EXACTLY match the requested fields (case-sensitive).
- Do NOT modify keys.
- Return ONLY valid JSON.
"""

    print("📨 Calling Sarvam AI...")
    ai_response = call_sarvam_ai(
        query=PROMPT,
        page_context="",
        chat_history=[]
    )
    print(f"🤖 AI Response: {ai_response}")

    try:
        response = extract_json(ai_response)
        print("Response: ", response)
    except Exception:
        response = {"error": "Invalid JSON from LLM", "raw": ai_response}

    print(f"📤 Returning response: {response}")
    return response

@app.post("/pdf-profile")
async def pdf_profile(req: PDFProfileRequest):
    print("📄 /pdf-profile called")

    # 1. Fetch PDF
    try:
        pdf_path = fetch_pdf(req.pdf_url)
        print(f"✅ PDF fetched: {pdf_path}")
    except Exception as e:
        return {"error": str(e)}

    # 2. Load user data
    file_path = "./userdata.json"

    if not os.path.exists(file_path):
        return {"error": "userdata.json not found"}

    with open(file_path, "r") as f:
        user_data = json.load(f)

    # 3. Fill PDF
    try:
        output_path = fill_pdf(pdf_path, user_data)
        print(f"✅ PDF generated: {output_path}")
    except Exception as e:
        return {"error": f"PDF generation failed: {str(e)}"}

    return {
        "message": "PDF generated successfully",
        "download_path": output_path
    }