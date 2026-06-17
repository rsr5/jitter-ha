"""Thin async client for the jitter REST API.

One method per HA service.  Each returns the parsed JSON response on
success, or raises HomeAssistantError on failure.  Uses HA's shared
aiohttp client session — no third-party deps, no blocking I/O.

The HA integration's whole job is to be a faithful door from
automations into jitter's existing /v1/* surface.  Anything specific
to HA (the source/recorded_via taxonomy, the dedup convention) is
pushed through the same fields jitter already understands rather than
inventing parallel concepts.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_RECORDED_VIA,
    DEFAULT_SOURCE,
    REQUEST_TIMEOUT_S,
)


class JitterClient:
    """REST client for jitter.  One instance per config entry."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_token: str,
    ) -> None:
        # Strip trailing slashes so callers can pass either form.
        self._base = base_url.rstrip("/")
        self._session = session
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    # ── Connection-test ────────────────────────────────────────────

    async def check_connection(self) -> bool:
        """Cheap auth-validating GET.

        Hits `/v1/today` — auth-gated, lightweight, always returns 200
        when creds + server are healthy.  Raises HomeAssistantError on
        any non-2xx so config_flow can surface "cannot connect" /
        "invalid auth" distinctly.
        """
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                resp = await self._session.get(
                    f"{self._base}/v1/today",
                    headers=self._headers,
                )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(f"Cannot reach jitter: {err}") from err

        if resp.status == 401:
            raise HomeAssistantError("Invalid bearer token (HTTP 401)")
        if resp.status >= 400:
            body = await resp.text()
            raise HomeAssistantError(
                f"Jitter returned HTTP {resp.status}: {body[:200]}"
            )
        return True

    # ── Observations ──────────────────────────────────────────────

    async def log_observation(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        related_habit_slug: str | None = None,
        recorded_at: str | None = None,
        external_id: str | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/observations.

        Tags the observation `source="sensor"` / `recorded_via="ha"`
        via the per-request override added to NewObservation — keeps
        the HA-driven readings cleanly separated from iOS HealthKit
        observations in `observation_stats` queries.

        Pass a stable `external_id` for idempotency: re-fires of the
        same automation (HA restarts, retriggers) won't duplicate the
        observation — jitter's `(user_id, source, external_id)` unique
        key returns the existing row instead.
        """
        body: dict[str, Any] = {
            "type": kind,
            "payload": payload,
            "source": DEFAULT_SOURCE,
            "recorded_via": DEFAULT_RECORDED_VIA,
        }
        if related_habit_slug is not None:
            body["related_habit_slug"] = related_habit_slug
        if recorded_at is not None:
            body["recorded_at"] = recorded_at
        if external_id is not None:
            body["external_id"] = external_id
        if window_start is not None:
            body["window_start"] = window_start
        if window_end is not None:
            body["window_end"] = window_end

        return await self._post("/v1/observations", body)

    # ── Habit actions ────────────────────────────────────────────

    async def complete_habit(
        self,
        *,
        slug: str,
        responded_at: str | None = None,
        observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/habits/:slug/complete.  Optionally attach an
        observation in the same call (jitter merges the two).
        """
        body: dict[str, Any] = {}
        if responded_at is not None:
            body["responded_at"] = responded_at
        if observation is not None:
            # Tag this nested observation the same way as standalone ones.
            obs = dict(observation)
            obs.setdefault("source", DEFAULT_SOURCE)
            obs.setdefault("recorded_via", DEFAULT_RECORDED_VIA)
            body["observation"] = obs
        return await self._post(f"/v1/habits/{slug}/complete", body)

    async def skip_habit(self, *, slug: str) -> dict[str, Any]:
        """POST /v1/habits/:slug/skip."""
        return await self._post(f"/v1/habits/{slug}/skip", {})

    async def snooze_habit(self, *, slug: str, minutes: int) -> dict[str, Any]:
        """POST /v1/habits/:slug/snooze."""
        return await self._post(
            f"/v1/habits/{slug}/snooze", {"minutes": minutes}
        )

    # ── Internal ──────────────────────────────────────────────────

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST helper.

        Raises HomeAssistantError on any non-2xx so the calling
        service registration surfaces the failure cleanly to the
        automation that triggered it (rather than crashing HA).
        """
        url = f"{self._base}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                resp = await self._session.post(
                    url, headers=self._headers, json=body
                )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(f"jitter POST {path} failed: {err}") from err

        if resp.status >= 400:
            body_text = await resp.text()
            raise HomeAssistantError(
                f"jitter POST {path} returned HTTP {resp.status}: {body_text[:200]}"
            )
        try:
            return await resp.json()
        except aiohttp.ContentTypeError:
            return {}
