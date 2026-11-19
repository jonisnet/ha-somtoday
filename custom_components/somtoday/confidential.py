"""Serving confidential records — grades, absences, measures, messages.

Home Assistant cannot restrict who reads an entity: every signed-in user can
open the history, the logbook or the entity search and see any state and
attribute. Hiding a dashboard card per user is cosmetic. So confidential
records never become entity states or attributes. They stay inside the
integration and leave it only through the ``somtoday.get_records`` service,
which checks the calling user against the per-category lists in the options.

Calls without a user — an automation, the notification blueprint — are
allowed: they run as the system, and whoever set them up chose where the
result goes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import CONF_ALLOWED_USERS, CONFIDENTIAL_CATEGORIES, DOMAIN

if TYPE_CHECKING:
    from . import SomtodayConfigEntry

# Hard cap per category, so a response stays a reasonable size for a card.
MAX_ITEMS = 200

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("student_id"): vol.Coerce(int),
        vol.Optional("categories"): vol.All(
            cv.ensure_list, [vol.In(CONFIDENTIAL_CATEGORIES)]
        ),
        vol.Optional("uids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("limit", default=MAX_ITEMS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_ITEMS)
        ),
    }
)


def allowed_categories(
    entry: SomtodayConfigEntry, user_id: str | None
) -> set[str]:
    """Return the confidential categories this user may see."""
    if user_id is None:
        return set(CONFIDENTIAL_CATEGORIES)
    return {
        category
        for category in CONFIDENTIAL_CATEGORIES
        if user_id in (entry.options.get(CONF_ALLOWED_USERS[category]) or [])
    }


def _entries(hass: HomeAssistant, entry_id: str | None) -> list[SomtodayConfigEntry]:
    from . import SomtodayData  # noqa: PLC0415 - avoid an import cycle

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(getattr(entry, "runtime_data", None), SomtodayData)
        and (entry_id is None or entry.entry_id == entry_id)
    ]
    if entry_id is not None and not entries:
        raise HomeAssistantError("Unknown or not loaded Somtoday config entry")
    return entries


def _pick(items: list[Any], uids: set[str] | None, limit: int, key: str = "uid") -> list[dict]:
    rows = [item.as_dict() for item in items]
    if uids is not None:
        rows = [
            row
            for row in rows
            # A grade is announced as "uid:result", so a resit with a new
            # result counts as new; match on either form.
            if row.get(key) in uids
            or row.get("latest_uid") in uids
            or f"{row.get(key)}:{row.get('result')}" in uids
        ]
    return rows[:limit]


async def async_get_records(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Return the confidential records the calling user is allowed to see."""
    requested = set(call.data.get("categories") or CONFIDENTIAL_CATEGORIES)
    uids = set(call.data["uids"]) if call.data.get("uids") else None
    limit = call.data["limit"]
    wanted_student = call.data.get("student_id")
    user_id = call.context.user_id

    entries_out: list[dict[str, Any]] = []
    for entry in _entries(hass, call.data.get("config_entry_id")):
        allowed = allowed_categories(entry, user_id)
        coordinator = entry.runtime_data.coordinator
        account = coordinator.account_extras
        out: dict[str, Any] = {
            "config_entry_id": entry.entry_id,
            "access": {category: category in allowed for category in CONFIDENTIAL_CATEGORIES},
            "students": [],
        }
        if "messages" in requested and "messages" in allowed:
            out["messages"] = (
                _pick(account.messages, uids, limit)
                if "messages" in account.available
                else None
            )
            out["unread_messages"] = sum(1 for m in account.messages if m.unread)
        for student_id, data in (coordinator.data or {}).items():
            if wanted_student is not None and student_id != wanted_student:
                continue
            extras = data.extras
            student: dict[str, Any] = {
                "student_id": student_id,
                "name": data.student.full_name,
            }
            if "grades" in requested and "grades" in allowed:
                if "grades" in extras.available:
                    student["grades"] = _pick(extras.grades, uids, limit)
                    student["averages"] = [a.as_dict() for a in extras.averages]
                    student["overall_average"] = extras.overall_average
                else:
                    student["grades"] = None
            if "absences" in requested and "absences" in allowed:
                student["absences"] = (
                    _pick(extras.registrations, uids, limit)
                    if "absences" in extras.available
                    else None
                )
            if "measures" in requested and "measures" in allowed:
                student["measures"] = (
                    _pick(extras.measures, uids, limit)
                    if "measures" in extras.available
                    else None
                )
            out["students"].append(student)
        entries_out.append(out)

    return {"generated": dt_util.utcnow().isoformat(), "entries": entries_out}
