"""Tests for the persistent log of detected timetable changes."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from custom_components.somtoday.changelog import (
    MAX_ENTRIES,
    RETENTION,
    SomtodayChangeLog,
    prune,
)

from . import payloads
from .test_coordinator import _coordinator
from .test_sensor import entity_id, iso, setup_with


def _entry(detected_at) -> dict:
    return {"detected_at": detected_at.isoformat(), "type": "cancelled"}


def test_prune_keeps_recent_entries_newest_first():
    now = dt_util.utcnow()
    older, newer = now - timedelta(hours=2), now - timedelta(minutes=5)
    kept = prune([_entry(older), _entry(newer)], now)
    assert [entry["detected_at"] for entry in kept] == [
        newer.isoformat(),
        older.isoformat(),
    ]


def test_prune_drops_entries_past_the_retention_window():
    now = dt_util.utcnow()
    assert prune([_entry(now - RETENTION - timedelta(minutes=1))], now) == []


def test_prune_drops_entries_without_a_readable_moment():
    """Better gone than stuck in the log forever."""
    assert prune([{"detected_at": "not a date"}, {}], dt_util.utcnow()) == []


def test_prune_caps_the_number_of_entries():
    now = dt_util.utcnow()
    entries = [_entry(now - timedelta(minutes=i)) for i in range(MAX_ENTRIES + 10)]
    assert len(prune(entries, now)) == MAX_ENTRIES


async def test_the_log_survives_a_reload(hass, hass_storage):
    """It lives in .storage, so a restart does not wipe what came in."""
    log = SomtodayChangeLog(hass, "entry-1")
    await log.async_load()
    hass_storage["somtoday.changes.entry-1"] = {
        "version": 1,
        "key": "somtoday.changes.entry-1",
        "data": {"students": {"555001": [_entry(dt_util.utcnow())]}},
    }
    reloaded = SomtodayChangeLog(hass, "entry-1")
    await reloaded.async_load()
    assert len(reloaded.entries(555001)) == 1


async def test_sensor_shows_when_a_change_came_in(hass, config_entry, mock_api):
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(900001, start=iso(1440), end=iso(1485))
    ]
    await setup_with(hass, config_entry, mock_api)
    sensor = entity_id(hass, "schedule_changes")
    assert hass.states.get(sensor).state == STATE_UNKNOWN

    before = dt_util.utcnow()
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900001, start=iso(1440), end=iso(1485), status="GEANNULEERD"
        )
    ]
    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(sensor)
    assert dt_util.parse_datetime(state.state) >= before.replace(microsecond=0)
    assert state.attributes["change_count"] == 1
    change = state.attributes["changes"][0]
    assert change["type"] == "cancelled"
    assert dt_util.parse_datetime(change["detected_at"]) >= before


async def test_only_the_changed_student_gets_the_change(hass, config_entry, mock_api):
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900001, start=iso(1440), end=iso(1485), student_ids=[payloads.STUDENT_A_ID]
        )
    ]
    await setup_with(hass, config_entry, mock_api)
    mock_api.async_get_appointments.return_value = [
        payloads.appointment(
            900001,
            start=iso(1440),
            end=iso(1485),
            status="GEANNULEERD",
            student_ids=[payloads.STUDENT_A_ID],
        )
    ]
    await _coordinator(config_entry).async_refresh()
    await hass.async_block_till_done()

    a = hass.states.get(entity_id(hass, "schedule_changes"))
    b = hass.states.get(
        entity_id(hass, "schedule_changes", payloads.STUDENT_B_ID)
    )
    assert a.attributes["change_count"] == 1
    assert b.attributes["change_count"] == 0
