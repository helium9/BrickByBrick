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
    except Exception:
        pass
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
    except Exception:
        pass

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

def get_query_embedding(text: str) -> list:
    url = "https://api.sarvam.ai/v1/embeddings"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    try:
        response = requests.post(url, json={"input": text}, headers=headers)
        if response.status_code == 200:
            return response.json()["data"][0]["embedding"]
    except Exception:
        pass
    return [0.0] * 768

def retrieve_neo4j_context(intent: str, current_url: str, query: str) -> str:
    context_data = []
    with neo4j_driver.session() as session:
        if intent == "KNOWLEDGE":
            query_vector = get_query_embedding(query)
            cypher_query = """
            CALL db.index.vector.queryNodes('summary_index', 1, $query_vector)
            YIELD node, score
            RETURN node.summary AS current_summary, node.url AS url
            """
            try:
                result = session.run(cypher_query, query_vector=query_vector).single()
                if result and result['current_summary']:
                    context_data.append(f"Fact Sheet ({result['url']}): {result['current_summary']}")
            except Exception:
                pass
                        
        elif intent == "NAVIGATION":
            cypher_query = """
            MATCH path = (start:Page {url: $url})-[:HAS_SUBPATH*1..4]->(target:Page {is_leaf: true})
            WITH target, 0.85 ^ length(path) AS path_score
            WITH target, sum(path_score) AS ppr_score
            ORDER BY ppr_score DESC
            LIMIT 5
            RETURN target.url AS url, target.summary AS summary
            """
            try:
                results = session.run(cypher_query, url=current_url)
                context_data.append("Available actions and sub-paths from this location:")
                for record in results:
                     if record['summary']:
                         context_data.append(f"Path -> {record['url']} | Content: {record['summary']}")
            except Exception:
                pass
                     
    return "\n".join(context_data) if context_data else "No specific graph context found for this query or URL."

def generate_final_answer(query: str, chat_history: list, context: str, intent: str) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions" 
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}
    
    if intent == "NAVIGATION":
        sys_msg = f"""You are a Navigation Assistant for the Indian Income Tax portal (incometax.gov.in).
        CRITICAL RULES:
        1. STRICT GROUNDING: You MUST ONLY suggest URLs explicitly listed in the "Available Paths" below. 
        2. NO HALLUCINATION: Never mention the IRS, US forms (like 1065), or external sites. 
        3. IF NO MATCH: If the paths below are empty or irrelevant, you MUST reply: "There are no direct links for that from this specific page. Please try from the homepage."
        4. FORMAT: Output 1-2 very short steps. You MUST format URLs as Markdown links: [Click here to proceed](URL).
        
        Available Paths:
        {context}"""
        
    elif intent == "KNOWLEDGE":
        sys_msg = f"""You are a Knowledge Assistant for the Indian Income Tax portal (incometax.gov.in).
        CRITICAL RULES:
        1. STRICT GROUNDING: Answer strictly using ONLY the "Fact Sheet" below. Do NOT use outside knowledge.
        2. NO HALLUCINATION: Never mention the IRS or US taxes. You handle Indian taxes exclusively.
        3. IF NO MATCH: If the Fact Sheet is empty or doesn't contain the answer, you MUST reply: "I do not have that information in my current database."
        4. FORMAT: Keep it extremely short (1-2 bullet points maximum).
        
        Fact Sheet:
        {context}"""
        
    else:
        sys_msg = "You are a friendly browser assistant for the Indian Income Tax portal. Greet the user in 1 short sentence."

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
            return f"Sarvam API Error ({response.status_code}): {response.text}"
    except Exception as e:
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
    past_history = get_chat_history(req.session_id, limit=5)

    if intent == "GENERAL":
        ai_answer = generate_final_answer(req.query, past_history, "", intent)
    else:
        graph_context = retrieve_neo4j_context(intent, req.url, req.query)
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
        
    return {"status": "success"}

@app.get("/")
def read_root():
    return {"status": "online", "message": "API is running successfully!"}