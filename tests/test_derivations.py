"""Tests for the pure derivations behind the sensors and the calendar."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.somtoday.calendar import lesson_to_event
from custom_components.somtoday.const import HomeworkType, LessonStatus
from custom_components.somtoday.coordinator import (
    current_lesson,
    next_lesson,
    next_school_day,
    next_test,
    open_homework,
    sort_lessons,
    today_lessons,
)
from custom_components.somtoday.models import HomeworkItem, Lesson

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def dutch_timezone():
    """Run these tests in the timezone a Dutch school actually lives in."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.DEFAULT_TIME_ZONE = AMSTERDAM
    yield
    dt_util.DEFAULT_TIME_ZONE = original


def at(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    """Return an aware datetime in September 2026, Amsterdam time."""
    return datetime(2026, month, day, hour, minute, tzinfo=AMSTERDAM)


def lesson(
    uid: str = "1",
    *,
    start: datetime | None = None,
    minutes: int = 50,
    subject: str = "wiskunde B",
    status: LessonStatus = LessonStatus.SCHEDULED,
    teacher: str | None = "Abc",
    location: str | None = "217",
    period: int | None = 1,
) -> Lesson:
    """Build a lesson without going through the payload parser."""
    start = start or at(7, 8, 30)
    return Lesson(
        uid=uid,
        start=start,
        end=start + timedelta(minutes=minutes),
        subject=subject,
        subject_short=subject[:4],
        teacher=teacher,
        location=location,
        status=status,
        raw_status=status.value.upper(),
        period_start=period,
        period_end=period,
        title=f"{location} - {subject}",
    )


def homework_item(
    uid: str = "h1",
    *,
    due: datetime | None = None,
    kind: HomeworkType = HomeworkType.HOMEWORK,
    done: bool = False,
    subject: str = "wiskunde B",
) -> HomeworkItem:
    """Build a homework item without going through the payload parser."""
    return HomeworkItem(
        uid=uid,
        kind=kind,
        due=due,
        subject=subject,
        topic="Hoofdstuk 3",
        description="Opgaven",
        done=done,
    )


# --------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------


def test_sort_lessons_orders_by_start_then_period():
    late = lesson("late", start=at(7, 10, 0), period=3)
    early = lesson("early", start=at(7, 8, 30), period=1)
    assert [item.uid for item in sort_lessons([late, early])] == ["early", "late"]


def test_current_lesson_during_a_lesson():
    lessons = [lesson("a", start=at(7, 8, 30)), lesson("b", start=at(7, 10, 0))]
    assert current_lesson(lessons, at(7, 8, 45)).uid == "a"


def test_current_lesson_between_lessons_is_none():
    lessons = [lesson("a", start=at(7, 8, 30)), lesson("b", start=at(7, 10, 0))]
    assert current_lesson(lessons, at(7, 9, 40)) is None


def test_current_lesson_ignores_a_cancelled_one():
    """Nobody is sitting in a lesson that was dropped."""
    lessons = [lesson("a", start=at(7, 8, 30), status=LessonStatus.CANCELLED)]
    assert current_lesson(lessons, at(7, 8, 45)) is None


def test_current_lesson_with_overlapping_appointments_picks_the_earliest():
    lessons = [
        lesson("second", start=at(7, 8, 45), subject="biologie"),
        lesson("first", start=at(7, 8, 30)),
    ]
    assert current_lesson(lessons, at(7, 8, 50)).uid == "first"


def test_next_lesson_skips_cancelled_ones():
    lessons = [
        lesson("dropped", start=at(7, 10, 0), status=LessonStatus.CANCELLED),
        lesson("real", start=at(7, 11, 0)),
    ]
    assert next_lesson(lessons, at(7, 9, 0)).uid == "real"


def test_next_lesson_is_none_when_everything_has_started():
    lessons = [lesson("a", start=at(7, 8, 30))]
    assert next_lesson(lessons, at(7, 12, 0)) is None


def test_next_lesson_with_no_lessons_at_all():
    assert next_lesson([], at(7, 9, 0)) is None
    assert current_lesson([], at(7, 9, 0)) is None
    assert next_school_day([], at(7, 9, 0)) == []


def test_next_school_day_rolls_over_after_the_last_bell():
    """The evening-before automation depends on this."""
    lessons = [
        lesson("today", start=at(7, 8, 30)),
        lesson("tomorrow-1", start=at(8, 8, 30)),
        lesson("tomorrow-2", start=at(8, 10, 0)),
    ]
    day = next_school_day(lessons, at(7, 20, 0))
    assert [item.uid for item in day] == ["tomorrow-1", "tomorrow-2"]


def test_today_keeps_the_complete_day_after_the_last_bell():
    lessons = [
        lesson("today-1", start=at(7, 8, 30)),
        lesson("today-cancelled", start=at(7, 10, 0), status=LessonStatus.CANCELLED),
        lesson("tomorrow", start=at(8, 8, 30)),
    ]
    day = today_lessons(lessons, at(7, 20, 0))
    assert [item.uid for item in day] == ["today-1", "today-cancelled"]


def test_today_is_empty_on_a_day_without_lessons():
    lessons = [lesson("tomorrow", start=at(8, 8, 30))]
    assert today_lessons(lessons, at(7, 20, 0)) == []


def test_next_school_day_stays_on_today_while_lessons_remain():
    lessons = [
        lesson("today-1", start=at(7, 8, 30)),
        lesson("today-2", start=at(7, 14, 0)),
        lesson("tomorrow", start=at(8, 8, 30)),
    ]
    day = next_school_day(lessons, at(7, 9, 0))
    assert [item.uid for item in day] == ["today-1", "today-2"]


def test_next_school_day_includes_that_days_cancelled_lessons():
    """A gap in the day is worth showing once the day itself is picked."""
    lessons = [
        lesson("dropped", start=at(8, 8, 30), status=LessonStatus.CANCELLED),
        lesson("real", start=at(8, 10, 0)),
    ]
    day = next_school_day(lessons, at(7, 20, 0))
    assert [item.uid for item in day] == ["dropped", "real"]


def test_next_school_day_skips_a_day_with_only_cancelled_lessons():
    lessons = [
        lesson("all-dropped", start=at(8, 8, 30), status=LessonStatus.CANCELLED),
        lesson("real", start=at(9, 8, 30)),
    ]
    day = next_school_day(lessons, at(7, 20, 0))
    assert [item.uid for item in day] == ["real"]


def test_derivations_survive_the_winter_time_switch():
    """The clock goes back on 25 October 2026; offsets come from Somtoday."""
    before = lesson("before", start=datetime(2026, 10, 23, 8, 30, tzinfo=AMSTERDAM))
    after = lesson("after", start=datetime(2026, 10, 26, 8, 30, tzinfo=AMSTERDAM))
    lessons = [after, before]

    assert before.start.utcoffset() == timedelta(hours=2)
    assert after.start.utcoffset() == timedelta(hours=1)
    assert [item.uid for item in sort_lessons(lessons)] == ["before", "after"]

    day = next_school_day(
        lessons, datetime(2026, 10, 24, 20, 0, tzinfo=AMSTERDAM)
    )
    assert [item.uid for item in day] == ["after"]


# --------------------------------------------------------------------------
# Homework and tests
# --------------------------------------------------------------------------


def test_open_homework_excludes_done_tests_and_overdue():
    now = at(7, 9, 0)
    items = [
        homework_item("open", due=at(8, 8, 30)),
        homework_item("done", due=at(8, 8, 30), done=True),
        homework_item("test", due=at(8, 8, 30), kind=HomeworkType.TEST),
        homework_item("overdue", due=at(6, 8, 30)),
    ]
    assert [item.uid for item in open_homework(items, now)] == ["open"]


def test_open_homework_keeps_undated_items_last():
    """Somtoday allows undated study items; hiding them would lose real work."""
    now = at(7, 9, 0)
    items = [
        homework_item("undated", due=None),
        homework_item("later", due=at(10, 8, 30)),
        homework_item("sooner", due=at(8, 8, 30)),
    ]
    assert [item.uid for item in open_homework(items, now)] == [
        "sooner",
        "later",
        "undated",
    ]


def test_next_test_picks_the_soonest():
    now = at(7, 9, 0)
    items = [
        homework_item("big", due=at(15, 8, 30), kind=HomeworkType.LARGE_TEST),
        homework_item("small", due=at(10, 8, 30), kind=HomeworkType.TEST),
        homework_item("homework", due=at(8, 8, 30)),
    ]
    assert next_test(items, now).uid == "small"


def test_next_test_ignores_past_and_undated_tests():
    now = at(7, 9, 0)
    items = [
        homework_item("past", due=at(6, 8, 30), kind=HomeworkType.TEST),
        homework_item("undated", due=None, kind=HomeworkType.TEST),
    ]
    assert next_test(items, now) is None


def test_next_test_without_any_homework():
    assert next_test([], at(7, 9, 0)) is None
    assert open_homework([], at(7, 9, 0)) == []


# --------------------------------------------------------------------------
# Calendar conversion
# --------------------------------------------------------------------------


def test_lesson_to_event():
    event = lesson_to_event(lesson())
    assert event.summary == "wiskunde B"
    assert event.location == "217"
    assert event.start == at(7, 8, 30)
    assert event.end == at(7, 9, 20)
    assert "Docent: Abc" in event.description
    assert "Lokaal: 217" in event.description
    assert "Lesuur: 1" in event.description


def test_cancelled_lesson_is_marked_in_the_summary():
    event = lesson_to_event(lesson(status=LessonStatus.CANCELLED))
    assert event.summary.startswith("Vervallen: ")
    assert "wiskunde B" in event.summary


def test_moved_lesson_is_marked_in_the_summary():
    event = lesson_to_event(lesson(status=LessonStatus.MOVED))
    assert event.summary.startswith("Gewijzigd: ")


def test_unknown_status_gets_no_misleading_prefix():
    event = lesson_to_event(lesson(status=LessonStatus.UNKNOWN))
    assert event.summary == "wiskunde B"


def test_lesson_to_event_without_optional_fields():
    bare = Lesson(
        uid="bare",
        start=at(7, 8, 30),
        end=at(7, 9, 20),
    )
    event = lesson_to_event(bare)
    assert event.summary == "Les"
    assert event.location is None
    assert event.description is None


def test_multi_period_lesson_shows_a_range():
    long_lesson = Lesson(
        uid="double",
        start=at(7, 8, 30),
        end=at(7, 10, 10),
        subject="scheikunde",
        period_start=1,
        period_end=2,
    )
    assert "Lesuur: 1-2" in lesson_to_event(long_lesson).description
