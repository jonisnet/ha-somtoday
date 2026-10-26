"""A persistent log of when timetable changes came in.

Somtoday does not say when a lesson was changed: neither the appointments the
integration reads nor Somtoday's own app carry a modification time. What can be
known is when *this integration* first saw the change — the poll that noticed
it. That moment is what the log records, as ``detected_at``.

The changes themselves already exist as the ``somtoday_schedule_changed``
event, but an event is gone once it has fired. This keeps them, per student,
in Home Assistant's ``.storage`` so a sensor (and a dashboard card) can show
what changed and when, also after a restart.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .changes import LessonChange

STORAGE_VERSION = 1

# How long a change stays in the log. Long enough to look back over a week and
# a weekend; short enough that the sensor attribute stays small.
RETENTION = timedelta(days=14)

# Hard cap per student, whatever the age: a school that reshuffles a whole
# week at once must not blow up the attribute.
MAX_ENTRIES = 50

# Writes are coalesced: several students changing in the same poll, or a few
# polls in a row, end up as one write.
SAVE_DELAY = 10


def prune(
    entries: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Return the entries still worth keeping, newest first.

    Pure on purpose, so the retention rules can be tested without a Home
    Assistant instance. An entry whose ``detected_at`` cannot be read is
    dropped rather than kept forever.
    """
    cutoff = now - RETENTION
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for entry in entries:
        detected = dt_util.parse_datetime(str(entry.get("detected_at") or ""))
        if detected is None or detected < cutoff:
            continue
        kept.append((detected, entry))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in kept[:MAX_ENTRIES]]


def entries_for(
    changes: Iterable[LessonChange], detected_at: datetime
) -> list[dict[str, Any]]:
    """Turn one poll's changes into log entries sharing one ``detected_at``."""
    stamp = detected_at.isoformat()
    return [{"detected_at": stamp, **change.as_event_data()} for change in changes]


class SomtodayChangeLog:
    """Per-student change log for one config entry, persisted in ``.storage``."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise an empty log; call ``async_load`` before use."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"somtoday.changes.{entry_id}"
        )
        self._entries: dict[int, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        """Read the stored log, dropping whatever has aged out meanwhile."""
        data = await self._store.async_load() or {}
        now = dt_util.utcnow()
        self._entries = {
            int(student_id): prune(entries, now)
            for student_id, entries in (data.get("students") or {}).items()
            if str(student_id).isdigit() and isinstance(entries, list)
        }

    def record(
        self,
        student_id: int,
        changes: list[LessonChange],
        detected_at: datetime | None = None,
    ) -> None:
        """Add one poll's changes for a student and schedule a save."""
        if not changes:
            return
        now = detected_at or dt_util.now()
        self._entries[student_id] = prune(
            [*entries_for(changes, now), *self._entries.get(student_id, [])],
            now,
        )
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def entries(self, student_id: int) -> list[dict[str, Any]]:
        """Return a student's logged changes, newest first, aged-out ones dropped."""
        return prune(self._entries.get(student_id, []), dt_util.now())

    def _data(self) -> dict[str, Any]:
        return {
            "students": {
                str(student_id): entries
                for student_id, entries in self._entries.items()
            }
        }
