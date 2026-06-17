"""Jitter custom component — OAuth-authenticated MCP client + HA services.

The four registered services (`jitter.log_observation` /
`complete_habit` / `skip_habit` / `snooze_habit`) translate one-shot
HA service calls into jitter MCP `tools/call` invocations.  Auth is
OAuth via Authentik — the same SSO claude.ai uses, so revoking HA's
access is one click in the Authentik admin UI.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow
import homeassistant.helpers.config_validation as cv

from .api import JitterClient
from .const import (
    CONF_BASE_URL,
    DOMAIN,
    SERVICE_COMPLETE_HABIT,
    SERVICE_LOG_OBSERVATION,
    SERVICE_SKIP_HABIT,
    SERVICE_SNOOZE_HABIT,
)

_LOGGER = logging.getLogger(__name__)


# ── Service schemas ─────────────────────────────────────────────────

def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


LOG_OBSERVATION_SCHEMA = vol.Schema(
    {
        vol.Required("type"): cv.string,
        vol.Required("payload"): dict,
        vol.Optional("related_habit_slug"): cv.string,
        vol.Optional("recorded_at"): cv.datetime,
        vol.Optional("external_id"): cv.string,
        vol.Optional("window_start"): cv.datetime,
        vol.Optional("window_end"): cv.datetime,
    }
)

COMPLETE_HABIT_SCHEMA = vol.Schema(
    {
        vol.Required("slug"): cv.string,
        vol.Optional("responded_at"): cv.datetime,
        vol.Optional("observation"): dict,
    }
)

SKIP_HABIT_SCHEMA = vol.Schema({vol.Required("slug"): cv.string})

SNOOZE_HABIT_SCHEMA = vol.Schema(
    {
        vol.Required("slug"): cv.string,
        vol.Required("minutes"): vol.All(int, vol.Range(min=5, max=1440)),
    }
)


# ── Entry lifecycle ─────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Wire up the OAuth2-backed MCP client + register services."""
    # Resolve the OAuth implementation HA stored when the user added
    # the integration — that's how `OAuth2Session` knows which
    # client_id / secret / endpoints to use on refresh.
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, implementation
    )

    http = aiohttp_client.async_get_clientsession(hass)
    client = JitterClient(http, oauth_session, base_url=entry.data[CONF_BASE_URL])

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    # ── log_observation ────────────────────────────────────────────
    async def handle_log_observation(call: ServiceCall) -> None:
        try:
            await client.log_observation(
                kind=call.data["type"],
                payload=call.data["payload"],
                related_habit_slug=call.data.get("related_habit_slug"),
                recorded_at=_iso(call.data.get("recorded_at")),
                external_id=call.data.get("external_id"),
                window_start=_iso(call.data.get("window_start")),
                window_end=_iso(call.data.get("window_end")),
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("log_observation failed unexpectedly")
            raise HomeAssistantError(f"log_observation failed: {err}") from err

    # ── complete_habit ─────────────────────────────────────────────
    async def handle_complete_habit(call: ServiceCall) -> None:
        try:
            await client.complete_habit(
                slug=call.data["slug"],
                responded_at=_iso(call.data.get("responded_at")),
                observation=call.data.get("observation"),
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("complete_habit failed unexpectedly")
            raise HomeAssistantError(f"complete_habit failed: {err}") from err

    # ── skip_habit ─────────────────────────────────────────────────
    async def handle_skip_habit(call: ServiceCall) -> None:
        try:
            await client.skip_habit(slug=call.data["slug"])
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("skip_habit failed unexpectedly")
            raise HomeAssistantError(f"skip_habit failed: {err}") from err

    # ── snooze_habit ───────────────────────────────────────────────
    async def handle_snooze_habit(call: ServiceCall) -> None:
        try:
            await client.snooze_habit(
                slug=call.data["slug"],
                minutes=call.data["minutes"],
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("snooze_habit failed unexpectedly")
            raise HomeAssistantError(f"snooze_habit failed: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_OBSERVATION, handle_log_observation,
        schema=LOG_OBSERVATION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_HABIT, handle_complete_habit,
        schema=COMPLETE_HABIT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SKIP_HABIT, handle_skip_habit,
        schema=SKIP_HABIT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SNOOZE_HABIT, handle_snooze_habit,
        schema=SNOOZE_HABIT_SCHEMA,
    )

    _LOGGER.info(
        "Jitter integration set up — registered 4 services against %s",
        entry.data[CONF_BASE_URL],
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    for svc in (
        SERVICE_LOG_OBSERVATION,
        SERVICE_COMPLETE_HABIT,
        SERVICE_SKIP_HABIT,
        SERVICE_SNOOZE_HABIT,
    ):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
