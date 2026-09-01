"""Fictional Somtoday API payloads for the test suite.

Every value here is invented. No real student, school, teacher, token or
account detail is present, and none may ever be added — these files are public.
The *shapes* mirror what Somtoday actually returns, which is the part the tests
need to be faithful to.
"""
from __future__ import annotations

from typing import Any

# Two fictional students, so the multi-student (parent account) paths are
# exercised as well as the single-student ones.
STUDENT_A_ID = 1000001
STUDENT_B_ID = 1000002


def link(item_id: int, item_type: str = "leerling.RLeerlingPrimer") -> dict[str, Any]:
    """Return a Somtoday ``self`` link block."""
    return {
        "id": item_id,
        "rel": "self",
        "type": item_type,
        "href": f"https://api.example.invalid/rest/v1/{item_id}",
    }


def student(
    student_id: int = STUDENT_A_ID,
    first_name: str = "Fien",
    last_name: str = "Voorbeeld",
) -> dict[str, Any]:
    """Return a ``/rest/v1/leerlingen`` item."""
    return {
        "$type": "leerling.RLeerling",
        "links": [link(student_id)],
        "permissions": [],
        "additionalObjects": {},
        "UUID": f"00000000-0000-4000-8000-{student_id:012d}",
        # Distinctive on purpose: the diagnostics test asserts it never leaks.
        "leerlingnummer": 600000 + (student_id - STUDENT_A_ID),
        "roepnaam": first_name,
        "achternaam": last_name,
    }


STUDENTS = {"items": [student()]}
STUDENTS_TWO = {
    "items": [
        student(),
        student(STUDENT_B_ID, "Joost", "Voorbeeld"),
    ]
}

ACCOUNT = {
    "$type": "account.RAccount",
    "links": [link(555000, "account.RAccount")],
    "permissions": [],
    "additionalObjects": {},
    "gebruikersnaam": "fictief-account",
}


def appointment(
    appointment_id: int = 900001,
    *,
    start: str = "2026-09-07T08:30:00.000+02:00",
    end: str = "2026-09-07T09:20:00.000+02:00",
    subject: str | None = "wiskunde B",
    subject_short: str | None = "wisB",
    teacher: str | None = "Abc",
    location: str | None = "217",
    status: str = "ACTIEF",
    period_start: int | None = 1,
    period_end: int | None = 1,
    student_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Return a ``/rest/v1/afspraken`` item.

    Optional fields are all nullable on purpose: Somtoday omits them regularly
    and the parser has to survive every combination.
    """
    additional: dict[str, Any] = {}
    if subject or subject_short:
        additional["vak"] = {
            "$type": "onderwijsinrichting.RVak",
            "links": [link(700001, "onderwijsinrichting.RVak")],
            "additionalObjects": {},
            "afkorting": subject_short,
            "naam": subject,
        }
    if teacher:
        additional["docentAfkortingen"] = teacher
    if student_ids is not None:
        additional["leerlingen"] = {
            "$type": "LinkableWrapper",
            "items": [student(sid) for sid in student_ids],
        }

    return {
        "$type": "participatie.RAfspraak",
        "links": [link(appointment_id, "participatie.RAfspraak")],
        "permissions": [],
        "additionalObjects": additional,
        "afspraakType": {"naam": "Les", "categorie": "Rooster"},
        "locatie": location,
        "beginDatumTijd": start,
        "eindDatumTijd": end,
        "beginLesuur": period_start,
        "eindLesuur": period_end,
        "titel": f"{location} - lesgroep - {teacher}",
        "omschrijving": f"{location} - lesgroep - {teacher}",
        "afspraakStatus": status,
    }


def homework(
    item_id: int = 800001,
    *,
    topic: str = "Hoofdstuk 3 maken",
    kind: str = "HUISWERK",
    description: str | None = "<p>Opgaven 1 t/m 12</p>",
    due: str | None = "2026-09-08T08:30:00.000+02:00",
    subject: str | None = "wiskunde B",
    done: bool | None = None,
    done_for_student: int | None = None,
    with_appointment: bool = True,
) -> dict[str, Any]:
    """Return a study-guide assignment item.

    ``done`` sets the direct ``huiswerkgemaakt`` flag; ``done_for_student``
    instead adds a per-student tick, which is the shape a parent account sees.
    """
    additional: dict[str, Any] = {"huiswerkgemaakt": done}
    if done_for_student is not None:
        additional["swigemaaktVinkjes"] = {
            "$type": "LinkableWrapper",
            "items": [
                {
                    "$type": "studiewijzer.RSWIGemaakt",
                    "links": [link(item_id + 5000, "studiewijzer.RSWIGemaakt")],
                    "additionalObjects": {},
                    "gemaakt": True,
                    "leerling": {
                        "links": [link(done_for_student)],
                        "additionalObjects": {},
                    },
                }
            ],
        }

    payload: dict[str, Any] = {
        "$type": "studiewijzer.RSWIAfspraakToekenning",
        "links": [link(item_id + 1000, "studiewijzer.RSWIAfspraakToekenning")],
        "permissions": [],
        "additionalObjects": additional,
        "studiewijzerItem": {
            "links": [link(item_id, "studiewijzer.RStudiewijzerItem")],
            "additionalObjects": {},
            "onderwerp": topic,
            "huiswerkType": kind,
            "omschrijving": description,
            "tonen": True,
        },
        "lesgroep": {
            "links": [link(700100, "onderwijsinrichting.RLesgroep")],
            "additionalObjects": {},
            "naam": "6wisB1",
            "vak": {
                "links": [link(700001, "onderwijsinrichting.RVak")],
                "additionalObjects": {},
                "afkorting": "wisB",
                "naam": subject,
            },
        },
        "datumTijd": due,
        "aangemaaktOpDatumTijd": "2026-09-01T12:00:00.000+02:00",
    }
    if with_appointment and due:
        payload["afspraak"] = {
            "links": [link(900500, "participatie.RAfspraak")],
            "additionalObjects": {},
            "beginDatumTijd": due,
            "eindDatumTijd": due,
            "locatie": "217",
        }
    return payload


TOKEN_RESPONSE = {
    "access_token": "fictional-access-token",
    "refresh_token": "fictional-refresh-token",
    "id_token": "fictional-id-token",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "openid",
    "somtoday_api_url": "https://api.example.invalid",
    "somtoday_tenant": "Voorbeeld College",
}

ROTATED_TOKEN_RESPONSE = {
    **TOKEN_RESPONSE,
    "access_token": "fictional-access-token-2",
    "refresh_token": "fictional-refresh-token-2",
}

INVALID_GRANT_RESPONSE = {
    "error": "invalid_grant",
    "error_description": "Invalid grant: Invalid token",
}
