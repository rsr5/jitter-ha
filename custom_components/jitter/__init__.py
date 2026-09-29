"""Jitter custom component — OAuth-authenticated MCP client + HA services.

The five registered services (`jitter.log_observation` /
`log_journal` / `complete_habit` / `skip_habit` / `snooze_habit`)
translate one-shot HA service calls into jitter MCP `tools/call`
invocations.  Auth is OAuth via Authentik — the same SSO claude.ai
uses, so revoking HA's access is one click in the Authentik admin UI.
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

from homeassistant.const import Platform

from .api import JitterClient
from .const import (
    CONF_CF_ACCESS_CLIENT_ID,
    CONF_CF_ACCESS_CLIENT_SECRET,
    CONF_BASE_URL,
    DEFAULT_JOURNAL_ACTOR,
    DOMAIN,
    SERVICE_COMPLETE_HABIT,
    SERVICE_LOG_JOURNAL,
    SERVICE_LOG_OBSERVATION,
    SERVICE_SKIP_HABIT,
    SERVICE_SNOOZE_HABIT,
)
from .coordinator import JitterCoordinator
from .delivery import JitterDeliveryQueue

# S38 — read-side platforms.  Each owns its entity classes; the
# coordinator is shared.  Platform forwarding happens at end of
# async_setup_entry.
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

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

LOG_JOURNAL_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        # Default to "user" — most HA automations represent user
        # intent (the user wrote them).  Advanced automations can
        # override with their own kebab-case slug; see the actor
        # registry in jitter's docs/observation-types.md.
        vol.Optional("actor", default=DEFAULT_JOURNAL_ACTOR): cv.string,
        vol.Optional("title"): cv.string,
        vol.Optional("external_id"): cv.string,
        vol.Optional("recorded_at"): cv.datetime,
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
    client = JitterClient(
        http,
        oauth_session,
        base_url=entry.data[CONF_BASE_URL],
        cf_access_client_id=entry.options.get(CONF_CF_ACCESS_CLIENT_ID, ""),
        cf_access_client_secret=entry.options.get(CONF_CF_ACCESS_CLIENT_SECRET, ""),
    )
    # A new Cloudflare Access pair entered under Configure takes effect
    # by reloading the entry (forge #608).
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))

    # S38 — read-side coordinator + entity platforms.  The coordinator
    # polls jitter's MCP at COORDINATOR_INTERVAL and feeds the sensor
    # + binary_sensor platforms.  First refresh blocks setup so the
    # platforms have data when they're forwarded — otherwise dynamic
    # discovery (one entity per habit/goal) sees an empty list and
    # nothing surfaces until the next tick.
    coordinator = JitterCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    # S50 — retry-until-delivered queue for the five write-side
    # service handlers below.  Read-side (sensor coordinator) still
    # calls the client directly since its natural retry is the next
    # poll interval; only the fire-side needs the queue.
    delivery = JitterDeliveryQueue(client)
    delivery.start()

    # `hass.data` bucket grew a layer: keep the existing `client`
    # at the entry_id (for the four service handlers below) but
    # park the coordinator alongside under a dict so the platform
    # files can find it.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "delivery": delivery,
    }

    # S50 — each write handler enqueues to the delivery queue and
    # returns immediately.  The queue worker retries transient
    # failures until the call lands (or a permanent 4xx drops it).
    # Automations calling these services always see success — they
    # can't and shouldn't be blocked on jitter's availability.

    # ── log_observation ────────────────────────────────────────────
    async def handle_log_observation(call: ServiceCall) -> None:
        delivery.enqueue(
            "log_observation",
            kind=call.data["type"],
            payload=call.data["payload"],
            related_habit_slug=call.data.get("related_habit_slug"),
            recorded_at=_iso(call.data.get("recorded_at")),
            external_id=call.data.get("external_id"),
            window_start=_iso(call.data.get("window_start")),
            window_end=_iso(call.data.get("window_end")),
        )

    # ── log_journal ────────────────────────────────────────────────
    async def handle_log_journal(call: ServiceCall) -> None:
        delivery.enqueue(
            "log_journal",
            text=call.data["text"],
            actor=call.data["actor"],
            title=call.data.get("title"),
            external_id=call.data.get("external_id"),
            recorded_at=_iso(call.data.get("recorded_at")),
        )

    # ── complete_habit ─────────────────────────────────────────────
    async def handle_complete_habit(call: ServiceCall) -> None:
        delivery.enqueue(
            "complete_habit",
            slug=call.data["slug"],
            responded_at=_iso(call.data.get("responded_at")),
            observation=call.data.get("observation"),
        )

    # ── skip_habit ─────────────────────────────────────────────────
    async def handle_skip_habit(call: ServiceCall) -> None:
        delivery.enqueue("skip_habit", slug=call.data["slug"])

    # ── snooze_habit ───────────────────────────────────────────────
    async def handle_snooze_habit(call: ServiceCall) -> None:
        delivery.enqueue(
            "snooze_habit",
            slug=call.data["slug"],
            minutes=call.data["minutes"],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_OBSERVATION, handle_log_observation,
        schema=LOG_OBSERVATION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_JOURNAL, handle_log_journal,
        schema=LOG_JOURNAL_SCHEMA,
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

    # S38 — forward to sensor + binary_sensor platforms so HA
    # constructs the read-side entities.  Must happen after the
    # coordinator + hass.data bucket are in place (platforms read
    # from it).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Jitter integration set up — 5 services + %d entity platforms against %s",
        len(PLATFORMS),
        entry.data[CONF_BASE_URL],
    )
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # S38 — drop the read-side platforms first; they hold references
    # to the coordinator that we're about to pop from hass.data.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    for svc in (
        SERVICE_LOG_OBSERVATION,
        SERVICE_LOG_JOURNAL,
        SERVICE_COMPLETE_HABIT,
        SERVICE_SKIP_HABIT,
        SERVICE_SNOOZE_HABIT,
    ):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)

    # S50 — stop the drain worker.  Pending events in the queue are
    # dropped by design (blocking HA shutdown on jitter round-trips
    # would stall the whole reload).
    bucket = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if bucket is not None:
        delivery = bucket.get("delivery")
        if delivery is not None:
            await delivery.stop()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
