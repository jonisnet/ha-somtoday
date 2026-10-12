/**
 * Strictly recognise the one OAuth callback this helper is allowed to retain.
 *
 * This module deliberately has no Chrome API dependencies so the security
 * boundary can be covered by fast, dependency-free unit tests.
 */

export const CALLBACK_PROTOCOL = "somtoday:";
export const CALLBACK_HOST = "nl.topicus.somtoday.leerling";
export const CALLBACK_PATH = "/oauth/callback";
export const CAPTURE_TTL_MS = 5 * 60 * 1000;

const MAX_REDIRECT_LENGTH = 8192;
const MAX_PARAMETER_LENGTH = 4096;

/** Return one header value, matching the header name case-insensitively. */
export function findHeader(headers, wantedName) {
  if (!Array.isArray(headers)) {
    return null;
  }

  const wanted = wantedName.toLowerCase();
  for (const header of headers) {
    if (
      typeof header?.name === "string" &&
      header.name.toLowerCase() === wanted &&
      typeof header.value === "string"
    ) {
      return header.value;
    }
  }
  return null;
}

/**
 * Return the callback header used by either a regular HTTP redirect or an
 * Apache Wicket Ajax redirect. Somtoday's login form is submitted through
 * Wicket, which can return a 200 response with Ajax-Location instead of a 3xx
 * response with Location.
 */
export function findRedirectHeader(headers) {
  return (
    findHeader(headers, "location") ??
    findHeader(headers, "ajax-location")
  );
}

/**
 * Return a validated callback or null.
 *
 * The full URL is kept because Home Assistant performs the authoritative
 * state check against the PKCE flow it created when Somtoday returns one. Some
 * Somtoday login paths omit state, so the exact callback origin/path and its
 * single-use code form the minimum accepted shape here.
 */
export function parseSomtodayCallback(rawValue) {
  if (typeof rawValue !== "string") {
    return null;
  }

  const redirectUrl = rawValue.trim();
  if (!redirectUrl || redirectUrl.length > MAX_REDIRECT_LENGTH) {
    return null;
  }

  let parsed;
  try {
    parsed = new URL(redirectUrl);
  } catch {
    return null;
  }

  if (
    parsed.protocol !== CALLBACK_PROTOCOL ||
    parsed.hostname !== CALLBACK_HOST ||
    parsed.pathname !== CALLBACK_PATH ||
    parsed.username ||
    parsed.password ||
    (parsed.port && parsed.port !== "443") ||
    parsed.hash
  ) {
    return null;
  }

  const codes = parsed.searchParams.getAll("code");
  const states = parsed.searchParams.getAll("state");
  if (codes.length !== 1 || states.length > 1) {
    return null;
  }

  const [code] = codes;
  const state = states[0] ?? null;
  if (
    !code ||
    code.length > MAX_PARAMETER_LENGTH ||
    containsControlCharacter(code) ||
    (state !== null &&
      (!state ||
        state.length > MAX_PARAMETER_LENGTH ||
        containsControlCharacter(state)))
  ) {
    return null;
  }

  return Object.freeze({ redirectUrl, code, state });
}

/** Build the short-lived record stored in chrome.storage.session. */
export function createCapture(redirectUrl, now = Date.now()) {
  return Object.freeze({
    redirectUrl,
    capturedAt: now,
    expiresAt: now + CAPTURE_TTL_MS,
  });
}

/** Return whether a value is a well-shaped capture that has not expired. */
export function isFreshCapture(value, now = Date.now()) {
  return Boolean(
    value &&
      typeof value.redirectUrl === "string" &&
      Number.isFinite(value.capturedAt) &&
      Number.isFinite(value.expiresAt) &&
      value.capturedAt <= value.expiresAt &&
      value.expiresAt > now &&
      value.expiresAt - value.capturedAt === CAPTURE_TTL_MS &&
      parseSomtodayCallback(value.redirectUrl),
  );
}

function containsControlCharacter(value) {
  for (let index = 0; index < value.length; index += 1) {
    const codePoint = value.charCodeAt(index);
    if (codePoint < 0x20 || codePoint === 0x7f) {
      return true;
    }
  }
  return false;
}
