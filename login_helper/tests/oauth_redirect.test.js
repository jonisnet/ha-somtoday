import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPTURE_TTL_MS,
  createCapture,
  findHeader,
  findRedirectHeader,
  isFreshCapture,
  parseSomtodayCallback,
} from "../oauth_redirect.js";

const CALLBACK =
  "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=one-time-code&state=flow-state";

test("accepts the exact Somtoday callback and preserves it byte-for-byte", () => {
  const parsed = parseSomtodayCallback(CALLBACK);
  assert.deepEqual(parsed, {
    redirectUrl: CALLBACK,
    code: "one-time-code",
    state: "flow-state",
  });
});

test("accepts Somtoday callbacks that omit state", () => {
  const callback =
    "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=one-time-code";
  assert.deepEqual(parseSomtodayCallback(callback), {
    redirectUrl: callback,
    code: "one-time-code",
    state: null,
  });
});

test("accepts the explicit port 443 returned by the live Somtoday login", () => {
  const callback =
    "somtoday://nl.topicus.somtoday.leerling:443/oauth/callback?code=one-time-code&state=flow-state";
  assert.deepEqual(parseSomtodayCallback(callback), {
    redirectUrl: callback,
    code: "one-time-code",
    state: "flow-state",
  });
});

test("finds Location without depending on header casing", () => {
  assert.equal(
    findHeader([{ name: "LOCATION", value: CALLBACK }], "location"),
    CALLBACK,
  );
  assert.equal(findHeader(undefined, "location"), null);
});

test("finds the Ajax-Location header used by Wicket login submissions", () => {
  assert.equal(
    findRedirectHeader([{ name: "Ajax-Location", value: CALLBACK }]),
    CALLBACK,
  );
  assert.equal(
    findRedirectHeader([{ name: "LOCATION", value: CALLBACK }]),
    CALLBACK,
  );
});

for (const [name, value] of [
  ["ordinary web URL", "https://example.invalid/?code=x&state=y"],
  ["lookalike host", "somtoday://nl.topicus.somtoday.leerling.evil.test/oauth/callback?code=x&state=y"],
  ["wrong callback path", "somtoday://nl.topicus.somtoday.leerling/oauth/other?code=x&state=y"],
  ["credentials", "somtoday://user@nl.topicus.somtoday.leerling/oauth/callback?code=x&state=y"],
  ["unexpected port", "somtoday://nl.topicus.somtoday.leerling:444/oauth/callback?code=x&state=y"],
  ["fragment", `${CALLBACK}#unexpected`],
  ["missing code", "somtoday://nl.topicus.somtoday.leerling/oauth/callback?state=y"],
  ["duplicate code", `${CALLBACK}&code=second`],
  ["duplicate state", `${CALLBACK}&state=second`],
  ["OAuth error", "somtoday://nl.topicus.somtoday.leerling/oauth/callback?error=access_denied&state=y"],
  ["not a URL", "location: somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=x&state=y"],
]) {
  test(`rejects ${name}`, () => {
    assert.equal(parseSomtodayCallback(value), null);
  });
}

test("decodes parameters only for validation while preserving the original URL", () => {
  const callback =
    "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=a%2Fb&state=s%20t";
  const parsed = parseSomtodayCallback(callback);
  assert.equal(parsed.code, "a/b");
  assert.equal(parsed.state, "s t");
  assert.equal(parsed.redirectUrl, callback);
});

test("capture expires after five minutes and rejects malformed records", () => {
  const now = 1_000_000;
  const capture = createCapture(CALLBACK, now);
  assert.equal(capture.expiresAt, now + CAPTURE_TTL_MS);
  assert.equal(isFreshCapture(capture, capture.expiresAt - 1), true);
  assert.equal(isFreshCapture(capture, capture.expiresAt), false);
  assert.equal(
    isFreshCapture({ ...capture, redirectUrl: "https://attacker.invalid" }, now),
    false,
  );
  assert.equal(isFreshCapture({ ...capture, expiresAt: capture.expiresAt + 1 }, now), false);
});
