"""Tests for timetable change detection."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.somtoday.changes import (
    ChangeType,
    diff_lessons,
    snapshot,
)
from custom_components.somtoday.const import LessonStatus

from .test_derivations import AMSTERDAM, at, lesson

NOW = at(1, 12, 0)
HORIZON = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def dutch_timezone():
    """Run in the timezone a Dutch school actually lives in."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.DEFAULT_TIME_ZONE = AMSTERDAM
    yield
    dt_util.DEFAULT_TIME_ZONE = original


def diff(before, after, *, now=NOW, horizon=HORIZON):
    """Compare two timetables with the usual scope."""
    return diff_lessons(snapshot(before), after, now=now, horizon=horizon)


def test_no_changes_is_silent():
    lessons = [lesson("a", start=at(2, 8, 30)), lesson("b", start=at(2, 10, 0))]
    assert diff(lessons, lessons) == []


def test_a_cancelled_lesson_is_reported():
    before = [lesson("a", start=at(2, 8, 30))]
    after = [lesson("a", start=at(2, 8, 30), status=LessonStatus.CANCELLED)]

    changes = diff(before, after)
    assert [c.type for c in changes] == [ChangeType.CANCELLED]
    assert changes[0].lesson.subject == "wiskunde B"
    assert changes[0].previous.status is LessonStatus.SCHEDULED


def test_a_reinstated_lesson_is_reported():
    before = [lesson("a", start=at(2, 8, 30), status=LessonStatus.CANCELLED)]
    after = [lesson("a", start=at(2, 8, 30))]
    assert [c.type for c in diff(before, after)] == [ChangeType.REINSTATED]


def test_a_lesson_that_stays_cancelled_is_not_reported_again():
    """Otherwise every poll would re-notify about the same dropped lesson."""
    dropped = [lesson("a", start=at(2, 8, 30), status=LessonStatus.CANCELLED)]
    assert diff(dropped, dropped) == []


def test_cancelling_outranks_room_and_teacher():
    """A dropped lesson is one piece of news, not three."""
    before = [lesson("a", start=at(2, 8, 30), location="205", teacher="roze")]
    after = [
        lesson(
            "a",
            start=at(2, 8, 30),
            location=None,
            teacher=None,
            status=LessonStatus.CANCELLED,
        )
    ]
    assert [c.type for c in diff(before, after)] == [ChangeType.CANCELLED]


def test_a_moved_lesson_is_reported_with_its_old_time():
    before = [lesson("a", start=at(2, 8, 30))]
    after = [lesson("a", start=at(2, 11, 0))]

    changes = diff(before, after)
    assert [c.type for c in changes] == [ChangeType.MOVED]
    assert changes[0].previous.start == at(2, 8, 30)
    assert changes[0].lesson.start == at(2, 11, 0)


def test_room_and_teacher_changes_are_separate():
    before = [lesson("a", start=at(2, 8, 30), location="205", teacher="roze")]
    after = [lesson("a", start=at(2, 8, 30), location="118", teacher="bent")]

    assert [c.type for c in diff(before, after)] == [
        ChangeType.ROOM_CHANGED,
        ChangeType.TEACHER_CHANGED,
    ]


def test_an_added_lesson_is_reported():
    """An extra lesson on a day that already had lessons is a real insertion."""
    before = [lesson("a", start=at(2, 8, 30))]
    after = [lesson("a", start=at(2, 8, 30)), lesson("b", start=at(2, 10, 0))]

    changes = diff(before, after)
    assert [c.type for c in changes] == [ChangeType.ADDED]
    assert changes[0].lesson.uid == "b"


def test_a_newly_published_day_is_not_a_change():
    """Reported from use: a school publishing the timetable a fortnight out
    produced "8 nieuwe lessen" for a day nobody had changed anything about.
    A day we held nothing for is publication, not a change to the schedule."""
    before = [lesson("bekend", start=at(2, 8, 30))]
    nieuwe_dag = [
        lesson(f"nieuw-{i}", start=at(15, 8 + i, 0)) for i in range(8)
    ]
    assert diff(before, before + nieuwe_dag) == []


def test_a_lesson_added_to_a_known_day_still_counts():
    """The mirror of the rule: only *unseen days* are treated as publication."""
    before = [lesson("bekend", start=at(15, 8, 30))]
    after = before + [lesson("extra", start=at(15, 14, 0), subject="bijles")]

    changes = diff(before, after)
    assert [c.type for c in changes] == [ChangeType.ADDED]
    assert changes[0].lesson.uid == "extra"


def test_a_removed_lesson_is_reported():
    before = [lesson("a", start=at(2, 8, 30)), lesson("b", start=at(2, 10, 0))]
    after = [lesson("a", start=at(2, 8, 30))]

    changes = diff(before, after)
    assert [c.type for c in changes] == [ChangeType.REMOVED]
    assert changes[0].lesson.uid == "b"


def test_lessons_that_already_started_are_ignored():
    """A lesson in the past cannot usefully change, and the window drops old
    days as it slides — reporting those would be noise every single day.

    This is what keeps the notification automation honest: days falling off the
    back of the fetch window are exactly how a past lesson disappears, and the
    user must never be told their child's finished lesson was "removed".
    """
    before = [lesson("past", start=at(1, 8, 30))]
    after = []
    assert diff(before, after) == []


def test_a_whole_past_day_dropping_out_of_the_window_is_silent():
    """What actually happens at midnight when the window moves on.

    NOW is 1 September midday, so these seven lessons on 31 August are wholly
    in the past — which is the point. A lesson later *today* that disappears is
    a different matter and does get reported.
    """
    gone = [lesson(f"gisteren-{i}", start=at(31, 8 + i, 0, month=8)) for i in range(7)]
    future = [lesson("morgen", start=at(2, 8, 30))]

    assert diff(gone + future, future) == []


def test_a_future_lesson_disappearing_is_still_reported():
    """The mirror of the test above: the guard is about the past, not about
    disappearing as such."""
    later_today = [lesson("straks", start=at(1, 15, 0))]
    assert [c.type for c in diff(later_today, [])] == [ChangeType.REMOVED]


def test_the_newly_revealed_far_edge_is_not_a_pile_of_new_lessons():
    """The fetch window moves forward about a day per poll. Without the horizon
    every poll would announce a whole new day of 'added' lessons."""
    before = [lesson("known", start=at(2, 8, 30))]
    after = [
        lesson("known", start=at(2, 8, 30)),
        lesson("beyond", start=at(15, 8, 30)),
    ]
    horizon = date(2026, 9, 14)
    assert diff(before, after, horizon=horizon) == []


def test_a_change_inside_the_horizon_still_counts():
    before = [lesson("a", start=at(10, 8, 30))]
    after = [lesson("a", start=at(10, 8, 30), status=LessonStatus.CANCELLED)]
    assert [c.type for c in diff(before, after, horizon=date(2026, 9, 14))] == [
        ChangeType.CANCELLED
    ]


def test_changes_come_back_in_timetable_order():
    before = [
        lesson("late", start=at(3, 14, 0)),
        lesson("early", start=at(2, 8, 30)),
    ]
    after = [
        lesson("late", start=at(3, 14, 0), status=LessonStatus.CANCELLED),
        lesson("early", start=at(2, 8, 30), status=LessonStatus.CANCELLED),
    ]
    assert [c.lesson.uid for c in diff(before, after)] == ["early", "late"]


def test_event_payload_shape():
    before = [lesson("a", start=at(2, 8, 30), location="205", teacher="roze")]
    after = [lesson("a", start=at(2, 8, 30), location="118", teacher="roze")]

    data = diff(before, after)[0].as_event_data()

    assert data["type"] == "room_changed"
    assert data["subject"] == "wiskunde B"
    assert data["location"] == "118"
    assert data["previous"]["location"] == "205"
    # The "was" side carries the same fields as the "now" side, so a
    # notification can show them together without special-casing.
    assert set(data["previous"]) == {
        "teacher", "location", "start", "end", "period_start", "status",
        "cancelled",
    }
    assert data["cancelled"] is False
    # The student is identified by device_id on the event, not by name here.
    assert "student" not in data
    assert "name" not in data


def test_event_payload_without_a_previous_version():
    """`added` carries no `previous` — there was no earlier version of it.

    The day has to be one we already knew, otherwise this counts as the
    timetable being published rather than changed.
    """
    known = [lesson("bekend", start=at(2, 8, 30))]
    after = known + [lesson("nieuw", start=at(2, 10, 0))]

    added = diff(known, after)[0].as_event_data()
    assert added["type"] == "added"
    assert "previous" not in added


def test_a_whole_day_being_dropped_is_one_batch_of_changes():
    """Seven cancellations must arrive together, so one notification covers them."""
    day = [lesson(str(i), start=at(2, 8 + i, 0)) for i in range(7)]
    dropped = [
        lesson(str(i), start=at(2, 8 + i, 0), status=LessonStatus.CANCELLED)
        for i in range(7)
    ]

    changes = diff(day, dropped)
    assert len(changes) == 7
    assert all(c.type is ChangeType.CANCELLED for c in changes)


def test_horizon_of_today_reports_nothing_beyond_it():
    before = [lesson("a", start=at(5, 8, 30))]
    after = [lesson("a", start=at(5, 8, 30), status=LessonStatus.CANCELLED)]
    horizon = (NOW + timedelta(days=1)).date()
    assert diff(before, after, horizon=horizon) == []
