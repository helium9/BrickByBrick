from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from databricks import sql
import requests
import datetime
from dotenv import load_dotenv
import os
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from neo4j import GraphDatabase

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABRICKS_SERVER_HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

class ChatRequest(BaseModel):
    session_id: str 
    query: str
    url: str
    auth_token: str | None = None

class SyncRequest(BaseModel):
    auth_token: str
    payload: dict

class ProfileUpdateRequest(BaseModel):
    auth_token: str
    data: dict

VALID_TABLES = ["personal_details", "address_details", "identity_documents", "additional_info"]

def get_db_connection():
    return sql.connect(
        server_hostname=DATABRICKS_SERVER_HOSTNAME,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN
    )

def get_chat_history(session_id, limit=5):
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
        for row in reversed(rows):
            history.append({"user": row.user_query, "ai": row.ai_response})
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"History Fetch Error: {e}")
    return history

def save_to_databricks(session_id, url, query, response):
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
        print(f"Databricks Insert Error: {e}")

def classify_intent_with_sarvam(query: str) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    
    system_prompt = """You are a classification agent. Classify the user's intent into exactly one of these THREE words:
    1. GENERAL: If the user is saying a greeting (like "hi" or "hello"), making small talk, or asking something completely unrelated to the website.
    2. KNOWLEDGE: If the user is asking "what is", asking for definitions, or seeking specific informational facts.
    3. NAVIGATION: If the user is asking "how to", asking for step-by-step guidance, form filling, or where to click next.
    Reply ONLY with the single word GENERAL, KNOWLEDGE, or NAVIGATION."""
    
    payload = {
        "model": "sarvam-30b", 
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            intent = response.json()["choices"][0]["message"]["content"].strip().upper()
            if "NAVIGATION" in intent: return "NAVIGATION"
            if "KNOWLEDGE" in intent: return "KNOWLEDGE"
            return "GENERAL"
        return "GENERAL" 
    except Exception:
        return "GENERAL"

def retrieve_neo4j_context(intent: str, current_url: str) -> str:
    if intent == "GENERAL":
        return "" 

    context_data = []
    with neo4j_driver.session() as session:
        if intent == "KNOWLEDGE":
            query = """
            MATCH (n:Page {url: $url})
            OPTIONAL MATCH (n)-[:HAS_SUBPATH]->(child:Page)
            RETURN n.summary AS current_summary, collect({url: child.url, summary: child.summary}) AS children
            """
            result = session.run(query, url=current_url).single()
            if result:
                context_data.append(f"Current Page Info: {result['current_summary']}")
                for child in result['children']:
                    if child['summary']:
                        context_data.append(f"Related Sub-page ({child['url']}): {child['summary']}")
                        
        elif intent == "NAVIGATION":
            query = """
            MATCH (n:Page {url: $url})-[:HAS_SUBPATH*1..3]->(descendant:Page {is_leaf: true})
            RETURN descendant.url AS url, descendant.summary AS summary
            LIMIT 5
            """
            results = session.run(query, url=current_url)
            context_data.append("Available actions and sub-paths from this location:")
            for record in results:
                 if record['summary']:
                     context_data.append(f"Path -> {record['url']} | Content: {record['summary']}")
                     
    return "\n".join(context_data) if context_data else "No specific graph context found for this URL."

def generate_final_answer(query: str, chat_history: list, context: str, intent: str) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    
    if intent == "NAVIGATION":
        sys_msg = f"You are a Government UI Assistant. Guide the user step-by-step based on the provided sub-paths. Context:\n{context}"
    elif intent == "KNOWLEDGE":
        sys_msg = f"You are a Government Knowledge Assistant. Answer the user's question directly using the provided context. Context:\n{context}"
    else:
        sys_msg = "You are a friendly and helpful Government AI Assistant. Respond conversationally to the user."

    messages = [{"role": "system", "content": sys_msg}]
    
    for exchange in chat_history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["ai"]})
        
    messages.append({"role": "user", "content": query})

    payload = {
        "model": "sarvam-30b", 
        "messages": messages,
        "max_tokens": 1500
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            error_msg = f"Sarvam API Error ({response.status_code}): {response.text}"
            print(f"❌ {error_msg}") 
            return error_msg
    except Exception as e:
        print(f"❌ Critical Sarvam Error: {e}")
        return f"Error connecting to Sarvam: {e}"

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing auth token")
    
    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={req.auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")
        
    token_info = token_resp.json()
    if token_info.get("aud") != "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com":
        raise HTTPException(status_code=401, detail="Unauthorized: Client ID mismatch")

    intent = classify_intent_with_sarvam(req.query)
    graph_context = retrieve_neo4j_context(intent, req.url)
    past_history = get_chat_history(req.session_id, limit=5)
    ai_answer = generate_final_answer(req.query, past_history, graph_context, intent)
    save_to_databricks(req.session_id, req.url, req.query, ai_answer)
    
    return {"answer": ai_answer, "intent_detected": intent}

@app.post("/sync")
def sync_local_storage(request: SyncRequest):
    if not request.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing auth token")

    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={request.auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")
        
    token_info = token_resp.json()
    user_email = token_info.get('email', 'Unknown Email')
    
    print(f"Syncing data for {user_email}")
    return {"status": "success"}

@app.get("/profile/status")
def check_profile_status(auth_token: str):
    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")
    token_info = token_resp.json()
    email = token_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")

    missing = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        for table in VALID_TABLES:
            cursor.execute(f"SELECT 1 FROM workspace.default.{table} WHERE email = ?", (email,))
            if not cursor.fetchone():
                missing.append(table)
        cursor.close()
        connection.close()
    except Exception as e:
        print("Status fetch error:", e)
    
    return {"missing_sections": missing}

@app.get("/profile/{table_name}")
def get_profile_data(table_name: str, auth_token: str):
    if table_name not in VALID_TABLES:
        raise HTTPException(status_code=400, detail="Invalid table name")

    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")
    token_info = token_resp.json()
    email = token_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")

    data = {}
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(f"SELECT * FROM workspace.default.{table_name} WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row))
        cursor.close()
        connection.close()
    except Exception as e:
        print("Fetch profile error:", e)
    
    return {"data": data}

@app.post("/profile/{table_name}")
def update_profile_data(table_name: str, req: ProfileUpdateRequest):
    if table_name not in VALID_TABLES:
        raise HTTPException(status_code=400, detail="Invalid table name")

    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={req.auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")
    token_info = token_resp.json()
    email = token_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")

    data = req.data
    if 'email' in data:
        del data['email']
        
    columns = ['email'] + list(data.keys())
    values = [email] + list(data.values())
    source_cols = ", ".join([f"? AS {col}" for col in columns])
    
    update_set = ", ".join([f"target.{col} = source.{col}" for col in data.keys()])
    if update_set:
        update_set += ", target.updated_at = current_timestamp()"
    
    insert_cols = ", ".join(columns) + ", updated_at"
    insert_vals = ", ".join([f"source.{col}" for col in columns]) + ", current_timestamp()"
    
    if table_name == "personal_details":
        insert_cols += ", created_at"
        insert_vals += ", current_timestamp()"

    if update_set:
        query_str = f'''
            MERGE INTO workspace.default.{table_name} AS target 
            USING (SELECT {source_cols}) AS source 
            ON target.email = source.email 
            WHEN MATCHED THEN UPDATE SET {update_set} 
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        '''
    else:
        query_str = f'''
            MERGE INTO workspace.default.{table_name} AS target 
            USING (SELECT {source_cols}) AS source 
            ON target.email = source.email 
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        '''
        
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(query_str, values)
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"Error in merge: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success"}

@app.get("/")
def read_root():
    return {"status": "online", "message": "API is running successfully!"}
