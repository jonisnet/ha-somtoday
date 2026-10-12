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
    void captureRedirect(
      findHeader(details.responseHeaders, "location"),
      details.tabId,
    );
  },
  SOMTODAY_LOGIN_FILTER,
  ["responseHeaders", "extraHeaders"],
);

// Chrome exposes the resolved target of a server redirect directly here. This
// is the primary path; the response-header listener above remains as a fallback
// for browser versions that do not report a custom-scheme redirect event.
chrome.webRequest.onBeforeRedirect.addListener(
  (details) => {
    void captureRedirect(details.redirectUrl, details.tabId);
  },
  SOMTODAY_LOGIN_FILTER,
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

async function captureRedirect(redirectUrl, tabId = -1) {
  const callback = parseSomtodayCallback(redirectUrl);
  if (!callback) {
    return;
  }

  const capture = createCapture(callback.redirectUrl);
  await chrome.storage.session.set({ [STORAGE_KEY]: capture });
  await chrome.alarms.create(EXPIRY_ALARM, { when: capture.expiresAt });
  await chrome.action.setBadgeBackgroundColor({ color: "#138A36" });
  await chrome.action.setBadgeText({ text: "1" });
  await chrome.action.setTitle({ title: "Somtoday-login gevonden" });

  // Make success visible even when Chrome silently discards the custom scheme
  // and the extension is hidden behind the puzzle-piece menu. Navigating a tab
  // does not require the broad `tabs` permission.
  if (Number.isInteger(tabId) && tabId >= 0) {
    try {
      await chrome.tabs.update(tabId, {
        url: chrome.runtime.getURL("popup.html"),
      });
    } catch {
      // The tab may have been closed between the redirect and this update. The
      // badge and popup remain available as a complete fallback.
    }
  }
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
