"""Fetching school records beyond the timetable, and spotting new ones.

Every fetch here is optional. A school can switch grades, absences or messages
off per account, and Somtoday then answers 403 — or, for some periods, 500.
One missing category must never take the timetable down with it, so each call
fails on its own and the category is simply reported as unavailable.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import SomtodayApiClient, SomtodayApiError
from .auth import SomtodayInvalidAuth
from .records import (
    SOMTODAY_TZ,
    Announcement,
    Assignment,
    Grade,
    Holiday,
    Measure,
    Message,
    Placement,
    Registration,
    SchoolYear,
    SubjectAverage,
    parse_announcement,
    parse_assignment,
    parse_averages,
    parse_conversation,
    parse_grade,
    parse_holiday,
    parse_measure,
    parse_placement,
    parse_registrations,
    parse_school_year,
    parse_subjects,
)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# Holidays, the school year, class and subject names change a handful of times
# a year. Fetching them twice a day is plenty.
STATIC_REFRESH = timedelta(hours=12)

# Categories of new records that can be announced on the event bus.
KIND_GRADE = "grade"
KIND_REGISTRATION = "registration"
KIND_MEASURE = "measure"
KIND_MESSAGE = "message"
KIND_ASSIGNMENT = "assignment"
KIND_ANNOUNCEMENT = "announcement"
RECORD_KINDS = (
    KIND_GRADE,
    KIND_REGISTRATION,
    KIND_MEASURE,
    KIND_MESSAGE,
    KIND_ASSIGNMENT,
    KIND_ANNOUNCEMENT,
)

# Kinds whose content is confidential; the event for these carries no content.
CONFIDENTIAL_KINDS = frozenset({KIND_GRADE, KIND_REGISTRATION, KIND_MEASURE, KIND_MESSAGE})


async def optional(
    label: str, call: Callable[[], Awaitable[T]]
) -> tuple[bool, T | None]:
    """Run one optional fetch; ``(False, None)`` when Somtoday refuses it.

    A dead session is re-raised: that one does mean the whole entry needs a
    new login, whichever endpoint happened to notice first.
    """
    try:
        return True, await call()
    except SomtodayInvalidAuth:
        raise
    except SomtodayApiError as err:
        _LOGGER.debug("Somtoday %s unavailable (HTTP %s)", label, err.status)
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError):
        _LOGGER.debug("Somtoday %s could not be fetched", label, exc_info=True)
    return False, None


@dataclass
class StudentExtras:
    """Everything beyond lessons and homework for one student."""

    holidays: list[Holiday] = field(default_factory=list)
    school_year: SchoolYear | None = None
    placement: Placement | None = None
    subjects: dict[str, str] = field(default_factory=dict)
    assignments: list[Assignment] = field(default_factory=list)
    grades: list[Grade] = field(default_factory=list)
    averages: list[SubjectAverage] = field(default_factory=list)
    overall_average: float | None = None
    registrations: list[Registration] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    # Which categories the last fetch actually got. A category missing here
    # was refused or failed, which is different from "nothing in it".
    available: set[str] = field(default_factory=set)
    static_fetched: datetime | None = None
    averages_key: frozenset[str] = field(default_factory=frozenset)


@dataclass
class AccountExtras:
    """Records that belong to the account rather than to one student."""

    announcements: list[Announcement] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    available: set[str] = field(default_factory=set)


async def async_fetch_student_extras(
    client: SomtodayApiClient,
    student_id: int,
    previous: StudentExtras | None,
    now: datetime,
) -> StudentExtras:
    """Fetch one student's records, reusing slow-changing data when fresh."""
    extras = StudentExtras()
    if previous is not None:
        # Carry everything over first: a category that fails this time keeps
        # showing the last good data instead of flickering empty.
        extras.holidays = previous.holidays
        extras.school_year = previous.school_year
        extras.placement = previous.placement
        extras.subjects = previous.subjects
        extras.averages = previous.averages
        extras.overall_average = previous.overall_average
        extras.static_fetched = previous.static_fetched
        extras.averages_key = previous.averages_key
        extras.available = {
            kind for kind in previous.available if kind in ("holidays", "school", "averages")
        }

    if extras.static_fetched is None or now - extras.static_fetched >= STATIC_REFRESH:
        await _async_fetch_static(client, student_id, extras, now.date())
        extras.static_fetched = now

    ok, raw = await optional("assignments", lambda: client.async_get_assignments(student_id))
    if ok:
        extras.available.add("assignments")
        extras.assignments = sorted(
            (
                item
                for payload in raw or []
                if (item := parse_assignment(payload, extras.subjects)) is not None
                and (not item.student_ids or student_id in item.student_ids)
            ),
            key=lambda item: (item.deadline is None, item.deadline or now),
        )

    grades: list[Grade] = []
    got_grades = False
    for exam_file in (False, True):
        ok, raw = await optional(
            "grades", lambda exam_file=exam_file: client.async_get_grades(student_id, exam_file)
        )
        if ok:
            got_grades = True
            grades.extend(
                grade
                for payload in raw or []
                if (grade := parse_grade(payload, extras.subjects, exam_file)) is not None
            )
    if got_grades:
        extras.available.add("grades")
        extras.grades = sorted(
            grades,
            key=lambda g: g.entered or datetime.min.replace(tzinfo=SOMTODAY_TZ),
            reverse=True,
        )
        # Averages only move when a result does, so refetch them on change.
        key = frozenset(f"{g.uid}:{g.result}:{g.alt_result}" for g in extras.grades)
        if key != extras.averages_key and extras.placement and extras.placement.uuid:
            uuid = extras.placement.uuid
            ok, raw = await optional("averages", lambda: client.async_get_averages(uuid))
            if ok:
                extras.available.add("averages")
                extras.averages, extras.overall_average = parse_averages(raw)
                extras.averages_key = key
        # A grade only names its alternative level; the main one is per subject.
        levels = {a.subject_short: a.level for a in extras.averages if a.level}
        if levels:
            extras.grades = [
                replace(g, level=levels.get(g.subject_short)) if g.alt_level else g
                for g in extras.grades
            ]

    ok, raw = await optional("absences", lambda: client.async_get_registrations(student_id))
    if ok:
        extras.available.add("absences")
        extras.registrations = parse_registrations(raw)

    ok, raw = await optional("measures", lambda: client.async_get_measures(student_id))
    if ok:
        extras.available.add("measures")
        extras.measures = [
            measure for payload in raw or [] if (measure := parse_measure(payload)) is not None
        ]

    return extras


async def _async_fetch_static(
    client: SomtodayApiClient, student_id: int, extras: StudentExtras, today: date
) -> None:
    """Refresh holidays, school year, placement and subject names."""
    ok, raw = await optional("holidays", lambda: client.async_get_holidays(student_id))
    if ok:
        extras.available.add("holidays")
        extras.holidays = sorted(
            (h for payload in raw or [] if (h := parse_holiday(payload)) is not None),
            key=lambda h: h.start,
        )
    ok, raw = await optional("school year", client.async_get_school_year)
    if ok:
        extras.school_year = parse_school_year(raw)
    ok, raw = await optional("placements", lambda: client.async_get_placements(student_id))
    if ok:
        extras.available.add("school")
        placements = [parse_placement(p) for p in raw or [] if isinstance(p, dict)]
        extras.placement = next((p for p in placements if p.current), None) or (
            placements[-1] if placements else None
        )
    ok, raw = await optional("subjects", lambda: client.async_get_subjects(student_id, today))
    if ok:
        extras.subjects = parse_subjects([s for s in raw or [] if isinstance(s, dict)])


async def async_fetch_account_extras(client: SomtodayApiClient) -> AccountExtras:
    """Fetch the account-wide notices and messages."""
    extras = AccountExtras()
    ok, raw = await optional("announcements", client.async_get_announcements)
    if ok:
        extras.available.add("announcements")
        extras.announcements = [
            a for payload in raw or [] if (a := parse_announcement(payload)) is not None
        ]
    ok, raw = await optional("messages", client.async_get_conversations)
    if ok:
        extras.available.add("messages")
        messages = [
            m for payload in raw or [] if (m := parse_conversation(payload)) is not None
        ]
        extras.messages = sorted(
            messages,
            key=lambda m: m.sent or datetime.min.replace(tzinfo=SOMTODAY_TZ),
            reverse=True,
        )
    return extras


def record_ids(
    kind: str, student: StudentExtras | None, account: AccountExtras | None
) -> set[str] | None:
    """Return the ids that identify ``kind`` right now, or ``None`` if unknown.

    ``None`` (rather than an empty set) means the category was not fetched, so
    nothing may be concluded about what is new.
    """
    if kind in (KIND_MESSAGE, KIND_ANNOUNCEMENT):
        if account is None:
            return None
        if kind == KIND_MESSAGE:
            return (
                {m.latest_uid or m.uid for m in account.messages}
                if "messages" in account.available
                else None
            )
        return (
            {a.uid for a in account.announcements}
            if "announcements" in account.available
            else None
        )
    if student is None:
        return None
    if kind == KIND_GRADE:
        return {f"{g.uid}:{g.result}" for g in student.grades} if "grades" in student.available else None
    if kind == KIND_REGISTRATION:
        return {r.uid for r in student.registrations} if "absences" in student.available else None
    if kind == KIND_MEASURE:
        return {m.uid for m in student.measures} if "measures" in student.available else None
    if kind == KIND_ASSIGNMENT:
        return {a.uid for a in student.assignments} if "assignments" in student.available else None
    return None


class SeenRecords:
    """Remembers which records have been seen, across restarts.

    The first time a category is ever fetched it is learned silently — a new
    install must not announce every grade of the school year as "new".
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise the store for one config entry."""
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"somtoday.seen.{entry_id}")
        self._seen: dict[str, set[str]] = {}

    async def async_load(self) -> None:
        """Load what was seen before."""
        data = await self._store.async_load() or {}
        self._seen = {key: set(values) for key, values in data.get("seen", {}).items()}

    def diff(self, scope: str, kind: str, current: set[str]) -> set[str]:
        """Return the ids in ``current`` not seen before, and remember them.

        Ids that disappeared are forgotten, so a list that is only ever a
        window (this school year) cannot grow without bound.
        """
        key = f"{scope}:{kind}"
        previous = self._seen.get(key)
        self._seen[key] = set(current)
        if previous is None:
            return set()
        return current - previous

    def schedule_save(self) -> None:
        """Persist in the background, coalescing bursts of changes."""
        self._store.async_delay_save(
            lambda: {"seen": {key: sorted(values) for key, values in self._seen.items()}},
            5,
        )
