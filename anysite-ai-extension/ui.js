// Generate a unique session ID when the panel is opened
const currentSessionId = Date.now().toString() + Math.random().toString(36).substring(2);
function appendMessage(sender, text) {
  const log = document.getElementById('log');
  const msgDiv = document.createElement('div');
  msgDiv.className = `msg ${sender}`;
  msgDiv.textContent = text;
  log.appendChild(msgDiv);
  log.scrollTop = log.scrollHeight;
}

document.getElementById('btn').onclick = async () => {
  const inputField = document.getElementById('q');
  const q = inputField.value.trim();
  if (!q) return;
  
  appendMessage('user', q);
  inputField.value = '';

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

if (!tab || !tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('edge://')) {
    appendMessage('ai', "⚠️ I cannot read system pages. Please navigate to a normal website.");
    return; 
  }

chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => document.body.innerText 
  }, async (res) => { 
    // 🛡️ THE FIX: Check if Chrome blocked the injection or if the result is empty
    if (chrome.runtime.lastError || !res || !res[0]) {
      appendMessage('ai', "⚠️ Chrome's strict security is blocking me from reading this specific page (e.g., Chrome Web Store or internal pages).");
      return; // Stop here so it doesn't crash
    }

    const pageText = res[0].result;
    
    // Show a loading indicator
    const log = document.getElementById('log');
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'msg ai';
    loadingDiv.id = 'loading';
    loadingDiv.innerHTML = '<i>Processing...</i>';
    log.appendChild(loadingDiv);
    log.scrollTop = log.scrollHeight;

    try {
      const response = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: q,
          url: tab.url,
          session_id: currentSessionId,
          context: pageText
        })
      });

      const data = await response.json();
      document.getElementById('loading').remove();
      appendMessage('ai', data.answer);

    } catch (error) {
      document.getElementById('loading').remove();
      appendMessage('ai', "⚠️ Error: Could not connect to backend.");
      console.error(error);
    }
  });
};

document.getElementById('q').addEventListener('keypress', function (e) {
  if (e.key === 'Enter') {
    document.getElementById('btn').click();
  }
});