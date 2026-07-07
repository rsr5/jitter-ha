"""In-memory retry-until-delivered queue for jitter write-side calls.

## Why

The five write-side service handlers (`log_observation`, `log_journal`,
`complete_habit`, `skip_habit`, `snooze_habit`) used to await the MCP
client directly and raise `HomeAssistantError` on failure.  In practice
that meant every jitter outage or transient Wi-Fi blip silently dropped
a habit completion — the toothbrush would trigger the service, the
service would fail, HA would log a warning, and the completion never
made it into jitter.

## What

A small `asyncio.Queue` fed by the service handlers and drained by a
single background task.  The drain worker:

- **Delivers on 2xx.**  Log INFO, move on.
- **Drops on 4xx** (other than 401): the request is permanently
  invalid, retrying won't help.  Log WARN with the payload preview
  so a bad automation is diagnosable.
- **Retries transient failures forever** — network timeouts, 5xx,
  and 401s (usually a mid-refresh OAuth race) — with capped
  exponential backoff (1s → 2s → ... → 300s cap).  The same call
  is popped, retried, and only removed from the queue on success or
  permanent failure.

Server-side idempotency (S12: `record_outcome_for_habit` short-circuits
same-local-day + same-habit + same-state) means retrying the same call
100 times is a safe no-op — so this is safe even if a 5xx came AFTER
the server had actually processed the write.

## Persistence

**In-memory only.**  If HA restarts while the queue has pending calls,
they're lost.  Acceptable at the household scale we're targeting (a
handful of write-side events per day); persistence-across-restarts is
a follow-up if we ever outgrow this.

## Read-side

The read-side coordinator (`fetch_today` / `fetch_habits` / etc.) still
uses the client directly.  A sensor coordinator fails to refresh, the
next tick tries again; there's nothing to queue.  Only the fire-side
service handlers go through this queue.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .api import JitterClient, JitterPermanentError, JitterTransientError

_LOGGER = logging.getLogger(__name__)

# Bounded so a runaway automation can't OOM HA.  128 events is roughly
# a full day of aggressive activity; if the queue ever fills, the
# overflow event is dropped with a warn.
QUEUE_CAP = 128

# 1s → 2s → 4s → 8s → 16s → 30s → 60s → 120s → 300s, then flat at
# 300s.  Roughly matches how HA's own retry policies look elsewhere.
BACKOFF_LADDER_SECS: tuple[int, ...] = (1, 2, 4, 8, 16, 30, 60, 120, 300)


@dataclass
class Call:
    """One queued call.  `method` is the name of a JitterClient
    method to call (`"complete_habit"`, `"log_journal"`, …); `kwargs`
    is the kwargs dict passed to it.
    """

    method: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class JitterDeliveryQueue:
    """One instance per config entry.  Owns the asyncio.Queue and
    the drain task; both live for the lifetime of the entry."""

    def __init__(self, client: JitterClient) -> None:
        self._client = client
        self._queue: asyncio.Queue[Call] = asyncio.Queue(maxsize=QUEUE_CAP)
        self._task: asyncio.Task[None] | None = None
        self._delivered = 0
        self._dropped_permanent = 0
        self._dropped_overflow = 0

    def start(self) -> None:
        """Spawn the drain worker.  Idempotent — subsequent calls
        are no-ops (safe under HA's reload cycles)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._drain(), name="jitter-delivery")
        _LOGGER.info("jitter delivery queue online (in-memory, cap %d)", QUEUE_CAP)

    async def stop(self) -> None:
        """Cancel the drain worker.  Any events still in the queue
        are dropped — deliberate: HA unload means the config entry is
        going away, and blocking shutdown on jitter round-trips would
        stall the whole HA restart cycle."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    def enqueue(self, method: str, **kwargs: Any) -> None:
        """Non-blocking put.  Drops with a warn if the queue is full.
        Returns immediately — the calling service handler is free to
        return success to the HA automation."""
        try:
            self._queue.put_nowait(Call(method=method, kwargs=kwargs))
        except asyncio.QueueFull:
            self._dropped_overflow += 1
            _LOGGER.warning(
                "jitter delivery queue full (cap %d); dropping %s call",
                QUEUE_CAP,
                method,
            )

    @property
    def pending(self) -> int:
        """Number of events currently waiting to be delivered.  Cheap
        to poll; useful for a future diagnostic sensor."""
        return self._queue.qsize()

    # ── Internals ──────────────────────────────────────────────────

    async def _drain(self) -> None:
        while True:
            call = await self._queue.get()
            try:
                await self._deliver_forever(call)
            finally:
                self._queue.task_done()

    async def _deliver_forever(self, call: Call) -> None:
        """Retry `call` forever on transient failures, drop on permanent
        failures, return on success."""
        attempt = 0
        while True:
            try:
                await self._invoke(call)
                self._delivered += 1
                _LOGGER.info(
                    "jitter: delivered %s (queue pending=%d)",
                    call.method,
                    self.pending,
                )
                return
            except JitterPermanentError as err:
                self._dropped_permanent += 1
                _LOGGER.warning(
                    "jitter: permanent failure on %s; dropping (%s)",
                    call.method,
                    err,
                )
                return
            except JitterTransientError as err:
                secs = BACKOFF_LADDER_SECS[
                    min(attempt, len(BACKOFF_LADDER_SECS) - 1)
                ]
                _LOGGER.debug(
                    "jitter: transient failure on %s (attempt %d); sleeping %ds (%s)",
                    call.method,
                    attempt,
                    secs,
                    err,
                )
                await asyncio.sleep(secs)
                attempt += 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                # Belt-and-braces: an unexpected exception should not
                # kill the drain worker (or leave the call spinning
                # forever).  Log at ERROR and drop.
                _LOGGER.exception(
                    "jitter: unexpected error delivering %s: %s",
                    call.method,
                    err,
                )
                return

    async def _invoke(self, call: Call) -> None:
        method = getattr(self._client, call.method, None)
        if method is None or not callable(method):
            # A typo in the enqueue site is a permanent bug — surface
            # loudly and drop.
            raise JitterPermanentError(
                f"JitterClient has no callable method '{call.method}'"
            )
        await method(**call.kwargs)
