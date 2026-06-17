"""OAuth authorization-server config for the Jitter integration.

Registered with HA's `application_credentials` platform — that's the
mechanism HA uses to collect a `client_id` + `client_secret` from the
user once (stored encrypted in the config dir), so individual config
entries don't need to re-prompt for them.

Authentik hosts the OAuth dance.  The user creates a new OAuth2
provider + application in Authentik for "jitter-ha", gets a
client_id + client_secret, and types them into HA's Application
Credentials UI.  Thereafter, adding the integration triggers a
standard Authorization Code + PKCE flow through Authentik.
"""
from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the Authentik OAuth2 endpoints jitter-ha authenticates against."""
    return AuthorizationServer(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
    )
