import { isFreshCapture } from "./oauth_redirect.js";

const STORAGE_KEY = "somtodayRedirect";
const waiting = document.querySelector("#waiting");
const captured = document.querySelector("#captured");
const redirect = document.querySelector("#redirect");
const copyButton = document.querySelector("#copy");
const clearButton = document.querySelector("#clear");
const status = document.querySelector("#status");

let capture = null;

copyButton.addEventListener("click", async () => {
  if (!capture) {
    return;
  }

  const copied = await copyText(capture.redirectUrl);
  if (!copied) {
    redirect.focus();
    redirect.select();
    status.textContent = "Automatisch kopiëren lukte niet. Het adres is geselecteerd; druk op Ctrl+C.";
    return;
  }

  await consumeCapture();
  status.textContent = "Gekopieerd en uit de helper gewist. Plak het nu in Home Assistant.";
  copyButton.disabled = true;
  clearButton.disabled = true;
});

clearButton.addEventListener("click", async () => {
  if (!capture) {
    return;
  }
  await consumeCapture();
  showWaiting();
});

await loadCapture();

async function loadCapture() {
  const stored = await chrome.storage.session.get(STORAGE_KEY);
  if (!isFreshCapture(stored[STORAGE_KEY])) {
    if (stored[STORAGE_KEY]) {
      await chrome.storage.session.remove(STORAGE_KEY);
    }
    showWaiting();
    return;
  }

  capture = stored[STORAGE_KEY];
  redirect.value = capture.redirectUrl;
  waiting.hidden = true;
  captured.hidden = false;
}

async function consumeCapture() {
  await chrome.runtime.sendMessage({
    type: "consume_capture",
    expiresAt: capture.expiresAt,
  });
  capture = null;
}

function showWaiting() {
  capture = null;
  redirect.value = "";
  status.textContent = "";
  waiting.hidden = false;
  captured.hidden = true;
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    // The user can still copy the selected readonly field manually. No
    // clipboard permission is requested solely to hide this fallback.
    return false;
  }
}
