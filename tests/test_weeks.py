"""Tests for the derived normal week and the active-week view."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.somtoday.const import LessonStatus
from custom_components.somtoday.weeks import (
    active_week_start,
    build_active_week,
    derive_base_week,
    week_start,
    weeks_covered,
)

from .test_derivations import AMSTERDAM, at, lesson

# 2026-09-02 is a Wednesday, so +7 lands on the same weekday a week later.
WED = 2


@pytest.fixture(autouse=True)
def dutch_timezone():
    """Run in the timezone a Dutch school actually lives in."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.DEFAULT_TIME_ZONE = AMSTERDAM
    yield
    dt_util.DEFAULT_TIME_ZONE = original


def weekly(uid, day, hour, minute=0, weeks=3, **kw):
    """Return the same lesson repeated on the same weekday for N weeks."""
    return [
        lesson(f"{uid}-{w}", start=at(day + 7 * w, hour, minute), **kw)
        for w in range(weeks)
    ]


def test_week_start_is_monday():
    assert week_start(date(2026, 9, 2)) == date(2026, 8, 31)
    assert week_start(date(2026, 8, 31)) == date(2026, 8, 31)
    assert week_start(date(2026, 9, 6)) == date(2026, 8, 31)


# --------------------------------------------------------------------------
# Deriving the normal week
# --------------------------------------------------------------------------


def test_a_recurring_lesson_becomes_the_normal_week():
    slots = derive_base_week(weekly("ne", WED, 8, 30, subject="Nederlands"))

    assert len(slots) == 1
    assert slots[0].subject == "Nederlands"
    assert slots[0].start_time == "08:30"
    assert slots[0].weekday == 3  # Wednesday
    assert slots[0].weeks_seen == 3


def test_a_single_occurrence_is_not_the_norm():
    """Otherwise a one-off extra lesson or an exam slot becomes 'normal'."""
    once = [lesson("eenmalig", start=at(WED, 8, 30))]
    assert derive_base_week(once) == []


def test_two_weeks_is_the_minimum():
    assert derive_base_week(weekly("a", WED, 8, 30, weeks=1)) == []
    assert len(derive_base_week(weekly("a", WED, 8, 30, weeks=2))) == 1


def test_the_most_common_variant_wins():
    """A one-week room swap must not redefine the norm."""
    lessons = [
        lesson("a-0", start=at(WED, 8, 30), location="205"),
        lesson("a-1", start=at(WED + 7, 8, 30), location="205"),
        lesson("a-2", start=at(WED + 14, 8, 30), location="118"),
    ]
    slots = derive_base_week(lessons)
    assert len(slots) == 1
    assert slots[0].location == "205"


def test_cancelled_lessons_do_not_shape_the_norm():
    """A dropped lesson is the deviation being measured; letting it vote would
    make the norm chase the exception."""
    lessons = weekly("a", WED, 8, 30, weeks=3)
    lessons[2] = lesson(
        "a-2", start=at(WED + 14, 8, 30), status=LessonStatus.CANCELLED
    )
    slots = derive_base_week(lessons)

    assert len(slots) == 1
    assert slots[0].weeks_seen == 2


def test_different_periods_are_different_slots():
    lessons = weekly("a", WED, 8, 30, period=1) + weekly(
        "b", WED, 10, 0, period=3, subject="biologie"
    )
    slots = derive_base_week(lessons)

    assert len(slots) == 2
    assert [s.period for s in slots] == [1, 3]


def test_slots_come_back_in_week_order():
    # 7 September 2026 is a Monday, two days after the Wednesday above.
    lessons = weekly("wed", WED, 10, 0) + weekly("mon", 7, 8, 30)
    slots = derive_base_week(lessons)
    assert [s.weekday for s in slots] == [1, 3]  # Monday before Wednesday


def test_weeks_covered_counts_distinct_weeks():
    assert weeks_covered(weekly("a", WED, 8, 30, weeks=3)) == 3
    assert weeks_covered([lesson("a", start=at(WED, 8, 30))]) == 1
    assert weeks_covered([]) == 0


# --------------------------------------------------------------------------
# The active week
# --------------------------------------------------------------------------


def test_active_week_is_the_week_of_the_next_lesson():
    lessons = weekly("a", WED, 8, 30, weeks=3)
    monday = active_week_start(lessons, at(1, 12, 0))
    assert monday == date(2026, 8, 31)


def test_active_week_rolls_over_once_the_week_is_done():
    """A finished week should not show as an empty Saturday and Sunday."""
    lessons = [lesson("a", start=at(WED, 8, 30)), lesson("b", start=at(WED + 7, 8, 30))]
    # Friday evening: this week's lesson is over, next week's is not.
    monday = active_week_start(lessons, at(4, 20, 0))
    assert monday == date(2026, 9, 7)


def test_a_fully_cancelled_week_is_still_the_active_week():
    """Rolling past it would hide exactly the week worth looking at."""
    lessons = [
        lesson("a", start=at(WED, 8, 30), status=LessonStatus.CANCELLED),
        lesson("b", start=at(WED + 7, 8, 30)),
    ]
    assert active_week_start(lessons, at(1, 12, 0)) == date(2026, 8, 31)


def test_active_week_falls_back_to_this_week_when_nothing_is_left():
    monday = active_week_start([], at(WED, 12, 0))
    assert monday == date(2026, 8, 31)


def test_active_week_groups_lessons_per_day():
    lessons = [
        lesson("a", start=at(WED, 8, 30)),
        lesson("b", start=at(WED, 10, 0), subject="biologie"),
        lesson("c", start=at(WED + 1, 8, 30), subject="Engels"),
    ]
    week = build_active_week(lessons, [], now=at(1, 12, 0))

    assert week["lesson_count"] == 3
    assert [d["date"] for d in week["days"]] == ["2026-09-02", "2026-09-03"]
    assert len(week["days"][0]["lessons"]) == 2


def test_a_week_matching_the_norm_has_no_deviations():
    lessons = weekly("a", WED, 8, 30, weeks=3)
    base = derive_base_week(lessons)
    week = build_active_week(lessons, base, now=at(1, 12, 0))

    assert week["deviation_count"] == 0
    assert all(
        not entry["deviates"] for day in week["days"] for entry in day["lessons"]
    )


def test_a_cancelled_lesson_shows_as_a_deviation():
    lessons = weekly("a", WED, 8, 30, weeks=3)
    base = derive_base_week(lessons)
    lessons[0] = lesson("a-0", start=at(WED, 8, 30), status=LessonStatus.CANCELLED)

    week = build_active_week(lessons, base, now=at(1, 12, 0))
    entry = week["days"][0]["lessons"][0]

    assert entry["deviates"] is True
    assert entry["deviation"] == "cancelled"
    assert week["deviation_count"] == 1


def test_a_room_change_shows_as_a_deviation():
    lessons = weekly("a", WED, 8, 30, weeks=3, location="205")
    base = derive_base_week(lessons)
    lessons[0] = lesson("a-0", start=at(WED, 8, 30), location="118")

    week = build_active_week(lessons, base, now=at(1, 12, 0))
    assert week["days"][0]["lessons"][0]["deviation"] == "different_room"


def test_an_extra_lesson_shows_as_a_deviation():
    base = derive_base_week(weekly("a", WED, 8, 30, weeks=3))
    lessons = [lesson("extra", start=at(WED, 15, 0), subject="bijles")]

    week = build_active_week(lessons, base, now=at(1, 12, 0))
    assert week["days"][0]["lessons"][0]["deviation"] == "extra"


def test_a_lesson_missing_this_week_is_listed_not_silently_absent():
    """A quietly removed lesson is the one thing an active-week view could
    otherwise hide completely."""
    base = derive_base_week(weekly("a", WED, 8, 30, weeks=3))
    week = build_active_week([], base, now=at(WED, 6, 0))

    day = next(d for d in week["days"] if d["weekday"] == 3)
    assert day["lessons"] == []
    assert len(day["missing"]) == 1
    assert day["missing"][0]["start_time"] == "08:30"
    assert week["deviation_count"] == 1


def test_empty_days_are_left_out():
    lessons = [lesson("a", start=at(WED, 8, 30))]
    week = build_active_week(lessons, [], now=at(1, 12, 0))
    assert len(week["days"]) == 1


def test_week_metadata():
    lessons = [lesson("a", start=at(WED, 8, 30))]
    week = build_active_week(lessons, [], now=at(1, 12, 0))

    assert week["week_start"] == "2026-08-31"
    assert week["week_end"] == "2026-09-06"
    assert week["week_number"] == date(2026, 8, 31).isocalendar().week


def test_an_empty_timetable_is_not_an_error():
    week = build_active_week([], [], now=at(1, 12, 0))
    assert week["lesson_count"] == 0
    assert week["days"] == []
    assert week["deviation_count"] == 0


def test_the_normal_week_survives_the_winter_time_switch():
    """Wall-clock times are what a base week is about, so 08:30 stays 08:30."""
    lessons = [
        lesson("a-0", start=at(21, 8, 30, month=10)),
        lesson("a-1", start=at(28, 8, 30, month=10)),
    ]
    slots = derive_base_week(lessons)

    assert len(slots) == 1
    assert slots[0].start_time == "08:30"
    # Genuinely either side of the 25 October switch.
    assert lessons[0].start.utcoffset() == timedelta(hours=2)
    assert lessons[1].start.utcoffset() == timedelta(hours=1)
