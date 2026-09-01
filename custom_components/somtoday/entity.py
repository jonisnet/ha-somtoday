"""Shared entity base for the Somtoday integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import SomtodayCoordinator, StudentData


class SomtodayStudentEntity(CoordinatorEntity[SomtodayCoordinator]):
    """Base class for every entity belonging to one student.

    Each student becomes its own Home Assistant device, so a parent account
    with two children gets two clean sets of entities instead of one device
    with ambiguous names.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: SomtodayCoordinator,
        student_id: int,
        key: str,
    ) -> None:
        """Initialise the entity for one student and one measurement."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._attr_translation_key = key
        # Somtoday student ids are unique across the platform, so this stays
        # stable if the entry is removed and re-added.
        self._attr_unique_id = f"{student_id}_{key}"

        student = coordinator.data[student_id].student
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(student_id))},
            name=f"Somtoday ({student.full_name})",
            manufacturer="Topicus",
            model="Somtoday",
        )

    @property
    def student_data(self) -> StudentData | None:
        """Return this student's latest data, or ``None`` if they disappeared."""
        return (self.coordinator.data or {}).get(self._student_id)

    @property
    def available(self) -> bool:
        """Return whether the entity has usable data.

        Unavailable covers both a failed poll (handled by the base class) and a
        student who is no longer on the account — a graduated child, say.
        """
        return super().available and self.student_data is not None
