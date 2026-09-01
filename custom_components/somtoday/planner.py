"""Local, persistent Somtoday planner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_PLANNER_ALLOWED_USERS

STORAGE_VERSION = 1


class SomtodayPlanner:
    """Store appointments locally and enforce edit permissions server-side."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]
    ) -> None:
        """Initialise the planner for one config entry."""
        self.hass = hass
        self.entry_id = entry_id
        self.options = options
        self.items: list[dict[str, Any]] = []
        self._listeners: list[Callable[[], None]] = []
        self._store = Store(hass, STORAGE_VERSION, f"somtoday.planner.{entry_id}")

    async def async_load(self) -> None:
        """Load stored planner items."""
        data = await self._store.async_load() or {}
        self.items = list(data.get("items", []))

    def listen(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Notify an entity whenever planner data changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_assert_allowed(self, call: ServiceCall) -> None:
        """Reject calls from users without planner edit permission."""
        user_id = call.context.user_id
        if not user_id:
            raise Unauthorized(context=call.context)
        user = await self.hass.auth.async_get_user(user_id)
        allowed = self.options.get(CONF_PLANNER_ALLOWED_USERS, [])
        if user is None or (not user.is_admin and user_id not in allowed):
            raise Unauthorized(context=call.context)

    async def async_add(self, call: ServiceCall) -> dict[str, Any]:
        """Add and persist an appointment."""
        await self.async_assert_allowed(call)
        start = _as_local_iso(call.data["start"])
        end = _as_local_iso(call.data["end"])
        if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
            raise HomeAssistantError("End time must be after start time")
        item = {
            "id": uuid4().hex,
            "student_id": int(call.data["student_id"]),
            "title": call.data["title"].strip(),
            "description": call.data.get("description", "").strip(),
            "start": start,
            "end": end,
            "created_by": call.context.user_id,
        }
        self.items.append(item)
        await self._save()
        return item

    async def async_delete(self, call: ServiceCall) -> None:
        """Delete and persist an appointment."""
        await self.async_assert_allowed(call)
        item_id = call.data["item_id"]
        before = len(self.items)
        self.items = [item for item in self.items if item["id"] != item_id]
        if len(self.items) == before:
            raise HomeAssistantError("Planner item not found")
        await self._save()

    async def _save(self) -> None:
        self.items.sort(key=lambda item: item["start"])
        await self._store.async_save({"items": self.items})
        for listener in list(self._listeners):
            listener()


def _as_local_iso(value: datetime | str) -> str:
    parsed = value if isinstance(value, datetime) else dt_util.parse_datetime(value)
    if parsed is None:
        raise HomeAssistantError("Invalid date/time")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed.isoformat()
