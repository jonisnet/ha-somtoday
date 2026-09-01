"""Shared fixtures for the Somtoday test suite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.somtoday.auth import SomtodayTokens
from custom_components.somtoday.const import (
    CONF_API_URL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT,
    DOMAIN,
)

from . import payloads

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components in every test."""
    yield


@pytest.fixture
def tokens() -> SomtodayTokens:
    """Return a valid, not-yet-expired token set."""
    return SomtodayTokens(
        access_token="fictional-access-token",
        refresh_token="fictional-refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        api_url="https://api.example.invalid",
        tenant="Voorbeeld College",
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a Somtoday config entry as the config flow would have stored it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Somtoday (Fien Voorbeeld)",
        unique_id="555000",
        data={
            CONF_REFRESH_TOKEN: "fictional-refresh-token",
            CONF_API_URL: "https://api.example.invalid",
            CONF_TENANT: "Voorbeeld College",
        },
    )


@pytest.fixture
def mock_api():
    """Patch the API client so no test ever reaches the network.

    Yields the mock so a test can change what Somtoday "returns" — including
    raising, to cover the failure paths.
    """
    with patch(
        "custom_components.somtoday.SomtodayApiClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.async_get_account = AsyncMock(return_value=payloads.ACCOUNT)
        client.async_get_students = AsyncMock(
            return_value=payloads.STUDENTS["items"]
        )
        client.async_get_appointments = AsyncMock(
            return_value=[payloads.appointment()]
        )
        client.async_get_homework = AsyncMock(return_value=[payloads.homework()])
        # A real token set, not a mock attribute: diagnostics serialises this to
        # JSON, and a MagicMock standing in for `expires_at` would blow up there
        # rather than in the code under test.
        client.auth.tokens = SomtodayTokens(
            access_token="fictional-access-token",
            refresh_token="fictional-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            api_url="https://api.example.invalid",
            tenant="Voorbeeld College",
        )
        yield client


@pytest.fixture
async def setup_integration(hass, config_entry, mock_api):
    """Set up the integration with a mocked API and return the entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
