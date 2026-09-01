"""Tests for the payload normalisation layer."""
from __future__ import annotations

from datetime import datetime, timezone

from custom_components.somtoday.const import HomeworkType, LessonStatus
from custom_components.somtoday.models import (
    clean_text,
    map_homework_type,
    map_lesson_status,
    parse_datetime,
    parse_homework,
    parse_lesson,
    parse_student,
)

from . import payloads


def test_parse_student():
    parsed = parse_student(payloads.student())
    assert parsed.student_id == payloads.STUDENT_A_ID
    assert parsed.full_name == "Fien Voorbeeld"


def test_parse_student_without_self_link_is_dropped():
    assert parse_student({"roepnaam": "Fien", "links": []}) is None


def test_parse_student_falls_back_when_name_is_empty():
    parsed = parse_student(payloads.student(first_name="", last_name=""))
    assert parsed.full_name == f"Leerling {payloads.STUDENT_A_ID}"


def test_parse_lesson_full():
    lesson = parse_lesson(payloads.appointment())
    assert lesson.subject == "wiskunde B"
    assert lesson.subject_short == "wisB"
    assert lesson.teacher == "Abc"
    assert lesson.location == "217"
    assert lesson.period_start == 1
    assert lesson.status is LessonStatus.SCHEDULED
    assert lesson.is_cancelled is False
    assert lesson.display_name == "wiskunde B"


def test_parse_lesson_without_optional_fields():
    """Somtoday omits these regularly; a lesson must still come through."""
    lesson = parse_lesson(
        payloads.appointment(
            subject=None,
            subject_short=None,
            teacher=None,
            location=None,
            period_start=None,
            period_end=None,
        )
    )
    assert lesson is not None
    assert lesson.subject is None
    assert lesson.teacher is None
    assert lesson.location is None
    assert lesson.period_start is None
    # Falls back to Somtoday's own title rather than showing nothing.
    assert lesson.display_name


def test_parse_lesson_without_times_is_dropped():
    """An appointment with no usable times cannot be placed on a calendar."""
    assert parse_lesson(payloads.appointment(start="", end="")) is None


def test_parse_lesson_with_inverted_times_is_dropped():
    assert (
        parse_lesson(
            payloads.appointment(
                start="2026-09-07T09:20:00.000+02:00",
                end="2026-09-07T08:30:00.000+02:00",
            )
        )
        is None
    )


def test_parse_lesson_cancelled():
    lesson = parse_lesson(payloads.appointment(status="GEANNULEERD"))
    assert lesson.status is LessonStatus.CANCELLED
    assert lesson.is_cancelled is True
    assert lesson.raw_status == "GEANNULEERD"


def test_parse_lesson_unknown_status_is_not_guessed():
    """An unmapped status must never be silently reported as scheduled."""
    lesson = parse_lesson(payloads.appointment(status="IETS_NIEUWS"))
    assert lesson.status is LessonStatus.UNKNOWN
    assert lesson.is_cancelled is False
    assert lesson.raw_status == "IETS_NIEUWS"


def test_parse_lesson_collects_student_ids():
    lesson = parse_lesson(
        payloads.appointment(student_ids=[payloads.STUDENT_A_ID, payloads.STUDENT_B_ID])
    )
    assert lesson.student_ids == frozenset(
        {payloads.STUDENT_A_ID, payloads.STUDENT_B_ID}
    )


def test_parse_lesson_without_student_block_applies_to_everyone():
    """An empty set means 'the whole account', not 'nobody'."""
    assert parse_lesson(payloads.appointment()).student_ids == frozenset()


def test_summer_and_winter_lessons_keep_their_local_time():
    """Offsets come from Somtoday, so the DST switch needs no guessing."""
    summer = parse_lesson(
        payloads.appointment(
            start="2026-09-07T08:30:00.000+02:00",
            end="2026-09-07T09:20:00.000+02:00",
        )
    )
    winter = parse_lesson(
        payloads.appointment(
            start="2026-11-09T08:30:00.000+01:00",
            end="2026-11-09T09:20:00.000+01:00",
        )
    )
    assert summer.start.utcoffset().total_seconds() == 2 * 3600
    assert winter.start.utcoffset().total_seconds() == 3600
    # Both start at 08:30 local, which is what a timetable actually says.
    assert summer.start.hour == winter.start.hour == 8
    # And they are genuinely a different absolute moment.
    assert summer.start.astimezone(timezone.utc).hour == 6
    assert winter.start.astimezone(timezone.utc).hour == 7


def test_parse_datetime_rejects_naive_values():
    """A timestamp without an offset would have to be guessed at — don't."""
    assert parse_datetime("2026-09-07T08:30:00") is None
    assert parse_datetime("not a date") is None
    assert parse_datetime(None) is None


def test_parse_datetime_accepts_zulu():
    parsed = parse_datetime("2026-09-07T06:30:00Z")
    assert parsed == datetime(2026, 9, 7, 6, 30, tzinfo=timezone.utc)


def test_clean_text_strips_html():
    assert clean_text("<p>Opgaven 1 &amp; 2</p>") == "Opgaven 1 & 2"
    assert clean_text("   ") is None
    assert clean_text(None) is None


def test_parse_homework():
    item = parse_homework(payloads.homework())
    assert item.kind is HomeworkType.HOMEWORK
    assert item.is_test is False
    assert item.subject == "wiskunde B"
    assert item.topic == "Hoofdstuk 3 maken"
    assert item.description == "Opgaven 1 t/m 12"
    assert item.done is False


def test_parse_homework_test_types():
    assert parse_homework(payloads.homework(kind="TOETS")).is_test is True
    assert parse_homework(payloads.homework(kind="GROTE_TOETS")).is_test is True


def test_parse_homework_unknown_type():
    item = parse_homework(payloads.homework(kind="PROJECT"))
    assert item.kind is HomeworkType.UNKNOWN
    assert item.is_test is False
    assert item.raw_kind == "PROJECT"


def test_parse_homework_done_flag():
    assert parse_homework(payloads.homework(done=True)).done is True


def test_parse_homework_done_tick_matches_the_right_student():
    """On a parent account, one child's tick must not mark the other's done."""
    raw = payloads.homework(done_for_student=payloads.STUDENT_A_ID)
    assert parse_homework(raw, payloads.STUDENT_A_ID).done is True
    assert parse_homework(raw, payloads.STUDENT_B_ID).done is False


def test_parse_homework_without_due_date():
    item = parse_homework(payloads.homework(due=None, with_appointment=False))
    assert item.due is None


def test_parse_homework_without_item_is_dropped():
    assert parse_homework({"datumTijd": "2026-09-08T08:30:00.000+02:00"}) is None


def test_status_and_type_mapping_edge_cases():
    assert map_lesson_status(None) is LessonStatus.UNKNOWN
    assert map_lesson_status("actief") is LessonStatus.SCHEDULED
    assert map_homework_type(None) is HomeworkType.UNKNOWN
    assert map_homework_type("toets") is HomeworkType.TEST
