"""S38 — DataUpdateCoordinator for the read-side jitter→HA bridge.

Polls jitter's MCP every `COORDINATOR_INTERVAL_SECONDS` for the
data the sensor + binary_sensor platforms expose as entities.  One
coordinator per config entry; all entities share the same fetched
payload so we don't make N separate calls per tick.

Fetches in parallel: today + habits + goals.  Per-goal detail
(get_goal with computed progress) is fetched on-demand from the
entity's own `async_added_to_hass` since the data shape there is
heavier and only a few goals are usually active.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import JitterClient
from .const import COORDINATOR_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class JitterData:
    """One coordinator-tick's payload — what every read-side entity
    consumes.  Goal *progress* (the engine-computed numbers) lives in a
    separate dict keyed by slug; populated incrementally as per-goal
    entities request it."""

    today_items: list[dict[str, Any]] = field(default_factory=list)
    habits: list[dict[str, Any]] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)
    goal_progress: dict[str, dict[str, Any]] = field(default_factory=dict)


class JitterCoordinator(DataUpdateCoordinator[JitterData]):
    """Single source of truth for read-side entities."""

    def __init__(self, hass: HomeAssistant, client: JitterClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=COORDINATOR_INTERVAL_SECONDS),
        )
        self._client = client

    @property
    def client(self) -> JitterClient:
        """Exposed so per-goal entities can fetch fresh progress
        without going through the global tick."""
        return self._client

    async def _async_update_data(self) -> JitterData:
        """Concurrent fetch of the three read-side aggregates.  Any
        single fetch failure raises UpdateFailed — HA shows the
        coordinator as unavailable and retries on the next tick."""
        try:
            today, habits, goals = await asyncio.gather(
                self._client.fetch_today(),
                self._client.fetch_habits(),
                self._client.fetch_goals(),
            )
        except Exception as err:
            raise UpdateFailed(f"jitter MCP fetch failed: {err}") from err

        # Preserve the goal_progress dict from the previous tick so
        # per-goal entities don't lose their cached values during a
        # coordinator refresh — they'll repopulate on next read.
        prev_progress: dict[str, dict[str, Any]] = {}
        if self.data is not None:
            prev_progress = self.data.goal_progress

        today_items = today.get("items", []) if isinstance(today, dict) else []
        return JitterData(
            today_items=today_items,
            habits=habits,
            goals=goals,
            goal_progress=prev_progress,
        )

    async def refresh_goal_progress(self, slug: str) -> dict[str, Any] | None:
        """Pull per-goal computed progress on demand (called from the
        per-goal sensor's update path).  Result is cached on the
        coordinator's data for the entity to read on subsequent ticks
        without re-fetching."""
        try:
            payload = await self._client.fetch_goal_with_progress(slug)
        except Exception as err:
            _LOGGER.warning("refresh_goal_progress(%s) failed: %s", slug, err)
            return None
        if self.data is not None:
            self.data.goal_progress[slug] = payload
        return payload
