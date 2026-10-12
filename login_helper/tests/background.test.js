import assert from "node:assert/strict";
import test from "node:test";

import { CAPTURE_TTL_MS } from "../oauth_redirect.js";

const CALLBACK =
  "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=one-time-code&state=flow-state";

const listeners = {};
const calls = {
  alarms: [],
  badges: [],
  titles: [],
};
const sessionValues = {};

globalThis.chrome = {
  action: {
    async setBadgeBackgroundColor(value) {
      calls.badges.push(value);
    },
    async setBadgeText(value) {
      calls.badges.push(value);
    },
    async setTitle(value) {
      calls.titles.push(value);
    },
  },
  alarms: {
    async clear(name) {
      calls.alarms.push({ clear: name });
    },
    async create(name, options) {
      calls.alarms.push({ create: name, ...options });
    },
    onAlarm: {
      addListener(listener) {
        listeners.alarm = listener;
      },
    },
  },
  runtime: {
    getURL(path) {
      return `chrome-extension://test-extension/${path}`;
    },
    onMessage: {
      addListener(listener) {
        listeners.message = listener;
      },
    },
  },
  storage: {
    session: {
      async get(key) {
        return { [key]: sessionValues[key] };
      },
      async remove(key) {
        delete sessionValues[key];
      },
      async set(values) {
        Object.assign(sessionValues, values);
      },
      async setAccessLevel(value) {
        calls.accessLevel = value;
      },
    },
  },
  tabs: {
    async update(tabId, options) {
      calls.tabUpdates ??= [];
      calls.tabUpdates.push({ tabId, ...options });
    },
  },
  webRequest: {
    onBeforeRedirect: {
      addListener(listener, filter) {
        listeners.redirect = listener;
        calls.redirectFilter = filter;
      },
    },
    onHeadersReceived: {
      addListener(listener, filter, extraInfo) {
        listeners.headers = listener;
        calls.filter = filter;
        calls.extraInfo = extraInfo;
      },
    },
  },
};

const { EXPIRY_ALARM, STORAGE_KEY } = await import("../background.js");
await settle();

test("registers a read-only listener for Somtoday main-frame responses only", () => {
  assert.deepEqual(calls.filter, {
    urls: ["https://inloggen.somtoday.nl/*"],
    types: ["main_frame"],
  });
  assert.deepEqual(calls.extraInfo, ["responseHeaders", "extraHeaders"]);
  assert.deepEqual(calls.redirectFilter, calls.filter);
  assert.deepEqual(calls.accessLevel, { accessLevel: "TRUSTED_CONTEXTS" });
});

test("captures a valid Location header, schedules deletion and marks the action", async () => {
  listeners.headers({
    responseHeaders: [{ name: "Location", value: CALLBACK }],
  });
  await settle();

  const capture = sessionValues[STORAGE_KEY];
  assert.equal(capture.redirectUrl, CALLBACK);
  assert.equal(capture.expiresAt - capture.capturedAt, CAPTURE_TTL_MS);
  assert.deepEqual(calls.alarms.at(-1), {
    create: EXPIRY_ALARM,
    when: capture.expiresAt,
  });
  assert.deepEqual(calls.badges.at(-1), { text: "1" });
  assert.deepEqual(calls.titles.at(-1), {
    title: "Somtoday-login gevonden",
  });
});

test("captures the direct redirect event and opens a visible result page", async () => {
  const callbackWithoutState =
    "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=direct-code";
  listeners.redirect({ redirectUrl: callbackWithoutState, tabId: 42 });
  await settle();

  assert.equal(sessionValues[STORAGE_KEY].redirectUrl, callbackWithoutState);
  assert.deepEqual(calls.tabUpdates.at(-1), {
    tabId: 42,
    url: "chrome-extension://test-extension/popup.html",
  });
});

test("ignores an unrelated redirect even on the permitted host", async () => {
  const originalCapture = sessionValues[STORAGE_KEY];
  listeners.headers({
    responseHeaders: [
      { name: "Location", value: "https://attacker.invalid/?code=x&state=y" },
    ],
  });
  await settle();
  assert.equal(sessionValues[STORAGE_KEY], originalCapture);
});

test("copy acknowledgement consumes only the capture shown by that popup", async () => {
  const currentExpiry = sessionValues[STORAGE_KEY].expiresAt;
  let response;

  assert.equal(
    listeners.message(
      { type: "consume_capture", expiresAt: currentExpiry - 1 },
      {},
      (value) => {
        response = value;
      },
    ),
    true,
  );
  await settle();
  assert.ok(sessionValues[STORAGE_KEY]);
  assert.deepEqual(response, { ok: true });

  listeners.message(
    { type: "consume_capture", expiresAt: currentExpiry },
    {},
    (value) => {
      response = value;
    },
  );
  await settle();
  assert.equal(sessionValues[STORAGE_KEY], undefined);
  assert.deepEqual(response, { ok: true });
  assert.deepEqual(calls.badges.at(-1), { text: "" });
});

test("expiry alarm removes a stale capture", async () => {
  const expiresAt = Date.now() - 1;
  sessionValues[STORAGE_KEY] = {
    redirectUrl: CALLBACK,
    capturedAt: expiresAt - CAPTURE_TTL_MS,
    expiresAt,
  };
  listeners.alarm({ name: EXPIRY_ALARM });
  await settle();
  assert.equal(sessionValues[STORAGE_KEY], undefined);
});

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}
