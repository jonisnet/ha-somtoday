"""Tests for the Somtoday sensors."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.somtoday.const import DOMAIN

from . import payloads


def entity_id(hass, key: str, student_id: int = payloads.STUDENT_A_ID) -> str:
    """Look an entity up by unique id, so the test is language-independent."""
    registry = er.async_get(hass)
    domain = "calendar" if key == "schedule" else "sensor"
    found = registry.async_get_entity_id(domain, DOMAIN, f"{student_id}_{key}")
    assert found, f"no entity registered for {key}"
    return found


def iso(offset_minutes: int) -> str:
    """Return an ISO timestamp relative to now, as Somtoday would send it."""
    moment = dt_util.now() + timedelta(minutes=offset_minutes)
    return moment.isoformat(timespec="milliseconds")


async def setup_with(hass, config_entry, mock_api, *, appointments=None, homework=None):
    """Set the integration up with a specific schedule and homework list."""
    if appointments is not None:
        mock_api.async_get_appointments.return_value = appointments
    if homework is not None:
        mock_api.async_get_homework.return_value = homework
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_all_sensors_are_created(hass, setup_integration):
    for key in (
        "current_lesson",
        "next_lesson",
        "next_school_day",
        "today",
        "planner",
        "next_week",
        "future_week_2",
        "future_week_8",
        "upcoming_work",
        "open_homework",
        "next_test",
        "last_update",
    ):
        assert hass.states.get(entity_id(hass, key)) is not None


async def test_future_week_sensors_expose_their_offset(hass, setup_integration):
    """Each future week is separately discoverable by the dashboard card."""
    second = hass.states.get(entity_id(hass, "future_week_2"))
    eighth = hass.states.get(entity_id(hass, "future_week_8"))
    assert second.attributes["week_offset"] == 2
    assert eighth.attributes["week_offset"] == 8


async def test_current_lesson_in_progress(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(-20), end=iso(30), subject="biologie")
        ],
    )
    state = hass.states.get(entity_id(hass, "current_lesson"))
    assert state.state == "biologie"
    assert state.attributes["location"] == "217"
    assert state.attributes["teacher"] == "Abc"
    assert state.attributes["cancelled"] is False


async def test_current_lesson_between_lessons(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[payloads.appointment(900001, start=iso(60), end=iso(110))],
    )
    assert hass.states.get(entity_id(hass, "current_lesson")).state == STATE_UNKNOWN


async def test_next_lesson_skips_a_cancelled_one(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(
                900001, start=iso(60), end=iso(110), status="GEANNULEERD"
            ),
            payloads.appointment(
                900002, start=iso(120), end=iso(170), subject="scheikunde"
            ),
        ],
    )
    state = hass.states.get(entity_id(hass, "next_lesson"))
    assert state.attributes["subject"] == "scheikunde"
    assert state.attributes["status"] == "scheduled"


async def test_next_lesson_when_the_day_is_over(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[payloads.appointment(900001, start=iso(-180), end=iso(-130))],
    )
    assert hass.states.get(entity_id(hass, "next_lesson")).state == STATE_UNKNOWN


async def test_next_school_day_summarises_the_day(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110)),
            payloads.appointment(
                900002, start=iso(120), end=iso(170), status="VERVALLEN"
            ),
            payloads.appointment(900003, start=iso(180), end=iso(230)),
        ],
    )
    state = hass.states.get(entity_id(hass, "next_school_day"))
    assert state.attributes["lesson_count"] == 2
    assert state.attributes["cancelled_count"] == 1
    assert len(state.attributes["lessons"]) == 3


async def test_today_summarises_the_current_date(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(-20), end=iso(30)),
            payloads.appointment(
                900002, start=iso(60), end=iso(110), status="VERVALLEN"
            ),
        ],
    )
    state = hass.states.get(entity_id(hass, "today"))
    assert state.attributes["lesson_count"] == 1
    assert state.attributes["cancelled_count"] == 1
    assert len(state.attributes["lessons"]) == 2


async def test_open_homework_counts_only_what_is_left(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        homework=[
            payloads.homework(800001, due=iso(1440)),
            payloads.homework(800002, due=iso(1440), done=True),
            payloads.homework(800003, due=iso(1440), kind="TOETS"),
            payloads.homework(800004, due=iso(-1440)),
        ],
    )
    state = hass.states.get(entity_id(hass, "open_homework"))
    assert state.state == "1"
    assert len(state.attributes["homework"]) == 1
    assert state.attributes["homework"][0]["type"] == "homework"


async def test_open_homework_with_nothing_left(hass, config_entry, mock_api):
    await setup_with(hass, config_entry, mock_api, homework=[])
    assert hass.states.get(entity_id(hass, "open_homework")).state == "0"


async def test_next_test(hass, config_entry, mock_api):
    await setup_with(
        hass,
        config_entry,
        mock_api,
        homework=[
            payloads.homework(800001, due=iso(4320), kind="GROTE_TOETS"),
            payloads.homework(
                800002, due=iso(2880), kind="TOETS", topic="SO hoofdstuk 1"
            ),
            payloads.homework(800003, due=iso(60)),
        ],
    )
    state = hass.states.get(entity_id(hass, "next_test"))
    assert state.attributes["topic"] == "SO hoofdstuk 1"
    assert state.attributes["type"] == "test"


async def test_next_test_when_there_is_none(hass, config_entry, mock_api):
    await setup_with(hass, config_entry, mock_api, homework=[])
    assert hass.states.get(entity_id(hass, "next_test")).state == STATE_UNKNOWN


async def test_lesson_without_optional_fields_still_reports(hass, config_entry, mock_api):
    """Somtoday omits these regularly; the sensor must not break."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(
                900001,
                start=iso(-20),
                end=iso(30),
                subject=None,
                subject_short=None,
                teacher=None,
                location=None,
                period_start=None,
                period_end=None,
            )
        ],
    )
    state = hass.states.get(entity_id(hass, "current_lesson"))
    assert state.state not in (STATE_UNKNOWN, None)
    assert state.attributes["teacher"] is None
    assert state.attributes["location"] is None


async def test_sensors_go_unavailable_when_somtoday_fails(
    hass, config_entry, mock_api
):
    from custom_components.somtoday.api import SomtodayApiError

    await setup_with(hass, config_entry, mock_api)
    coordinator = config_entry.runtime_data.coordinator

    mock_api.async_get_students.side_effect = SomtodayApiError(503)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert hass.states.get(entity_id(hass, "next_lesson")).state == "unavailable"


async def test_last_update_is_a_diagnostic_entity(hass, setup_integration):
    entry = er.async_get(hass).async_get(entity_id(hass, "last_update"))
    assert entry.entity_category == EntityCategory.DIAGNOSTIC
