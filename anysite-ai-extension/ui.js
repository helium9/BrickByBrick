// Generate a unique session ID when the panel is opened
const currentSessionId =
  Date.now().toString() + Math.random().toString(36).substring(2);

let authToken = null;

const clientId =
  "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com"; // Safe to be public in Chrome Extensions
const redirectUri = chrome.identity.getRedirectURL();
console.log("Your EXACT Redirect URI is:", redirectUri);
const scopes = ["https://www.googleapis.com/auth/userinfo.email"];
const authUrl = `https://accounts.google.com/o/oauth2/auth?client_id=${clientId}&response_type=token&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(scopes.join(" "))}`;

chrome.identity.launchWebAuthFlow(
  {
    url: authUrl,
    interactive: true,
  },
  function (redirectUrl) {
    if (chrome.runtime.lastError) {
      console.error(
        "OAuth Error:",
        chrome.runtime.lastError.message || chrome.runtime.lastError,
      );
      appendMessage(
        "ai",
        `⚠️ Could not authenticate with Google: ${chrome.runtime.lastError.message || "Unknown error"}`,
      );
      return;
    }

    const urlParams = new URLSearchParams(
      new URL(redirectUrl).hash.substring(1),
    );
    authToken = urlParams.get("access_token");
    console.log("OAuth token received.");
    syncLocalStorage();
  },
);

async function syncLocalStorage() {
  if (!authToken) return;
  const data = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    data[key] = localStorage.getItem(key);
  }

  try {
    const response = await fetch("http://127.0.0.1:8000/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auth_token: authToken,
        payload: data,
      }),
    });
    if (response.ok) {
      console.log("Local storage synced with backend successfully.");
    }
  } catch (error) {
    console.error("Failed to sync storage:", error);
  }
}

function appendMessage(sender, text) {
  const log = document.getElementById("log");
  const msgDiv = document.createElement("div");
  msgDiv.className = `msg ${sender}`;
  msgDiv.textContent = text;
  log.appendChild(msgDiv);
  log.scrollTop = log.scrollHeight;
}

// 1. The Mini Markdown Parser
function renderMarkdown(text) {
  if (!text) return "";

  let html = text
    // 1. Bold text: **word** -> <strong>word</strong>
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")

    // 2. Italic text: *word* -> <em>word</em>
    .replace(/(?<!\*)\*(?!\*)(.*?)\*/g, "<em>$1</em>")

    // 3. Bullet points: * Item -> <li>Item</li>
    .replace(/^\s*[\*\-]\s+(.*)$/gm, "<li>$1</li>")

    // 4. Wrap consecutive <li> items in a <ul> block
    .replace(/(<li>.*?<\/li>(\s*|$))+/g, (match) => `<ul>${match}</ul>`)

    // 5. Line breaks: Convert \n to <br>, but ignore newlines inside lists
    .replace(/\n(?!<ul|<\/ul|<li|<\/li)/g, "<br>");

  return html;
}

// 2. The Updated appendMessage Function
function appendMessage(sender, text) {
  const log = document.getElementById("log");
  const msgDiv = document.createElement("div");
  msgDiv.className = `msg ${sender}`;

  if (sender === "ai") {
    // 🛡️ THE FIX: Render AI text as formatted HTML
    msgDiv.innerHTML = renderMarkdown(text);
  } else {
    // Keep user text raw for safety (prevents HTML injection if the user types code)
    msgDiv.textContent = text;
  }

  log.appendChild(msgDiv);
  log.scrollTop = log.scrollHeight; // Auto-scroll
}

document.getElementById("btn").onclick = async () => {
  if (!authToken) {
    appendMessage(
      "ai",
      "⚠️ Please wait for authentication to complete before sending messages.",
    );
    return;
  }

  const inputField = document.getElementById("q");
  const q = inputField.value.trim();
  if (!q) return;

  appendMessage("user", q);
  inputField.value = "";

  // Show a loading indicator immediately
  const log = document.getElementById("log");
  const loadingDiv = document.createElement("div");
  loadingDiv.className = "msg ai";
  loadingDiv.id = "loading";
  loadingDiv.innerHTML = "<i>Processing...</i>";
  log.appendChild(loadingDiv);
  log.scrollTop = log.scrollHeight;

  // We still grab the tab URL just so we can log it in Databricks, but we NO LONGER read the page text.
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const currentUrl = tab ? tab.url : "unknown";

  try {
    const response = await fetch("http://127.0.0.1:8000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: q,
        url: currentUrl,
        session_id: currentSessionId,
        auth_token: authToken,
      }),
    });

    const data = await response.json();
    document.getElementById("loading").remove();
    appendMessage("ai", data.answer);
  } catch (error) {
    document.getElementById("loading").remove();
    appendMessage("ai", "⚠️ Error: Could not connect to backend.");
    console.error(error);
  }
};

document.getElementById("q").addEventListener("keypress", function (e) {
  if (e.key === "Enter") {
    document.getElementById("btn").click();
  }
});
