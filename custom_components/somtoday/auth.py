"""OAuth2 authorization-code + PKCE authentication for Somtoday.

Somtoday's own mobile app is a public OAuth2 client that redirects to a custom
URI scheme. Home Assistant cannot receive that redirect, so the config flow
asks the user to complete the login in their own browser and paste the failed
redirect URL back. That keeps every school working — including the ones behind
Microsoft/Google SSO or MFA — and means no password ever reaches Home
Assistant.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from .const import (
    AUTHORIZE_URL,
    CLIENT_ID,
    DEFAULT_API_URL,
    OAUTH_SCOPE,
    REDIRECT_URI,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

# The community documentation specifies a 128-character verifier drawn from
# lowercase letters and digits. RFC 7636 allows more, but there is no reason to
# deviate from what Somtoday's own client sends.
_VERIFIER_ALPHABET = string.ascii_lowercase + string.digits
_VERIFIER_LENGTH = 128

# Patterns for pulling the pieces of the redirect out of whatever the user
# pasted — a bare URL, a DevTools `location:` header line, or a block of
# copied response headers. Stop at anything that cannot be part of a query
# value, so a trailing header or a stray quote never ends up inside the code.
_VALUE = r"([^&\s\"'<>]+)"
_CODE_RE = re.compile(rf"[?&]code={_VALUE}")
_STATE_RE = re.compile(rf"[?&]state={_VALUE}")
_ERROR_RE = re.compile(rf"[?&]error={_VALUE}")
_AUTH_RE = re.compile(rf"[?&]auth={_VALUE}")
_LOGIN_HOST = "inloggen.somtoday.nl"


class SomtodayAuthError(Exception):
    """A transient authentication failure — worth retrying.

    Network blips, 5xx responses and unexpected bodies land here. These must
    never push the user into a reauth flow: the stored refresh token is
    probably still fine.
    """


class SomtodayInvalidAuth(SomtodayAuthError):
    """A definitive credential rejection — retrying will never help.

    Raised only when Somtoday answers HTTP 400 with ``error=invalid_grant``,
    meaning the authorization code or refresh token is genuinely dead. This is
    the only failure that escalates to Home Assistant's reauth flow.
    """


@dataclass
class SomtodayTokens:
    """A token set as returned by Somtoday's token endpoint."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    api_url: str
    tenant: str | None = None

    @property
    def is_expired(self) -> bool:
        """Return whether the access token is expired, or about to be.

        A margin is applied so a request never races the actual expiry.
        """
        margin = timedelta(seconds=TOKEN_EXPIRY_MARGIN)
        return datetime.now(timezone.utc) + margin >= self.expires_at


def generate_code_verifier() -> str:
    """Return a fresh PKCE code verifier."""
    return "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(_VERIFIER_LENGTH))


def code_challenge_from_verifier(verifier: str) -> str:
    """Return the S256 code challenge for ``verifier`` (base64url, unpadded)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_state() -> str:
    """Return a random ``state`` value to bind the redirect to this flow."""
    return secrets.token_urlsafe(8)


def build_authorize_url(code_challenge: str, state: str) -> str:
    """Build the Somtoday login URL the user opens in their own browser.

    Deliberately built **without** ``tenant_uuid``. The community docs pass a
    school UUID looked up in ``servers.somtoday.nl/organisaties.json``, but
    Somtoday removed that endpoint in February 2025. Omitting the parameter
    makes Somtoday show its own school picker on the login page, which is both
    more robust and better UX than making the user pick a school in Home
    Assistant first.
    """
    params = {
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "session": "no_session",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def extract_code(redirect_url: str, expected_state: str | None = None) -> str:
    """Pull the authorization code out of whatever the user pasted.

    Accepts the whole ``somtoday://…/oauth/callback?code=…&state=…`` redirect,
    a DevTools ``location:`` header line, a block of copied response headers,
    or a bare code on its own. Deliberately forgiving: on Chrome the redirect
    never reaches the address bar at all, so the code has to be read out of
    DevTools, and demanding a pristine URL would reject the only paste those
    users can actually produce.

    Raises:
        ValueError: ``login_page`` when the user copied the address too early,
            ``no_code`` when nothing usable is in it, ``state_mismatch`` when
            it belongs to another flow, or ``redirect_error:<code>`` when
            Somtoday reported a failure in the redirect itself.

    """
    value = redirect_url.strip()
    if not value:
        raise ValueError("no_code")

    # Scan the raw text rather than insisting on a well-formed URL. Chrome
    # never navigates to an unregistered scheme, so on the browser most people
    # use, the redirect is invisible in the address bar and has to be read out
    # of DevTools instead — where it arrives as a `location: somtoday://…`
    # header line, sometimes with neighbouring headers attached. Parsing only
    # clean URLs would reject every one of those pastes.
    #
    # `[?&]code=` cannot match `code_challenge=`, so the authorization URL from
    # the form above is never mistaken for a redirect.
    if match := _CODE_RE.search(value):
        code = match.group(1)
        # The state check is best-effort: it only fires when the paste actually
        # carried one, so a trimmed URL is not blocked.
        if expected_state and (found := _STATE_RE.search(value)):
            if found.group(1) != expected_state:
                raise ValueError("state_mismatch")
        return code

    if error := _ERROR_RE.search(value):
        raise ValueError(f"redirect_error:{error.group(1)}")

    # The most common mistake by far: copying the address bar while still on
    # Somtoday's login page. That URL carries an `auth` JWT describing the
    # pending authorization request, never a code — the code only exists once
    # the login completes. Worth its own message, because "no code found"
    # gives the user nothing to act on.
    if _AUTH_RE.search(value) or urlparse(value).netloc.endswith(_LOGIN_HOST):
        raise ValueError("login_page")

    # A bare code that the user copied on its own: no scheme, no path, no
    # whitespace, nothing that looks like a URL someone pasted incompletely.
    if not urlparse(value).scheme and not set(value) & {"/", " ", "=", "?", "&"}:
        return value

    raise ValueError("no_code")


async def async_exchange_code(
    session: aiohttp.ClientSession, code: str, code_verifier: str
) -> SomtodayTokens:
    """Exchange an authorization code for a token set."""
    return await _async_token_request(
        session,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": CLIENT_ID,
            "scope": OAUTH_SCOPE,
            "session": "no_session",
        },
    )


async def async_refresh_tokens(
    session: aiohttp.ClientSession, refresh_token: str
) -> SomtodayTokens:
    """Exchange a refresh token for a fresh token set.

    Somtoday rotates refresh tokens, so the returned set normally carries a new
    one. When it does not, the old token is preserved — spending a rotating
    token and then forgetting it would lock the account out.
    """
    tokens = await _async_token_request(
        session,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "scope": OAUTH_SCOPE,
        },
    )
    if not tokens.refresh_token:
        tokens.refresh_token = refresh_token
    return tokens


async def _async_token_request(
    session: aiohttp.ClientSession, payload: dict[str, str]
) -> SomtodayTokens:
    """POST to the token endpoint and parse the response.

    Raises:
        SomtodayInvalidAuth: HTTP 400 with ``error=invalid_grant`` — the code
            or refresh token is dead and reauth is the only way forward.
        SomtodayAuthError: Any other non-200, a malformed body, or a network
            failure. Retryable; never escalate these to reauth.

    """
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    try:
        async with session.post(TOKEN_URL, data=payload, headers=headers) as response:
            body = await response.text()
            status = response.status
    except aiohttp.ClientError as err:
        raise SomtodayAuthError(f"Network error talking to Somtoday: {err}") from err

    if status != 200:
        # Somtoday answers with the standard OAuth2 error object. Only
        # ``invalid_grant`` is definitive; everything else (rate limiting, a
        # 5xx, a changed endpoint) must stay retryable so a Somtoday outage
        # never pushes a working account into reauth.
        error = _error_code(body)
        if status == 400 and error == "invalid_grant":
            raise SomtodayInvalidAuth("Somtoday rejected the token as invalid")
        raise SomtodayAuthError(
            f"Somtoday token request failed with status {status} "
            f"({error or 'no error code'})"
        )

    try:
        data: Any = json.loads(body)
    except ValueError as err:
        raise SomtodayAuthError("Somtoday returned a malformed token response") from err

    if not isinstance(data, dict) or not data.get("access_token"):
        raise SomtodayAuthError("Somtoday token response contained no access token")

    try:
        expires_in = int(data.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600

    return SomtodayTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or "",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        api_url=(data.get("somtoday_api_url") or DEFAULT_API_URL).rstrip("/"),
        tenant=data.get("somtoday_tenant"),
    )


def _error_code(body: str) -> str | None:
    """Return the OAuth2 ``error`` code from an error body, if it has one."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed.get("error") if isinstance(parsed, dict) else None


class SomtodayAuth:
    """Holds the token set and keeps the access token fresh.

    One instance per config entry. :meth:`async_get_access_token` is the only
    entry point: it refreshes when needed, under a lock so two concurrent
    callers never spend the same rotating refresh token twice.
    """

    def __init__(
        self, session: aiohttp.ClientSession, tokens: SomtodayTokens
    ) -> None:
        """Initialise with an already-obtained token set."""
        self._session = session
        self._tokens = tokens
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> SomtodayTokens:
        """Return the current token set."""
        return self._tokens

    @property
    def api_url(self) -> str:
        """Return the account's Somtoday API base URL."""
        return self._tokens.api_url

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it first when needed."""
        if not self._tokens.is_expired:
            return self._tokens.access_token

        async with self._lock:
            # Re-check inside the lock: another caller may have refreshed while
            # we waited, and the refresh token they used is now spent.
            if not self._tokens.is_expired:
                return self._tokens.access_token

            _LOGGER.debug("Access token expired, refreshing")
            tokens = await async_refresh_tokens(
                self._session, self._tokens.refresh_token
            )
            # Somtoday keeps the API URL stable across refreshes, but a
            # response can omit it; never downgrade a known-good value.
            if not tokens.api_url:
                tokens.api_url = self._tokens.api_url
            self._tokens = tokens
            return tokens.access_token
