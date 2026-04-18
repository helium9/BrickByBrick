function extractPDFUrl(url) {
  if (!url) return null;

  const clean = url.split("?")[0].split("#")[0];

  // Direct PDF
  if (clean.toLowerCase().endsWith(".pdf")) {
    return url;
  }

  // Chrome PDF viewer case
  if (url.includes("viewer") && url.includes("file=")) {
    const match = url.match(/file=([^&]+)/);
    if (match && match[1]) {
      return decodeURIComponent(match[1]);
    }
  }

  return null;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "gen-ai-fill",
    title: "Gen AI Fill",
    contexts: ["page"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "gen-ai-fill") return;

  console.log("🔥 Gen AI Fill clicked");
  console.log("TAB:", tab);

  const currentUrl = tab?.url;

  try {
    // =========================
    // 📄 PDF FLOW (WORKS EVEN WITH tab.id = -1)
    // =========================
    const pdfUrl = extractPDFUrl(currentUrl);

    if (pdfUrl) {
      console.log("📄 PDF detected:", pdfUrl);

      const res = await fetch("http://127.0.0.1:8000/pdf-profile", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          pdf_url: pdfUrl
        })
      });

      const data = await res.json();

      console.log("✅ PDF generated:", data);

      // Optional: notify user (no tab messaging needed)
      return;
    }

    // =========================
    // 🌐 NON-PDF FLOW (REQUIRES VALID tab.id)
    // =========================
    if (!tab || tab.id === undefined || tab.id < 0) {
      console.error("❌ Invalid tab for HTML form");
      return;
    }

    // Force inject
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"]
    });

    console.log("✅ content.js injected");

    // small delay
    await new Promise(r => setTimeout(r, 150));

    // Get labels
    chrome.tabs.sendMessage(
      tab.id,
      { type: "GET_LABELS" },
      async (response) => {
        if (chrome.runtime.lastError) {
          console.error("❌ Messaging error:", chrome.runtime.lastError.message);
          return;
        }

        if (!response || !response.labels) {
          console.error("❌ No labels received");
          return;
        }

        const requiredFields = response.labels;
        console.log("📋 Labels:", requiredFields);

        // Call backend
        const res = await fetch("http://127.0.0.1:8000/profile", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            required_data: requiredFields
          })
        });

        const data = await res.json();

        console.log("✅ Filled data:", data);

        // Send back to content.js
        chrome.tabs.sendMessage(tab.id, {
          type: "FILLED_DATA",
          data: data
        });
      }
    );

  } catch (err) {
    console.error("❌ Fatal error:", err);
  }
});