# jitter-ha — Home Assistant custom integration

Exposes jitter as four HA services so automations, scripts, and
devices can write into jitter natively.  Authenticated via the same
Authentik SSO that gates the MCP endpoint for claude.ai — no
per-integration bearer to manage.

| Service | What |
|---|---|
| `jitter.log_observation` | Free-form observation (the workhorse — feed scale weights, walks, sleep snippets, anything) |
| `jitter.log_journal` | Free-text journal entry with a named actor (defaults to `user`).  See [observation-types.md](https://github.com/rsr5/nas-buildout/blob/main/jitter/docs/observation-types.md) for the actor registry. |
| `jitter.complete_habit` | Mark a habit done; optionally attach an observation in the same call |
| `jitter.skip_habit` | Skip today's instance of a habit |
| `jitter.snooze_habit` | Push the next nudge out 5–1440 min |

## Sensors (S38)

The integration also exposes jitter state as HA entities so dashboards
and automations can read habit + goal progress without going through
Claude.  Polled every 5 minutes (configurable via
`COORDINATOR_INTERVAL_SECONDS`).  All entities grouped under a single
"Jitter" device in HA's registry.

| Entity | State | Notes |
|---|---|---|
| `sensor.jitter_habits_done_today` | count | Attrs: list of done items |
| `sensor.jitter_habits_remaining_today` | count | Attrs: list with `start` + `status` |
| `sensor.jitter_next_habit_due` | habit name | Attrs: slug, start, status |
| `sensor.jitter_active_streaks` | count | (v1 = done-today count; real streak data when habit_history is in the coordinator) |
| `binary_sensor.jitter_today_complete` | on/off | On when every due habit reached a terminal status |
| `sensor.jitter_habit_<slug>` *(per habit)* | due / done / skipped / snoozed / … | Attrs: name, category, duration, due_at, title, subtitle |
| `sensor.jitter_goal_<slug>` *(per goal)* | on_track / ahead / behind / achieved / missed / unknown | Attrs: current, target, gap, days_remaining, projection, catch_up_rate, … |

Dynamic discovery: every habit and goal returned by jitter gets an
entity automatically.  New ones surface on the next coordinator tick.

## Architecture

```
                    ┌─────────────────┐
                    │  Jitter server  │
                    │   /v1/* + /mcp  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────┴────┐         ┌─────┴────┐         ┌────┴────┐
   │  iOS    │         │   MCP    │         │   HA    │
   │ (Swift) │         │ (Claude) │         │  (this) │
   │ S08/S19 │         │  /mcp    │         │  /v1/*  │
   └─────────┘         └──────────┘         └─────────┘
   source=               source=              source=
   healthkit             agent                sensor
```

Three doors, one backend.  Each tags its observations with a
distinct `source` so jitter's stats tools can filter by where a
reading came from.

## Install via HACS

⚠️ Currently lives inside the `nas-buildout` monorepo at
`integrations/jitter-ha/`.  HACS expects a repo where
`custom_components/<domain>/` sits at the **repo root**, so the
HACS-installable path is one of:

**Local development (recommended right now)** — symlink directly:

```bash
ln -s ~/code/nas-buildout/integrations/jitter-ha/custom_components/jitter \
      ~/.homeassistant/custom_components/jitter
# (or wherever your HA config lives — on southside it's typically
# /var/lib/hass/.homeassistant/custom_components/)
```

Then restart HA → **Settings → Devices & Services → Add Integration → Jitter**.

**HACS** — once we're ready to publish, extract this directory into
its own repo (it's already shaped correctly — `hacs.json` at root,
`custom_components/jitter/` underneath).  Then in HACS: **⋮ → Custom
repositories → add the repo URL → category Integration → install →
restart HA**.

## Setup

Three one-off steps, then standard HA OAuth.  Full walkthrough in
[`INSTALL.md`](./INSTALL.md); summary:

1. **Authentik** — create a new OAuth2 provider + application for
   "jitter-ha", get a `client_id` + `client_secret`.  Redirect URI
   must exactly match `https://<your-ha-external-url>/auth/external/callback`.
2. **Jitter** — extend `JITTER_OIDC_AUDIENCE` to a comma-separated
   list including HA's client_id, then rebuild the jitter Pi.
3. **HA Application Credentials** — paste the Authentik client_id +
   secret in **Settings → Devices & Services → Application
   Credentials → Add**, integration "Jitter".

Then **Add Integration → Jitter** triggers the Authorization Code
flow.  HA redirects to Authentik, you approve, redirects back, done.

## First automation — the scale → jitter pipe

```yaml
automation:
  - alias: "Log bodyweight to Jitter"
    trigger:
      - platform: state
        entity_id: sensor.smart_scale_p2_pro_weight    # settled, NOT real_time
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.state not in ['unknown', 'unavailable'] }}
    action:
      - service: jitter.log_observation
        data:
          type: bodyweight
          payload:
            weight_kg: "{{ trigger.to_state.state | float }}"
          external_id: "scale-{{ trigger.to_state.last_changed }}"
```

Two things worth noting:

1. **`external_id` is the dedup hook.**  Jitter has a unique key on
   `(user_id, source, external_id)` — re-firing the automation with
   the same `external_id` returns the existing observation rather
   than inserting a duplicate.  Anchoring on
   `trigger.to_state.last_changed` is a good cheap stable id.
2. **Trigger off the settled entity, not the real-time one.**
   Smart-scale integrations typically expose a `_real_time_weight`
   entity that updates while you're still standing on the scale —
   logging that would spray junk observations into jitter.  The
   non-`_real_time_` entity is what you want.

## Design notes (non-functional)

- **Async only.**  All I/O goes through `aiohttp_client.async_get_clientsession(hass)`
  — no blocking calls on the event loop.
- **Graceful failure.**  A jitter outage raises `HomeAssistantError`
  rather than crashing HA — the calling automation surfaces the
  failure, everything else carries on.
- **Short timeout.**  10 s per request.  Automations shouldn't stall
  waiting on jitter; the next trigger will get its own attempt.
- **Token management.**  OAuth access tokens + refresh tokens live
  in the config entry, managed by HA's `OAuth2Session`.  Refresh is
  automatic on expiry.  Revoke access by deleting the Application
  Credentials entry in HA, or by disabling the application in
  Authentik admin.
- **Idempotency is the caller's job.**  Pass a stable `external_id`
  on observations that may re-fire.  Without one, every retrigger
  creates a fresh row — fine for one-off events, bad for state-
  change triggers.

## Phase 2 (planned)

A `sensor.py` platform backed by a `DataUpdateCoordinator` polling
`/v1/today` + per-habit `/history` would expose read-side state:

- `sensor.jitter_today_due` — count of habits still pending today
- `sensor.jitter_<slug>_streak` — current streak per habit

That lets HA automations **react** to jitter state ("flash the office
light red if the dumbbell session is still undone by 14:00").  Out
of scope for the first ship.
