"""Tests for the school records beyond the timetable.

All payloads are fictional; the shapes follow live Somtoday responses.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.somtoday.api import SomtodayApiError
from custom_components.somtoday.auth import SomtodayInvalidAuth
from custom_components.somtoday.extras import (
    AccountExtras,
    SeenRecords,
    StudentExtras,
    async_fetch_account_extras,
    async_fetch_student_extras,
    record_ids,
)
from custom_components.somtoday.records import (
    SOMTODAY_TZ,
    parse_assignment,
    parse_averages,
    parse_conversation,
    parse_grade,
    parse_holiday,
    parse_local_datetime,
    parse_measure,
    parse_registrations,
)

from .payloads import STUDENT_A_ID, STUDENT_B_ID, link


def grade(uid=700001, result="7,5", value=7.5, short="wi", entered="2026-11-07T22:20:13"):
    return {
        "links": [link(uid, "resultaten.RGeldendVoortgangsdossierResultaat")],
        "additionalObjects": {"vaknaam": short},
        "cijfer": value,
        "formattedResultaat": result,
        "isVoldoende": True,
        "periode": 1,
        "type": "Toetskolom",
        "toetscode": "T1",
        "omschrijving": "Hoofdstuk 1",
        "weging": 2,
        "datumInvoerEerstePoging": entered,
        "herkansing": "Geen",
        "toetssoort": "Proefwerk",
    }


def assignment(uid=600001, end="2026-11-20T23:59:00", students=None):
    return {
        "studiewijzerItem": {
            "links": [link(uid, "studiewijzer.RStudiewijzerItem")],
            "onderwerp": "Werkstuk",
            "omschrijving": "<p>Inleveren als pdf</p>",
            "inlevermomenten": [
                {
                    "startGeldigheid": "2026-11-01T08:00:00",
                    "eindGeldigheid": end,
                    "inleveringenAantal": 0,
                    "inleveringenVerwacht": 1,
                }
            ],
        },
        "lesgroep": {"vak": {"afkorting": "ak", "naam": "aardrijkskunde"}},
        "datumTijd": "2026-11-20T08:30:00.000+01:00",
        "additionalObjects": {
            "leerlingen": {"items": [{"links": [link(s)]} for s in (students or [])]}
        },
    }


def test_naive_somtoday_times_are_dutch_time():
    parsed = parse_local_datetime("2026-11-07T22:20:13")
    assert parsed.tzinfo is SOMTODAY_TZ
    assert parsed.utcoffset() == timedelta(hours=1)
    # A value with an offset keeps it.
    assert parse_local_datetime("2026-09-07T08:30:00+02:00").utcoffset() == timedelta(hours=2)
    assert parse_local_datetime("nonsense") is None


def test_parse_grade_maps_subject_names():
    parsed = parse_grade(grade(), {"wi": "wiskunde"})
    assert parsed.subject == "wiskunde"
    assert parsed.subject_short == "wi"
    assert parsed.result == "7,5"
    assert parsed.value == 7.5
    assert parsed.weight == 2
    assert parsed.resit is None
    assert parsed.entered.year == 2026


def test_parse_grade_without_any_result_is_dropped():
    assert parse_grade(grade(result=None, value=None)) is None


def test_label_grades_are_kept():
    """A 'G' (goed) is a result too, even without a usable number."""
    parsed = parse_grade(grade(result="G", value=None))
    assert parsed.result == "G"
    assert parsed.value is None


def test_parse_holiday_rejects_inverted_dates():
    assert parse_holiday({"naam": "x", "beginDatum": "2026-12-19", "eindDatum": "2026-12-01"}) is None
    holiday = parse_holiday({"naam": "Kerstvakantie", "beginDatum": "2026-12-19", "eindDatum": "2027-01-03"})
    assert holiday.start == date(2026, 12, 19)


def test_parse_assignment_and_status():
    item = parse_assignment(assignment())
    assert item.subject == "aardrijkskunde"
    assert item.description == "Inleveren als pdf"
    before = datetime(2026, 11, 10, tzinfo=timezone.utc)
    after = datetime(2026, 11, 25, tzinfo=timezone.utc)
    early = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert item.status(before) == "open"
    assert item.status(after) == "late"
    assert item.status(early) == "upcoming"


def test_registrations_are_flattened_newest_first():
    payload = {
        "geoorloofdAfwezig": [
            {"omschrijving": "Ziek", "begin": "2026-10-02T08:30:00", "eind": "2026-10-02T12:30:00",
             "geoorloofd": True, "afgehandeld": True, "registratieSoort": "ABSENTIEMELDING",
             "registratieId": 1},
        ],
        "teLaat": [
            {"omschrijving": "Te laat", "begin": "2026-11-03T08:35:00", "eind": "2026-11-03T09:20:00",
             "geoorloofd": False, "afgehandeld": False, "registratieSoort": "WAARNEMING",
             "registratieId": 2, "afspraak": {"beginLesuur": 1, "vak": {"naam": "Engels"}}},
        ],
        "huiswerkNietGemaakt": [],
    }
    found = parse_registrations(payload)
    assert [r.kind for r in found] == ["late", "authorised_absence"]
    assert found[0].subject == "Engels"
    assert found[0].period == 1
    assert len({r.uid for r in found}) == 2


def test_parse_averages():
    payload = {
        "voortgangsdossierGemiddelde": 7.2,
        "gemiddelden": [
            {
                "vakkeuze": {"vak": {"afkorting": "en", "naam": "Engelse taal"}},
                "niveauOmschrijving": "BB",
                "afwijkendNiveauOmschrijving": "KB",
                "voortgangsdossierResultaat": {"cijfer": 8.9, "formattedResultaat": "8,9", "isVoldoende": True},
                "voortgangsdossierResultaatAfwijkend": {"formattedResultaat": "8,6"},
            }
        ],
    }
    averages, overall = parse_averages(payload)
    assert overall == 7.2
    assert averages[0].subject == "Engelse taal"
    assert averages[0].alt_result == "8,6"


def test_parse_measure():
    measure = parse_measure(
        {"links": [link(5)], "maatregel": {"omschrijving": "Nablijven"},
         "maatregelDatum": "2026-11-10T15:00:00", "nagekomen": False}
    )
    assert measure.description == "Nablijven"
    assert measure.completed is False


def _message(uid, sent_by_user=False, active=True):
    return {
        "links": [link(uid, "boodschappen.RBoodschap")],
        "onderwerp": "Ouderavond",
        "inhoud": "<p>Welkom</p>",
        "verzendDatum": "2026-11-08T10:00:00",
        "prioriteit": "NORMAAL",
        "notificatieType": "Bericht",
        "bijlages": [{}],
        "additionalObjects": {
            "verzondenDoorGebruiker": sent_by_user,
            "actiefVoorGebruiker": active,
            "verzenderCorrespondent": {"naam": "Mentor"},
        },
    }


def test_parse_conversation_received_and_unread():
    convo = {"boodschappen": [_message(3), _message(1)], "datumOudsteOngelezenBoodschap": "2026-11-08T10:00:00"}
    message = parse_conversation(convo)
    assert message.uid == "1"  # the thread is named after its first message
    assert message.latest_uid == "3"
    assert message.unread is True
    assert message.body == "Welkom"
    assert message.sender == "Mentor"
    assert message.attachments == 1


def test_sent_and_deleted_conversations_are_skipped():
    assert parse_conversation({"boodschappen": [_message(1, sent_by_user=True)]}) is None
    assert parse_conversation({"boodschappen": [_message(1, active=False)]}) is None


def _client(**overrides):
    client = MagicMock()
    defaults = {
        "async_get_holidays": [],
        "async_get_school_year": {},
        "async_get_placements": [{"UUID": "p-1", "huidig": True, "stamgroepnaam": "1A", "leerjaar": 1}],
        "async_get_subjects": [{"vak": {"afkorting": "wi", "naam": "wiskunde"}}],
        "async_get_assignments": [],
        "async_get_grades": [grade()],
        "async_get_averages": {"gemiddelden": [], "voortgangsdossierGemiddelde": 7.0},
        "async_get_registrations": {},
        "async_get_measures": [],
        "async_get_announcements": [],
        "async_get_conversations": [],
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return client


async def test_a_refused_category_does_not_sink_the_others():
    client = _client(async_get_registrations=SomtodayApiError(403))
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    extras = await async_fetch_student_extras(client, STUDENT_A_ID, None, now)
    assert "absences" not in extras.available
    assert "grades" in extras.available
    assert extras.grades[0].subject == "wiskunde"
    assert extras.placement.class_name == "1A"
    assert extras.overall_average == 7.0


async def test_a_dead_session_still_surfaces():
    client = _client(async_get_grades=SomtodayInvalidAuth("gone"))
    with pytest.raises(SomtodayInvalidAuth):
        await async_fetch_student_extras(client, STUDENT_A_ID, None, datetime.now(timezone.utc))


async def test_static_data_is_reused_and_averages_only_refetched_on_change():
    client = _client()
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    first = await async_fetch_student_extras(client, STUDENT_A_ID, None, now)
    second = await async_fetch_student_extras(client, STUDENT_A_ID, first, now + timedelta(minutes=30))
    assert client.async_get_placements.await_count == 1
    assert client.async_get_averages.await_count == 1
    assert second.placement.class_name == "1A"

    client.async_get_grades.return_value = [grade(), grade(700002, "5,0", 5.0)]
    await async_fetch_student_extras(client, STUDENT_A_ID, second, now + timedelta(hours=1))
    assert client.async_get_averages.await_count == 2


async def test_assignments_for_another_child_are_filtered():
    client = _client(async_get_assignments=[assignment(1, students=[STUDENT_B_ID]), assignment(2)])
    extras = await async_fetch_student_extras(client, STUDENT_A_ID, None, datetime.now(timezone.utc))
    assert [a.uid for a in extras.assignments] == ["2"]


async def test_account_extras_messages():
    client = _client(async_get_conversations=[{"boodschappen": [_message(1)]}])
    account = await async_fetch_account_extras(client)
    assert "messages" in account.available
    assert record_ids("message", None, account) == {"1"}


def test_record_ids_unknown_when_not_fetched():
    assert record_ids("grade", StudentExtras(), AccountExtras()) is None


async def test_seen_records_learn_silently_then_report_new(hass):
    seen = SeenRecords(hass, "entry")
    await seen.async_load()
    assert seen.diff("1", "grade", {"a", "b"}) == set()
    assert seen.diff("1", "grade", {"a", "b", "c"}) == {"c"}
    # Disappeared ids are forgotten, then a return counts as new again.
    assert seen.diff("1", "grade", {"c"}) == set()
    assert seen.diff("1", "grade", {"a", "c"}) == {"a"}
