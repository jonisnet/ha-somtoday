"""Tests for the OAuth2 / PKCE authentication layer."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.somtoday.auth import (
    SomtodayAuth,
    SomtodayAuthError,
    SomtodayInvalidAuth,
    SomtodayTokens,
    async_exchange_code,
    async_refresh_tokens,
    build_authorize_url,
    code_challenge_from_verifier,
    extract_code,
    generate_code_verifier,
)
from custom_components.somtoday.const import TOKEN_URL

from . import payloads

REDIRECT = "somtoday://nl.topicus.somtoday.leerling/oauth/callback"


@pytest.fixture
async def session():
    """Return a throwaway aiohttp session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


# --------------------------------------------------------------------------
# PKCE
# --------------------------------------------------------------------------


def test_code_verifier_shape():
    verifier = generate_code_verifier()
    assert len(verifier) == 128
    assert verifier.isalnum()
    assert verifier.islower() or verifier.isdigit() or not verifier.isupper()
    # Two calls must not collide.
    assert verifier != generate_code_verifier()


def test_code_challenge_is_unpadded_s256():
    verifier = "abc123"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    challenge = code_challenge_from_verifier(verifier)
    assert challenge == expected
    assert "=" not in challenge


def test_authorize_url_omits_tenant_uuid():
    """Somtoday killed the school-list endpoint; the login page picks instead."""
    url = build_authorize_url("challenge-value", "state-value")
    query = parse_qs(urlparse(url).query)
    assert "tenant_uuid" not in query
    assert query["code_challenge"] == ["challenge-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-value"]
    assert query["client_id"] == ["somtoday-leerling-native"]
    assert query["redirect_uri"] == [REDIRECT]


# --------------------------------------------------------------------------
# Parsing the pasted redirect
# --------------------------------------------------------------------------


def test_extract_code_from_full_redirect():
    assert extract_code(f"{REDIRECT}?code=abc123&state=xyz", "xyz") == "abc123"


def test_extract_code_tolerates_surrounding_whitespace():
    assert extract_code(f"  {REDIRECT}?code=abc123  ") == "abc123"


def test_extract_code_accepts_a_bare_code():
    """Some users copy only the code out of the address."""
    assert extract_code("abc123") == "abc123"


def test_extract_code_without_state_is_not_blocked():
    """A trimmed URL should still work; the state check is best-effort."""
    assert extract_code(f"{REDIRECT}?code=abc123", "xyz") == "abc123"


@pytest.mark.parametrize(
    "value",
    ["", "   ", REDIRECT, f"{REDIRECT}?code=", "https://example.invalid/no-query"],
)
def test_extract_code_rejects_addresses_without_a_code(value):
    with pytest.raises(ValueError, match="no_code"):
        extract_code(value)


@pytest.mark.parametrize(
    "value",
    [
        # The address bar while still on the login page: an `auth` JWT
        # describing the pending request, and no code yet. By far the most
        # common mistake, so it gets its own message.
        "https://inloggen.somtoday.nl/?2&auth=eyJhbGciOiJSUzI1NiJ9.e30.sig",
        "https://inloggen.somtoday.nl/?auth=whatever",
        "https://inloggen.somtoday.nl/oauth2/authorize?client_id=x&scope=openid",
    ],
)
def test_extract_code_recognises_the_login_page_address(value):
    with pytest.raises(ValueError, match="login_page"):
        extract_code(value)


def test_login_page_detection_does_not_swallow_a_real_redirect():
    """A genuine callback still wins, even from the same host."""
    assert extract_code(f"{REDIRECT}?code=abc123&auth=leftover") == "abc123"


@pytest.mark.parametrize(
    "pasted",
    [
        # Chrome never navigates to an unregistered scheme, so the redirect is
        # invisible in the address bar and has to be read out of DevTools.
        # These are the shapes that actually come back from there.
        f"location: {REDIRECT}?code=abc123&state=xyz",
        f"Location: {REDIRECT}?code=abc123&state=xyz",
        f"location:\n{REDIRECT}?code=abc123&state=xyz",
        (
            "cache-control: no-store\n"
            f"location: {REDIRECT}?code=abc123&state=xyz\n"
            "content-length: 0"
        ),
        f'"{REDIRECT}?code=abc123&state=xyz"',
    ],
)
def test_extract_code_from_a_devtools_paste(pasted):
    assert extract_code(pasted, "xyz") == "abc123"


def test_the_authorization_url_is_never_mistaken_for_a_redirect():
    """`code_challenge=` must not read as `code=`, or the form would loop."""
    auth_url = build_authorize_url("EujyZLFhcX6RJqHPT21AQYI6MNIj_BYbLfuRW9hphls", "st")
    with pytest.raises(ValueError, match="login_page"):
        extract_code(auth_url)


def test_a_devtools_paste_still_honours_the_state_check():
    with pytest.raises(ValueError, match="state_mismatch"):
        extract_code(f"location: {REDIRECT}?code=abc123&state=other", "xyz")


def test_extract_code_rejects_a_mismatched_state():
    with pytest.raises(ValueError, match="state_mismatch"):
        extract_code(f"{REDIRECT}?code=abc123&state=other", "xyz")


def test_extract_code_surfaces_a_reported_error():
    with pytest.raises(ValueError, match="redirect_error:access_denied"):
        extract_code(f"{REDIRECT}?error=access_denied")


# --------------------------------------------------------------------------
# Token endpoint
# --------------------------------------------------------------------------


async def test_exchange_code_success(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.TOKEN_RESPONSE, status=200)
        tokens = await async_exchange_code(session, "the-code", "the-verifier")

    assert tokens.access_token == "fictional-access-token"
    assert tokens.refresh_token == "fictional-refresh-token"
    assert tokens.api_url == "https://api.example.invalid"
    assert tokens.tenant == "Voorbeeld College"
    assert tokens.is_expired is False


async def test_invalid_grant_is_a_definitive_rejection(session):
    """Only this maps to reauth; everything else must stay retryable."""
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.INVALID_GRANT_RESPONSE, status=400)
        with pytest.raises(SomtodayInvalidAuth):
            await async_exchange_code(session, "dead-code", "verifier")


async def test_server_error_stays_retryable(session):
    """A Somtoday outage must never push a working account into reauth."""
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, status=503, body="upstream down")
        with pytest.raises(SomtodayAuthError) as excinfo:
            await async_exchange_code(session, "code", "verifier")
    assert not isinstance(excinfo.value, SomtodayInvalidAuth)


async def test_rate_limit_stays_retryable(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload={"error": "slow_down"}, status=400)
        with pytest.raises(SomtodayAuthError) as excinfo:
            await async_exchange_code(session, "code", "verifier")
    assert not isinstance(excinfo.value, SomtodayInvalidAuth)


async def test_malformed_body_is_retryable(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, status=200, body="<html>not json</html>")
        with pytest.raises(SomtodayAuthError):
            await async_exchange_code(session, "code", "verifier")


async def test_missing_access_token_is_retryable(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload={"token_type": "Bearer"}, status=200)
        with pytest.raises(SomtodayAuthError):
            await async_exchange_code(session, "code", "verifier")


async def test_network_failure_is_retryable(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(SomtodayAuthError):
            await async_exchange_code(session, "code", "verifier")


async def test_refresh_keeps_the_old_token_when_none_is_returned(session):
    """Spending a rotating token and forgetting it would lock the account out."""
    response = {k: v for k, v in payloads.TOKEN_RESPONSE.items() if k != "refresh_token"}
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=response, status=200)
        tokens = await async_refresh_tokens(session, "the-old-refresh-token")

    assert tokens.refresh_token == "the-old-refresh-token"


async def test_refresh_adopts_a_rotated_token(session):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        tokens = await async_refresh_tokens(session, "fictional-refresh-token")

    assert tokens.refresh_token == "fictional-refresh-token-2"


# --------------------------------------------------------------------------
# SomtodayAuth
# --------------------------------------------------------------------------


def _tokens(*, expires_in_seconds: int) -> SomtodayTokens:
    return SomtodayTokens(
        access_token="cached-token",
        refresh_token="fictional-refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        api_url="https://api.example.invalid",
    )


async def test_valid_token_is_reused_without_a_request(session):
    auth = SomtodayAuth(session, _tokens(expires_in_seconds=3600))
    with aioresponses() as mocked:
        assert await auth.async_get_access_token() == "cached-token"
        assert not mocked.requests


async def test_expired_token_is_refreshed(session):
    auth = SomtodayAuth(session, _tokens(expires_in_seconds=-10))
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        assert await auth.async_get_access_token() == "fictional-access-token-2"

    assert auth.tokens.refresh_token == "fictional-refresh-token-2"


async def test_token_about_to_expire_is_refreshed_early(session):
    """A token with seconds left would otherwise race its own expiry."""
    auth = SomtodayAuth(session, _tokens(expires_in_seconds=5))
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        assert await auth.async_get_access_token() == "fictional-access-token-2"


async def test_concurrent_callers_refresh_only_once(session):
    """Two callers must never spend the same rotating refresh token."""
    auth = SomtodayAuth(session, _tokens(expires_in_seconds=-10))
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        results = await asyncio.gather(
            auth.async_get_access_token(),
            auth.async_get_access_token(),
            auth.async_get_access_token(),
        )
        calls = sum(len(v) for v in mocked.requests.values())

    assert results == ["fictional-access-token-2"] * 3
    assert calls == 1


async def test_dead_refresh_token_raises_invalid_auth(session):
    auth = SomtodayAuth(session, _tokens(expires_in_seconds=-10))
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.INVALID_GRANT_RESPONSE, status=400)
        with pytest.raises(SomtodayInvalidAuth):
            await auth.async_get_access_token()
