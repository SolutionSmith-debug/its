# Operator Dashboard — WS2 D1-1 (read-only core)

A loginless, **localhost-only** FastAPI app that gives the operator one screen
of ITS runtime health. It **observes** the live daemon tree at `~/its`; it
changes nothing.

## Run

```bash
# from a worktree/venv that has the project installed (fastapi/uvicorn/jinja2):
python -m operator_dashboard          # serves http://127.0.0.1:8484
```

Expose to your other devices over Tailscale (never a public interface — D1-1
has no auth):

```bash
tailscale serve 8484
```

## Panels

Local-files-first (always on, no credentials):

| Panel | Source |
|-------|--------|
| launchd daemons | `launchctl list` (read-only, allowlisted argv) |
| Watchdog markers | `~/its/.watchdog/*.last_run` + `scripts.watchdog` tracked-jobs/windows |
| Circuit breaker | `~/its/state/circuit_breaker.json` (`shared.circuit_breaker`) |
| Daemon liveness | `~/its/state/*_heartbeat.txt` + `heartbeat_row_ids.json` |
| State locks | `~/its/state/*.lock` (passive, non-mutating fcntl probe) |
| Recent log tail | newest `~/its/logs/YYYY-MM-DD.log` (redacted on render) |

TTL-cached Smartsheet reads (degrade to "unavailable" without credentials):

| Panel | Source |
|-------|--------|
| ITS_Errors — recent | `shared.smartsheet_client.get_rows(SHEET_ERRORS)` |
| ITS_Review_Queue — depth | `shared.review_queue.get_pending()` |

Every panel is **fail-soft**: a missing file, a dead read, or a failed import
degrades that one card to "unavailable" — the page never crashes.

## Config editor — the ACT surface (D1-2)

`GET /config` + `POST /act/config` add the **Class-A runtime config editor**: pause/resume
gates, tuning knobs, and behavior/data config, edited straight into `ITS_Config` (effective
on the daemon's next cycle). See the runbook: `docs/runbooks/operator_dashboard_config_editor.md`.

- **One mutating route.** `POST /act/config` is the *only* non-GET route
  (`tests/test_operator_dashboard.py` asserts exactly that). It writes **only** to `ITS_Config`
  — an internal system-of-record write, **not** an external send; the External Send Gate stays
  with the daemons (no `anthropic`/`graph_client.send_mail`/`resend` here).
- **PIN + Origin, fail-closed.** Every write requires the operator PIN (Keychain
  `ITS_OPERATOR_PIN`, constant-time compare — a missing/locked keychain **denies**) and an
  allowlisted Origin (localhost + `ITS_DASH_ALLOWED_ORIGINS`). Not gated behind `@require_active`
  (works while PAUSED/MAINTENANCE).
- **Validated + audited.** A fixed `(Setting, Workstream)` registry (anything else is read-only —
  `external_send_gate`, `system.state`, `config_actuator`, `*.poll_interval_seconds` are refused);
  a per-key validator rejects out-of-bounds values with no write; a send-poller `false→true`
  activation is **escalated, not applied**; every applied edit + escalation writes a `config_audit`
  WARN row to `ITS_Errors` (auto-redacted).

## Safety posture (read panels, D1-1)

- **Adversarial input.** Smartsheet cells and raw log lines are untrusted:
  every displayed value is redacted (`shared.redact`) and HTML-escaped (Jinja
  `autoescape`), so a `<script>`-shaped or secret-shaped value renders inert.
- **Read-first.** Every panel route is `GET` and fail-soft; the only write is the audited,
  PIN-gated config editor above.

## If it misbehaves (Tier-2)

The dashboard is a convenience view with no data at risk. If it won't start or
a page errors: stop it (`Ctrl-C`) and re-run `python -m operator_dashboard`; a
single panel showing "unavailable" is expected when its underlying file/daemon
isn't present. It runs no launchd job yet (the plist is **D1-3**), so there is
nothing to unload. Escalate to Seth only if the read helpers in `shared/`
themselves are failing — that is a `shared/`-code issue, not a dashboard one.
