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

## Safety posture (D1-1)

- **Read-only by construction.** Every route is `GET`. There is no write, act,
  or send path anywhere in this package (`tests/test_operator_dashboard.py`
  proves zero non-GET routes).
- **Adversarial input.** Smartsheet cells and raw log lines are untrusted:
  every displayed value is redacted (`shared.redact`) and HTML-escaped (Jinja
  `autoescape`), so a `<script>`-shaped or secret-shaped value renders inert.
- **No secrets.** No Keychain PIN, no `@require_active`, no CSRF/Origin auth —
  all of that lands with the **D1-2** ACT surface (`app.py` marks the mount
  point). The Smartsheet client's own internal Keychain-backed token is the
  only credential touched, and only to *read*.

## If it misbehaves (Tier-2)

The dashboard is a convenience view with no data at risk. If it won't start or
a page errors: stop it (`Ctrl-C`) and re-run `python -m operator_dashboard`; a
single panel showing "unavailable" is expected when its underlying file/daemon
isn't present. It runs no launchd job yet (the plist is **D1-3**), so there is
nothing to unload. Escalate to Seth only if the read helpers in `shared/`
themselves are failing — that is a `shared/`-code issue, not a dashboard one.
