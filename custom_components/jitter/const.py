"""Constants for the Jitter integration."""

DOMAIN = "jitter"

# Config-entry keys
CONF_BASE_URL = "base_url"

# Authentik (OAuth/OIDC issuer) — same provider as claude.ai uses for
# the MCP endpoint.  Hard-coded since this integration only ever talks
# to one Authentik tenant.  If you ever move OAuth providers, change
# both URLs here in lockstep.
OAUTH_AUTHORIZE_URL = "https://auth.ridlers.org/application/o/authorize/"
OAUTH_TOKEN_URL = "https://auth.ridlers.org/application/o/token/"

# OAuth scopes the integration requests.  `openid` and `offline_access`
# are mandatory for id_token + refresh_token; the rest are
# resource-scopes the MCP endpoint validates.  Adjust to match the
# Authentik application's allowed scopes.
OAUTH_SCOPES = ["openid", "offline_access"]

# Tag for every observation the HA integration writes — keeps the source
# taxonomy clean (vs the iOS / MCP / agent paths).  See the jitter server's
# `NewObservation` model.
DEFAULT_SOURCE = "sensor"
DEFAULT_RECORDED_VIA = "ha"

# Request timeout in seconds.  Short on purpose — automations should never
# stall waiting on jitter; HomeAssistantError fires instead and the calling
# automation moves on.
REQUEST_TIMEOUT_S = 10

# Service names
SERVICE_LOG_OBSERVATION = "log_observation"
SERVICE_COMPLETE_HABIT = "complete_habit"
SERVICE_SKIP_HABIT = "skip_habit"
SERVICE_SNOOZE_HABIT = "snooze_habit"

# MCP tool names — exact strings the jitter server registers in tools/list.
MCP_TOOL_RECORD_OBSERVATION = "record_observation"
MCP_TOOL_COMPLETE_HABIT = "complete_habit"
MCP_TOOL_SKIP_HABIT = "skip_habit"
MCP_TOOL_SNOOZE_HABIT = "snooze_habit"

# Where the MCP JSON-RPC endpoint lives, appended to the user's base URL.
MCP_PATH = "/mcp"
