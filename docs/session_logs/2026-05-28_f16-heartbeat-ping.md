---
type: session_log
date: 2026-05-28
status: closed
workstream: infrastructure
related_prs: [114]
tags: [f16, heartbeat, watchdog, healthchecks-io, observability, fail-soft, option-a, network-boundary, integration-test, send-gate-classification]
---

# 2026-05-28 — F16: wire the external heartbeat ping (Option A)

Close audit **F16** — the largest documented-architecture-vs-actual-code gap. Doctrine (CLAUDE.md observability stack, FM v8) treats an external heartbeat monitor as the wired "the MacBook is dead" detector, but nothing in the repo read or pinged the seeded `system.heartbeat_url` ITS_Config row. A total-host failure (crash, disk-full, launchd unload, user logout) went undetected — every in-tenant signal goes silent in that scenario with nothing to raise the alarm.

## Commits landed

- PR [#114](https://github.com/SolutionSmith-debug/its/pull/114) — squash-merged 2026-05-29T13:31:00Z, merge commit `692a1c50c0ff5e1557a4f4c31c351260c6fb8e26`. Branch work: `7ab6dd8` (feat: new `shared/heartbeat_client.py`, watchdog `main()` wiring, seed label correction, unit + integration tests) + session-log/index commits + two `origin/main` merges resolving overlap with PR #113 (see below).

## CI runs — four-part PR-landed verify clean

Per `pr-landed-verifier` (`docs/operations/pr_merge_discipline.md`):

```
PR #114 — four-part verify clean
- state: MERGED
- mergedAt: 2026-05-29T13:31:00Z
- mergeCommit: 692a1c50c0ff5e1557a4f4c31c351260c6fb8e26
- main CI on merge commit: SUCCESS
  - workflow `ci` — job `test`: success
  - workflow `CodeQL` — job `Analyze (python)`: success
  - workflow `CodeQL` — job `Analyze (actions)`: success
```

Operator manual-smoke confirmed before merge (Healthchecks.io dashboard showed the live ping land). This finalization (status `active`→`closed` + merge facts) follows the repo's post-merge session-log discipline (cf. #108, #115).

## Decisions made during session

- **Option A (ratified before coding), not an inline `requests.get`.** `scripts/watchdog.py` had zero HTTP capability. The next item, **F02**, inverts capability-gating into a network-library allowlist where only `shared/*_client.py` may import `requests`/`urllib`/`httpx`/`socket`/`subprocess`. A bare inline `requests.get` in watchdog would plant exactly what F02 then has to dig out — so the ping goes behind `shared/heartbeat_client.py`, mirroring `resend_client`/`graph_client`. watchdog imports the wrapper, never `requests` (verified: `git grep "import requests" scripts/watchdog.py` → none).
- **`ping()` swallows internally + logs WARN, rather than raising for the caller to swallow.** Both are valid per the brief; chose swallow-internally because the only caller (watchdog) would just swallow anyway, and it keeps the §3.1 fail-open rationale in one place. `HeartbeatError` is still defined for symmetry with sibling clients + future opt-in callers, even though `ping()` never raises it.
- **`raise_for_status()` inside the try.** A non-2xx (mistyped URL → 404, monitor outage → 5xx) routes through the *same* WARN path as a connection failure. `HTTPError ⊂ RequestException`, so one `except` covers it. Rejected the alternative (treat only connection/timeout as failure, accept any HTTP response) — a 404/5xx is a real "beacon not landing" signal worth a log line, not a silent success.
- **Ping fires on MAINTENANCE, not PAUSED.** Placed after the existing PAUSED early-return (so PAUSED never pings — a deliberately-paused system doesn't claim liveness) but inside the MAINTENANCE path. Rationale: the heartbeat answers "is the host alive," which is true during MAINTENANCE; suppressing it would trip a false "host dead" alert on the external monitor. Alert *suppression* during MAINTENANCE applies to the checks' own alerts, not to the liveness beacon. (Surfaced per the brief's request to flag if PAUSED handling changed — it did not; ping stays below the PAUSED return.)
- **Doctrine label corrected UptimeRobot → Healthchecks.io.** The operator provisioned a Healthchecks.io check (the free UptimeRobot tier gated heartbeat behind Pro + restricts commercial use), so the seed-row `Description` was corrected. The seed `Value` placeholder *token* (`PLACEHOLDER_uptimerobot_heartbeat_url`) was left unchanged so it stays char-for-char equal to the watchdog guard token — guard == seed token is the invariant; renaming the token would have required editing both in lockstep for no benefit.
- **Send-gate classification: observability beacon, not a customer send.** Intentionally NOT added to `SEND_SCRIPTS`/`GATED_SCRIPTS`; `tests/test_capability_gating.py` left unchanged. It targets a fixed monitoring endpoint, carries no customer data, and is analogous to Sentry capture — External Send Gate (FM v8 Invariant 1) does not apply. Documented in the module's §42 Invariants heading.
- **Integration test kept in this PR**, not split to a follow-on. The `sdk-integration-test-scaffold` agent recommended a separate PR per narrow-scoping discipline; overridden because the brief explicitly lists `tests/test_heartbeat_client_integration.py` as part of F16's deliverable and verification gates. The test exercises the real wired path (`get_setting → ping`) and asserts a 2xx via the no-WARN signal (fail-soft `ping` returns None on both success and failure, so absence-of-WARN *is* the success signal), plus an independent direct-GET 2xx check.

## Verify-before-fix — brief assertions re-checked against live HEAD

The audit's F16 line numbers were stale (authored against `40a3509`). `brief-validator` re-confirmed all 12 current-state claims against HEAD `5bb6486`; two notes acted on:
- The seed row carries `Workstream: "global"` (the brief omitted it) — the `get_setting` call uses `workstream="global"` accordingly.
- Op Stds §30 has no standalone heading (rolled into the `§25-§30 Carry Forward` block) — citation is correct by intent; non-blocking.

## Operator close-out (completed)

1. **Manual smoke (REQUIRED — external side effect)** — ✅ DONE. Operator confirmed the Healthchecks.io dashboard showed the live ping land before authorizing the squash-merge.
2. **Four-part merge verify** — ✅ DONE. `pr-landed-verifier` clean (see CI runs above): MERGED / mergedAt non-null / mergeCommit `692a1c5` / main CI on merge commit SUCCESS.

Remaining optional:
- **Live integration test (operator-gated)**: `pytest -m integration tests/test_heartbeat_client_integration.py` (needs `ITS_SMARTSHEET_TOKEN` in Keychain + network). Not run by CC — avoids an unsanctioned outbound ping to the operator's live monitor during dev. Deselected by default in CI; the wired path was already exercised by the operator manual-smoke.

## Merge-treadmill note

PRs #113 (F17/F04 sweep), #115, #116 landed on `main` mid-session. #113 independently rewrote the *same* watchdog self-marker comment F16 touches → resolved by keeping the F16 phrasing (it correctly separates the local Check-C marker from the heartbeat ping now sitting below it); #113's `safety_intake` `TRACKED_JOBS` entry preserved. #115/#116 (docs) auto-merged. Required three `origin/main` integrations before the branch stayed `CLEAN` long enough to squash.

## What was NOT touched (deliberate)

- **`tests/test_capability_gating.py`** — unchanged on purpose (heartbeat is a beacon, not a send/generation script).
- **F02 network-library allowlist** — not built here; this PR is *designed to satisfy* it (the `requests` import is correctly housed), but the allowlist test is F02's job.
- **The watchdog's self-marker (`write_last_run_marker("watchdog")`)** — kept; its purpose (Check-C self-tracking) is distinct from the heartbeat. Only its stale comment was rewritten to stop conflating the local marker with the (now-real) external beacon. The pre-existing stale "TRACKED_JOBS is empty" doc-drift in the module docstring was left for a doc pass (out of scope).
- **No watchdog refactor** (Op Stds §14) — additive import + one block appended after the marker.
- **The F17/F04/docstring sweep** (PR #113) — functionally independent; land order did not matter for correctness, though it landed first and required a textual merge resolution (see Merge-treadmill note).

## Lessons captured to memory

- New auto-memory candidate proposed to `session-close-maintainer`: the **send-gate classification rule** — outbound observability beacons (heartbeat, Sentry) to fixed monitoring endpoints are NOT customer-facing sends and do not enter `SEND_SCRIPTS`/`GATED_SCRIPTS`, even though they cross a network boundary. The discriminator is "fixed monitoring endpoint + no customer data," not "makes an outbound call."
