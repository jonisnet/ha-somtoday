"""Run the notification blueprint end to end against a fake phone."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.somtoday.const import EVENT_NEW_RECORD

from .test_confidential import _setup, progress_only
from .test_records import _message, assignment, grade

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints/automation/jonisnet/somtoday_meldingen.yaml"
)


@pytest.fixture
async def phone(hass):
    """A Companion App phone called 'Telefoon' and its notify service."""
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", "x")}, name="Telefoon"
    )
    calls = async_mock_service(hass, "notify", "mobile_app_telefoon")
    return device, calls


async def _automation(hass, device_id, **inputs):
    target = Path(hass.config.path("blueprints/automation/jonisnet"))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target / BLUEPRINT.name)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "somtoday_meldingen",
                "use_blueprint": {
                    "path": f"jonisnet/{BLUEPRINT.name}",
                    "input": {"ontvangers": [device_id], **inputs},
                },
            }
        },
    )
    await hass.async_block_till_done()


async def test_new_grade_with_content(hass, config_entry, mock_api, phone):
    device, calls = phone
    await _setup(hass, config_entry, mock_api)
    await _automation(hass, device.id)
    progress_only(mock_api, [grade(), grade(700002, "5,0", 5.0, entered="2026-11-09T10:00:00")])
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(calls) == 1
    data = calls[0].data
    assert data["title"].startswith("Nieuw cijfer")
    assert "wi: 5,0" in data["message"]
    assert "weging 2" in data["message"]


async def test_without_content_only_says_something_is_new(hass, config_entry, mock_api, phone):
    device, calls = phone
    await _setup(hass, config_entry, mock_api)
    await _automation(hass, device.id, inhoud=False)
    progress_only(mock_api, [grade(), grade(700002, "5,0", 5.0)])
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert "5,0" not in calls[0].data["message"]
    assert "Open de Somtoday-kaart" in calls[0].data["message"]


async def test_message_and_assignment(hass, config_entry, mock_api, phone):
    device, calls = phone
    await _setup(hass, config_entry, mock_api)
    await _automation(hass, device.id)
    mock_api.async_get_conversations.return_value = [
        {"boodschappen": [_message(1)]},
        {"boodschappen": [_message(5)]},
    ]
    mock_api.async_get_assignments.return_value = [assignment(42)]
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    by_title = {c.data["title"].split(" - ")[0]: c.data["message"] for c in calls}
    assert "Mentor: Ouderavond" in by_title["Nieuw bericht van school"]
    assert "Welkom" in by_title["Nieuw bericht van school"]
    assert "aardrijkskunde: Werkstuk" in by_title["Nieuwe inleveropdracht"]


async def test_unticked_kind_stays_silent(hass, config_entry, mock_api, phone):
    device, calls = phone
    await _setup(hass, config_entry, mock_api)
    await _automation(hass, device.id, soorten=["message"])
    progress_only(mock_api, [grade(), grade(700002, "5,0", 5.0)])
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert calls == []


async def test_event_type_constant_matches_blueprint():
    assert EVENT_NEW_RECORD in BLUEPRINT.read_text(encoding="utf-8")
