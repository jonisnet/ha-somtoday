"""Tests for the Somtoday config, reauth and options flows."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.somtoday.api import SomtodayApiError
from custom_components.somtoday.auth import SomtodayAuthError, SomtodayInvalidAuth
from custom_components.somtoday.const import (
    CONF_API_URL,
    CONF_DAYS_AHEAD,
    CONF_PLANNER_ALLOWED_USERS,
    CONF_REDIRECT_URL,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from . import payloads

REDIRECT = "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=fictional-code"


@pytest.fixture
def mock_flow_api(tokens):
    """Patch the token exchange and the API calls the config flow makes."""
    with (
        patch(
            "custom_components.somtoday.config_flow.async_exchange_code",
            AsyncMock(return_value=tokens),
        ) as exchange,
        patch(
            "custom_components.somtoday.config_flow.SomtodayApiClient", autospec=True
        ) as client_class,
        patch(
            "custom_components.somtoday.async_setup_entry", AsyncMock(return_value=True)
        ),
    ):
        client = client_class.return_value
        client.async_get_account = AsyncMock(return_value=payloads.ACCOUNT)
        client.async_get_students = AsyncMock(return_value=payloads.STUDENTS["items"])
        yield exchange, client


async def _start(hass):
    """Open the user flow and return its first result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_form_offers_a_login_link(hass, mock_flow_api):
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    auth_url = result["description_placeholders"]["auth_url"]
    assert auth_url.startswith("https://inloggen.somtoday.nl/oauth2/authorize?")
    assert "code_challenge=" in auth_url


async def test_successful_login(hass, mock_flow_api):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Somtoday (Fien Voorbeeld)"
    assert result["data"][CONF_REFRESH_TOKEN] == "fictional-refresh-token"
    assert result["data"][CONF_API_URL] == "https://api.example.invalid"
    assert result["result"].unique_id == "555000"


async def test_only_the_refresh_token_is_persisted(hass, mock_flow_api):
    """Access tokens live an hour; storing one would add risk, not value."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )
    assert "access_token" not in result["data"]
    assert "id_token" not in result["data"]


async def test_address_without_a_code(hass, mock_flow_api):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: "https://example.invalid/nothing-here"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}
    assert "code_challenge=" in result["description_placeholders"]["auth_url"]


async def test_login_page_address_gets_its_own_message(hass, mock_flow_api):
    """Copying the address bar before finishing the login is the common slip."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_REDIRECT_URL: "https://inloggen.somtoday.nl/?2&auth=eyJhbGciOiJSUzI1NiJ9.e30.s"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "login_page"}


async def test_a_wrong_paste_keeps_the_link_the_user_already_opened(
    hass, mock_flow_api
):
    """Regenerating here would break the login they have open in their browser.

    The code they are about to receive is bound to the challenge from the link
    they already followed, so handing them a new verifier would turn a
    recoverable typo into a PKCE mismatch and a second full login.
    """
    result = await _start(hass)
    original = result["description_placeholders"]["auth_url"]

    for pasted in (
        "https://example.invalid/nothing-here",
        "https://inloggen.somtoday.nl/?auth=still-on-the-login-page",
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_REDIRECT_URL: pasted}
        )
        assert result["description_placeholders"]["auth_url"] == original

    # And the link that survived still completes the flow.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_rejected_code_does_mint_a_new_link(hass, mock_flow_api):
    """A code that reached Somtoday is spent, so the old link is worthless."""
    exchange, _client = mock_flow_api
    exchange.side_effect = SomtodayInvalidAuth("dead code")

    result = await _start(hass)
    original = result["description_placeholders"]["auth_url"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["errors"] == {"base": "invalid_auth"}
    assert result["description_placeholders"]["auth_url"] != original


async def test_expired_code(hass, mock_flow_api):
    exchange, _client = mock_flow_api
    exchange.side_effect = SomtodayInvalidAuth("dead code")

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_somtoday_unreachable(hass, mock_flow_api):
    exchange, _client = mock_flow_api
    exchange.side_effect = SomtodayAuthError("network down")

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_api_error_while_identifying_the_account(hass, mock_flow_api):
    _exchange, client = mock_flow_api
    client.async_get_students.side_effect = SomtodayApiError(500)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_account_without_students(hass, mock_flow_api):
    _exchange, client = mock_flow_api
    client.async_get_students.return_value = []

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["errors"] == {"base": "no_students"}


async def test_duplicate_account_is_rejected(hass, config_entry, mock_flow_api):
    config_entry.add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_multiple_students_land_in_the_title(hass, mock_flow_api):
    _exchange, client = mock_flow_api
    client.async_get_students.return_value = payloads.STUDENTS_TWO["items"]

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["title"] == "Somtoday (Fien Voorbeeld, Joost Voorbeeld)"


# --------------------------------------------------------------------------
# Reauth
# --------------------------------------------------------------------------


async def _start_reauth(hass, config_entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": config_entry.entry_id},
        data=config_entry.data,
    )


async def test_reauth_updates_the_stored_token(hass, config_entry, mock_flow_api, tokens):
    config_entry.add_to_hass(hass)
    tokens.refresh_token = "fictional-refresh-token-2"

    result = await _start_reauth(hass, config_entry)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "fictional-refresh-token-2"


async def test_reauth_with_a_different_account_aborts(
    hass, config_entry, mock_flow_api
):
    """Signing in as someone else must not silently rebind the entities."""
    config_entry.add_to_hass(hass)
    _exchange, client = mock_flow_api
    client.async_get_account.return_value = {
        "links": [{"id": 999999, "rel": "self", "type": "account.RAccount"}]
    }

    result = await _start_reauth(hass, config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "fictional-refresh-token"


async def test_reauth_error_re_renders_the_form(hass, config_entry, mock_flow_api):
    config_entry.add_to_hass(hass)
    exchange, _client = mock_flow_api
    exchange.side_effect = SomtodayInvalidAuth("dead code")

    result = await _start_reauth(hass, config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: REDIRECT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


async def test_options_flow(hass, setup_integration):
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFRESH_INTERVAL: "60", CONF_DAYS_AHEAD: "21"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Stored as integers, not the dropdown's strings.
    assert setup_integration.options == {
        CONF_REFRESH_INTERVAL: 60,
        CONF_DAYS_AHEAD: 21,
        CONF_PLANNER_ALLOWED_USERS: [],
    }

async def test_options_default_to_the_four_week_window(hass, setup_integration):
    """Four weeks, because the normal week is derived from the weeks held —
    the window doubles as the sample size for that derivation."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    schema = result["data_schema"].schema
    default = next(
        key.default() for key in schema if key == CONF_DAYS_AHEAD
    )
    assert default == "28"


async def test_a_choice_that_no_longer_exists_falls_back(hass, config_entry, mock_api):
    """The 30-day window was replaced by a four-week one. An entry configured
    before that must not open the form with a blank dropdown, which reads as
    the setting having been lost."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_DAYS_AHEAD: 30})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    schema = result["data_schema"].schema
    default = next(key.default() for key in schema if key == CONF_DAYS_AHEAD)
    assert default == "28"
