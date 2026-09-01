"""Tests for setting up, unloading and reloading the config entry."""
from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

from custom_components.somtoday.api import SomtodayApiError
from custom_components.somtoday.auth import SomtodayInvalidAuth
from custom_components.somtoday.const import DOMAIN

from . import payloads


async def test_setup_and_unload(hass, setup_integration):
    assert setup_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_reload(hass, setup_integration):
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.LOADED


async def test_one_device_per_student(hass, config_entry, mock_api):
    """A parent account gets a clean set of entities per child."""
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    names = sorted(device.name for device in devices)
    assert names == ["Somtoday (Fien Voorbeeld)", "Somtoday (Joost Voorbeeld)"]


async def test_dead_session_starts_reauth(hass, config_entry, mock_api):
    """A rejected refresh token is the only failure that asks for a new login."""
    mock_api.async_get_students.side_effect = SomtodayInvalidAuth("dead")
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_somtoday_outage_retries_instead_of_asking_for_a_login(
    hass, config_entry, mock_api
):
    mock_api.async_get_students.side_effect = SomtodayApiError(503)
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_network_failure_retries(hass, config_entry, mock_api):
    mock_api.async_get_students.side_effect = aiohttp.ClientConnectionError("boom")
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_account_without_students_retries(hass, config_entry, mock_api):
    mock_api.async_get_students.return_value = []
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
