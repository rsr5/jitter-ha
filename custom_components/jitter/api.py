"""MCP JSON-RPC client for jitter, authenticated via Authentik OAuth.

The integration talks to jitter's `/mcp` endpoint — the same surface
claude.ai uses — instead of the bearer-gated `/v1/*` REST.  Pros:

- Same auth as the rest of the household uses (Authentik SSO)
- No separate bearer to mint, manage, rotate
- Revoking access is one click in Authentik

Each HA service maps to a single MCP `tools/call` invocation.  Token
refresh is handled by HA's `OAuth2Session`; we just pull the current
access_token off the session for the Authorization header on each
request.
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session


class JitterTransientError(HomeAssistantError):
    """Retryable failure — network blip, 5xx, or a timeout.  The
    delivery queue (S50) catches these, backs off, and retries the
    same call.  Also propagates as HomeAssistantError so any direct
    synchronous caller (the read-side coordinator) still sees a
    conventional HA error surface."""


class JitterPermanentError(HomeAssistantError):
    """Non-retryable failure — 4xx (bad slug, malformed payload,
    revoked auth).  Retrying won't help; the queue logs and drops."""

from .const import (
    DEFAULT_RECORDED_VIA,
    DEFAULT_SOURCE,
    MCP_PATH,
    MCP_TOOL_COMPLETE_HABIT,
    MCP_TOOL_GET_GOAL,
    MCP_TOOL_LIST_GOALS,
    MCP_TOOL_LIST_HABITS,
    MCP_TOOL_LOG_JOURNAL,
    MCP_TOOL_RECORD_OBSERVATION,
    MCP_TOOL_SKIP_HABIT,
    MCP_TOOL_SNOOZE_HABIT,
    MCP_TOOL_TODAY,
    REQUEST_TIMEOUT_S,
)


class JitterClient:
    """OAuth-authenticated MCP client.  One instance per config entry."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        oauth_session: OAuth2Session,
        base_url: str,
    ) -> None:
        self._http = session
        self._oauth = oauth_session
        self._base = base_url.rstrip("/")

    # ── Connection-test ────────────────────────────────────────────

    async def check_connection(self) -> bool:
        """Cheap auth-validating ping — calls MCP `tools/list` which
        any properly-scoped token can hit.  Raises HomeAssistantError
        on auth failure or unreachable server.
        """
        return await self._rpc("tools/list", {}) is not None

    # ── Public surface — one method per HA service ─────────────────

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
        """Call MCP tool `record_observation`.

        Same dedup contract as the REST endpoint — pass a stable
        `external_id` and re-fires of the same automation collapse to
        one observation via the `(user_id, source, external_id)`
        unique key on the server.
        """
        args: dict[str, Any] = {
            "type": kind,
            "payload": payload,
            # Per-request source / recorded_via override on the server
            # so this lands as `sensor` / `ha` regardless of which
            # transport the MCP handler defaults to.
            "source": DEFAULT_SOURCE,
            "recorded_via": DEFAULT_RECORDED_VIA,
        }
        if related_habit_slug is not None:
            args["related_habit_slug"] = related_habit_slug
        if recorded_at is not None:
            args["recorded_at"] = recorded_at
        if external_id is not None:
            args["external_id"] = external_id
        if window_start is not None:
            args["window_start"] = window_start
        if window_end is not None:
            args["window_end"] = window_end
        return await self._call_tool(MCP_TOOL_RECORD_OBSERVATION, args)

    async def log_journal(
        self,
        *,
        text: str,
        actor: str,
        title: str | None = None,
        external_id: str | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        """Call MCP tool `log_journal`.

        Convenience wrapper around `record_observation` with
        `type: "journal"` pre-set and `actor` surfaced as a first-class
        field.  See `docs/observation-types.md` for the actor registry.
        """
        args: dict[str, Any] = {"text": text, "actor": actor}
        if title is not None:
            args["title"] = title
        if external_id is not None:
            args["external_id"] = external_id
        if recorded_at is not None:
            args["recorded_at"] = recorded_at
        return await self._call_tool(MCP_TOOL_LOG_JOURNAL, args)

    async def complete_habit(
        self,
        *,
        slug: str,
        responded_at: str | None = None,
        observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"slug": slug}
        if responded_at is not None:
            args["responded_at"] = responded_at
        if observation is not None:
            obs = dict(observation)
            obs.setdefault("source", DEFAULT_SOURCE)
            obs.setdefault("recorded_via", DEFAULT_RECORDED_VIA)
            args["observation"] = obs
        return await self._call_tool(MCP_TOOL_COMPLETE_HABIT, args)

    async def skip_habit(self, *, slug: str) -> dict[str, Any]:
        return await self._call_tool(MCP_TOOL_SKIP_HABIT, {"slug": slug})

    async def snooze_habit(self, *, slug: str, minutes: int) -> dict[str, Any]:
        return await self._call_tool(
            MCP_TOOL_SNOOZE_HABIT, {"slug": slug, "minutes": minutes}
        )

    # ── Read-side methods (S38 sensor platform) ────────────────────

    async def fetch_today(self) -> dict[str, Any]:
        """Return today's planned items (habits + meetings) with their
        outcomes.  Matches the MCP `today` tool's response shape."""
        return await self._call_tool(MCP_TOOL_TODAY, {})

    async def fetch_habits(self) -> list[dict[str, Any]]:
        """All habits (enabled + disabled).  Returned list mirrors the
        MCP `list_habits` tool's `habits` envelope."""
        result = await self._call_tool(MCP_TOOL_LIST_HABITS, {})
        return result.get("habits", []) if isinstance(result, dict) else []

    async def fetch_goals(self, status: str | None = None) -> list[dict[str, Any]]:
        """All goals matching the optional status filter."""
        args: dict[str, Any] = {}
        if status:
            args["status"] = status
        result = await self._call_tool(MCP_TOOL_LIST_GOALS, args)
        return result.get("goals", []) if isinstance(result, dict) else []

    async def fetch_goal_with_progress(self, slug: str) -> dict[str, Any]:
        """Goal + computed progress + chart-ready series.  This is the
        per-goal read the sensor platform exposes as the goal sensor's
        attributes."""
        return await self._call_tool(MCP_TOOL_GET_GOAL, {"slug": slug})

    # ── Internals ─────────────────────────────────────────────────

    async def _call_tool(
        self, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Wrap a JSON-RPC `tools/call` for the given tool name."""
        return await self._rpc(
            "tools/call",
            {"name": tool, "arguments": args},
        )

    async def _rpc(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """POST a JSON-RPC 2.0 envelope to /mcp, return the `result`
        (or raise on error)."""
        await self._oauth.async_ensure_token_valid()
        access_token = self._oauth.token["access_token"]
        envelope = {
            "jsonrpc": "2.0",
            "id": secrets.token_hex(8),
            "method": method,
            "params": params,
        }

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                resp = await self._http.post(
                    f"{self._base}{MCP_PATH}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=envelope,
                )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            # Transport-level failure (timeout, connection refused,
            # DNS blip) — the queue backs off and retries.
            raise JitterTransientError(f"Cannot reach jitter MCP: {err}") from err

        # 401 = token was rejected.  HA's OAuth session will refresh
        # on the next call; that's usually a transient (mid-refresh)
        # situation rather than a "you're locked out" one, so we retry.
        # Genuine "revoked in Authentik" cases still cycle a few times
        # before the user notices the queue metric climbing — an
        # acceptable trade for keeping brief token races from dropping
        # events.
        if resp.status == 401:
            raise JitterTransientError(
                "Jitter MCP rejected the OAuth token (HTTP 401)"
            )
        # 5xx = server had a problem; retry.
        if resp.status >= 500:
            body = await resp.text()
            raise JitterTransientError(
                f"Jitter MCP HTTP {resp.status}: {body[:200]}"
            )
        # 4xx (other than 401) = the *request* is invalid — bad slug,
        # malformed payload, unsupported tool.  No amount of retrying
        # fixes that.
        if resp.status >= 400:
            body = await resp.text()
            raise JitterPermanentError(
                f"Jitter MCP HTTP {resp.status}: {body[:200]}"
            )

        body = await resp.json()
        if "error" in body and body["error"] is not None:
            err = body["error"]
            code = err.get("code")
            message = err.get("message", "unknown")
            # JSON-RPC errors are application-level and typically
            # non-retryable (unknown tool, invalid params).  If a
            # future MCP flavour signals "temporary" via a specific
            # code, split it here.
            raise JitterPermanentError(
                f"Jitter MCP error {code}: {message}"
            )
        return body.get("result")
