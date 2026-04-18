const currentSessionId =
  Date.now().toString() + Math.random().toString(36).substring(2);

let authToken = null;

const clientId =
  "793504204288-6llr8actft5lg39atdblgat9vmadq4su.apps.googleusercontent.com";
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
      if (typeof window.hideSplash === "function") window.hideSplash();
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
    if (typeof window.hideSplash === "function") window.hideSplash();
    checkProfileStatus();
  },
);

async function checkProfileStatus() {
  if (!authToken) return;
  try {
    const fetchHeaders = { "Content-Type": "application/json" };
    if (CONFIG.DATABRICKS_PAT) {
      fetchHeaders["Authorization"] = `Bearer ${CONFIG.DATABRICKS_PAT}`;
    }

    const response = await fetch(
      `${CONFIG.BACKEND_URL}/profile/status?auth_token=${authToken}`,
      {
        headers: fetchHeaders,
      },
    );

    if (response.ok) {
      const data = await response.json();
      if (data.missing_sections && data.missing_sections.length > 0) {
        appendMessage(
          "ai",
          `**Profile Incomplete**<br>Please complete the following sections from the top menu: ${data.missing_sections.join(", ").replace(/_/g, " ")}`,
        );
      }
    }
  } catch (err) {
    console.error("Could not check profile status:", err);
  }
}

// 1. The Mini Markdown Parser
function renderMarkdown(text) {
  if (!text) return "";

  let html = text
    .replace(
      /\[([^\]]+)\]\(([^\)]+)\)/g,
      '<a href="$2" target="_blank" style="color: #1a73e8; text-decoration: underline;">$1</a>',
    )
    .replace(
      /^###\s+(.*)$/gm,
      '<h3 style="margin: 8px 0; font-size: 14px;">$1</h3>',
    )
    .replace(
      /^##\s+(.*)$/gm,
      '<h2 style="margin: 8px 0; font-size: 16px;">$1</h2>',
    )
    .replace(
      /^#\s+(.*)$/gm,
      '<h1 style="margin: 8px 0; font-size: 18px;">$1</h1>',
    )
    .replace(
      /^---$/gm,
      '<hr style="margin: 10px 0; border: 0; border-top: 1px solid #ddd;" />',
    )
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\*)(.*?)\*/g, "<em>$1</em>")
    .replace(/^\s*[\*\-]\s+(.*)$/gm, "<li>$1</li>")
    .replace(
      /(<li>.*?<\/li>(\s*|$))+/g,
      (match) => `<ul style="margin: 5px 0; padding-left: 20px;">${match}</ul>`,
    )
    .replace(/\n(?!<ul|<\/ul|<li|<\/li|<h|<hr)/g, "<br>");

  return html;
}

// 2. The Single, Corrected appendMessage Function
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

// Ripple Effect
function createRipple(event) {
  const button = event.currentTarget;
  const ripple = document.createElement("span");
  const diameter = Math.max(button.clientWidth, button.clientHeight);
  const radius = diameter / 2;

  ripple.style.width = ripple.style.height = `${diameter}px`;
  ripple.style.left = `${event.clientX - button.getBoundingClientRect().left - radius}px`;
  ripple.style.top = `${event.clientY - button.getBoundingClientRect().top - radius}px`;
  ripple.classList.add("ripple");

  const existingRipple = button.querySelector(".ripple");
  if (existingRipple) existingRipple.remove();

  button.appendChild(ripple);
}

document.getElementById("btn").addEventListener("mousedown", createRipple);
const chips = document.querySelectorAll(".suggestion-chip");
chips.forEach((chip) => {
  chip.addEventListener("mousedown", createRipple);
  chip.addEventListener("click", () => {
    document.getElementById("q").value = chip.textContent;
    document.getElementById("btn").click();
  });
});

document.getElementById("new-chat-btn").addEventListener("click", () => {
  const log = document.getElementById("log");
  log.innerHTML =
    '<div class="msg ai">New conversation started. How can I help you today?</div>';
});

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

  const log = document.getElementById("log");
  const loadingDiv = document.createElement("div");
  loadingDiv.className = "msg ai code-block";
  loadingDiv.id = "loading";
  loadingDiv.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
  log.appendChild(loadingDiv);
  log.scrollTop = log.scrollHeight;

  try {
    let currentUrl = "unknown";
    if (chrome.tabs) {
      const [tab] = await chrome.tabs.query({
        active: true,
        currentWindow: true,
      });
      currentUrl = tab ? tab.url : "unknown";
    }

    const fetchHeaders = { "Content-Type": "application/json" };
    if (CONFIG.DATABRICKS_PAT) {
      fetchHeaders["Authorization"] = `Bearer ${CONFIG.DATABRICKS_PAT}`;
    }

    const response = await fetch(`${CONFIG.BACKEND_URL}/chat`, {
      method: "POST",
      headers: fetchHeaders,
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
    console.error("Chat Error:", error);
  }
};

document.getElementById("q").addEventListener("keypress", function (e) {
  if (e.key === "Enter") {
    document.getElementById("btn").click();
  }
});

// Profile Management Logic
const SCHEMAS = {
  personal_details: [
    { id: "full_name", label: "Full Name", type: "text" },
    { id: "dob", label: "Date of Birth", type: "date" },
    { id: "gender", label: "Gender", type: "text" },
    { id: "marital_status", label: "Marital Status", type: "text" },
  ],
  address_details: [
    { id: "street_address", label: "Street Address", type: "text" },
    { id: "city", label: "City", type: "text" },
    { id: "state", label: "State", type: "text" },
    { id: "postal_code", label: "Postal Code", type: "text" },
    { id: "country", label: "Country", type: "text" },
  ],
  identity_documents: [
    { id: "aadhaar_number", label: "Aadhaar Number", type: "text" },
    { id: "pan_number", label: "PAN Number", type: "text" },
    { id: "passport_number", label: "Passport Number", type: "text" },
    { id: "voter_id", label: "Voter ID", type: "text" },
  ],
  additional_info: [
    { id: "occupation", label: "Occupation", type: "text" },
    { id: "annual_income", label: "Annual Income", type: "number" },
    { id: "education_level", label: "Education Level", type: "text" },
  ],
};

const viewSelector = document.getElementById("view-selector");
const logBox = document.getElementById("log");
const inputContainer = document.getElementById("input-container");
const formContainer = document.getElementById("form-container");
const formContent = document.getElementById("form-content");
const saveFormBtn = document.getElementById("save-form-btn");

viewSelector.addEventListener("change", (e) => {
  const val = e.target.value;
  if (val === "chat") {
    formContainer.style.display = "none";
    logBox.style.display = "flex";
    inputContainer.style.display = "flex";
  } else {
    logBox.style.display = "none";
    inputContainer.style.display = "none";
    formContainer.style.display = "flex";
    renderForm(val);
  }
});

async function renderForm(tableName) {
  formContent.innerHTML = "<div style='color:#fff;'>Loading...</div>";
  saveFormBtn.dataset.table = tableName;

  const schema = SCHEMAS[tableName];
  if (!schema) return;

  let formData = {};
  if (authToken) {
    try {
      const fetchHeaders = {};
      if (CONFIG.DATABRICKS_PAT) {
        fetchHeaders["Authorization"] = `Bearer ${CONFIG.DATABRICKS_PAT}`;
      }

      const res = await fetch(
        `${CONFIG.BACKEND_URL}/profile/${tableName}?auth_token=${authToken}`,
        {
          headers: fetchHeaders,
        },
      );
      if (res.ok) {
        const json = await res.json();
        formData = json.data || {};
      }
    } catch (e) {
      console.error(e);
    }
  }

  let html = "";
  schema.forEach((field) => {
    let value = formData[field.id] || "";
    html += `
      <div class="form-group">
        <label for="${field.id}">${field.label}</label>
        <input type="${field.type}" id="${field.id}" value="${value}" autocomplete="off" />
      </div>
    `;
  });
  formContent.innerHTML = html;
}

saveFormBtn.addEventListener("click", async () => {
  if (!authToken) {
    alert("Please wait for authentication.");
    return;
  }
  const tableName = saveFormBtn.dataset.table;
  const schema = SCHEMAS[tableName];
  if (!schema) return;

  const data = {};
  let emptyCount = 0;
  schema.forEach((field) => {
    const val = document.getElementById(field.id).value.trim();
    if (!val) emptyCount++;
    data[field.id] = val;
  });

  if (emptyCount === schema.length) {
    alert("Cannot save completely empty form.");
    return;
  }

  saveFormBtn.textContent = "Saving...";
  saveFormBtn.disabled = true;

  try {
    const fetchHeaders = { "Content-Type": "application/json" };
    if (CONFIG.DATABRICKS_PAT) {
      fetchHeaders["Authorization"] = `Bearer ${CONFIG.DATABRICKS_PAT}`;
    }

    const res = await fetch(`${CONFIG.BACKEND_URL}/profile/${tableName}`, {
      method: "POST",
      headers: fetchHeaders,
      body: JSON.stringify({
        auth_token: authToken,
        data: data,
      }),
    });
    if (res.ok) {
      saveFormBtn.textContent = "Saved ✓";
      setTimeout(() => {
        saveFormBtn.textContent = "Save";
        saveFormBtn.disabled = false;
      }, 2000);
    } else {
      throw new Error("Failed");
    }
  } catch (e) {
    console.error(e);
    saveFormBtn.textContent = "Error";
    setTimeout(() => {
      saveFormBtn.textContent = "Save";
      saveFormBtn.disabled = false;
    }, 2000);
  }
});
