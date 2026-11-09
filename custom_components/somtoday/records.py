"""School records beyond the timetable: holidays, grades, absences and more.

Kept apart from :mod:`models` on purpose. Those models feed the timetable and
homework entities that everyone in the household sees; several of the records
here (grades, absences, measures, messages) are confidential and only leave the
integration through :mod:`confidential`, after a per-user permission check.

Field names come from live responses on a parent account (2026-11-09) and,
for messages and measures, from Somtoday's own app source, because the test
account had none. Every parser tolerates missing fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .models import _self_link_id, clean_text, parse_datetime

# Somtoday sends several timestamps without an offset ("2026-11-07T22:20:13").
# They are Dutch school times, so they are read as Amsterdam time rather than
# whatever zone the Home Assistant install happens to be in.
SOMTODAY_TZ = ZoneInfo("Europe/Amsterdam")


def parse_local_datetime(value: Any) -> datetime | None:
    """Parse a Somtoday timestamp, reading one without offset as Dutch time."""
    if not value or not isinstance(value, str):
        return None
    if (aware := parse_datetime(value)) is not None:
        return aware
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None
    return naive.replace(tzinfo=SOMTODAY_TZ) if naive.tzinfo is None else naive


def parse_date(value: Any) -> date | None:
    """Parse a ``YYYY-MM-DD`` value (or the date part of a timestamp)."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------
# Public records â€” fine for every household member
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Holiday:
    """A school holiday (``vakanties/leerling/{id}``)."""

    name: str
    start: date
    end: date

    def as_dict(self) -> dict[str, Any]:
        """Return the attribute shape."""
        return {"name": self.name, "start": _iso(self.start), "end": _iso(self.end)}


def parse_holiday(payload: dict[str, Any]) -> Holiday | None:
    """Build a :class:`Holiday`; drop one without usable dates."""
    start = parse_date(payload.get("beginDatum"))
    end = parse_date(payload.get("eindDatum"))
    if start is None or end is None or end < start:
        return None
    return Holiday(
        name=clean_text(payload.get("naam")) or "Vakantie", start=start, end=end
    )


@dataclass(frozen=True)
class SchoolYear:
    """The current school year (``schooljaren/huidig``)."""

    name: str
    start: date
    end: date

    def as_dict(self) -> dict[str, Any]:
        """Return the attribute shape."""
        return {"name": self.name, "start": _iso(self.start), "end": _iso(self.end)}


def parse_school_year(payload: dict[str, Any] | None) -> SchoolYear | None:
    """Build a :class:`SchoolYear` from the ``schooljaren/huidig`` object."""
    if not isinstance(payload, dict):
        return None
    start = parse_date(payload.get("vanafDatum"))
    end = parse_date(payload.get("totDatum"))
    if start is None or end is None:
        return None
    return SchoolYear(name=str(payload.get("naam") or ""), start=start, end=end)


@dataclass(frozen=True)
class Placement:
    """Which class, year and school a student is in (``plaatsingen``)."""

    uuid: str | None
    class_name: str | None
    year: int | None
    programme: str | None
    location: str | None
    current: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the attribute shape (the UUID is internal only)."""
        return {
            "class_name": self.class_name,
            "year": self.year,
            "programme": self.programme,
            "location": self.location,
        }


def parse_placement(payload: dict[str, Any]) -> Placement:
    """Build a :class:`Placement`."""
    return Placement(
        uuid=payload.get("UUID"),
        class_name=clean_text(payload.get("stamgroepnaam")),
        year=payload.get("leerjaar") if isinstance(payload.get("leerjaar"), int) else None,
        programme=clean_text(payload.get("opleidingsnaam")),
        location=clean_text((payload.get("vestiging") or {}).get("naam")),
        current=bool(payload.get("huidig")),
    )


def parse_subjects(items: list[dict[str, Any]]) -> dict[str, str]:
    """Return ``{abbreviation: full name}`` from ``vakkeuzes`` items."""
    subjects: dict[str, str] = {}
    for item in items:
        vak = item.get("vak") or {}
        short, name = clean_text(vak.get("afkorting")), clean_text(vak.get("naam"))
        if short and name:
            subjects[short] = name
    return subjects


@dataclass(frozen=True)
class Assignment:
    """Work to hand in, with a submission window (``inlevermomenten``)."""

    uid: str
    subject: str | None
    topic: str | None
    description: str | None
    opens: datetime | None
    deadline: datetime | None
    handed_in: int | None
    expected: int | None
    student_ids: frozenset[int] = field(default_factory=frozenset)

    def status(self, now: datetime) -> str:
        """Return ``handed_in``, ``late``, ``open`` or ``upcoming``.

        A parent account does not get the per-student submission status back,
        so ``handed_in`` relies on Somtoday's own counters when present.
        """
        if self.expected and self.handed_in is not None and self.handed_in >= self.expected:
            return "handed_in"
        if self.deadline is not None and self.deadline < now:
            return "late"
        if self.opens is not None and self.opens > now:
            return "upcoming"
        return "open"

    def as_dict(self, now: datetime) -> dict[str, Any]:
        """Return the attribute shape."""
        return {
            "uid": self.uid,
            "subject": self.subject,
            "topic": self.topic,
            "description": self.description,
            "opens": _iso(self.opens),
            "deadline": _iso(self.deadline),
            "status": self.status(now),
        }


def parse_assignment(
    payload: dict[str, Any], subjects: dict[str, str] | None = None
) -> Assignment | None:
    """Build an :class:`Assignment` from a study-guide item with a hand-in window."""
    item = payload.get("studiewijzerItem")
    if not isinstance(item, dict):
        return None
    item_id = _self_link_id(item) or _self_link_id(payload)
    if item_id is None:
        return None
    moments = [m for m in item.get("inlevermomenten") or [] if isinstance(m, dict)]
    moment = moments[-1] if moments else {}
    vak = (payload.get("lesgroep") or {}).get("vak") or {}
    short = clean_text(vak.get("afkorting"))
    wrapper = (payload.get("additionalObjects") or {}).get("leerlingen") or {}
    ids = {
        found
        for student in wrapper.get("items") or []
        if isinstance(student, dict) and (found := _self_link_id(student)) is not None
    }
    return Assignment(
        uid=str(item_id),
        subject=clean_text(vak.get("naam")) or (subjects or {}).get(short or "") or short,
        topic=clean_text(item.get("onderwerp")),
        description=clean_text(item.get("omschrijving")),
        opens=parse_local_datetime(moment.get("startGeldigheid")),
        deadline=parse_local_datetime(moment.get("eindGeldigheid"))
        or parse_datetime(payload.get("datumTijd")),
        handed_in=moment.get("inleveringenAantal")
        if isinstance(moment.get("inleveringenAantal"), int)
        else None,
        expected=moment.get("inleveringenVerwacht")
        if isinstance(moment.get("inleveringenVerwacht"), int)
        else None,
        student_ids=frozenset(ids),
    )


@dataclass(frozen=True)
class Announcement:
    """A nationwide Somtoday notice: outage, maintenance (``mededelingen``)."""

    uid: str
    subject: str | None
    body: str | None
    level: str | None
    start: datetime | None
    end: datetime | None

    def as_dict(self) -> dict[str, Any]:
        """Return the attribute shape."""
        return {
            "uid": self.uid,
            "subject": self.subject,
            "body": self.body,
            "level": (self.level or "").lower() or None,
            "start": _iso(self.start),
            "end": _iso(self.end),
        }


def parse_announcement(payload: dict[str, Any]) -> Announcement | None:
    """Build an :class:`Announcement`."""
    uid = payload.get("id") or _self_link_id(payload)
    if uid is None:
        return None
    return Announcement(
        uid=str(uid),
        subject=clean_text(payload.get("onderwerp")),
        body=clean_text(payload.get("inhoud")),
        level=payload.get("notificatieNiveau"),
        start=parse_local_datetime(payload.get("startPublicatie")),
        end=parse_local_datetime(payload.get("eindPublicatie")),
    )


# --------------------------------------------------------------------------
# Confidential records â€” only via the permission-checked service
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Grade:
    """One result from the progress or exam file."""

    uid: str
    subject: str | None
    subject_short: str | None
    description: str | None
    result: str | None
    value: float | None
    sufficient: bool | None
    weight: float | None
    period: int | None
    test_type: str | None
    test_code: str | None
    resit: str | None
    entered: datetime | None
    exam_file: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the service-response shape."""
        return {
            "uid": self.uid,
            "subject": self.subject,
            "subject_short": self.subject_short,
            "description": self.description,
            "result": self.result,
            "value": self.value,
            "sufficient": self.sufficient,
            "weight": self.weight,
            "period": self.period,
            "test_type": self.test_type,
            "test_code": self.test_code,
            "resit": self.resit,
            "entered": _iso(self.entered),
            "exam_file": self.exam_file,
        }


def parse_grade(
    payload: dict[str, Any],
    subjects: dict[str, str] | None = None,
    exam_file: bool = False,
) -> Grade | None:
    """Build a :class:`Grade`; a column without any result is dropped."""
    uid = _self_link_id(payload)
    result = clean_text(payload.get("formattedResultaat"))
    value = _num(payload.get("cijfer"))
    if uid is None or (result is None and value is None):
        return None
    short = clean_text((payload.get("additionalObjects") or {}).get("vaknaam"))
    herkansing = payload.get("herkansing")
    return Grade(
        uid=str(uid),
        subject=(subjects or {}).get(short or "") or short,
        subject_short=short,
        description=clean_text(payload.get("omschrijving")),
        result=result,
        value=value,
        sufficient=payload.get("isVoldoende")
        if isinstance(payload.get("isVoldoende"), bool)
        else None,
        weight=_num(payload.get("weging")),
        period=payload.get("periode") if isinstance(payload.get("periode"), int) else None,
        test_type=clean_text(payload.get("toetssoort")),
        test_code=clean_text(payload.get("toetscode")),
        resit=None if herkansing in (None, "Geen") else str(herkansing),
        entered=parse_local_datetime(
            payload.get("datumInvoer") or payload.get("datumInvoerEerstePoging")
        ),
        exam_file=exam_file,
    )


@dataclass(frozen=True)
class SubjectAverage:
    """A subject's average (``vakkeuzes/plaatsing/{uuid}/vakgemiddelden``)."""

    subject: str | None
    subject_short: str | None
    result: str | None
    value: float | None
    sufficient: bool | None
    level: str | None
    alt_level: str | None
    alt_result: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return the service-response shape."""
        return {
            "subject": self.subject,
            "subject_short": self.subject_short,
            "result": self.result,
            "value": self.value,
            "sufficient": self.sufficient,
            "level": self.level,
            "alt_level": self.alt_level,
            "alt_result": self.alt_result,
        }


def parse_averages(payload: dict[str, Any] | None) -> tuple[list[SubjectAverage], float | None]:
    """Return every subject average plus the overall progress average."""
    if not isinstance(payload, dict):
        return [], None
    averages: list[SubjectAverage] = []
    for item in payload.get("gemiddelden") or []:
        if not isinstance(item, dict):
            continue
        vak = (item.get("vakkeuze") or {}).get("vak") or {}
        main = item.get("voortgangsdossierResultaat") or {}
        alt = item.get("voortgangsdossierResultaatAfwijkend") or {}
        averages.append(
            SubjectAverage(
                subject=clean_text(vak.get("naam")),
                subject_short=clean_text(vak.get("afkorting")),
                result=clean_text(main.get("formattedResultaat")),
                value=_num(main.get("cijfer")),
                sufficient=main.get("isVoldoende")
                if isinstance(main.get("isVoldoende"), bool)
                else None,
                level=clean_text(item.get("niveauOmschrijving")),
                alt_level=clean_text(item.get("afwijkendNiveauOmschrijving")),
                alt_result=clean_text(alt.get("formattedResultaat")),
            )
        )
    return averages, _num(payload.get("voortgangsdossierGemiddelde"))


# ``registratieOverzicht`` lists, in the order they are reported.
REGISTRATION_KINDS = {
    "ongeoorloofdAfwezig": "unauthorised_absence",
    "geoorloofdAfwezig": "authorised_absence",
    "afwezigWaarnemingen": "absence_observed",
    "teLaat": "late",
    "verwijderd": "removed_from_class",
    "huiswerkNietGemaakt": "homework_not_done",
    "materiaalNietInOrde": "materials_missing",
}


@dataclass(frozen=True)
class Registration:
    """An absence, late arrival or similar registration."""

    uid: str
    kind: str
    description: str | None
    start: datetime | None
    end: datetime | None
    authorised: bool | None
    handled: bool | None
    source: str | None
    subject: str | None
    period: int | None

    def as_dict(self) -> dict[str, Any]:
        """Return the service-response shape."""
        return {
            "uid": self.uid,
            "kind": self.kind,
            "description": self.description,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "authorised": self.authorised,
            "handled": self.handled,
            "source": self.source,
            "subject": self.subject,
            "period": self.period,
        }


def parse_registrations(payload: dict[str, Any] | None) -> list[Registration]:
    """Flatten a ``registratieOverzicht`` into one list, newest first."""
    if not isinstance(payload, dict):
        return []
    found: list[Registration] = []
    for key, kind in REGISTRATION_KINDS.items():
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            start = parse_local_datetime(item.get("begin"))
            appointment = item.get("afspraak") or {}
            vak = appointment.get("vak") or {}
            found.append(
                Registration(
                    uid=f"{kind}:{item.get('registratieId')}:{_iso(start)}",
                    kind=kind,
                    description=clean_text(item.get("omschrijving")),
                    start=start,
                    end=parse_local_datetime(item.get("eind")),
                    authorised=item.get("geoorloofd")
                    if isinstance(item.get("geoorloofd"), bool)
                    else None,
                    handled=item.get("afgehandeld")
                    if isinstance(item.get("afgehandeld"), bool)
                    else None,
                    source=item.get("registratieSoort"),
                    subject=clean_text(vak.get("naam")) or clean_text(vak.get("afkorting")),
                    period=appointment.get("beginLesuur")
                    if isinstance(appointment.get("beginLesuur"), int)
                    else None,
                )
            )
    return sorted(found, key=lambda r: r.start or datetime.min.replace(tzinfo=SOMTODAY_TZ), reverse=True)


@dataclass(frozen=True)
class Measure:
    """A disciplinary measure (``maatregeltoekenningen/actief``)."""

    uid: str
    description: str | None
    date: datetime | None
    remarks: str | None
    completed: bool | None
    automatic: bool | None

    def as_dict(self) -> dict[str, Any]:
        """Return the service-response shape."""
        return {
            "uid": self.uid,
            "description": self.description,
            "date": _iso(self.date),
            "remarks": self.remarks,
            "completed": self.completed,
            "automatic": self.automatic,
        }


def parse_measure(payload: dict[str, Any]) -> Measure | None:
    """Build a :class:`Measure`."""
    uid = _self_link_id(payload)
    if uid is None:
        return None
    return Measure(
        uid=str(uid),
        description=clean_text((payload.get("maatregel") or {}).get("omschrijving")),
        date=parse_local_datetime(payload.get("maatregelDatum")),
        remarks=clean_text(payload.get("opmerkingen")),
        completed=payload.get("nagekomen") if isinstance(payload.get("nagekomen"), bool) else None,
        automatic=payload.get("automatischToegekend")
        if isinstance(payload.get("automatischToegekend"), bool)
        else None,
    )


@dataclass(frozen=True)
class Message:
    """One received message thread (``boodschappen/conversaties``).

    Reading this list never marks anything read: Somtoday only does that on an
    explicit ``markeerGelezen`` POST, which this integration never sends.
    """

    uid: str
    subject: str | None
    body: str | None
    sender: str | None
    sent: datetime | None
    unread: bool
    priority: str | None
    kind: str | None
    attachments: int
    message_count: int
    latest_uid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the service-response shape."""
        return {
            "uid": self.uid,
            "subject": self.subject,
            "body": self.body,
            "sender": self.sender,
            "sent": _iso(self.sent),
            "unread": self.unread,
            "priority": (self.priority or "").lower() or None,
            "kind": self.kind,
            "attachments": self.attachments,
            "message_count": self.message_count,
            "latest_uid": self.latest_uid,
        }


def parse_conversation(payload: dict[str, Any]) -> Message | None:
    """Build a :class:`Message` from a received conversation; skip sent/deleted ones."""
    messages = [m for m in payload.get("boodschappen") or [] if isinstance(m, dict)]
    if not messages:
        return None
    received = [
        m
        for m in messages
        if not (m.get("additionalObjects") or {}).get("verzondenDoorGebruiker")
        and (m.get("additionalObjects") or {}).get("actiefVoorGebruiker") is not False
        and not m.get("draft")
    ]
    if not received:
        return None
    newest = received[0]
    uid = _self_link_id(messages[-1]) or _self_link_id(newest)
    if uid is None:
        return None
    sender = (newest.get("additionalObjects") or {}).get("verzenderCorrespondent") or {}
    return Message(
        uid=str(uid),
        subject=clean_text(newest.get("onderwerp")),
        body=clean_text(newest.get("inhoud")),
        sender=clean_text(sender.get("naam")) or "Automatisch bericht",
        sent=parse_local_datetime(newest.get("verzendDatum")),
        unread=bool(payload.get("datumOudsteOngelezenBoodschap")),
        priority=newest.get("prioriteit"),
        kind=newest.get("notificatieType"),
        attachments=len(newest.get("bijlages") or []),
        message_count=len(messages),
        latest_uid=str(_self_link_id(newest) or uid),
    )
