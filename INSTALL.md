# jitter-ha — full setup walkthrough

End-to-end instructions to wire the jitter HA integration via
Authentik SSO and validate with the first automation.

## Architecture recap

```
HA integration ──OAuth Authorization Code──→ Authentik (SSO)
                                              │ access_token
                                              ▼
                ──JSON-RPC tools/call──→ jitter.ridlers.org/mcp
                  Authorization: Bearer <oauth_access_token>
```

Same MCP endpoint claude.ai already uses, same Authentik tenant —
one auth surface, revoke from one admin UI.  HA gets its own OAuth
client (separate from claude.ai's) so the two can be rotated /
revoked independently.

## Prerequisites you already have

- ✅ Jitter MCP server live at `https://jitter.ridlers.org/mcp`
- ✅ Authentik issuing JWTs for the `claude-mcp` client
- ✅ Server change shipped: `JITTER_OIDC_AUDIENCE` now accepts a
  comma-separated list, so HA's tokens (different `aud`) validate
  against the same MCP endpoint
- ✅ HA running with Application Credentials platform available
  (built-in since HA 2022.x)

## Step 1 — register a new OAuth2 application in Authentik

This is the one-time admin setup that gives HA its own credentials.

1. Open `https://auth.ridlers.org/if/admin/` → **Applications → Providers → Create**.
2. Pick **OAuth2/OpenID Provider**.  Settings:
   - **Name**: `jitter-ha-provider`
   - **Authentication flow**: same as your existing `claude-mcp` provider
   - **Authorization flow**: same as your existing `claude-mcp` provider
   - **Client type**: Confidential
   - **Client ID** + **Client Secret**: leave auto-generated, copy both at the end
   - **Redirect URIs**: `https://<your-ha-external-url>/auth/external/callback`
     - The exact HA external URL: **Settings → System → Network → "External URL"** in your HA UI.  Common shapes: `https://ha.ridlers.org/auth/external/callback` or Nabu Casa's `https://<id>.ui.nabu.casa/auth/external/callback`.  Get this right — if it doesn't match HA's actual external URL exactly the OAuth redirect 400s.
   - **Signing key**: same RS256 key your `claude-mcp` provider uses (so jitter's JWKS cache validates both)
   - **Subject mode**: based on User UUID (standard)
   - **Token validity** (access_token): 5 minutes is fine; HA refreshes automatically
   - **Refresh token validity**: 30 days or whatever your `claude-mcp` provider uses
3. **Save**.  Copy the **Client ID** and **Client Secret** — Authentik shows the secret only once.
4. Create the **Application** that uses this provider:
   - **Applications → Applications → Create**
   - **Name**: `Jitter HA`
   - **Slug**: `jitter-ha`
   - **Provider**: pick `jitter-ha-provider` from step 2
   - Save.

## Step 2 — make jitter accept the new audience

Jitter's MCP middleware validates JWTs against `JITTER_OIDC_AUDIENCE`.
Add HA's client_id to that list (comma-separated alongside the existing
`claude-mcp` one).

This lives in the jitter-host NixOS module config — find the `oidc`
block in `jitter-host/configuration.nix` (or wherever
`services.jitter.oidc.audience` is set).  Change from:

```nix
audience = "<existing-claude-mcp-client-id>";
```

to:

```nix
audience = "<existing-claude-mcp-client-id>,<new-ha-client-id>";
```

Then rebuild the jitter Pi:

```bash
ssh roridler@jitter.local 'cd ~/jitter-src && sudo nixos-rebuild switch --flake ".#jitter" --option build-dir /var/tmp/nix-build'
ssh roridler@jitter.local 'sudo systemctl restart jitter'  # picks up the new env
```

## Step 3 — configure HA Application Credentials

This is where HA stores the OAuth `client_id` + `client_secret` for jitter-ha.

1. In HA UI: **Settings → Devices & Services → Helpers tab → ⋮ → Application Credentials → Add Application Credential**.
2. Fields:
   - **Integration**: pick **Jitter** from the dropdown (only appears after the integration is installed)
   - **Name**: `Authentik (jitter-ha)`
   - **OAuth Client ID**: paste the Client ID from Authentik step 1
   - **OAuth Client Secret**: paste the Client Secret from Authentik step 1
3. Save.

You only do this once per HA install.  If you ever rotate the
Authentik secret, edit the entry here.

## Step 4 — install the integration via HACS

1. **HACS → ⋮ (top right) → Custom repositories**.
2. Repository URL: `https://github.com/rsr5/jitter-ha`.
3. Category: **Integration** → **Add**.
4. Find Jitter in the HACS list → **Download** → restart HA.

(If the repo is private, configure HACS with a GitHub PAT first.  Or
make it public — the integration source has no secrets.)

## Step 5 — add the integration to HA

After the HACS install + HA restart:

1. **Settings → Devices & Services → Add Integration → Jitter**.
2. **Base URL**: `https://jitter.ridlers.org` → **Submit**.
3. HA redirects you to Authentik.  Log in if not already.  Approve the
   "Jitter HA" application's access request.
4. Authentik redirects back to HA.  Integration finishes setup.

If you see "Cannot connect" or "Invalid auth", check Step 6
troubleshooting.

## Step 6 — smoke-test from Developer Tools

**Developer Tools → Services**.  Pick `jitter.log_observation`.  Enter:

```yaml
type: setup-test
payload:
  source: ha-developer-tools
  ok: true
external_id: ha-setup-test-{{ now().isoformat() }}
```

Click **Call Service**.  Should complete without errors.

Verify the observation landed (via the jitter MCP from claude.ai, or
direct from the jitter Pi):

```bash
ssh roridler@jitter.local 'sudo mysql jitter -e "
  SELECT id, source, recorded_via, type, payload
  FROM observations
  WHERE type = \"setup-test\"
  ORDER BY recorded_at DESC LIMIT 1"'
```

Should show `source: sensor`, `recorded_via: ha`.

## Step 7 — first real automation (scale → bodyweight)

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
      external_id: "scale-{{ trigger.to_state.last_updated }}"
```

**Why `_real_time_weight` is wrong**: smart scales typically expose
two entities — `_weight` (final, settled, fires once per weigh-in) and
`_real_time_weight` (jitters mid-weigh-in as you step on, breathe,
shift weight).  Triggering off real-time would spray dozens of
observations per weigh-in.

**Why the `external_id` shape**: `last_updated` changes on every state
write, so it is unique per weigh-in.  A re-fire from an HA restart or
recovery replays the same value and won't duplicate the observation —
jitter's `(user_id, source, external_id)` unique key returns the
existing row instead.

> **Use `last_updated`, not `last_changed`.**  `last_changed` only
> advances when the state VALUE changes, so weighing in two days
> running at the same rounded weight produces the SAME `external_id`
> twice.  The server then treats the second reading as a duplicate of
> the first and that day's weigh-in is lost.  This cost a real
> weigh-in before it was found; the server now returns 409 on a reused
> id with different content rather than silently dropping it, but the
> template is the actual fix.

## Troubleshooting

### "Missing configuration" on integration add

You skipped Step 3 — Application Credentials.  HA can't OAuth without
the client_id + client_secret stored locally.

### OAuth redirect URI mismatch

Authentik refuses the authorize request with an error mentioning
redirect URI.  The fix:
- HA's actual external URL is shown in **Settings → System → Network → "External URL"**.
- Authentik's allowed redirect URI must be **exactly** `<that URL>/auth/external/callback`.
- Trailing slashes matter, scheme matters, port matters if non-standard.

### "Invalid auth" / 401 after OAuth completes

Either:
- HA's token isn't reaching jitter — check **Settings → System → Logs** filtered to `jitter`.
- Jitter is rejecting the token's audience — verify Step 2 went in:
  ```bash
  ssh roridler@jitter.local 'sudo cat /run/secrets/jitter/env | grep AUDIENCE'
  ```
  Should show both client_ids comma-separated.

### Token refresh failures

OAuth2Session refreshes silently on access_token expiry.  If you see
401s sporadically after extended use:
- Authentik may have a max refresh_token lifetime — check the
  provider settings.
- Reauth via the integration's **⋮ → Reconfigure**.

## What's NOT in this version

- **Phase 2 sensors** — read-side state (`sensor.jitter_today_due`,
  per-habit streaks) would let HA *react* to jitter state, not just
  write to it.  Out of scope; open a follow-up when needed.
- **Reauth flow on token revocation** — if you delete the Authentik
  application, the integration goes 401-forever until you reconfigure.
  HA's reauth UX is there; just hasn't been wired explicitly into
  this integration's config_flow yet.
