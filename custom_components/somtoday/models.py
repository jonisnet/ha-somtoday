"""Normalised models for the Somtoday integration.

Somtoday's REST payloads are deeply nested, inconsistently populated and use a
Dutch vocabulary that is not an enum anywhere. Everything the entities consume
goes through this module first, so a missing field degrades to ``None`` in one
place instead of raising somewhere inside an entity property.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import (
    HOMEWORK_TYPE_MAP,
    LESSON_STATUS_MAP,
    NEW_ISSUE_URL,
    TEST_TYPES,
    HomeworkType,
    LessonStatus,
)

_LOGGER = logging.getLogger(__name__)

# Somtoday puts rich text in ``omschrijving``. Entity attributes want plain
# text, so tags are stripped and entities unescaped.
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Raw values we have already warned about, so each unknown one surfaces exactly
# once per Home Assistant run rather than on every poll.
_logged_unknown_statuses: set[str] = set()
_logged_unknown_homework_types: set[str] = set()


@dataclass(frozen=True)
class Student:
    """A student visible to the signed-in Somtoday account."""

    student_id: int
    uuid: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        """Return the student's full name, or a fallback when both parts are empty."""
        name = " ".join(part for part in (self.first_name, self.last_name) if part)
        return name or f"Leerling {self.student_id}"


@dataclass(frozen=True)
class Lesson:
    """One scheduled appointment from the student's timetable."""

    uid: str
    start: datetime
    end: datetime
    subject: str | None = None
    subject_short: str | None = None
    location: str | None = None
    teacher: str | None = None
    status: LessonStatus = LessonStatus.SCHEDULED
    raw_status: str | None = None
    period_start: int | None = None
    period_end: int | None = None
    title: str | None = None
    student_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def is_cancelled(self) -> bool:
        """Return whether this lesson has been dropped."""
        return self.status is LessonStatus.CANCELLED

    @property
    def display_name(self) -> str:
        """Return the best human label for this lesson.

        Falls back through subject name, subject abbreviation and Somtoday's
        own ``titel`` before giving up — a schedule entry with none of those is
        rare but not impossible (an exam slot, a school trip).
        """
        return self.subject or self.subject_short or self.title or "Les"


@dataclass(frozen=True)
class HomeworkItem:
    """One study-guide item: homework, a test, or a large test."""

    uid: str
    kind: HomeworkType
    due: datetime | None = None
    subject: str | None = None
    topic: str | None = None
    description: str | None = None
    raw_kind: str | None = None
    done: bool = False

    @property
    def is_test(self) -> bool:
        """Return whether this item is a test rather than plain homework."""
        return self.kind in TEST_TYPES

    @property
    def display_name(self) -> str:
        """Return the best human label for this item."""
        return self.topic or self.subject or "Huiswerk"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_datetime(value: Any) -> datetime | None:
    """Parse a Somtoday ISO 8601 timestamp into an aware datetime.

    Somtoday sends offsets (``+02:00``), so the result is always timezone-aware
    and daylight-saving transitions are handled by the offset itself rather
    than by us guessing a zone. A value without an offset is rejected rather
    than assumed to be local time — a silently wrong lesson time is worse than
    a missing one.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _LOGGER.debug("Could not parse Somtoday timestamp %r", value)
        return None
    if parsed.tzinfo is None:
        _LOGGER.debug("Ignoring Somtoday timestamp without offset: %r", value)
        return None
    return parsed


def clean_text(value: Any) -> str | None:
    """Strip HTML from a Somtoday free-text field and collapse whitespace."""
    if not value or not isinstance(value, str):
        return None
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def _self_link_id(payload: dict[str, Any]) -> int | None:
    """Return the numeric id from a payload's ``self`` link."""
    for link in payload.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "self":
            value = link.get("id")
            if isinstance(value, int):
                return value
    return None


def map_lesson_status(raw: Any) -> LessonStatus:
    """Map Somtoday's ``afspraakStatus`` onto a canonical status.

    The vocabulary is undocumented, so an unrecognised value maps to
    ``UNKNOWN`` and is logged once with a ready-to-paste issue link rather than
    being guessed at. Reporting a cancelled lesson as scheduled would be worse
    than reporting it as unknown.
    """
    if not raw or not isinstance(raw, str):
        return LessonStatus.UNKNOWN

    normalised = raw.strip().upper()
    if normalised in LESSON_STATUS_MAP:
        return LESSON_STATUS_MAP[normalised]

    if normalised not in _logged_unknown_statuses:
        _logged_unknown_statuses.add(normalised)
        _LOGGER.warning(
            "Unrecognised Somtoday lesson status %r — reported as 'unknown'. "
            "Help us map it by opening an issue at %s",
            raw,
            NEW_ISSUE_URL,
        )
    return LessonStatus.UNKNOWN


def map_homework_type(raw: Any) -> HomeworkType:
    """Map Somtoday's ``huiswerkType`` onto a canonical type."""
    if not raw or not isinstance(raw, str):
        return HomeworkType.UNKNOWN

    normalised = raw.strip().upper()
    if normalised in HOMEWORK_TYPE_MAP:
        return HOMEWORK_TYPE_MAP[normalised]

    if normalised not in _logged_unknown_homework_types:
        _logged_unknown_homework_types.add(normalised)
        _LOGGER.warning(
            "Unrecognised Somtoday homework type %r — reported as 'unknown'. "
            "Help us map it by opening an issue at %s",
            raw,
            NEW_ISSUE_URL,
        )
    return HomeworkType.UNKNOWN


def parse_student(payload: dict[str, Any]) -> Student | None:
    """Build a :class:`Student` from a ``/rest/v1/leerlingen`` item."""
    student_id = _self_link_id(payload)
    if student_id is None:
        return None
    return Student(
        student_id=student_id,
        uuid=str(payload.get("UUID") or student_id),
        first_name=str(payload.get("roepnaam") or "").strip(),
        last_name=str(payload.get("achternaam") or "").strip(),
    )


def _lesson_student_ids(payload: dict[str, Any]) -> frozenset[int]:
    """Return the ids of the students attached to an appointment.

    Empty when Somtoday did not include them — on a single-student account the
    ``leerlingen`` block is often omitted, so an empty set must be read as
    "applies to everyone", not "applies to nobody".
    """
    wrapper = (payload.get("additionalObjects") or {}).get("leerlingen") or {}
    ids: set[int] = set()
    for item in wrapper.get("items") or []:
        if isinstance(item, dict) and (found := _self_link_id(item)) is not None:
            ids.add(found)
    return frozenset(ids)


def parse_lesson(payload: dict[str, Any]) -> Lesson | None:
    """Build a :class:`Lesson` from a ``/rest/v1/afspraken`` item.

    Returns ``None`` for an appointment without a usable start and end — those
    cannot be placed on a calendar and are dropped rather than faked.
    """
    start = parse_datetime(payload.get("beginDatumTijd"))
    end = parse_datetime(payload.get("eindDatumTijd"))
    if start is None or end is None:
        return None
    if end <= start:
        # A zero-length or inverted appointment breaks calendar rendering.
        _LOGGER.debug("Dropping Somtoday appointment with a non-positive duration")
        return None

    additional = payload.get("additionalObjects") or {}
    subject_block = additional.get("vak") or {}
    appointment_id = _self_link_id(payload)

    return Lesson(
        uid=str(appointment_id or f"{start.isoformat()}-{payload.get('titel')}"),
        start=start,
        end=end,
        subject=clean_text(subject_block.get("naam")),
        subject_short=clean_text(subject_block.get("afkorting")),
        location=clean_text(payload.get("locatie")),
        teacher=clean_text(additional.get("docentAfkortingen")),
        status=map_lesson_status(payload.get("afspraakStatus")),
        raw_status=payload.get("afspraakStatus"),
        period_start=payload.get("beginLesuur")
        if isinstance(payload.get("beginLesuur"), int)
        else None,
        period_end=payload.get("eindLesuur")
        if isinstance(payload.get("eindLesuur"), int)
        else None,
        title=clean_text(payload.get("titel")),
        student_ids=_lesson_student_ids(payload),
    )


def _homework_done(payload: dict[str, Any], student_id: int | None) -> bool:
    """Return whether the student has ticked this item off.

    ``huiswerkgemaakt`` is the direct answer when present. Otherwise the
    ``swigemaaktVinkjes`` list holds one tick per student, so a parent account
    seeing several children must match on the student id.
    """
    additional = payload.get("additionalObjects") or {}

    done = additional.get("huiswerkgemaakt")
    if isinstance(done, bool):
        return done

    for tick in (additional.get("swigemaaktVinkjes") or {}).get("items") or []:
        if not isinstance(tick, dict) or not tick.get("gemaakt"):
            continue
        if student_id is None:
            return True
        if _self_link_id(tick.get("leerling") or {}) == student_id:
            return True
    return False


def parse_homework(
    payload: dict[str, Any], student_id: int | None = None
) -> HomeworkItem | None:
    """Build a :class:`HomeworkItem` from a study-guide assignment item.

    Handles all three assignment shapes (per lesson, per day, per week). The
    per-lesson variant nests the appointment under ``afspraak``, which carries
    a more precise due moment than the item's own ``datumTijd``.
    """
    item = payload.get("studiewijzerItem")
    if not isinstance(item, dict):
        return None

    item_id = _self_link_id(item) or _self_link_id(payload)
    if item_id is None:
        return None

    appointment = payload.get("afspraak")
    due = None
    if isinstance(appointment, dict):
        due = parse_datetime(appointment.get("beginDatumTijd"))
    if due is None:
        due = parse_datetime(payload.get("datumTijd"))

    subject_block = ((payload.get("lesgroep") or {}).get("vak")) or {}

    return HomeworkItem(
        uid=str(item_id),
        kind=map_homework_type(item.get("huiswerkType")),
        due=due,
        subject=clean_text(subject_block.get("naam"))
        or clean_text(subject_block.get("afkorting")),
        topic=clean_text(item.get("onderwerp")),
        description=clean_text(item.get("omschrijving")),
        raw_kind=item.get("huiswerkType"),
        done=_homework_done(payload, student_id),
    )
