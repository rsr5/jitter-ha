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
SERVICE_LOG_JOURNAL = "log_journal"
SERVICE_COMPLETE_HABIT = "complete_habit"
SERVICE_SKIP_HABIT = "skip_habit"
SERVICE_SNOOZE_HABIT = "snooze_habit"

# MCP tool names — exact strings the jitter server registers in tools/list.
MCP_TOOL_RECORD_OBSERVATION = "record_observation"
MCP_TOOL_LOG_JOURNAL = "log_journal"
MCP_TOOL_COMPLETE_HABIT = "complete_habit"
MCP_TOOL_SKIP_HABIT = "skip_habit"
MCP_TOOL_SNOOZE_HABIT = "snooze_habit"

# Read-side MCP tools used by the S38 sensor platform.  Polled by the
# DataUpdateCoordinator at COORDINATOR_INTERVAL.
MCP_TOOL_TODAY = "today"
MCP_TOOL_LIST_HABITS = "list_habits"
MCP_TOOL_LIST_GOALS = "list_goals"
MCP_TOOL_GET_GOAL = "get_goal"

# Polling cadence for the read-side coordinator.  5 min is the
# habits-data sweet spot — automation triggers still feel responsive,
# but we're not hammering the server (~288 calls/day).  If "next habit
# due" automations turn out to need faster reaction, drop to 60s.
COORDINATOR_INTERVAL_SECONDS = 300

# Default actor for journal entries posted by HA automations.  Most
# automations represent user intent — the user wrote the automation,
# so its journal entries are "user voice".  Automations doing
# something more like AI inference (e.g. an HA-side ML model writing
# observations) can override via the service's `actor` field.
DEFAULT_JOURNAL_ACTOR = "user"

# Where the MCP JSON-RPC endpoint lives, appended to the user's base URL.
MCP_PATH = "/mcp"
