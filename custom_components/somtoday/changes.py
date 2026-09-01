"""Detecting changes between two versions of a student's timetable.

Somtoday has no push and no "what changed" endpoint, so a change is only ever
visible by comparing one poll against the last. Kept apart from the coordinator
because the interesting part is the comparison rules, and those are worth
testing without a Home Assistant instance in the way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from .models import Lesson


class ChangeType(StrEnum):
    """What happened to a lesson between two polls."""

    CANCELLED = "cancelled"          # Went from taking place to dropped
    REINSTATED = "reinstated"        # A dropped lesson is back on
    MOVED = "moved"                  # Start or end time changed
    ROOM_CHANGED = "room_changed"    # Same lesson, different room
    TEACHER_CHANGED = "teacher_changed"
    ADDED = "added"                  # A lesson that was not on the timetable
    REMOVED = "removed"              # Gone entirely, not merely marked dropped


@dataclass(frozen=True)
class LessonChange:
    """One difference between the previous timetable and the current one."""

    type: ChangeType
    lesson: Lesson
    previous: Lesson | None = None

    def as_event_data(self) -> dict[str, Any]:
        """Return the change as a plain dict for the event bus.

        Only the fields an automation would act on. No student name and no
        identifiers — the event already carries a ``device_id``, and the payload
        ends up in the logbook.
        """
        data: dict[str, Any] = {
            "type": self.type.value,
            "subject": self.lesson.subject,
            "subject_short": self.lesson.subject_short,
            "teacher": self.lesson.teacher,
            "location": self.lesson.location,
            "start": self.lesson.start.isoformat(),
            "end": self.lesson.end.isoformat(),
            "period_start": self.lesson.period_start,
            "status": self.lesson.status.value,
            "cancelled": self.lesson.is_cancelled,
        }
        if self.previous is not None:
            # The same fields as above, so a notification can put "was" and
            # "now" side by side without special-casing per change type.
            data["previous"] = {
                "teacher": self.previous.teacher,
                "location": self.previous.location,
                "start": self.previous.start.isoformat(),
                "end": self.previous.end.isoformat(),
                "period_start": self.previous.period_start,
                "status": self.previous.status.value,
                "cancelled": self.previous.is_cancelled,
            }
        return data


def snapshot(lessons: list[Lesson]) -> dict[str, Lesson]:
    """Index a timetable by lesson id, ready to compare against the next poll."""
    return {lesson.uid: lesson for lesson in lessons}


def diff_lessons(
    previous: dict[str, Lesson],
    current: list[Lesson],
    *,
    now: datetime,
    horizon: date,
) -> list[LessonChange]:
    """Return what changed, ignoring everything the user cannot act on.

    Two filters keep this from crying wolf, and both are load-bearing:

    * **Only lessons that have not started yet.** A lesson that already happened
      cannot usefully change, and the fetch window's trailing edge drops old
      days as it slides.
    * **Only lessons up to ``horizon``**, the end of the *previous* fetch
      window. The window moves forward roughly a day per poll, so without this
      every newly revealed day at the far end would be reported as a timetable
      full of "added" lessons, every single day.
    """
    changes: list[LessonChange] = []
    current_by_id = snapshot(current)

    def in_scope(lesson: Lesson) -> bool:
        return lesson.start > now and lesson.start.date() <= horizon

    for uid, lesson in current_by_id.items():
        before = previous.get(uid)
        if before is None:
            if in_scope(lesson):
                changes.append(LessonChange(ChangeType.ADDED, lesson))
            continue
        if not in_scope(lesson) and not in_scope(before):
            continue
        changes.extend(_compare(before, lesson))

    for uid, before in previous.items():
        if uid not in current_by_id and in_scope(before):
            changes.append(LessonChange(ChangeType.REMOVED, before))

    changes.sort(key=lambda change: change.lesson.start)
    return changes


def _compare(before: Lesson, after: Lesson) -> list[LessonChange]:
    """Return the changes between two versions of the same lesson.

    A cancellation outranks everything else: when a lesson is dropped, its room
    and teacher becoming meaningless is not news the user needs three separate
    notifications about.
    """
    if before.is_cancelled != after.is_cancelled:
        kind = ChangeType.CANCELLED if after.is_cancelled else ChangeType.REINSTATED
        return [LessonChange(kind, after, before)]

    if after.is_cancelled:
        # Already dropped and still dropped — nothing worth reporting.
        return []

    changes = []
    if (before.start, before.end) != (after.start, after.end):
        changes.append(LessonChange(ChangeType.MOVED, after, before))
    if before.location != after.location:
        changes.append(LessonChange(ChangeType.ROOM_CHANGED, after, before))
    if before.teacher != after.teacher:
        changes.append(LessonChange(ChangeType.TEACHER_CHANGED, after, before))
    return changes
