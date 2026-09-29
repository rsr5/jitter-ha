"""OAuth Authorization Code config flow for the Jitter integration.

Inherits HA's `AbstractOAuth2FlowHandler` — that drives the full
PKCE Authorization Code dance against Authentik using credentials the
user previously entered into HA's Application Credentials.

After Authentik redirects back with a `code`, HA exchanges it for an
access_token + refresh_token, hands us the resulting OAuth2Session,
and `async_oauth_create_entry` runs.  We persist the user-supplied
`base_url` alongside the token; the OAuth2Session takes care of
refresh from then on.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    CONF_BASE_URL,
    CONF_CF_ACCESS_CLIENT_ID,
    CONF_CF_ACCESS_CLIENT_SECRET,
    DOMAIN,
    OAUTH_SCOPES,
)

_LOGGER = logging.getLogger(__name__)


class JitterOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """OAuth flow handler — gates the integration on Authentik SSO."""

    DOMAIN = DOMAIN
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return JitterOptionsFlow()

    # The wizard order: the user first picks (or enters) the base URL +
    # OAuth implementation, then HA hands them off to Authentik.
    _base_url: str | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra params on the Authentik /authorize redirect.

        Authentik expects scopes space-separated on the `scope` query
        parameter — HA assembles this from the dict we return.
        """
        return {"scope": " ".join(OAUTH_SCOPES)}

    # ── Steps ──────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect the base URL first, then delegate to OAuth pick-impl."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_BASE_URL,
                            default="https://jitter.ridlers.org",
                        ): str,
                    }
                ),
            )

        # Stash for `async_oauth_create_entry` to read after the OAuth
        # round-trip finishes.
        self._base_url = user_input[CONF_BASE_URL].rstrip("/")
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Persist the base URL + OAuth tokens into a new config entry.

        HA stuffs the token dict under data["token"] for us; we just
        add the base_url alongside.
        """
        return self.async_create_entry(
            title="Jitter",
            data={
                **data,
                CONF_BASE_URL: self._base_url,
            },
        )


class JitterOptionsFlow(config_entries.OptionsFlow):
    """Configure → the Cloudflare Access service token (forge #608).

    The pair is the `jitter-ha` service token from Zero Trust → Access →
    Service credentials.  Saving reloads the entry, so the next /mcp call
    carries the new headers.  Leave both empty while the host has no
    `deny-all` Access application.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_CF_ACCESS_CLIENT_ID: user_input.get(
                        CONF_CF_ACCESS_CLIENT_ID, ""
                    ).strip(),
                    CONF_CF_ACCESS_CLIENT_SECRET: user_input.get(
                        CONF_CF_ACCESS_CLIENT_SECRET, ""
                    ).strip(),
                }
            )

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CF_ACCESS_CLIENT_ID,
                        default=current.get(CONF_CF_ACCESS_CLIENT_ID, ""),
                    ): str,
                    vol.Optional(
                        CONF_CF_ACCESS_CLIENT_SECRET,
                        default=current.get(CONF_CF_ACCESS_CLIENT_SECRET, ""),
                    ): str,
                }
            ),
        )
