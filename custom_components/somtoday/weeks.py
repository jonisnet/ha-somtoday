"""Week views: the timetable as it normally runs, and as it runs this week.

Somtoday publishes no base timetable — there is only a list of concrete
appointments. The "normal week" is therefore *derived*: for each weekday and
lesson period, whichever lesson shows up most often across the weeks we hold.
That is what makes it possible to say where the coming week deviates, which is
the whole point of having both views.

Kept out of the coordinator because the rules are the interesting part and they
are worth testing on their own.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .models import Lesson

# A slot needs to be seen in at least this many distinct weeks before it counts
# as "normal". With only one week of data every lesson would be its own norm,
# and the comparison would be vacuously perfect.
MIN_WEEKS_FOR_BASE = 2


@dataclass(frozen=True)
class BaseSlot:
    """One recurring lesson in the derived normal week."""

    weekday: int  # 1 = Monday, matching ISO
    period: int | None
    subject: str | None
    subject_short: str | None
    teacher: str | None
    location: str | None
    start_time: str  # "08:30" — a wall-clock time, not a moment
    end_time: str
    weeks_seen: int

    def as_dict(self) -> dict[str, Any]:
        """Return the slot as a plain dict for a state attribute."""
        return {
            "weekday": self.weekday,
            "period": self.period,
            "subject": self.subject,
            "subject_short": self.subject_short,
            "teacher": self.teacher,
            "location": self.location,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "weeks_seen": self.weeks_seen,
        }


def _local(moment: datetime) -> datetime:
    """Return a lesson moment in its own offset — Somtoday already sends one."""
    return moment


def week_start(day: date) -> date:
    """Return the Monday of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def _slot_key(lesson: Lesson) -> tuple[int, int | None, str]:
    """Return the key a lesson occupies in a normal week.

    Keyed on period where Somtoday gives one and on start time otherwise, so
    schools that do not use lesson periods still get a usable base week.
    """
    start = _local(lesson.start)
    return (start.isoweekday(), lesson.period_start, start.strftime("%H:%M"))


def derive_base_week(
    lessons: list[Lesson], *, min_weeks: int = MIN_WEEKS_FOR_BASE
) -> list[BaseSlot]:
    """Derive the normal week from the concrete lessons we hold.

    For every weekday-and-period slot, the most common lesson wins. Cancelled
    lessons are excluded from the vote: a lesson being dropped is precisely the
    deviation this is meant to measure, so letting it shape the norm would make
    the norm chase the exception.

    Slots seen in fewer than ``min_weeks`` distinct weeks are dropped, which is
    what keeps a one-off extra lesson or an exam slot out of the base week.
    """
    by_slot: dict[tuple[int, int | None, str], list[Lesson]] = {}
    for lesson in lessons:
        if lesson.is_cancelled:
            continue
        by_slot.setdefault(_slot_key(lesson), []).append(lesson)

    slots: list[BaseSlot] = []
    for (weekday, period, start_time), candidates in by_slot.items():
        weeks = {week_start(_local(item.start).date()) for item in candidates}
        if len(weeks) < min_weeks:
            continue

        # The winner is the most common (subject, teacher, room) combination,
        # not merely the first one seen — a one-week room swap must not become
        # the norm.
        counted = Counter(
            (
                item.subject,
                item.subject_short,
                item.teacher,
                item.location,
                _local(item.end).strftime("%H:%M"),
            )
            for item in candidates
        )
        (subject, short, teacher, location, end_time), _count = counted.most_common(1)[0]
        slots.append(
            BaseSlot(
                weekday=weekday,
                period=period,
                subject=subject,
                subject_short=short,
                teacher=teacher,
                location=location,
                start_time=start_time,
                end_time=end_time,
                weeks_seen=len(weeks),
            )
        )

    slots.sort(key=lambda slot: (slot.weekday, slot.start_time, slot.period or 0))
    return slots


def weeks_covered(lessons: list[Lesson]) -> int:
    """Return how many distinct weeks the held lessons span.

    Surfaced as an attribute so the base week can be read with the right amount
    of trust: derived from two weeks it is a guess, from four it is a pattern.
    """
    return len({week_start(_local(lesson.start).date()) for lesson in lessons})


def active_week_start(lessons: list[Lesson], now: datetime) -> date:
    """Return the Monday of the week worth showing right now.

    The week containing the next lesson that has not finished yet, which means
    the view rolls over to next week once the current one is done rather than
    showing an empty Saturday and Sunday. Falls back to the current week when
    there is nothing left at all.

    Cancelled lessons still anchor the week, deliberately. Rolling forward past
    them would hide the very week the user most wants to look at — the one
    where their lessons were dropped — and "is this week over" is a question
    about time, not about whether the lessons survived.
    """
    upcoming = [lesson for lesson in lessons if lesson.end > now]
    if not upcoming:
        return week_start(_local(now).date())
    soonest = min(upcoming, key=lambda lesson: lesson.start)
    return week_start(_local(soonest.start).date())


def _deviation(lesson: Lesson, slot: BaseSlot | None) -> str | None:
    """Return how a lesson differs from its normal-week slot, if it does."""
    if lesson.is_cancelled:
        return "cancelled"
    if slot is None:
        return "extra"
    if lesson.subject != slot.subject:
        return "different_subject"
    if lesson.location != slot.location:
        return "different_room"
    if lesson.teacher != slot.teacher:
        return "different_teacher"
    if _local(lesson.end).strftime("%H:%M") != slot.end_time:
        return "different_time"
    return None


def build_active_week(
    lessons: list[Lesson],
    base: list[BaseSlot],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Return the active week grouped by day, marked against the normal week.

    Each lesson carries whether it deviates and how; each day also lists the
    normal-week slots that have no lesson at all this week, which is how a
    quietly removed lesson becomes visible rather than simply absent.
    """
    monday = active_week_start(lessons, now)
    sunday = monday + timedelta(days=6)
    by_slot = {(slot.weekday, slot.period, slot.start_time): slot for slot in base}

    days: list[dict[str, Any]] = []
    total = 0
    deviations = 0

    for offset in range(7):
        day = monday + timedelta(days=offset)
        day_lessons = sorted(
            (
                lesson
                for lesson in lessons
                if _local(lesson.start).date() == day
            ),
            key=lambda lesson: (lesson.start, lesson.period_start or 0),
        )
        seen_keys = {_slot_key(lesson) for lesson in day_lessons}

        entries = []
        for lesson in day_lessons:
            slot = by_slot.get(_slot_key(lesson))
            reason = _deviation(lesson, slot)
            if reason is not None:
                deviations += 1
            entries.append(
                {
                    "subject": lesson.subject,
                    "subject_short": lesson.subject_short,
                    "teacher": lesson.teacher,
                    "location": lesson.location,
                    "start": lesson.start.isoformat(),
                    "end": lesson.end.isoformat(),
                    "period": lesson.period_start,
                    "cancelled": lesson.is_cancelled,
                    "deviates": reason is not None,
                    "deviation": reason,
                }
            )

        missing = [
            slot.as_dict()
            for slot in base
            if slot.weekday == day.isoweekday()
            and (slot.weekday, slot.period, slot.start_time) not in seen_keys
        ]
        deviations += len(missing)
        total += len(entries)

        if entries or missing:
            days.append(
                {
                    "date": day.isoformat(),
                    "weekday": day.isoweekday(),
                    "lessons": entries,
                    "missing": missing,
                }
            )

    return {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "week_number": monday.isocalendar().week,
        "lesson_count": total,
        "deviation_count": deviations,
        "days": days,
    }
