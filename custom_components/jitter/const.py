"""Constants for the Jitter integration."""

DOMAIN = "jitter"

# Config-entry keys
CONF_BASE_URL = "base_url"
CONF_API_TOKEN = "api_token"

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
