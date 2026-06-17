"""Config flow for the Jitter integration.

Collects (base_url, api_token) and validates with a cheap GET /v1/today
before persisting.  Single config entry — one jitter instance per HA.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from .api import JitterClient
from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default="https://jitter.ridlers.org"): str,
        vol.Required(CONF_API_TOKEN): str,
    }
)


class JitterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial user-driven setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        # Single-instance — abort if already configured.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            session = aiohttp_client.async_get_clientsession(self.hass)
            client = JitterClient(
                session,
                base_url=user_input[CONF_BASE_URL],
                api_token=user_input[CONF_API_TOKEN],
            )
            try:
                await client.check_connection()
            except HomeAssistantError as err:
                msg = str(err).lower()
                if "401" in msg or "invalid bearer" in msg:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title="Jitter",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-prompt for credentials so the bearer token can be rotated
        without removing + re-adding the integration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            session = aiohttp_client.async_get_clientsession(self.hass)
            client = JitterClient(
                session,
                base_url=user_input[CONF_BASE_URL],
                api_token=user_input[CONF_API_TOKEN],
            )
            try:
                await client.check_connection()
            except HomeAssistantError as err:
                msg = str(err).lower()
                errors["base"] = (
                    "invalid_auth"
                    if "401" in msg or "invalid bearer" in msg
                    else "cannot_connect"
                )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_URL,
                        default=entry.data.get(CONF_BASE_URL, ""),
                    ): str,
                    vol.Required(CONF_API_TOKEN): str,
                }
            ),
            errors=errors,
        )
