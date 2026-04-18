// =========================
// LABEL EXTRACTION
// =========================
function getAllLabels() {
  const inputs = document.querySelectorAll("input, textarea, select");
  const labels = [];

  inputs.forEach(input => {
    const type = input.type?.toLowerCase();

    if (
      type === "hidden" ||
      input.name?.includes("captcha") ||
      input.id?.includes("captcha") ||
      input.name?.includes("recaptcha") ||
      input.id?.includes("recaptcha")
    ) return;

    let labelText = "";

    // 1. aria-labelledby
    const labelledBy = input.getAttribute("aria-labelledby");
    if (labelledBy) {
      for (const id of labelledBy.split(" ")) {
        const el = document.getElementById(id);
        if (el?.innerText?.trim()) {
          labelText = el.innerText.trim();
          break;
        }
      }
    }

    // 2. Angular Material
    if (!labelText) {
      const formField = input.closest("mat-form-field");
      const wrapper = formField?.parentElement;

      let prev = wrapper?.previousElementSibling;
      while (prev) {
        const matLabel = prev.querySelector("mat-label");
        if (matLabel?.innerText?.trim()) {
          labelText = matLabel.innerText.trim();
          break;
        }
        prev = prev.previousElementSibling;
      }
    }

    // 3. normal label
    if (!labelText) {
      const label = document.querySelector(`label[for="${input.id}"]`);
      if (label) labelText = label.innerText.trim();
    }

    // 4. fallback
    if (!labelText) {
      labelText =
        input.placeholder ||
        input.name ||
        input.id ||
        input.getAttribute("aria-label") ||
        "";
    }

    if (labelText) {
      labels.push(labelText.replace("*", "").trim());
    }
  });

  console.log("📋 Extracted fields:", labels);
  return labels;
}


// =========================
// MESSAGE LISTENER
// =========================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  try {
    if (message.type === "GET_LABELS") {
      const labels = getAllLabels();
      sendResponse({ labels });
      return true;
    }

    if (message.type === "FILLED_DATA") {
      console.log("🔥 Filled data received:", message.data);
      autofillForm(message.data);
      return true;
    }

  } catch (err) {
    console.error("❌ content.js error:", err);
  }

  return true;
});


// =========================
// NORMALIZATION
// =========================
function normalizeValue(labelText, value) {
  const key = labelText.toLowerCase();

  const monthMap = {
    "01": "January", "1": "January",
    "02": "February", "2": "February",
    "03": "March", "3": "March",
    "04": "April", "4": "April",
    "05": "May", "5": "May",
    "06": "June", "6": "June",
    "07": "July", "7": "July",
    "08": "August", "8": "August",
    "09": "September", "9": "September",
    "10": "October",
    "11": "November",
    "12": "December"
  };

  if (key.includes("month")) {
    return monthMap[value] || value;
  }

  return value;
}


// =========================
// AUTOFILL
// =========================
function autofillForm(data) {
  const inputs = document.querySelectorAll("input, textarea, select");

  inputs.forEach(input => {
    const type = input.type?.toLowerCase();

    if (
      type === "hidden" ||
      input.name?.includes("captcha") ||
      input.id?.includes("captcha") ||
      input.name?.includes("recaptcha") ||
      input.id?.includes("recaptcha")
    ) return;

    let labelText = "";

    const labelledBy = input.getAttribute("aria-labelledby");
    if (labelledBy) {
      for (const id of labelledBy.split(" ")) {
        const el = document.getElementById(id);
        if (el?.innerText?.trim()) {
          labelText = el.innerText.trim();
          break;
        }
      }
    }

    if (!labelText) {
      const label = document.querySelector(`label[for="${input.id}"]`);
      if (label) labelText = label.innerText.trim();
    }

    if (!labelText) {
      labelText =
        input.placeholder ||
        input.name ||
        input.id ||
        input.getAttribute("aria-label") ||
        "";
    }

    labelText = labelText.replace("*", "").trim();

    if (data[labelText]) {
      const finalValue = normalizeValue(labelText, data[labelText]);

      input.focus();
      input.value = finalValue;

      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));

      console.log(`✅ Filled ${labelText}`);
    }
  });
}