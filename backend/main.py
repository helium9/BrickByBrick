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

# --- Configuration ---
DATABRICKS_SERVER_HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Initialize Neo4j Driver globally
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

class ChatRequest(BaseModel):
    session_id: str 
    query: str
    url: str
    auth_token: str | None = None

class SyncRequest(BaseModel):
    auth_token: str
    payload: dict

# --- Database Functions ---
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
        print(f"⚠️ Could not fetch history: {e}")
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
        print(f"❌ Databricks Insert Error: {e}")

# --- Agentic RAG Pipeline Functions ---

def classify_intent_with_sarvam(query: str) -> str:
    """Uses Sarvam to determine if the query is KNOWLEDGE or NAVIGATION."""
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    
    system_prompt = """You are a classification agent. Classify the user's intent into exactly one of these two words:
    1. KNOWLEDGE: If the user is asking "what is", asking for definitions, or general information.
    2. NAVIGATION: If the user is asking "how to", asking for step-by-step guidance, form filling, or where to click next.
    Reply ONLY with the single word KNOWLEDGE or NAVIGATION."""
    
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
            return "NAVIGATION" if "NAVIGATION" in intent else "KNOWLEDGE"
        return "KNOWLEDGE" # Default fallback
    except Exception:
        return "KNOWLEDGE"

def retrieve_neo4j_context(intent: str, current_url: str) -> str:
    """Fetches dynamic context from Neo4j based on the intent routing."""
    context_data = []
    
    with neo4j_driver.session() as session:
        if intent == "KNOWLEDGE":
            # Fetch current node and its immediate children (depth 1)
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
            # Fetch deeper paths for step-by-step guidance (depth 1 to 3)
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
    """Calls Sarvam with the retrieved context to answer the user."""
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    
    if intent == "NAVIGATION":
        sys_msg = f"You are a Government UI Assistant. Guide the user step-by-step based on the provided sub-paths. Context:\n{context}"
    else:
        sys_msg = f"You are a Government Knowledge Assistant. Answer the user's question directly using the provided context. Context:\n{context}"

    messages = [{"role": "system", "content": sys_msg}]
    
    for exchange in chat_history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["ai"]})
        
    messages.append({"role": "user", "content": query})

    payload = {
        "model": "sarvam-30b", 
        "messages": messages
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return "Error: Sarvam AI rejected the request."
    except Exception as e:
        return f"Error connecting to Sarvam: {e}"

# --- API Endpoints ---

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing auth token")
    
    try:
        idinfo = id_token.verify_oauth2_token(
            req.auth_token, 
            google_requests.Request(), 
            "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com"
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")

    # Step 1: Agentic Intent Classification
    intent = classify_intent_with_sarvam(req.query)
    
    # Step 2: Retrieve from Neo4j based on intent
    graph_context = retrieve_neo4j_context(intent, req.url)
    
    # Step 3: Get SQL History
    past_history = get_chat_history(req.session_id, limit=5)
    
    # Step 4: Final Synthesized Answer
    ai_answer = generate_final_answer(req.query, past_history, graph_context, intent)
    
    # Step 5: Save State
    save_to_databricks(req.session_id, req.url, req.query, ai_answer)
    
    return {"answer": ai_answer, "intent_detected": intent}

@app.post("/sync")
def sync_local_storage(request: SyncRequest):
    try:
        idinfo = id_token.verify_oauth2_token(
            request.auth_token, 
            google_requests.Request(), 
            "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com"
        )
        user_email = idinfo['email']
        print(f"Syncing data for {user_email}")
        return {"status": "success"}
    except ValueError:
        return {"error": "Invalid token"}, 401