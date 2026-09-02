"""Sensor platform for the Somtoday integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SomtodayConfigEntry
from .const import MAX_ATTRIBUTE_ITEMS
from .coordinator import (
    SomtodayCoordinator,
    current_lesson,
    next_lesson,
    next_school_day,
    next_test,
    open_homework,
    today_lessons,
)
from .entity import SomtodayStudentEntity
from .models import HomeworkItem, Lesson
from .weeks import (
    MIN_WEEKS_FOR_BASE,
    build_active_week,
    derive_base_week,
    weeks_covered,
)

# Everything is read from the coordinator's cached data; no sensor performs
# I/O of its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SomtodayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Somtoday sensors for every student on the account."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SomtodayStudentEntity] = []
    for student_id in coordinator.data:
        entities.extend(
            [
                SomtodayCurrentLessonSensor(coordinator, student_id),
                SomtodayNextLessonSensor(coordinator, student_id),
                SomtodayNextSchoolDaySensor(coordinator, student_id),
                SomtodayTodaySensor(coordinator, student_id),
                SomtodayPlannerSensor(entry, student_id),
                SomtodayNextWeekSensor(coordinator, student_id),
                *(
                    SomtodayFutureWeekSensor(coordinator, student_id, week_offset)
                    for week_offset in range(2, 9)
                ),
                SomtodayUpcomingWorkSensor(coordinator, student_id),
                SomtodayOpenHomeworkSensor(coordinator, student_id),
                SomtodayNextTestSensor(coordinator, student_id),
                SomtodayActiveWeekSensor(coordinator, student_id),
                SomtodayBaseWeekSensor(coordinator, student_id),
                SomtodayLastUpdateSensor(coordinator, student_id),
            ]
        )
    async_add_entities(entities)


class SomtodayPlannerSensor(SomtodayStudentEntity, SensorEntity):
    """Locally stored appointments for one student."""

    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, entry: SomtodayConfigEntry, student_id: int) -> None:
        """Initialise the planner sensor."""
        super().__init__(entry.runtime_data.coordinator, student_id, "planner")
        self._entry = entry
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to local planner changes."""
        await super().async_added_to_hass()
        self._remove_listener = self._entry.runtime_data.planner.listen(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from local planner changes."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> int:
        """Return the number of stored appointments."""
        return len(self._items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose identifiers and appointments to the dashboard card."""
        return {
            "student_id": self._student_id,
            "config_entry_id": self._entry.entry_id,
            "items": self._items,
        }

    @property
    def _items(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self._entry.runtime_data.planner.items
            if item["student_id"] == self._student_id
        ]


class SomtodayFutureWeekSensor(SomtodayStudentEntity, SensorEntity):
    """One concrete future calendar week for dashboard browsing."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"days"})

    def __init__(
        self,
        coordinator: SomtodayCoordinator,
        student_id: int,
        week_offset: int,
    ) -> None:
        """Initialise a future-week sensor at ``week_offset`` weeks ahead."""
        super().__init__(coordinator, student_id, f"future_week_{week_offset}")
        self._week_offset = week_offset
        self._attr_translation_key = "future_week"

    @property
    def native_value(self) -> int | None:
        """Return the number of lessons in this future week."""
        return len(self._lessons) if self.student_data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return this future week's lessons grouped by day."""
        monday = self._monday
        days = []
        for offset in range(7):
            day = monday + timedelta(days=offset)
            lessons = [
                lesson
                for lesson in self._lessons
                if dt_util.as_local(lesson.start).date() == day
            ]
            if lessons:
                attrs = _day_attributes(lessons)
                days.append(
                    {
                        "date": day.isoformat(),
                        "lessons": attrs.get("lessons", []),
                        "missing": [],
                    }
                )
        return {
            "student_id": self._student_id,
            "week_offset": self._week_offset,
            "week_start": monday.isoformat(),
            "week_number": monday.isocalendar().week,
            "lesson_count": len(self._lessons),
            "days": days,
        }

    @property
    def _monday(self) -> date:
        today = dt_util.now().date()
        return (
            today
            - timedelta(days=today.weekday())
            + timedelta(weeks=self._week_offset)
        )

    @property
    def _lessons(self) -> list[Lesson]:
        data = self.student_data
        if data is None:
            return []
        monday = self._monday
        following_monday = monday + timedelta(days=7)
        return [
            lesson
            for lesson in data.lessons
            if monday
            <= dt_util.as_local(lesson.start).date()
            < following_monday
        ]


def _lesson_attributes(lesson: Lesson) -> dict[str, Any]:
    """Return the shared attribute shape for a single lesson."""
    return {
        "subject": lesson.subject,
        "subject_short": lesson.subject_short,
        "teacher": lesson.teacher,
        "location": lesson.location,
        "start": lesson.start.isoformat(),
        "end": lesson.end.isoformat(),
        "period_start": lesson.period_start,
        "period_end": lesson.period_end,
        "status": lesson.status.value,
        "raw_status": lesson.raw_status,
        "cancelled": lesson.is_cancelled,
    }


def _homework_attributes(item: HomeworkItem) -> dict[str, Any]:
    """Return the shared attribute shape for a single homework item."""
    return {
        "subject": item.subject,
        "topic": item.topic,
        "description": item.description,
        "type": item.kind.value,
        "raw_type": item.raw_kind,
        "due": item.due.isoformat() if item.due else None,
        "done": item.done,
    }


def _day_attributes(lessons: list[Lesson]) -> dict[str, Any]:
    """Return the shared summary shape for a day of lessons."""
    if not lessons:
        return {}
    active = [lesson for lesson in lessons if not lesson.is_cancelled]
    return {
        "date": dt_util.as_local(lessons[0].start).date().isoformat(),
        "lesson_count": len(active),
        "cancelled_count": len(lessons) - len(active),
        "first_lesson": active[0].start.isoformat() if active else None,
        "last_lesson_end": active[-1].end.isoformat() if active else None,
        "lessons": [
            _lesson_attributes(lesson) for lesson in lessons[:MAX_ATTRIBUTE_ITEMS]
        ],
    }


class SomtodayNextWeekSensor(SomtodayStudentEntity, SensorEntity):
    """The concrete timetable for the next calendar week."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"days"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the next-week sensor."""
        super().__init__(coordinator, student_id, "next_week")

    @property
    def native_value(self) -> int | None:
        """Return the number of lessons next week."""
        return len(self._lessons) if self.student_data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return next week's lessons grouped by day."""
        monday = (
            dt_util.now().date()
            - timedelta(days=dt_util.now().weekday())
            + timedelta(days=7)
        )
        days = []
        for offset in range(7):
            date = monday + timedelta(days=offset)
            lessons = [
                lesson
                for lesson in self._lessons
                if dt_util.as_local(lesson.start).date() == date
            ]
            if lessons:
                attrs = _day_attributes(lessons)
                days.append(
                    {
                        "date": date.isoformat(),
                        "lessons": attrs.get("lessons", []),
                        "missing": [],
                    }
                )
        return {
            "student_id": self._student_id,
            "week_offset": 1,
            "week_start": monday.isoformat(),
            "week_number": monday.isocalendar().week,
            "lesson_count": len(self._lessons),
            "days": days,
        }

    @property
    def _lessons(self) -> list[Lesson]:
        data = self.student_data
        if data is None:
            return []
        monday = (
            dt_util.now().date()
            - timedelta(days=dt_util.now().weekday())
            + timedelta(days=7)
        )
        sunday = monday + timedelta(days=7)
        return [
            lesson
            for lesson in data.lessons
            if monday <= dt_util.as_local(lesson.start).date() < sunday
        ]


class SomtodayUpcomingWorkSensor(SomtodayStudentEntity, SensorEntity):
    """All dated homework and tests in the fetched window."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the upcoming-work sensor."""
        super().__init__(coordinator, student_id, "upcoming_work")

    @property
    def native_value(self) -> int | None:
        """Return the number of upcoming items."""
        return len(self._items) if self.student_data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all items so the card can attach them to lessons."""
        return {
            "student_id": self._student_id,
            "items": [
                _homework_attributes(item) for item in self._items[:MAX_ATTRIBUTE_ITEMS]
            ],
        }

    @property
    def _items(self) -> list[HomeworkItem]:
        data = self.student_data
        if data is None:
            return []
        today = dt_util.now().date()
        monday = today - timedelta(days=today.weekday())
        return sorted(
            (
                item
                for item in data.homework
                if not item.done
                and item.due
                and dt_util.as_local(item.due).date() >= monday
            ),
            key=lambda item: item.due,
        )


class SomtodayCurrentLessonSensor(SomtodayStudentEntity, SensorEntity):
    """The lesson currently in progress."""

    _unrecorded_attributes = frozenset({"raw_status"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the current-lesson sensor."""
        super().__init__(coordinator, student_id, "current_lesson")

    @property
    def native_value(self) -> str | None:
        """Return the subject of the lesson in progress, if there is one."""
        if (lesson := self._lesson) is None:
            return None
        return lesson.display_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details of the lesson in progress."""
        if (lesson := self._lesson) is None:
            return {}
        return _lesson_attributes(lesson)

    @property
    def _lesson(self) -> Lesson | None:
        """Return the lesson happening right now."""
        data = self.student_data
        return current_lesson(data.lessons, dt_util.now()) if data else None


class SomtodayNextLessonSensor(SomtodayStudentEntity, SensorEntity):
    """When the next lesson starts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"raw_status"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the next-lesson sensor."""
        super().__init__(coordinator, student_id, "next_lesson")

    @property
    def native_value(self) -> datetime | None:
        """Return the start time of the next lesson."""
        if (lesson := self._lesson) is None:
            return None
        return lesson.start

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details of the next lesson."""
        if (lesson := self._lesson) is None:
            return {}
        return _lesson_attributes(lesson)

    @property
    def _lesson(self) -> Lesson | None:
        """Return the next lesson that has not started yet."""
        data = self.student_data
        return next_lesson(data.lessons, dt_util.now()) if data else None


class SomtodayNextSchoolDaySensor(SomtodayStudentEntity, SensorEntity):
    """When the next school day starts, plus that day's lessons."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # The lesson list is the whole point of this sensor but would bloat the
    # recorder's long-term tables.
    _unrecorded_attributes = frozenset({"lessons"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the next-school-day sensor."""
        super().__init__(coordinator, student_id, "next_school_day")

    @property
    def native_value(self) -> datetime | None:
        """Return the start time of the first lesson of the next school day."""
        lessons = self._lessons
        active = [lesson for lesson in lessons if not lesson.is_cancelled]
        return active[0].start if active else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return that day's lessons and a short summary of them."""
        return _day_attributes(self._lessons)

    @property
    def _lessons(self) -> list[Lesson]:
        """Return every lesson on the next school day."""
        data = self.student_data
        return next_school_day(data.lessons, dt_util.now()) if data else []


class SomtodayTodaySensor(SomtodayStudentEntity, SensorEntity):
    """Today's complete timetable, including lessons that already ended."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"lessons"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the today sensor."""
        super().__init__(coordinator, student_id, "today")

    @property
    def native_value(self) -> datetime | None:
        """Return the first active lesson's start, even after it has ended."""
        active = [lesson for lesson in self._lessons if not lesson.is_cancelled]
        return active[0].start if active else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all of today's lessons and their summary."""
        return _day_attributes(self._lessons)

    @property
    def _lessons(self) -> list[Lesson]:
        """Return every lesson on today's local calendar date."""
        data = self.student_data
        return today_lessons(data.lessons, dt_util.now()) if data else []


class SomtodayOpenHomeworkSensor(SomtodayStudentEntity, SensorEntity):
    """How much homework is still open."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"homework"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the open-homework sensor."""
        super().__init__(coordinator, student_id, "open_homework")

    @property
    def native_value(self) -> int | None:
        """Return the number of unfinished homework items."""
        if self.student_data is None:
            return None
        return len(self._items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the open homework items themselves."""
        return {
            "homework": [
                _homework_attributes(item) for item in self._items[:MAX_ATTRIBUTE_ITEMS]
            ]
        }

    @property
    def _items(self) -> list[HomeworkItem]:
        """Return the open homework items, soonest first."""
        data = self.student_data
        return open_homework(data.homework, dt_util.now()) if data else []


class SomtodayNextTestSensor(SomtodayStudentEntity, SensorEntity):
    """When the next test is."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"description", "raw_type"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the next-test sensor."""
        super().__init__(coordinator, student_id, "next_test")

    @property
    def native_value(self) -> datetime | None:
        """Return when the next test takes place."""
        if (item := self._item) is None:
            return None
        return item.due

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details of the next test."""
        if (item := self._item) is None:
            return {}
        return _homework_attributes(item)

    @property
    def _item(self) -> HomeworkItem | None:
        """Return the soonest upcoming test."""
        data = self.student_data
        return next_test(data.homework, dt_util.now()) if data else None


class SomtodayActiveWeekSensor(SomtodayStudentEntity, SensorEntity):
    """The current week's timetable, marked against the normal week."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    # The whole week is the point of this sensor, and far too big to keep in
    # the recorder's long-term tables.
    _unrecorded_attributes = frozenset({"days"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the active-week sensor."""
        super().__init__(coordinator, student_id, "active_week")

    @property
    def native_value(self) -> int | None:
        """Return how many lessons are scheduled in the active week."""
        week = self._week
        return week["lesson_count"] if week else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the week grouped by day, with deviations marked."""
        return self._week or {}

    @property
    def _week(self) -> dict[str, Any] | None:
        """Return the active week, compared against the derived normal week."""
        data = self.student_data
        if data is None:
            return None
        return build_active_week(
            data.lessons, derive_base_week(data.lessons), now=dt_util.now()
        )


class SomtodayBaseWeekSensor(SomtodayStudentEntity, SensorEntity):
    """The week as it normally runs, derived from the weeks held.

    Disabled by default: it only becomes trustworthy once several weeks have
    been fetched, so it should be an explicit choice rather than something that
    quietly shows a one-week guess as if it were the timetable.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({"days"})

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the base-week sensor."""
        super().__init__(coordinator, student_id, "base_week")

    @property
    def native_value(self) -> int | None:
        """Return how many recurring lessons the normal week has."""
        data = self.student_data
        if data is None:
            return None
        return len(derive_base_week(data.lessons))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the normal week grouped by weekday.

        ``weeks_observed`` is published so the value can be read with the right
        amount of trust: derived from two weeks this is a guess, from four it
        is a pattern.
        """
        data = self.student_data
        if data is None:
            return {}

        slots = derive_base_week(data.lessons)
        days = []
        for weekday in range(1, 8):
            on_day = [slot for slot in slots if slot.weekday == weekday]
            if on_day:
                days.append(
                    {
                        "weekday": weekday,
                        "lessons": [slot.as_dict() for slot in on_day],
                    }
                )

        return {
            "weeks_observed": weeks_covered(data.lessons),
            "minimum_weeks": MIN_WEEKS_FOR_BASE,
            "lesson_count": len(slots),
            "days": days,
        }


class SomtodayLastUpdateSensor(SomtodayStudentEntity, SensorEntity):
    """When Somtoday was last reached successfully.

    The other sensors only change when their value changes, so a silently
    stalled integration is invisible without this one.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SomtodayCoordinator, student_id: int) -> None:
        """Initialise the last-update sensor."""
        super().__init__(coordinator, student_id, "last_update")

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last successful poll."""
        return self.coordinator.last_success_time
