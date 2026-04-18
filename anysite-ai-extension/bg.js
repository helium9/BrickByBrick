chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getAuthToken") {
    console.log("Background: Starting OAuth flow...");
    const manifest = chrome.runtime.getManifest();
    const clientId = manifest.oauth2.client_id;
    const redirectUri = chrome.identity.getRedirectURL();
    const scopes = ["https://www.googleapis.com/auth/userinfo.email"];
    const authUrl = `https://accounts.google.com/o/oauth2/auth?client_id=${clientId}&response_type=token&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(scopes.join(" "))}`;

    chrome.identity.launchWebAuthFlow(
      { url: authUrl, interactive: true },
      (redirectUrl) => {
        if (chrome.runtime.lastError) {
          console.error("Background OAuth Error:", chrome.runtime.lastError);
          sendResponse({ error: chrome.runtime.lastError.message });
          return;
        }
        if (redirectUrl) {
          const urlParams = new URLSearchParams(new URL(redirectUrl).hash.substring(1));
          const token = urlParams.get("access_token");
          sendResponse({ token });
        } else {
          sendResponse({ error: "Failed to get redirect URL" });
        }
      }
    );
    return true; // Keep message channel open for async response
  }
});