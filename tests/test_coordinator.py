"""Tests for the coordinator's polling, splitting and change detection."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.somtoday.const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    EVENT_SCHEDULE_CHANGED,
)

from . import payloads
from .test_sensor import iso, setup_with


def _coordinator(config_entry):
    return config_entry.runtime_data.coordinator


async def test_data_is_keyed_by_student(hass, setup_integration):
    data = _coordinator(setup_integration).data
    assert list(data) == [payloads.STUDENT_A_ID]
    assert data[payloads.STUDENT_A_ID].student.full_name == "Fien Voorbeeld"
    assert len(data[payloads.STUDENT_A_ID].lessons) == 1
    assert len(data[payloads.STUDENT_A_ID].homework) == 1


async def test_appointments_are_split_per_student(hass, config_entry, mock_api):
    """One fetch covers a parent account; the split happens on our side."""
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001, student_ids=[payloads.STUDENT_A_ID]),
        payloads.appointment(900002, student_ids=[payloads.STUDENT_B_ID]),
        payloads.appointment(900003, student_ids=[payloads.STUDENT_B_ID]),
    ]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    data = _coordinator(config_entry).data
    assert len(data[payloads.STUDENT_A_ID].lessons) == 1
    assert len(data[payloads.STUDENT_B_ID].lessons) == 2


async def test_appointments_without_a_student_block_go_to_everyone(
    hass, config_entry, mock_api
):
    """Single-student logins omit the block; that means 'mine', not 'nobody'."""
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    mock_api.async_get_appointments.return_value = [payloads.appointment()]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    data = _coordinator(config_entry).data
    assert len(data[payloads.STUDENT_A_ID].lessons) == 1
    assert len(data[payloads.STUDENT_B_ID].lessons) == 1


async def test_unparseable_appointments_are_skipped_not_fatal(
    hass, config_entry, mock_api
):
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001),
        payloads.appointment(900002, start="", end=""),
        {"links": [], "beginDatumTijd": None},
    ]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert len(_coordinator(config_entry).data[payloads.STUDENT_A_ID].lessons) == 1


async def test_empty_schedule_is_not_an_error(hass, config_entry, mock_api):
    """A holiday week is normal, not a failure."""
    mock_api.async_get_appointments.return_value = []
    mock_api.async_get_homework.return_value = []
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = _coordinator(config_entry)
    assert coordinator.last_update_success is True
    assert coordinator.data[payloads.STUDENT_A_ID].lessons == []


async def test_last_success_time_is_stamped(hass, setup_integration):
    assert _coordinator(setup_integration).last_success_time is not None


async def test_rotated_refresh_token_is_persisted(hass, config_entry, mock_api):
    """Losing a rotated token would lock the account out on the next restart."""
    mock_api.auth.tokens.refresh_token = "rotated-refresh-token"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"


async def test_unchanged_refresh_token_is_not_rewritten(hass, setup_integration):
    assert setup_integration.data[CONF_REFRESH_TOKEN] == "fictional-refresh-token"


# --------------------------------------------------------------------------
# Schedule-change events
# --------------------------------------------------------------------------


async def test_no_event_on_the_first_poll(hass, config_entry, mock_api):
    """The previous timetable lives in memory, so a restart would otherwise
    look like the whole schedule had just appeared."""
    events = async_capture_events(hass, EVENT_SCHEDULE_CHANGED)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert events == []


async def test_a_cancelled_lesson_fires_one_event(hass, config_entry, mock_api):
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001, start=iso(1440), end=iso(1485))
    ]
    await setup_with(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_SCHEDULE_CHANGED)

    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900001, start=iso(1440), end=iso(1485), status="GEANNULEERD"
        )
    ]
    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["change_count"] == 1
    assert data["cancelled_count"] == 1
    assert data["changes"][0]["type"] == "cancelled"
    assert data["changes"][0]["subject"] == "wiskunde B"


async def test_a_whole_day_dropped_is_still_one_event(hass, config_entry, mock_api):
    """One notification should cover the day, not seven."""
    day = [
        payloads.appointment(900000 + i, start=iso(1440 + i * 60), end=iso(1485 + i * 60))
        for i in range(7)
    ]
    mock_api.async_get_appointments.return_value = day
    await setup_with(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_SCHEDULE_CHANGED)

    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900000 + i,
            start=iso(1440 + i * 60),
            end=iso(1485 + i * 60),
            status="VERVALLEN",
        )
        for i in range(7)
    ]
    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["change_count"] == 7


async def test_the_event_carries_the_student_device(hass, config_entry, mock_api):
    """Without this a parent account cannot tell which child changed."""
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001, start=iso(1440), end=iso(1485))
    ]
    await setup_with(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_SCHEDULE_CHANGED)

    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900001, start=iso(1440), end=iso(1485), status="GEANNULEERD"
        )
    ]
    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, str(payloads.STUDENT_A_ID))}
    )
    assert events[0].data["device_id"] == device.id
    assert events[0].data["student_id"] == payloads.STUDENT_A_ID


async def test_an_unchanged_schedule_stays_quiet(hass, config_entry, mock_api):
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001, start=iso(1440), end=iso(1485))
    ]
    await setup_with(hass, config_entry, mock_api)
    events = async_capture_events(hass, EVENT_SCHEDULE_CHANGED)

    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    assert events == []
