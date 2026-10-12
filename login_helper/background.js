import {
  createCapture,
  findHeader,
  isFreshCapture,
  parseSomtodayCallback,
} from "./oauth_redirect.js";

export const STORAGE_KEY = "somtodayRedirect";
export const EXPIRY_ALARM = "expireSomtodayRedirect";

const SOMTODAY_LOGIN_FILTER = {
  urls: ["https://inloggen.somtoday.nl/*"],
  types: ["main_frame"],
};

// Listener registration must happen synchronously at module evaluation time;
// an MV3 service worker can be stopped and recreated between browser events.
chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    void captureRedirectFromHeaders(details.responseHeaders);
  },
  SOMTODAY_LOGIN_FILTER,
  ["responseHeaders"],
);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === EXPIRY_ALARM) {
    void clearExpiredCapture();
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "consume_capture") {
    return false;
  }

  consumeCapture(message.expiresAt)
    .then(() => sendResponse({ ok: true }))
    .catch(() => sendResponse({ ok: false }));
  return true;
});

// Session storage is memory-only and remains inaccessible to content scripts.
void chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
void clearExpiredCapture();

async function captureRedirectFromHeaders(headers) {
  const location = findHeader(headers, "location");
  const callback = parseSomtodayCallback(location);
  if (!callback) {
    return;
  }

  const capture = createCapture(callback.redirectUrl);
  await chrome.storage.session.set({ [STORAGE_KEY]: capture });
  await chrome.alarms.create(EXPIRY_ALARM, { when: capture.expiresAt });
  await chrome.action.setBadgeBackgroundColor({ color: "#138A36" });
  await chrome.action.setBadgeText({ text: "1" });
  await chrome.action.setTitle({ title: "Somtoday-login gevonden" });
}

async function clearExpiredCapture(now = Date.now()) {
  const stored = await chrome.storage.session.get(STORAGE_KEY);
  if (!stored[STORAGE_KEY] || isFreshCapture(stored[STORAGE_KEY], now)) {
    return;
  }
  await clearCapture();
}

async function consumeCapture(expectedExpiresAt) {
  const stored = await chrome.storage.session.get(STORAGE_KEY);
  const capture = stored[STORAGE_KEY];

  // Do not let an old popup erase a newer login captured after it was opened.
  if (capture?.expiresAt === expectedExpiresAt) {
    await clearCapture();
  }
}

async function clearCapture() {
  await chrome.storage.session.remove(STORAGE_KEY);
  await chrome.alarms.clear(EXPIRY_ALARM);
  await chrome.action.setBadgeText({ text: "" });
  await chrome.action.setTitle({ title: "Somtoday Login Helper" });
}
