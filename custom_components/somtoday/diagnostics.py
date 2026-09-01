"""Diagnostics support for the Somtoday integration.

Somtoday holds a minor's school data, so this deliberately publishes a
*structural* picture rather than redacted payloads: counts, statuses, field
coverage and timings. That is what actually helps debug a schedule problem,
and it means no name, class, teacher, room, homework topic or token can leak
into a diagnostics file attached to a public issue.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SomtodayConfigEntry
from .coordinator import StudentData

# Everything secret or account-identifying on the config entry itself.
TO_REDACT = {
    "refresh_token",
    "access_token",
    "id_token",
    "code",
    "code_verifier",
    "tenant",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SomtodayConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Somtoday config entry."""
    coordinator = entry.runtime_data.coordinator
    tokens = entry.runtime_data.client.auth.tokens

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "session": {
            # Whether a session exists and when it expires is exactly what a
            # token bug needs; the token values themselves are never useful.
            "has_refresh_token": bool(tokens.refresh_token),
            "access_token_expires_at": tokens.expires_at.isoformat(),
            "access_token_expired": tokens.is_expired,
            "api_url": tokens.api_url,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_success_time": (
                coordinator.last_success_time.isoformat()
                if coordinator.last_success_time
                else None
            ),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        # Students are reported positionally. The count matters for debugging a
        # parent account; which children they are does not.
        "students": [
            _student_diagnostics(index, data)
            for index, data in enumerate(sorted(
                (coordinator.data or {}).values(),
                key=lambda item: item.student.student_id,
            ))
        ],
    }


def _student_diagnostics(index: int, data: StudentData) -> dict[str, Any]:
    """Summarise one student's data without identifying them."""
    lessons = data.lessons
    homework = data.homework

    return {
        "index": index,
        "lessons": {
            "count": len(lessons),
            "first_start": lessons[0].start.isoformat() if lessons else None,
            "last_end": lessons[-1].end.isoformat() if lessons else None,
            "by_status": dict(Counter(lesson.status.value for lesson in lessons)),
            # The raw Dutch status strings are the whole reason an unmapped
            # status needs reporting, and they carry no personal data.
            "raw_statuses_seen": sorted(
                {lesson.raw_status for lesson in lessons if lesson.raw_status}
            ),
            # Field coverage tells us whether Somtoday changed a payload shape.
            "with_subject": sum(1 for lesson in lessons if lesson.subject),
            "with_teacher": sum(1 for lesson in lessons if lesson.teacher),
            "with_location": sum(1 for lesson in lessons if lesson.location),
            "with_period": sum(1 for lesson in lessons if lesson.period_start),
        },
        "homework": {
            "count": len(homework),
            "by_type": dict(Counter(item.kind.value for item in homework)),
            "raw_types_seen": sorted(
                {item.raw_kind for item in homework if item.raw_kind}
            ),
            "done": sum(1 for item in homework if item.done),
            "with_due_date": sum(1 for item in homework if item.due is not None),
            "with_subject": sum(1 for item in homework if item.subject),
        },
    }
