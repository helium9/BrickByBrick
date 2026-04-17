from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from databricks import sql
import requests
import uuid
import datetime
from dot_env import load_dotenv
import os
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

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    # 1. Get the last 5 messages for this specific user session
    past_history = get_chat_history(req.session_id, limit=5)
    
    # 2. Ask Sarvam, passing the history
    ai_answer = call_sarvam_ai(req.query, req.context, past_history)
    
    # 3. Save this new Q&A to Databricks
    save_to_databricks(req.session_id, req.url, req.query, ai_answer)
    
    return {"answer": ai_answer}