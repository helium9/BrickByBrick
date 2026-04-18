from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from databricks import sql
import requests
import datetime
import logging
from dotenv import load_dotenv
import os
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from neo4j import GraphDatabase

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

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
GOOGLE_CLIENT_ID = "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com"

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


@app.on_event("startup")
def log_neo4j_indexes():
    try:
        with neo4j_driver.session() as session:
            result = session.run("SHOW INDEXES YIELD name, type, labelsOrTypes, properties")
            records = list(result)
            logger.info("=== Neo4j Indexes ===")
            for r in records:
                logger.info("  Index: name=%s type=%s labels=%s props=%s", r["name"], r["type"], r["labelsOrTypes"], r["properties"])
            if not records:
                logger.warning("No indexes found in Neo4j")
    except Exception as e:
        logger.error("Failed to list Neo4j indexes: %s", e)


class ChatRequest(BaseModel):
    session_id: str
    query: str
    url: str
    page_context: str | None = None
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


def get_chat_history(session_id, limit=3):
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
        logger.info("Fetched %d chat history entries for session=%s", len(history), session_id)
    except Exception as e:
        logger.error("Failed to fetch chat history: %s", e)
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
        logger.info("Saved chat to Databricks: session=%s", session_id)
    except Exception as e:
        logger.error("Failed to save to Databricks: %s", e)


def classify_intent_with_sarvam(query: str) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}

    system_prompt = """You are a strict classification agent. Classify the user query into exactly one of these three categories. Reply with ONLY the single word.

GENERAL - greetings like "hi", "hello", "thanks", small talk, or anything completely unrelated to a website or portal.
KNOWLEDGE - factual questions: "what is ITR-1", "who can file ITR-4", definitions, eligibility, rules, tax slabs, deadlines, policy explanations.
NAVIGATION - action-oriented: "how to file ITR", "where to download Form 16", "take me to e-filing", "I want to pay tax online", step-by-step guidance, wanting to reach a page or complete a task.

Examples:
"hi there" -> GENERAL
"what is form 26AS" -> KNOWLEDGE
"how do I file my return" -> NAVIGATION
"what are the due dates for ITR" -> KNOWLEDGE
"take me to e-verify" -> NAVIGATION
"thanks for the help" -> GENERAL
"where can I link my aadhaar" -> NAVIGATION
"explain section 80C" -> KNOWLEDGE

Reply ONLY: GENERAL, KNOWLEDGE, or NAVIGATION."""

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
            if "NAVIGATION" in intent:
                return "NAVIGATION"
            if "KNOWLEDGE" in intent:
                return "KNOWLEDGE"
            return "GENERAL"
        logger.error("Intent classification API returned status %d", response.status_code)
        return "GENERAL"
    except Exception as e:
        logger.error("Intent classification failed: %s", e)
        return "GENERAL"


def retrieve_neo4j_context(intent: str, current_url: str, query: str) -> str:
    context_data = []
    with neo4j_driver.session() as session:
        if intent == "KNOWLEDGE":
            search_query = """
            MATCH (p:Page)
            WHERE p.summary IS NOT NULL AND toLower(p.summary) CONTAINS toLower($search_term)
            RETURN p.url AS url, p.summary AS summary
            LIMIT 3
            """
            try:
                results = session.run(search_query, search_term=query)
                for record in results:
                    logger.info("KNOWLEDGE node: url=%s | summary=%s",
                                record["url"], (record["summary"][:120] if record["summary"] else "None"))
                    if record["summary"]:
                        context_data.append(f"Source ({record['url']}): {record['summary']}")
            except Exception as e:
                logger.error("KNOWLEDGE query failed: %s", e)

        elif intent == "NAVIGATION":
            logger.info("NAVIGATION PPR traversal starting from: %s", current_url)

            ppr_query = """
            MATCH path = (start:Page {url: $url})-[:HAS_SUBPATH*1..4]->(target:Page)
            WITH nodes(path) AS path_nodes, length(path) AS depth
            UNWIND range(0, size(path_nodes)-1) AS idx
            WITH path_nodes[idx] AS node, idx AS step, depth,
                 0.85 ^ idx AS ppr_score
            WITH node, step, max(ppr_score) AS best_score
            ORDER BY best_score DESC
            LIMIT 10
            RETURN node.url AS url, node.summary AS summary, best_score, step
            """
            try:
                results = session.run(ppr_query, url=current_url)
                records = list(results)

                if not records:
                    logger.info("No subpaths found from: %s", current_url)
                    context_data.append("No navigation paths found from the current page.")
                else:
                    logger.info("Found %d nodes in PPR traversal", len(records))
                    context_data.append("Navigation subgraph from current page (ordered by relevance):")
                    for record in records:
                        logger.info("  PPR node: url=%s | step=%d | ppr=%.4f | summary=%s",
                                    record["url"], record["step"], record["best_score"],
                                    (record["summary"][:100] if record["summary"] else "None"))
                        summary_part = f" | {record['summary']}" if record["summary"] else ""
                        context_data.append(f"- Step {record['step']}: {record['url']}{summary_part}")

            except Exception as e:
                logger.error("NAVIGATION PPR query failed: %s", e)

    logger.info("Final context built with %d entries", len(context_data))
    return "\n".join(context_data) if context_data else "No graph context found."


def generate_final_answer(query: str, chat_history: list, context: str, intent: str, page_context: str | None = None) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {SARVAM_API_KEY}"}

    if intent == "NAVIGATION":
        sys_msg = f"""You are a Navigation Assistant for the Indian Income Tax portal (incometax.gov.in).

RULES:
1. ONLY use URLs from the "Navigation Context" below. Never invent or guess URLs.
2. If the context contains a step-by-step subgraph of links, present the full path the user should follow as a numbered list with each step as a clickable markdown link.
3. If there is a single "Direct match" URL that exactly answers the query, respond with just that one link.
4. If no relevant paths exist in the context, reply: "No direct navigation path found from this page. Try navigating from the homepage."
5. Include the page summary next to each link so the user knows what each page contains.
6. Do not use emojis. Keep responses concise.

Navigation Context:
{context}"""

    elif intent == "KNOWLEDGE":
        sys_msg = f"""You are a Knowledge Assistant for the Indian Income Tax portal (incometax.gov.in).

RULES:
1. Answer strictly using ONLY the source material below. Do not use outside knowledge.
2. Never mention the IRS or US taxes. You handle Indian taxes exclusively.
3. If the source material does not contain the answer, reply: "I do not have that information in my current database."
4. Keep answers concise (2-3 bullet points max).
5. Do not use emojis.

Source Material:
{context}"""

    else:
        sys_msg = "You are a friendly browser assistant for the Indian Income Tax portal. Greet the user in one short sentence. Do not use emojis."

    if page_context:
        sys_msg += f"\n\nContext from User's Current Page:\n{page_context}"

    messages = [{"role": "system", "content": sys_msg}]

    for exchange in chat_history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["ai"]})

    messages.append({"role": "user", "content": query})

    payload = {
        "model": "sarvam-30b",
        "messages": messages
    }

    logger.info("Sending to LLM: intent=%s, history_pairs=%d, context_len=%d",
                intent, len(chat_history), len(context))

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            logger.info("LLM response received, length=%d", len(answer))
            return answer
        else:
            logger.error("LLM API error: status=%d body=%s", response.status_code, response.text[:200])
            return f"API Error ({response.status_code}): {response.text}"
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return f"Connection error: {e}"


@app.post("/chat")
async def chat_endpoint(req: ChatRequest, background_tasks: BackgroundTasks):
    logger.info("=== /chat request: session=%s url=%s query=%s ===", req.session_id, req.url, req.query[:80])

    if not req.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing auth token")

    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={req.auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")

    token_info = token_resp.json()
    if token_info.get("aud") != GOOGLE_CLIENT_ID:
        logger.error("Client ID mismatch: expected=%s got=%s", GOOGLE_CLIENT_ID, token_info.get("aud"))
        raise HTTPException(status_code=401, detail="Unauthorized: Client ID mismatch")

    intent = classify_intent_with_sarvam(req.query)
    logger.info("Intent classified: %s", intent)

    past_history = []
    if intent != "GENERAL":
        past_history = get_chat_history(req.session_id, limit=3)

    if intent == "GENERAL":
        ai_answer = generate_final_answer(req.query, [], "", intent, req.page_context)
    else:
        graph_context = retrieve_neo4j_context(intent, req.url, req.query)
        ai_answer = generate_final_answer(req.query, past_history, graph_context, intent, req.page_context)

    background_tasks.add_task(save_to_databricks, req.session_id, req.url, req.query, ai_answer)

    logger.info("=== /chat response sent: intent=%s ===", intent)
    return {"answer": ai_answer, "intent_detected": intent}


@app.post("/sync")
def sync_local_storage(request: SyncRequest):
    logger.info("=== /sync request received ===")
    if not request.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing auth token")

    token_resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={request.auth_token}")
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")

    logger.info("Sync completed successfully")
    return {"status": "success"}


@app.get("/")
def read_root():
    return {"status": "online", "message": "API is running"}