"""Tests for the Somtoday schedule calendar entity.

Events are read through the public ``calendar.get_events`` service rather than
by reaching into Home Assistant's entity component, so these tests keep working
across Home Assistant releases.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.somtoday.api import SomtodayApiError
from custom_components.somtoday.const import DOMAIN

from . import payloads
from .test_sensor import iso, setup_with


def calendar_id(hass, student_id: int = payloads.STUDENT_A_ID) -> str:
    """Look the calendar up by unique id, so the test is language-independent."""
    found = er.async_get(hass).async_get_entity_id(
        CALENDAR_DOMAIN, DOMAIN, f"{student_id}_schedule"
    )
    assert found
    return found


async def get_events(hass, start, end, student_id: int = payloads.STUDENT_A_ID):
    """Call ``calendar.get_events`` and return the plain event dicts."""
    entity_id = calendar_id(hass, student_id)
    response = await hass.services.async_call(
        CALENDAR_DOMAIN,
        "get_events",
        {
            "entity_id": entity_id,
            "start_date_time": start,
            "end_date_time": end,
        },
        blocking=True,
        return_response=True,
    )
    return response[entity_id]["events"]


async def test_calendar_is_created_per_student(hass, config_entry, mock_api):
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    await setup_with(hass, config_entry, mock_api)

    assert hass.states.get(calendar_id(hass, payloads.STUDENT_A_ID))
    assert hass.states.get(calendar_id(hass, payloads.STUDENT_B_ID))


async def test_state_reflects_the_next_lesson(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110), subject="Frans")
        ],
    )
    state = hass.states.get(calendar_id(hass))
    assert state.attributes["message"] == "Frans"


async def test_events_are_served_from_the_cached_window(hass, config_entry, mock_api):
    """A normal week view must not cost an extra API call."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[payloads.appointment(900001, start=iso(60), end=iso(110))],
    )
    calls_before = mock_api.async_get_appointments.call_count

    now = dt_util.now()
    events = await get_events(hass, now - timedelta(hours=1), now + timedelta(days=2))

    assert len(events) == 1
    assert mock_api.async_get_appointments.call_count == calls_before


async def test_events_outside_the_window_trigger_a_fetch(hass, config_entry, mock_api):
    """Browsing to next month must not silently show an empty calendar."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[payloads.appointment(900001, start=iso(60), end=iso(110))],
    )
    calls_before = mock_api.async_get_appointments.call_count

    far_start = dt_util.now() + timedelta(days=90)
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900500,
            start=(far_start + timedelta(hours=1)).isoformat(timespec="milliseconds"),
            end=(far_start + timedelta(hours=2)).isoformat(timespec="milliseconds"),
            subject="Duits",
        )
    ]

    events = await get_events(hass, far_start, far_start + timedelta(days=1))

    assert mock_api.async_get_appointments.call_count == calls_before + 1
    assert [event["summary"] for event in events] == ["Duits"]


async def test_events_only_include_overlapping_lessons(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110)),
            payloads.appointment(900002, start=iso(2880), end=iso(2930)),
        ],
    )
    now = dt_util.now()
    events = await get_events(hass, now, now + timedelta(hours=6))
    assert len(events) == 1


async def test_cancelled_lessons_stay_visible_but_marked(hass, config_entry, mock_api):
    """Hiding them would make a dropped lesson indistinguishable from a gap."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(
                900001, start=iso(60), end=iso(110), status="GEANNULEERD"
            )
        ],
    )
    now = dt_util.now()
    events = await get_events(hass, now, now + timedelta(days=1))
    assert events[0]["summary"].startswith("Vervallen: ")


async def test_overlapping_lessons_both_appear(hass, config_entry, mock_api):
    """Two appointments at the same moment are legitimate — show both."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110), subject="Frans"),
            payloads.appointment(
                900002, start=iso(60), end=iso(110), subject="mentoraat"
            ),
        ],
    )
    now = dt_util.now()
    events = await get_events(hass, now, now + timedelta(days=1))
    assert sorted(event["summary"] for event in events) == ["Frans", "mentoraat"]


async def test_lesson_details_land_on_the_event(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[payloads.appointment(900001, start=iso(60), end=iso(110))],
    )
    now = dt_util.now()
    event = (await get_events(hass, now, now + timedelta(days=1)))[0]

    assert event["summary"] == "wiskunde B"
    assert event["location"] == "217"
    assert "Docent: Abc" in event["description"]


async def test_empty_schedule_yields_no_events(hass, config_entry, mock_api):
    """A holiday week is normal, not a failure."""
    await setup_with(hass, config_entry, mock_api, appointments=[])

    now = dt_util.now()
    assert await get_events(hass, now, now + timedelta(days=1)) == []
    assert hass.states.get(calendar_id(hass)).state == "off"


async def test_a_failing_out_of_window_fetch_surfaces_an_error(
    hass, config_entry, mock_api
):
    """Returning an empty week would look like a holiday instead of a failure."""
    await setup_with(hass, config_entry, mock_api)
    mock_api.async_get_appointments.side_effect = SomtodayApiError(503)

    far_start = dt_util.now() + timedelta(days=90)
    with pytest.raises(HomeAssistantError):
        await get_events(hass, far_start, far_start + timedelta(days=1))
