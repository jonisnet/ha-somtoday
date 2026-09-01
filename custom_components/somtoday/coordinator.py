"""Data update coordinator for the Somtoday integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SomtodayApiClient, SomtodayApiError
from .auth import SomtodayAuthError, SomtodayInvalidAuth
from .changes import ChangeType, LessonChange, diff_lessons, snapshot
from .const import (
    CONF_DAYS_AHEAD,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    DAYS_BEHIND,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    EVENT_SCHEDULE_CHANGED,
)
from .models import (
    HomeworkItem,
    Lesson,
    Student,
    parse_homework,
    parse_lesson,
    parse_student,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class StudentData:
    """Everything known about one student after a successful poll."""

    student: Student
    lessons: list[Lesson] = field(default_factory=list)
    homework: list[HomeworkItem] = field(default_factory=list)


def refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured poll interval as a ``timedelta``."""
    minutes = int(entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    return timedelta(minutes=minutes)


def days_ahead(entry: ConfigEntry) -> int:
    """Return how many days of schedule to pre-fetch."""
    return int(entry.options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD))


# --------------------------------------------------------------------------
# Derivations — pure functions over a student's lessons and homework
# --------------------------------------------------------------------------


def sort_lessons(lessons: list[Lesson]) -> list[Lesson]:
    """Return the lessons ordered by start time, then by lesson period."""
    return sorted(lessons, key=lambda lesson: (lesson.start, lesson.period_start or 0))


def current_lesson(lessons: list[Lesson], now: datetime) -> Lesson | None:
    """Return the lesson happening right now, if any.

    Cancelled lessons are skipped: nobody is sitting in one. Overlapping
    appointments are resolved by earliest start, which matches how a timetable
    reads top to bottom.
    """
    candidates = [
        lesson
        for lesson in lessons
        if not lesson.is_cancelled and lesson.start <= now < lesson.end
    ]
    return sort_lessons(candidates)[0] if candidates else None


def next_lesson(lessons: list[Lesson], now: datetime) -> Lesson | None:
    """Return the next lesson that has not started yet.

    Cancelled lessons are skipped — reporting "your next lesson is the one
    that was dropped" would be actively misleading.
    """
    candidates = [
        lesson for lesson in lessons if not lesson.is_cancelled and lesson.start > now
    ]
    return sort_lessons(candidates)[0] if candidates else None


def next_school_day(lessons: list[Lesson], now: datetime) -> list[Lesson]:
    """Return every lesson on the next day that still has one.

    "Next" means the earliest date with an uncancelled lesson that has not
    finished yet — so during a school day this stays on today until the last
    bell, then rolls over to tomorrow. Cancelled lessons of that day are
    included in the result, because a gap in the day is worth showing once the
    day itself is picked.
    """
    upcoming = [lesson for lesson in lessons if lesson.end > now]
    day_dates = sorted(
        {
            dt_util.as_local(lesson.start).date()
            for lesson in upcoming
            if not lesson.is_cancelled
        }
    )
    if not day_dates:
        return []
    target = day_dates[0]
    return sort_lessons(
        [
            lesson
            for lesson in lessons
            if dt_util.as_local(lesson.start).date() == target
        ]
    )


def today_lessons(lessons: list[Lesson], now: datetime) -> list[Lesson]:
    """Return every lesson on today's local calendar date.

    Unlike :func:`next_school_day`, this deliberately does not roll forward
    after the last bell. Cancelled and already-finished lessons remain part of
    the day because this is a timetable view, not an upcoming-event sensor.
    """
    today = dt_util.as_local(now).date()
    return sort_lessons(
        [
            lesson
            for lesson in lessons
            if dt_util.as_local(lesson.start).date() == today
        ]
    )


def open_homework(homework: list[HomeworkItem], now: datetime) -> list[HomeworkItem]:
    """Return unfinished homework that is not yet past its due moment.

    Items without a due moment are kept: Somtoday does allow undated study
    items, and silently hiding them would lose real work.
    """
    return sorted(
        (
            item
            for item in homework
            if not item.done and not item.is_test and (item.due is None or item.due >= now)
        ),
        key=lambda item: (item.due is None, item.due or now),
    )


def next_test(homework: list[HomeworkItem], now: datetime) -> HomeworkItem | None:
    """Return the soonest upcoming test, if there is one."""
    upcoming = [
        item
        for item in homework
        if item.is_test and item.due is not None and item.due >= now
    ]
    return min(upcoming, key=lambda item: item.due) if upcoming else None


class SomtodayCoordinator(DataUpdateCoordinator[dict[int, StudentData]]):
    """Polls Somtoday and publishes normalised per-student data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SomtodayApiClient,
    ) -> None:
        """Initialise the coordinator for one Somtoday account."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=refresh_interval(entry),
        )
        self.client = client
        self.last_success_time: datetime | None = None
        # Previous timetable per student, and how far the last fetch looked
        # ahead. Both are needed to tell a real change from the window
        # simply sliding forward a day.
        self._known_lessons: dict[int, dict[str, Lesson]] = {}
        self._known_horizon: date | None = None
        self._cached_device_ids: dict[int, str] = {}

    async def _async_update_data(self) -> dict[int, StudentData]:
        """Fetch students, schedule and homework for the configured window.

        ``aiohttp.ClientError`` is deliberately not caught — ``DataUpdateCoordinator``
        already converts it into a retryable failure.
        """
        today = dt_util.now().date()
        window_start = today - timedelta(days=DAYS_BEHIND)
        window_end = today + timedelta(days=days_ahead(self.config_entry))

        try:
            students = await self._async_students()
            appointments = await self.client.async_get_appointments(
                window_start, window_end
            )
            result = await self._async_build(students, appointments, today)
        except SomtodayInvalidAuth as err:
            # The only failure that means "these credentials are dead". Anything
            # else stays retryable so a Somtoday outage never forces a re-login.
            raise ConfigEntryAuthFailed(
                "Somtoday rejected the stored session; sign in again"
            ) from err
        except (SomtodayApiError, SomtodayAuthError) as err:
            raise UpdateFailed(f"Could not fetch data from Somtoday: {err}") from err

        self._persist_rotated_refresh_token()
        self._fire_schedule_changes(result, window_end)
        self.last_success_time = dt_util.utcnow()
        return result

    async def _async_students(self) -> list[Student]:
        """Return the parsed student list, or raise when it is unusable."""
        parsed = [
            student
            for raw in await self.client.async_get_students()
            if (student := parse_student(raw)) is not None
        ]
        if not parsed:
            raise UpdateFailed("Somtoday returned no students for this account")
        return parsed

    async def _async_build(
        self,
        students: list[Student],
        appointments: list[dict],
        today: date,
    ) -> dict[int, StudentData]:
        """Split the fetched payloads per student."""
        lessons = [
            lesson
            for raw in appointments
            if (lesson := parse_lesson(raw)) is not None
        ]

        result: dict[int, StudentData] = {}
        for student in students:
            # An appointment without a student list applies to the whole
            # account — that is the normal shape on a single-student login.
            own_lessons = [
                lesson
                for lesson in lessons
                if not lesson.student_ids or student.student_id in lesson.student_ids
            ]
            homework = [
                item
                for raw in await self.client.async_get_homework(
                    today, student.student_id
                )
                if (item := parse_homework(raw, student.student_id)) is not None
            ]
            result[student.student_id] = StudentData(
                student=student,
                lessons=sort_lessons(own_lessons),
                homework=homework,
            )
        return result

    def _fire_schedule_changes(
        self, result: dict[int, StudentData], window_end: date
    ) -> None:
        """Compare against the previous poll and announce what changed.

        One event per student per poll, carrying every change together, rather
        than one event per lesson: a whole day being dropped is a single piece
        of news, not seven notifications.

        Nothing fires on the first poll after a restart. The previous timetable
        lives in memory only, so every restart would otherwise look like the
        entire schedule had just appeared.
        """
        first_poll = self._known_horizon is None
        horizon = self._known_horizon or window_end
        now = dt_util.now()

        for student_id, data in result.items():
            previous = self._known_lessons.get(student_id)
            if not first_poll and previous is not None:
                changes = diff_lessons(
                    previous, data.lessons, now=now, horizon=horizon
                )
                if changes:
                    self._fire(student_id, data, changes)
            self._known_lessons[student_id] = snapshot(data.lessons)

        # Students who left the account should not keep a stale timetable.
        for student_id in set(self._known_lessons) - set(result):
            del self._known_lessons[student_id]

        self._known_horizon = window_end

    def _fire(
        self, student_id: int, data: StudentData, changes: list[LessonChange]
    ) -> None:
        """Put one schedule-changed event on the bus."""
        payload = {
            "device_id": self._device_id(student_id),
            "student_id": student_id,
            "changes": [change.as_event_data() for change in changes],
            "change_count": len(changes),
            "cancelled_count": sum(
                1 for change in changes if change.type is ChangeType.CANCELLED
            ),
        }
        _LOGGER.debug(
            "Somtoday schedule changed: %s change(s) for student %s",
            len(changes),
            student_id,
        )
        self.hass.bus.async_fire(EVENT_SCHEDULE_CHANGED, payload)

    def _device_id(self, student_id: int) -> str | None:
        """Return this student's device id, so triggers can filter per child.

        ``None`` until the device exists, which is fine: nothing fires on the
        first poll anyway, and by the second one the platforms have registered.
        """
        if cached := self._cached_device_ids.get(student_id):
            return cached
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, str(student_id))}
        )
        if device is None:
            return None
        self._cached_device_ids[student_id] = device.id
        return device.id

    def _persist_rotated_refresh_token(self) -> None:
        """Write a rotated refresh token back to the config entry.

        Somtoday rotates the refresh token on every use. Losing the new one
        would lock the account out at the next Home Assistant restart, so it is
        persisted as soon as it changes.
        """
        entry = self.config_entry
        current = self.client.auth.tokens.refresh_token
        if not current or current == entry.data.get(CONF_REFRESH_TOKEN):
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: current}
        )
