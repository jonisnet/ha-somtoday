"""Confidential records: permission checks, events and what never leaks."""
from __future__ import annotations

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.somtoday.const import DOMAIN, EVENT_NEW_RECORD

from .test_records import _message, assignment, grade


def progress_only(mock_api, grades):
    """Serve ``grades`` from the progress file and nothing from the exam file."""
    mock_api.async_get_grades.side_effect = (
        lambda student_id, exam_file=False: [] if exam_file else list(grades)
    )


async def _setup(hass, config_entry, mock_api, options=None):
    progress_only(mock_api, [grade()])
    mock_api.async_get_registrations.return_value = {
        "teLaat": [{"omschrijving": "Te laat", "begin": "2026-11-03T08:35:00", "registratieId": 9}]
    }
    mock_api.async_get_conversations.return_value = [{"boodschappen": [_message(1)]}]
    config_entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(config_entry, options=options)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def _call(hass, user_id=None, **data):
    return await hass.services.async_call(
        DOMAIN,
        "get_records",
        data,
        blocking=True,
        return_response=True,
        context=Context(user_id=user_id),
    )


async def test_no_user_is_allowed_by_default(hass, config_entry, mock_api, hass_admin_user):
    """Not even an administrator: the owner ticks people explicitly."""
    await _setup(hass, config_entry, mock_api)
    response = await _call(hass, hass_admin_user.id)
    entry = response["entries"][0]
    assert entry["access"] == {
        "grades": False, "absences": False, "measures": False, "messages": False
    }
    assert "grades" not in entry["students"][0]
    assert "messages" not in entry


async def test_allowed_user_gets_only_their_categories(
    hass, config_entry, mock_api, hass_read_only_user
):
    await _setup(
        hass, config_entry, mock_api, {"grades_allowed_users": [hass_read_only_user.id]}
    )
    response = await _call(hass, hass_read_only_user.id)
    student = response["entries"][0]["students"][0]
    assert student["grades"][0]["result"] == "7,5"
    assert "absences" not in student
    assert "messages" not in response["entries"][0]


async def test_automations_get_everything(hass, config_entry, mock_api):
    await _setup(hass, config_entry, mock_api)
    response = await _call(hass)
    entry = response["entries"][0]
    assert entry["messages"][0]["subject"] == "Ouderavond"
    assert entry["unread_messages"] == 0
    assert entry["students"][0]["absences"][0]["kind"] == "late"


async def test_uids_filter_matches_event_ids(hass, config_entry, mock_api):
    await _setup(hass, config_entry, mock_api)
    response = await _call(hass, categories=["grades"], uids=["700001:7,5"])
    assert len(response["entries"][0]["students"][0]["grades"]) == 1
    response = await _call(hass, categories=["grades"], uids=["nope"])
    assert response["entries"][0]["students"][0]["grades"] == []


async def test_unknown_category_is_rejected(hass, config_entry, mock_api):
    await _setup(hass, config_entry, mock_api)
    with pytest.raises((ServiceValidationError, Exception)):
        await _call(hass, categories=["passwords"])


async def test_confidential_values_never_reach_entity_states(hass, config_entry, mock_api):
    await _setup(hass, config_entry, mock_api)
    for state in hass.states.async_all():
        dump = str(state.attributes) + str(state.state)
        assert "7,5" not in dump
        assert "Ouderavond" not in dump
        assert "Te laat" not in dump


async def test_new_records_fire_events_without_content(hass, config_entry, mock_api):
    """The first poll learns silently; the next one reports what is new."""
    await _setup(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_NEW_RECORD)

    progress_only(mock_api, [grade(), grade(700002, "5,0", 5.0)])
    mock_api.async_get_assignments.return_value = [assignment(42)]
    coordinator = config_entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    by_kind = {event.data["kind"]: event.data for event in events}
    assert set(by_kind) == {"grade", "assignment"}
    assert by_kind["grade"]["uids"] == ["700002:5,0"]
    assert by_kind["grade"]["confidential"] is True
    assert "items" not in by_kind["grade"]
    assert "5,0" not in str({k: v for k, v in by_kind["grade"].items() if k != "uids"})
    # Assignments are not confidential, so they travel with the event.
    assert by_kind["assignment"]["items"][0]["topic"] == "Werkstuk"


async def test_a_changed_result_counts_as_new(hass, config_entry, mock_api):
    """A resit that replaces a 4,0 with a 6,5 is news too."""
    await _setup(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_NEW_RECORD)
    progress_only(mock_api, [grade(result="6,5", value=6.5)])
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [e.data["uids"] for e in events if e.data["kind"] == "grade"] == [["700001:6,5"]]


async def test_public_sensors(hass, config_entry, mock_api):
    mock_api.async_get_holidays.return_value = [
        {"naam": "Kerstvakantie", "beginDatum": "2099-12-19", "eindDatum": "2100-01-03"}
    ]
    mock_api.async_get_placements.return_value = [
        {"UUID": "p", "huidig": True, "stamgroepnaam": "1A", "leerjaar": 1}
    ]
    mock_api.async_get_assignments.return_value = [assignment(42, end="2099-01-01T12:00:00")]
    await _setup(hass, config_entry, mock_api)
    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    holiday = next(s for k, s in states.items() if k.endswith("next_holiday"))
    assert holiday.state == "2099-12-19"
    assert holiday.attributes["name"] == "Kerstvakantie"
    klass = next(s for k, s in states.items() if k.endswith("_class"))
    assert klass.state == "1A"
    work = next(s for k, s in states.items() if "assignments" in k)
    assert work.state == "1"
