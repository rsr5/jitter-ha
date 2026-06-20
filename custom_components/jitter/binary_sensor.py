"""S38 — Jitter → HA binary sensors.

Just one for v1: `binary_sensor.jitter_today_complete`, on when every
due habit for today has a done/skipped/snoozed outcome.  Useful as an
automation trigger ("when all habits done before 19:00 → reward
ritual").
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JitterCoordinator
from .sensor import _device_info, _habit_items


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN][entry.entry_id]
    coordinator: JitterCoordinator = bucket["coordinator"]
    async_add_entities([JitterTodayCompleteBinarySensor(coordinator, entry)])


class JitterTodayCompleteBinarySensor(
    CoordinatorEntity[JitterCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "Today complete"
    _attr_icon = "mdi:flag-checkered"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: JitterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_today_complete"

    @property
    def is_on(self) -> bool:
        # ON when every habit item on today's plan has reached a
        # terminal status (done / skipped / snoozed / missed).
        # OFF if any are still pending / due_now / overdue / upcoming.
        # Treats "no habits planned" as ON (vacuously complete) — the
        # user can downgrade to "false-when-empty" later if they want.
        items = _habit_items(self.coordinator.data)
        if not items:
            return True
        return all(
            it.get("status") in ("done", "skipped", "snoozed", "missed")
            for it in items
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = _habit_items(self.coordinator.data)
        return {
            "total": len(items),
            "done": sum(1 for it in items if it.get("status") == "done"),
            "remaining": sum(
                1 for it in items
                if it.get("status") in ("pending", "due_now", "overdue", "upcoming")
            ),
        }
