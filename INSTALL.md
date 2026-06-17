# jitter-ha — full setup walkthrough

End-to-end instructions to wire the jitter HA integration into your
Home Assistant install, generate credentials, and validate with the
first automation.

## Prerequisites you already have

- ✅ Jitter server running at `https://jitter.ridlers.org` (jitter Pi on VLAN 30)
- ✅ Server change B deployed (per-request `source`/`recorded_via` on `/v1/observations`)
- ✅ Dedicated HA bearer token added to `JITTER_BEARER_TOKENS`:
  ```
  <YOUR_HA_BEARER_TOKEN>
  ```
- ✅ Integration source at `~/code/nas-buildout/integrations/jitter-ha/`

## Step 1 — find HA's `config/` dir

The install path depends on **how HA is running**.  Pick the row that matches yours:

| HA flavour | `config/` location | Shell access? |
|---|---|---|
| **Home Assistant OS** (single-purpose Pi image) | inside the OS, exposed only via add-ons or the UI | no shell — install via HACS UI, not symlink |
| **HA Supervised** | `/usr/share/hassio/homeassistant/` (Debian) | root |
| **HA Container** (docker) | wherever you bind-mounted it, e.g. `/var/lib/homeassistant/` | yes |
| **HA Core** (Python venv) | `~/.homeassistant/` of the `homeassistant` user | yes |

Quick check from the **HA web UI**: **Settings → System → Repairs → ⋮ (top-right) → System Information** — the "Home Assistant Core" row shows the supervisor/OS version if applicable; the "OS Agent" / "Installation type" rows tell you the flavour.

## Step 2A — drop-install via symlink (HA Container / Supervised / Core)

If you have shell access on the HA host, this is the fastest install path.  The integration repo lives on your Mac at `~/code/nas-buildout`; you'll either need to **rsync it to the HA host first**, or store the repo on a path the HA host can reach.

Two approaches:

### Approach 1 — rsync the integration to the HA host

From your Mac:

```bash
rsync -av --delete \
  ~/code/nas-buildout/integrations/jitter-ha/custom_components/jitter/ \
  <ha-user>@192.168.1.50:/path/to/ha/config/custom_components/jitter/
```

Then on the HA host:

```bash
ls /path/to/ha/config/custom_components/jitter/
# Should show: __init__.py, manifest.json, api.py, config_flow.py,
#              const.py, services.yaml, strings.json, translations/
```

### Approach 2 — symlink if you have the repo on the HA host

```bash
ssh <ha-user>@192.168.1.50
git clone <repo-url> ~/nas-buildout    # or rsync from your Mac
sudo ln -sf ~/nas-buildout/integrations/jitter-ha/custom_components/jitter \
            /path/to/ha/config/custom_components/jitter
```

### Restart HA

| HA flavour | Restart command |
|---|---|
| HA OS / Supervised | UI: **Developer Tools → ⋮ → Restart**, or `ha core restart` |
| HA Container | `docker restart <ha-container>` (e.g. `homeassistant`) |
| HA Core | `sudo systemctl restart home-assistant` (unit name varies) |

## Step 2B — install via HACS UI (HA OS, no shell access)

HA OS doesn't give you shell access to drop files directly.  Use **HACS custom repository**:

### Prerequisite: HACS must already be installed

If HACS isn't installed yet, follow the official HACS install steps (one-time SSH add-on, then UI install).  It's a one-evening thing.

### Once HACS is installed

1. **Carve the integration into its own repo.**  HACS expects the integration files at the **repo root** as `custom_components/jitter/`.  Currently they live inside `nas-buildout/integrations/jitter-ha/`.  Two options:

   - **Option A** — push the `nas-buildout` repo to GitHub and add HACS with the **path** option set:
     ```
     Repository URL: github.com/rsr5/nas-buildout
     Category: Integration
     ```
     HACS doesn't natively support sub-directory custom integrations, so this approach won't work directly. You'd need Option B.

   - **Option B** (recommended) — extract to its own repo:
     ```bash
     mkdir ~/code/jitter-ha
     cp -r ~/code/nas-buildout/integrations/jitter-ha/* ~/code/jitter-ha/
     cd ~/code/jitter-ha
     git init && git add . && git commit -m "Initial extract from nas-buildout"
     gh repo create rsr5/jitter-ha --private --source . --push
     ```
     The extracted repo's root has `hacs.json` + `custom_components/jitter/` — exactly the HACS layout.

2. **HACS → Integrations → ⋮ (top right) → Custom repositories.**  Paste `https://github.com/rsr5/jitter-ha` (or whatever URL), set category **Integration**, **Add**.

3. HACS lists the new repo.  Click **Download** on Jitter.  Restart HA.

For local iteration with HACS: bump `version` in `manifest.json`, commit, push.  HACS sees the new release/commit and offers an update.

## Step 3 — add the integration in HA

After restart:

1. **Settings → Devices & Services → Add Integration**.
2. Search **Jitter**.  If it doesn't appear, the custom_components install didn't work — see **Troubleshooting** below.
3. Fill in:
   - **Base URL**: `https://jitter.ridlers.org`
   - **API bearer token**: `<YOUR_HA_BEARER_TOKEN>`
4. **Submit**.  The integration does a `GET /v1/today` to validate; clear errors on 401 vs network failure.

If it succeeds: green "Jitter" tile appears under Devices & Services.

## Step 4 — smoke-test from Developer Tools

**Developer Tools → Services**.  Pick `jitter.log_observation`.  Enter:

```yaml
type: setup-test
payload:
  source: ha-developer-tools
  ok: true
external_id: ha-setup-test-{{ now().isoformat() }}
```

Click **Call Service**.  Should complete without errors.

Verify from your Mac:

```bash
# Get today's observations
curl -sS -H 'Authorization: Bearer <YOUR_HA_BEARER_TOKEN>' \
  'https://jitter.ridlers.org/v1/observations?type=setup-test' | python3 -m json.tool
```

If the entry's there with `source: "sensor"` and `recorded_via: "ha"` — full chain works.

## Step 5 — first real automation (scale → bodyweight)

In HA: **Settings → Automations & Scenes → Create Automation → Start with an empty automation**.  Switch to **Edit in YAML**:

```yaml
alias: Log bodyweight to Jitter
description: ""
mode: single
trigger:
  - platform: state
    entity_id: sensor.smart_scale_p2_pro_weight   # the SETTLED entity, not _real_time
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

**Why `_real_time_weight` is wrong**: smart scales typically expose two entities — `_weight` (final, settled, fires once per weigh-in) and `_real_time_weight` (jitters mid-weigh-in as you step on, breathe, shift weight).  Triggering off real-time would spray dozens of observations per weigh-in.

**Why the `external_id` shape**: `last_changed` of the settled state is unique per weigh-in.  Same automation re-fires from HA restarts or recovery won't duplicate the observation — jitter's `(user_id, source, external_id)` unique key returns the existing row instead.

Save the automation.  Step on the scale.  Within a few seconds:

```bash
curl -sS -H 'Authorization: Bearer ...' \
  'https://jitter.ridlers.org/v1/observations?type=bodyweight&window_days=1' | python3 -m json.tool
```

Should show your latest reading.

## Troubleshooting

### "Integration not found" in the Add Integration list

Likely the custom_components/jitter dir isn't where HA expects:

- HA logs (Settings → System → Logs) will show `Unable to find component jitter` or similar.
- Confirm via shell: `ls <config>/custom_components/jitter/__init__.py` exists.
- Restart HA *fully* — a config reload doesn't pick up new integrations.

### "Cannot connect" in the config flow

- HA host can reach `https://jitter.ridlers.org`?  From the HA shell:
  ```bash
  curl -sS https://jitter.ridlers.org/v1/today  # should 401, not network error
  ```
- DNS resolution working?  HA OS uses its own resolver; check **Settings → System → Network**.

### "Invalid auth" in the config flow

- Double-check no trailing whitespace in the token (copy from `/tmp/ha-jitter-bearer.txt`, not the inline value in this doc — your shell may have wrapped).
- Verify the token's actually in `JITTER_BEARER_TOKENS` on the Pi:
  ```bash
  ssh roridler@192.168.30.51 'sudo cat /run/secrets/jitter/env | grep BEARER'
  ```
  The HA token should be one of the comma-separated values.

### Service call works but observation doesn't appear

- The service may have succeeded silently against the wrong endpoint.  Check **Settings → System → Logs** filtered to `jitter` — every service call logs.
- If you see `HomeAssistantError: jitter POST /v1/observations returned HTTP ...` the response code tells you what's wrong.

## Phase 2 (planned, not yet shipped)

A `sensor.py` platform polling `/v1/today` + per-habit `/history` so HA can **read** jitter state, not just write to it:

- `sensor.jitter_today_due` — count of habits pending today
- `sensor.jitter_<slug>_streak` — current streak per habit

Useful for "flash the office light red if dumbbell session not done by 14:00"-style automations.  Out of scope for the first ship — open an issue when the read side becomes interesting.
