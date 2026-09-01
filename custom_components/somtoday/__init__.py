"""The Somtoday integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SomtodayApiClient
from .auth import SomtodayAuth, SomtodayTokens
from .const import (
    CONF_API_URL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT,
    DEFAULT_API_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_PLANNER_ADD,
    SERVICE_PLANNER_DELETE,
)
from .coordinator import SomtodayCoordinator
from .planner import SomtodayPlanner

_LOGGER = logging.getLogger(__name__)


@dataclass
class SomtodayData:
    """Runtime data attached to a Somtoday config entry."""

    client: SomtodayApiClient
    coordinator: SomtodayCoordinator
    planner: SomtodayPlanner


type SomtodayConfigEntry = ConfigEntry[SomtodayData]


async def async_setup_entry(hass: HomeAssistant, entry: SomtodayConfigEntry) -> bool:
    """Set up Somtoday from a config entry."""
    session = async_get_clientsession(hass)

    # Only the refresh token is persisted; access tokens live an hour and are
    # not worth storing. Starting with an expired placeholder means the first
    # API call transparently mints a fresh one — and a dead refresh token
    # surfaces as ConfigEntryAuthFailed from the first refresh, which is
    # exactly where Home Assistant wants to start a reauth flow.
    tokens = SomtodayTokens(
        access_token="",
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=datetime.now(timezone.utc),
        api_url=entry.data.get(CONF_API_URL) or DEFAULT_API_URL,
        tenant=entry.data.get(CONF_TENANT),
    )
    auth = SomtodayAuth(session, tokens)
    client = SomtodayApiClient(session, auth)
    coordinator = SomtodayCoordinator(hass, entry, client)
    planner = SomtodayPlanner(hass, entry.entry_id, dict(entry.options))
    await planner.async_load()

    entry.runtime_data = SomtodayData(
        client=client, coordinator=coordinator, planner=planner
    )

    # The first refresh runs here, before the platforms are forwarded: from a
    # forwarded platform Home Assistant cannot catch ConfigEntryNotReady and
    # would half-set-up the entry. It also guarantees every platform sees a
    # populated student list when it registers its entities.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SomtodayConfigEntry) -> bool:
    """Unload a Somtoday config entry.

    The aiohttp session is Home Assistant's shared one, so there is nothing to
    close here — Somtoday is authenticated with a bearer header rather than
    cookies, so no per-entry jar is needed either.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _planner_for(hass: HomeAssistant, entry_id: str) -> SomtodayPlanner:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or not isinstance(
        getattr(entry, "runtime_data", None), SomtodayData
    ):
        raise HomeAssistantError("Unknown Somtoday config entry")
    return entry.runtime_data.planner


def _async_register_services(hass: HomeAssistant) -> None:
    async def add(call: ServiceCall) -> dict[str, Any]:
        return await _planner_for(hass, call.data["config_entry_id"]).async_add(call)

    async def delete(call: ServiceCall) -> None:
        await _planner_for(hass, call.data["config_entry_id"]).async_delete(call)

    if not hass.services.has_service(DOMAIN, SERVICE_PLANNER_ADD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_PLANNER_ADD,
            add,
            schema=vol.Schema(
                {
                    vol.Required("config_entry_id"): cv.string,
                    vol.Required("student_id"): vol.Coerce(int),
                    vol.Required("title"): cv.string,
                    vol.Required("start"): cv.datetime,
                    vol.Required("end"): cv.datetime,
                    vol.Optional("description", default=""): cv.string,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_PLANNER_DELETE,
            delete,
            schema=vol.Schema(
                {
                    vol.Required("config_entry_id"): cv.string,
                    vol.Required("item_id"): cv.string,
                }
            ),
        )
