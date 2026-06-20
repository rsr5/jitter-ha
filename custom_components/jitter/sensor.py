"""S38 — Jitter → HA sensor entities.

What this exposes:

- Daily aggregates (4 sensors): habits_done_today, habits_remaining_today,
  next_habit_due, active_streaks.
- Per-habit sensors (dynamic, one per active habit): jitter_habit_<slug>
  with state ∈ due/done/skipped/snoozed, attrs carrying streak,
  last_done, etc.
- Per-goal sensors (dynamic, one per active goal): jitter_goal_<slug>
  with state ∈ on_track/ahead/behind/achieved/missed/unknown, attrs
  carrying current/target/gap/days_remaining/projection/catch_up_rate.

Design philosophy: one entity per "thing" with state in attrs (not
N entities per thing).  See jitter/docs/voice-capture-diary.md /
agreed design.

Dynamic discovery: every habit and goal returned by the coordinator
gets an entity automatically.  If you want to hide noisy ones, add a
`expose_in_ha: false` field to the habit/goal server-side later — for
now everything surfaces.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JitterCoordinator, JitterData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build the static aggregates + dynamically-discovered per-habit
    + per-goal entities from the coordinator's first tick.  After
    setup we listen for coordinator updates and add fresh entities
    when habits/goals appear that we haven't surfaced yet."""
    bucket = hass.data[DOMAIN][entry.entry_id]
    coordinator: JitterCoordinator = bucket["coordinator"]

    # Static aggregates — always present.
    entities: list[SensorEntity] = [
        JitterHabitsDoneTodaySensor(coordinator, entry),
        JitterHabitsRemainingTodaySensor(coordinator, entry),
        JitterNextHabitDueSensor(coordinator, entry),
        JitterActiveStreaksSensor(coordinator, entry),
    ]

    # Dynamic per-habit + per-goal entities — one per item in the
    # coordinator's snapshot.
    seen_habits: set[str] = set()
    seen_goals: set[str] = set()
    for h in coordinator.data.habits:
        slug = h.get("slug")
        if slug and slug not in seen_habits:
            entities.append(JitterHabitSensor(coordinator, entry, slug))
            seen_habits.add(slug)
    for g in coordinator.data.goals:
        slug = g.get("slug")
        if slug and slug not in seen_goals:
            entities.append(JitterGoalSensor(coordinator, entry, slug))
            seen_goals.add(slug)

    async_add_entities(entities)

    # Discovery hook: when a tick brings in a new habit/goal, surface
    # it.  Won't go back and remove deleted ones in v1 — HA's UI
    # disabled-entity mechanism is the right place for that.
    @callback
    def _maybe_add_new() -> None:
        new_entities: list[SensorEntity] = []
        for h in coordinator.data.habits:
            slug = h.get("slug")
            if slug and slug not in seen_habits:
                new_entities.append(JitterHabitSensor(coordinator, entry, slug))
                seen_habits.add(slug)
        for g in coordinator.data.goals:
            slug = g.get("slug")
            if slug and slug not in seen_goals:
                new_entities.append(JitterGoalSensor(coordinator, entry, slug))
                seen_goals.add(slug)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_maybe_add_new))


# ── Base + device grouping ──────────────────────────────────────

def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Group all jitter entities under a single Device in HA's UI —
    keeps the registry tidy and makes 'Jitter' findable in the device
    list (entities are namespaced by domain but devices are first-class)."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Jitter",
        manufacturer="ridlers.org",
        model="Self-hosted habit + goal tracker",
        configuration_url=entry.data.get("base_url"),
    )


class _JitterBase(CoordinatorEntity[JitterCoordinator], SensorEntity):
    """Shared device-info + entry attribution for every jitter sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: JitterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


# ── Aggregate sensors ───────────────────────────────────────────

class JitterHabitsDoneTodaySensor(_JitterBase):
    _attr_name = "Habits done today"
    _attr_icon = "mdi:check-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_habits_done_today"

    @property
    def native_value(self) -> int:
        return sum(1 for it in self._items() if it.get("status") == "done")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        done = [
            {
                "slug": it.get("habit_slug"),
                "title": it.get("title"),
            }
            for it in self._items()
            if it.get("status") == "done" and it.get("habit_slug")
        ]
        return {"items": done}

    def _items(self) -> list[dict[str, Any]]:
        return _habit_items(self.coordinator.data)


class JitterHabitsRemainingTodaySensor(_JitterBase):
    _attr_name = "Habits remaining today"
    _attr_icon = "mdi:checkbox-blank-circle-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_habits_remaining_today"

    @property
    def native_value(self) -> int:
        return sum(
            1 for it in _habit_items(self.coordinator.data)
            if it.get("status") in ("pending", "due_now", "overdue", "upcoming")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        remaining = [
            {
                "slug": it.get("habit_slug"),
                "title": it.get("title"),
                "start": it.get("start"),
                "status": it.get("status"),
            }
            for it in _habit_items(self.coordinator.data)
            if it.get("status") in ("pending", "due_now", "overdue", "upcoming")
        ]
        return {"items": remaining}


class JitterNextHabitDueSensor(_JitterBase):
    _attr_name = "Next habit due"
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_next_habit_due"

    @property
    def native_value(self) -> str:
        nxt = self._next()
        return nxt.get("title", "Nothing due") if nxt else "All done"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        nxt = self._next() or {}
        return {
            "slug": nxt.get("habit_slug"),
            "start": nxt.get("start"),
            "status": nxt.get("status"),
        }

    def _next(self) -> dict[str, Any] | None:
        candidates = [
            it for it in _habit_items(self.coordinator.data)
            if it.get("status") in ("due_now", "overdue", "pending", "upcoming")
        ]
        # Items already come back sorted by start in the today response;
        # take the first.
        return candidates[0] if candidates else None


class JitterActiveStreaksSensor(_JitterBase):
    _attr_name = "Active streaks"
    _attr_icon = "mdi:fire"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_active_streaks"

    @property
    def native_value(self) -> int:
        # Streak data isn't in `today` — it'd need habit_history per
        # habit.  Until we add a per-habit streak fetcher to the
        # coordinator, this is a placeholder count of habits flagged
        # as "in a streak" if/when that surfaces.  v1: count of done-today.
        return sum(1 for it in _habit_items(self.coordinator.data) if it.get("status") == "done")


# ── Per-habit sensor ────────────────────────────────────────────

class JitterHabitSensor(_JitterBase):
    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(
        self,
        coordinator: JitterCoordinator,
        entry: ConfigEntry,
        slug: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._slug = slug
        self._attr_name = f"Habit {slug}"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_habit_{self._slug}"

    @property
    def native_value(self) -> str:
        item = self._today_item()
        if item is None:
            return "idle"
        # today_item.status values: pending / due_now / overdue / done / skipped / snoozed / missed / upcoming
        return item.get("status") or "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        habit = self._habit_row() or {}
        item = self._today_item() or {}
        return {
            "name": habit.get("name"),
            "category": habit.get("category"),
            "duration_seconds": habit.get("duration_seconds"),
            "due_at": item.get("start"),
            "title": item.get("title"),
            "subtitle": item.get("subtitle"),
        }

    def _habit_row(self) -> dict[str, Any] | None:
        for h in self.coordinator.data.habits:
            if h.get("slug") == self._slug:
                return h
        return None

    def _today_item(self) -> dict[str, Any] | None:
        for it in _habit_items(self.coordinator.data):
            if it.get("habit_slug") == self._slug:
                return it
        return None


# ── Per-goal sensor ─────────────────────────────────────────────

class JitterGoalSensor(_JitterBase):
    _attr_icon = "mdi:target"

    def __init__(
        self,
        coordinator: JitterCoordinator,
        entry: ConfigEntry,
        slug: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._slug = slug
        self._attr_name = f"Goal {slug}"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_goal_{self._slug}"

    @property
    def native_value(self) -> str:
        progress = self._progress() or {}
        return progress.get("status") or "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        progress = self._progress() or {}
        goal = self._goal_row() or {}
        return {
            "name": goal.get("name"),
            "direction": goal.get("direction"),
            "target_value": progress.get("target_value"),
            "target_date": progress.get("target_date"),
            "current_value": progress.get("current_value"),
            "gap": progress.get("gap"),
            "days_remaining": progress.get("days_remaining"),
            "required_rate_per_day": progress.get("required_rate_per_day"),
            "actual_rate_per_day": progress.get("actual_rate_per_day"),
            "projection_at_target": progress.get("projection_at_target"),
            "catch_up_rate_per_day": progress.get("catch_up_rate_per_day"),
        }

    async def async_added_to_hass(self) -> None:
        """Pull per-goal progress when this entity is first added."""
        await super().async_added_to_hass()
        await self._refresh_progress()

    async def async_update(self) -> None:
        """Re-pull per-goal progress on each entity update tick.
        DataUpdateCoordinator handles the main poll; this hook fires
        when HA's entity registry pulls fresh state."""
        await self._refresh_progress()

    async def _refresh_progress(self) -> None:
        await self.coordinator.refresh_goal_progress(self._slug)

    def _goal_row(self) -> dict[str, Any] | None:
        for g in self.coordinator.data.goals:
            if g.get("slug") == self._slug:
                return g
        return None

    def _progress(self) -> dict[str, Any] | None:
        # GoalWithProgress comes back as {goal fields..., progress: {...}, series: [...]}
        payload = self.coordinator.data.goal_progress.get(self._slug)
        if not payload:
            return None
        return payload.get("progress") if isinstance(payload, dict) else None


# ── Helpers ─────────────────────────────────────────────────────

def _habit_items(data: JitterData) -> list[dict[str, Any]]:
    """Filter today's planned items down to just the habit kind —
    meetings sit in the same list."""
    return [it for it in data.today_items if it.get("kind") == "habit"]
