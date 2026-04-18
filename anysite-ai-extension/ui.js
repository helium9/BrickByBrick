let currentSessionId = Date.now().toString() + Math.random().toString(36).substring(2);

let authToken = null;
let appReady = false;

// --- 1. Strict 2-second splash, then reveal chatbot ---
setTimeout(() => {
  if (window.hideSplash) window.hideSplash();
  appReady = true;
  // If OAuth failed before UI was ready, show the error now
  if (window._pendingAuthError) {
    appendMessage("ai", window._pendingAuthError);
    window._pendingAuthError = null;
  }
}, 2000);

// --- 2. OAuth runs in parallel (does NOT block UI) ---
chrome.runtime.sendMessage({ action: "getAuthToken" }, (response) => {
  if (chrome.runtime.lastError || !response || response.error) {
    const errMsg = `Authentication failed: ${chrome.runtime.lastError?.message || response?.error || "Unknown error"}`;
    console.error("OAuth Error:", errMsg);
    // If chatbot is already visible, show error immediately; otherwise queue it
    if (appReady) {
      appendMessage("ai", errMsg);
    } else {
      window._pendingAuthError = errMsg;
    }
    return;
  }

  authToken = response.token;
  console.log("OAuth token received from background.");
  syncLocalStorage();
});

async function syncLocalStorage() {
  if (!authToken) return;
  const data = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    data[key] = localStorage.getItem(key);
  }

  try {
    const response = await fetch("https://fastapi-app-7474650190496857.aws.databricksapps.com/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auth_token: authToken,
        payload: data,
      }),
    });
    if (response.ok) {
      console.log("Local storage synced.");
    }
  } catch (error) {
    console.error("Failed to sync storage:", error);
  }
}

function renderMarkdown(text) {
  if (!text) return "";

  let html = text
    .replace(/\[([^\]]+)\]\(([^\)]+)\)/g, '<a href="$2" target="_blank" style="color: #E8311A; text-decoration: underline; text-underline-offset: 2px;">$1</a>')
    .replace(/^###\s+(.*)$/gm, '<h3 style="margin: 8px 0; font-size: 14px;">$1</h3>')
    .replace(/^##\s+(.*)$/gm, '<h2 style="margin: 8px 0; font-size: 16px;">$1</h2>')
    .replace(/^#\s+(.*)$/gm, '<h1 style="margin: 8px 0; font-size: 18px;">$1</h1>')
    .replace(/^---$/gm, '<hr style="margin: 10px 0; border: 0; border-top: 1px solid rgba(255,255,255,0.2);" />')
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\*)(.*?)\*/g, "<em>$1</em>")
    .replace(/^\s*[\*\-]\s+(.*)$/gm, "<li>$1</li>")
    .replace(/(<li>.*?<\/li>(\s*|$))+/g, (match) => `<ul style="margin: 5px 0; padding-left: 20px;">${match}</ul>`)
    .replace(/\n(?!<ul|<\/ul|<li|<\/li|<h|<hr)/g, "<br>");

  return html;
}

function appendMessage(sender, text) {
  const log = document.getElementById("log");
  const msgDiv = document.createElement("div");
  msgDiv.className = `msg ${sender}`;

  if (sender === "ai") {
    msgDiv.innerHTML = renderMarkdown(text);
  } else {
    msgDiv.textContent = text;
  }

  log.appendChild(msgDiv);
  log.scrollTop = log.scrollHeight;
}

document.getElementById("btn").onclick = async () => {
  if (!authToken) {
    appendMessage("ai", "Please wait for authentication to complete before sending messages.");
    return;
  }

  const inputField = document.getElementById("q");
  const q = inputField.value.trim();
  if (!q) return;

  appendMessage("user", q);
  inputField.value = "";

  const log = document.getElementById("log");
  const loadingDiv = document.createElement("div");
  loadingDiv.className = "msg ai";
  loadingDiv.id = "loading";
  loadingDiv.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  log.appendChild(loadingDiv);
  log.scrollTop = log.scrollHeight;

  try {
    let currentUrl = "unknown";
    let pageContext = "";
    if (chrome.tabs) {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) {
        currentUrl = tab.url || "unknown";
        try {
          const results = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => document.body.innerText.substring(0, 5000)
          });
          if (results && results[0] && results[0].result) {
            pageContext = results[0].result;
          }
        } catch (e) {
          console.warn("Could not retrieve page text context:", e);
        }
      }
    }

    const response = await fetch("https://fastapi-app-7474650190496857.aws.databricksapps.com/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: q,
        url: currentUrl,
        page_context: pageContext,
        session_id: currentSessionId,
        auth_token: authToken,
      }),
    });

    const data = await response.json();
    document.getElementById("loading").remove();
    appendMessage("ai", data.answer);

  } catch (error) {
    document.getElementById("loading").remove();
    appendMessage("ai", "Error: Could not connect to backend.");
    console.error("Chat Error:", error);
  }
};

document.getElementById("q").addEventListener("keypress", function (e) {
  if (e.key === "Enter") {
    document.getElementById("btn").click();
  }
});

document.getElementById("new-chat-btn")?.addEventListener("click", () => {
  const log = document.getElementById("log");
  log.innerHTML = '<div class="msg ai">Welcome. Ask me anything about navigating this page or finding government services.</div>';
  currentSessionId = Date.now().toString() + Math.random().toString(36).substring(2);
});

document.querySelectorAll(".suggestion-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const q = chip.textContent;
    const inputField = document.getElementById("q");
    inputField.value = q;
    document.getElementById("btn").click();
  });
});