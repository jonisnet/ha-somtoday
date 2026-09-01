"""Calendar platform for the Somtoday integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SomtodayConfigEntry
from .api import SomtodayApiError
from .auth import SomtodayAuthError
from .const import DAYS_BEHIND, LessonStatus
from .coordinator import SomtodayCoordinator, days_ahead, sort_lessons
from .entity import SomtodayStudentEntity
from .models import Lesson, parse_lesson

_LOGGER = logging.getLogger(__name__)

# The calendar is a read-only view over data the coordinator already holds, so
# it never triggers a write and needs no write throttling.
PARALLEL_UPDATES = 0

# Somtoday is a Dutch product used by Dutch schools, so the marker a cancelled
# lesson carries in its calendar summary is Dutch too — it has to read
# correctly in the calendar UI, which shows the summary verbatim. Automations
# should key on the ``status`` attribute of the next-lesson sensor instead of
# parsing this string.
CANCELLED_PREFIX = "Vervallen: "
MOVED_PREFIX = "Gewijzigd: "

_STATUS_PREFIXES = {
    LessonStatus.CANCELLED: CANCELLED_PREFIX,
    LessonStatus.MOVED: MOVED_PREFIX,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SomtodayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one schedule calendar per student."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        SomtodayScheduleCalendar(coordinator, student_id)
        for student_id in coordinator.data
    )


def lesson_to_event(lesson: Lesson) -> CalendarEvent:
    """Convert a lesson into a Home Assistant calendar event.

    Times stay timezone-aware exactly as Somtoday sent them, so a lesson keeps
    its correct wall-clock time across the daylight-saving switch instead of
    being shifted by an assumed offset.
    """
    details = [
        f"Vak: {lesson.subject}" if lesson.subject else None,
        f"Docent: {lesson.teacher}" if lesson.teacher else None,
        f"Lokaal: {lesson.location}" if lesson.location else None,
        _period_text(lesson),
        f"Status: {lesson.raw_status}" if lesson.raw_status else None,
    ]

    return CalendarEvent(
        summary=f"{_STATUS_PREFIXES.get(lesson.status, '')}{lesson.display_name}",
        start=lesson.start,
        end=lesson.end,
        description="\n".join(line for line in details if line) or None,
        location=lesson.location,
        uid=lesson.uid,
    )


def _period_text(lesson: Lesson) -> str | None:
    """Return a human description of the lesson period(s), if known."""
    if lesson.period_start is None:
        return None
    if lesson.period_end is None or lesson.period_end == lesson.period_start:
        return f"Lesuur: {lesson.period_start}"
    return f"Lesuur: {lesson.period_start}-{lesson.period_end}"


class SomtodayScheduleCalendar(SomtodayStudentEntity, CalendarEntity):
    """A student's timetable as a Home Assistant calendar."""

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the schedule calendar for one student."""
        super().__init__(coordinator, student_id, "schedule")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next lesson.

        Reads only the coordinator's cached data — a calendar entity property
        must never perform I/O.
        """
        data = self.student_data
        if data is None:
            return None

        now = dt_util.now()
        upcoming = [lesson for lesson in data.lessons if lesson.end > now]
        if not upcoming:
            return None
        return lesson_to_event(sort_lessons(upcoming)[0])

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return every lesson overlapping the requested range.

        The coordinator only pre-fetches a window around today, so a request
        outside it (browsing to next month, an automation looking further out)
        triggers a direct fetch rather than silently returning an empty week.
        """
        lessons = await self._async_lessons_for(start_date, end_date)
        return [
            lesson_to_event(lesson)
            for lesson in lessons
            if lesson.start < end_date and lesson.end > start_date
        ]

    async def _async_lessons_for(
        self, start_date: datetime, end_date: datetime
    ) -> list[Lesson]:
        """Return the lessons covering a range, from cache or a live fetch."""
        data = self.student_data
        if data is None:
            return []

        today = dt_util.now().date()
        cached_from = today - timedelta(days=DAYS_BEHIND)
        cached_to = today + timedelta(days=days_ahead(self.coordinator.config_entry))
        wanted_from = dt_util.as_local(start_date).date()
        wanted_to = dt_util.as_local(end_date).date()

        if cached_from <= wanted_from and wanted_to <= cached_to:
            return data.lessons

        try:
            raw = await self.coordinator.client.async_get_appointments(
                wanted_from, wanted_to
            )
        except (SomtodayApiError, SomtodayAuthError) as err:
            raise HomeAssistantError(
                f"Could not fetch the Somtoday schedule for this period: {err}"
            ) from err

        lessons = [
            lesson for item in raw if (lesson := parse_lesson(item)) is not None
        ]
        # An appointment without a student list applies to the whole account,
        # which is the normal shape on a single-student login.
        return sort_lessons(
            [
                lesson
                for lesson in lessons
                if not lesson.student_ids or self._student_id in lesson.student_ids
            ]
        )
