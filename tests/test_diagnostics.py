"""Tests for the Somtoday diagnostics output.

Somtoday holds a minor's school data and diagnostics files get attached to
public issues, so these tests are the guardrail: they assert that nothing
identifying can appear in the output, not merely that it is redacted today.
"""
from __future__ import annotations

import json

from custom_components.somtoday.diagnostics import (
    async_get_config_entry_diagnostics,
)

from . import payloads
from .test_sensor import iso, setup_with


async def test_no_secrets_or_personal_data_in_the_output(hass, setup_integration):
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    dumped = json.dumps(diagnostics)

    forbidden = [
        # Tokens
        "fictional-refresh-token",
        "fictional-access-token",
        "fictional-id-token",
        # The student, their number and their school
        "Fien",
        "Voorbeeld",
        "600000",
        "Voorbeeld College",
        # Teacher, room and homework detail
        "Abc",
        "Hoofdstuk 3 maken",
        "Opgaven 1 t/m 12",
    ]
    for value in forbidden:
        assert value not in dumped, f"{value!r} leaked into diagnostics"


async def test_config_entry_data_is_redacted(hass, setup_integration):
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    data = diagnostics["entry"]["data"]

    assert data["refresh_token"] == "**REDACTED**"
    assert data["tenant"] == "**REDACTED**"
    # The API base URL is not a secret and is genuinely useful when debugging.
    assert data["api_url"] == "https://api.example.invalid"


async def test_session_state_is_reported_without_the_tokens(hass, setup_integration):
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    session = diagnostics["session"]

    assert session["has_refresh_token"] in (True, False)
    assert "refresh_token" not in session
    assert "access_token" not in session
    assert "access_token_expires_at" in session


async def test_students_are_reported_positionally(hass, config_entry, mock_api):
    mock_api.async_get_students.return_value = payloads.STUDENTS_TWO["items"]
    await setup_with(hass, config_entry, mock_api)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert [student["index"] for student in diagnostics["students"]] == [0, 1]
    for student in diagnostics["students"]:
        assert set(student) == {"index", "lessons", "homework"}


async def test_structural_counts_are_useful(hass, config_entry, mock_api):
    """The point of the summary is that it still explains a schedule bug."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110)),
            payloads.appointment(
                900002, start=iso(120), end=iso(170), status="GEANNULEERD"
            ),
            payloads.appointment(
                900003, start=iso(180), end=iso(230), teacher=None, location=None
            ),
        ],
        homework=[
            payloads.homework(800001, due=iso(1440)),
            payloads.homework(800002, due=iso(2880), kind="TOETS"),
        ],
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    lessons = diagnostics["students"][0]["lessons"]
    homework = diagnostics["students"][0]["homework"]

    assert lessons["count"] == 3
    assert lessons["by_status"]["scheduled"] == 2
    assert lessons["by_status"]["cancelled"] == 1
    assert sorted(lessons["raw_statuses_seen"]) == ["ACTIEF", "GEANNULEERD"]
    assert lessons["with_teacher"] == 2
    assert lessons["with_location"] == 2

    assert homework["count"] == 2
    assert homework["by_type"] == {"homework": 1, "test": 1}
    assert sorted(homework["raw_types_seen"]) == ["HUISWERK", "TOETS"]


async def test_unmapped_status_reaches_diagnostics(hass, config_entry, mock_api):
    """An unknown raw status is exactly what a bug report needs to carry."""
    await setup_with(
        hass,
        config_entry,
        mock_api,
        appointments=[
            payloads.appointment(900001, start=iso(60), end=iso(110), status="IETS_NIEUWS")
        ],
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    lessons = diagnostics["students"][0]["lessons"]
    assert lessons["raw_statuses_seen"] == ["IETS_NIEUWS"]
    assert lessons["by_status"] == {"unknown": 1}
