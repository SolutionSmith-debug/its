# Session log — 2026-05-20 Watchdog Session 2

## Purpose

Second half of Excellence Roadmap v2.1 Track 1 R2. Ships Checks **C** (scheduled-jobs
marker scaffold), **D** (14-day reviewer-chain forward scan per Op Stds v9 §18),
and **F** (Mail.app silent-disable inbound-activity check per `docs/tech_debt.md`),
plus the `scripts/smoke_test_watchdog.py` runner exercising all six phases.

Check **E** (Anthropic spend trend) **deferred to PR #37** — operator decision
2026-05-20 after pre-flight surfaced that `ITS_ANTHROPIC_ADMIN_API_KEY` in Keychain
holds a workspace key (`sk-ant-api03-...`), not an Admin key (`sk-ant-admin01-...`).
The `/v1/organizations/cost_report` endpoint returns 401 to workspace keys; without
a real Admin key the live smoke for Check E could never pass. Operator provisions
in parallel and ships Check E in a follow-on PR.

Follow-on to PR #35 (PTO fetcher) at 7d85f2d; Check D depends on the live
`_live_fetcher` shipped there.

## Pre-flight findings (Op Stds v9 §13)

Nine items + seven open questions swept. Eight items resolved cleanly; four
findings deserve durable mention.

1. **Admin API key class wrong.** `security find-generic-password -s
   ITS_ANTHROPIC_ADMIN_API_KEY -w` returned a key with prefix `sk-ant-api03-…`
   (workspace API key). Direct probe of `/v1/organizations/cost_report` returned
   HTTP 401 `{"error":{"type":"authentication_error","message":"invalid x-api-key"}}`.
   Operator-confirmed path forward: defer Check E + `shared/anthropic_billing.py`
   to PR #37 (Admin key provisioning is the operator's prerequisite, not a code
   path). This PR ships A/B/C/D/F.

2. **`review_queue.add` signature differs from brief's Check D pseudocode.**
   The brief used `item_id=`, `review_reason="anomaly"`, `workstream="watchdog"`.
   Live API: item ID auto-generated, `reason` is a `ReviewReason` enum (no
   `ANOMALY` value), `workstream` validated against `VALID_WORKSTREAMS` (no
   `"watchdog"`), `sla_tier=SlaTier.X` required. Operator-confirmed mapping:
   `workstream="global"`, `reason=ReviewReason.OTHER`, `sla_tier=SlaTier.SUBCONTRACT_DRAFT`,
   `severity=Severity.INFO`, `summary="reviewer-chain gap detected (<N> day(s))…"`.
   Documented in `_log_anomaly_to_review_queue`'s docstring + tested explicitly
   in `test_check_reviewer_chain_single_gap`.

3. **Operator-answer ambiguity on Check D SLA tier resolved by rationale.**
   The operator's directive literally said `sla_tier=SlaTier.SAFETY_INTAKE`,
   then immediately added "Document the SLA-tier rationale in the session log:
   SUBCONTRACT_DRAFT chosen specifically to avoid Check A's stale-row WARN
   auto-firing on Check D anomaly rows within the operator's normal triage
   window." Those two are contradictory — SAFETY_INTAKE's 2× threshold is
   8 hours, so Check A would WARN on every anomaly row within 24 hours of
   creation; SUBCONTRACT_DRAFT's 2× threshold is 96 hours / 4 days, matching
   the stated rationale. **I implemented SUBCONTRACT_DRAFT** (the
   rationale-coherent reading). If the literal text was the operator's intent
   and SAFETY_INTAKE is wanted instead, one-line change to
   `_log_anomaly_to_review_queue` in a follow-up.

4. **Helpers added per brief specs (both anticipated by pre-flight Q2/Q3):**
   - `shared.smartsheet_client.get_settings_with_prefix(prefix, *, workstream=None) -> dict[str, str]`
     — minimal wrapper around `get_rows` + prefix-filter, drops non-string
     values to match `get_setting`'s contract. Used by Check F.
   - `shared.graph_client.fetch_latest_inbound_timestamp(mailbox) -> datetime | None`
     — `$select=receivedDateTime&$top=1&$orderby=receivedDateTime desc`, ISO
     parsing with `'Z'` → `'+00:00'` normalization, returns None on empty
     mailbox (distinct from raised error). Used by Check F.

**Other pre-flight items, verified clean:**
- Post-Session-1 watchdog harness matches brief — `_run_check(check_fn, *,
  alerts_suppressed)`, MAINTENANCE/PAUSED semantics correct, ERROR not
  downgraded, `main()` decorated with `@its_error_log` (not `@require_active`).
- `TRACKED_JOBS` not pre-defined; clean slate to declare as `[]`.
- No existing `.last_run` / `~/its/.watchdog/` references in the repo.
- 5 of 6 candidate `ITS_Config` rows were missing (clean seed); only
  `mail_intake.safety.max_idle_hours = "96"` seeded this PR (the spend.*
  knobs + `system.anthropic_admin_api_keychain_key` belong to Check E).
- Application Access Policy already verified by operator 2026-05-20
  (per brief preface) — `safety/procurement/subcontracts/its` in scope; no
  PowerShell re-run needed.

## Code changes

### `shared/smartsheet_client.py`
- Added `get_settings_with_prefix(prefix, *, workstream=None) -> dict[str, str]`.
  ~25 LOC. Filters via existing `get_rows`; skips non-string `Value` cells.

### `shared/graph_client.py`
- Added `fetch_latest_inbound_timestamp(mailbox: str) -> datetime | None`.
  ~25 LOC. ISO 8601 parse + UTC normalization. Uses existing `_request` so
  retry/auth/error-translation behavior is inherited.

### `scripts/watchdog.py`
- New module imports: `date`, `datetime`, `timedelta`, `UTC`, `Path`,
  `graph_client`, `is_federal_holiday`, `resolve_chain`, `TimeOffClient`,
  `ReviewReason`, `SlaTier`.
- Constants: `WATCHDOG_MARKER_DIR`, `TRACKED_JOBS=[]`,
  `REVIEWER_CHAIN_SCAN_DAYS=14`, `WORKSTREAMS_TO_SCAN=["safety_reports"]`,
  `WORKSTREAM_TO_MAILBOX={"safety": "safety@evergreenmirror.com"}`.
- `write_last_run_marker(job_name)` helper — fail-soft, WARN on `OSError`,
  creates `WATCHDOG_MARKER_DIR` on demand.
- `_check_scheduled_jobs()` — returns INFO when `TRACKED_JOBS` empty (today's
  state by design per planning decision C1); WARN on missing/unreadable/stale
  markers when tracked entries exist.
- `_check_reviewer_chain_forward()` — 14-day scan per workstream in
  `WORKSTREAMS_TO_SCAN`, federal holidays skipped, one INFO ANOMALY row per
  workstream collected gaps (not per-gap row, to keep the queue manageable).
  Per-instance `TimeOffClient` caching → one Smartsheet read for the entire
  cross-workstream scan.
- `_log_anomaly_to_review_queue(workstream, gaps)` — adapts to the live
  `review_queue.add` signature per pre-flight finding #2.
- `_check_mail_intake_silent_disable()` — iterates `mail_intake.*.max_idle_hours`
  rows via `get_settings_with_prefix`, resolves mailbox via
  `WORKSTREAM_TO_MAILBOX`, WARNs on idle > threshold. Per-mailbox Graph
  failures WARN and continue (failure isolation per Op Stds v9 §27).
- `CHECKS` registry extended to 5 entries (A, B, C, D, F). Check E intentionally
  absent with an inline comment explaining the PR #37 deferral.
- `main()` calls `write_last_run_marker("watchdog")` after the check loop —
  marks the watchdog's own last run so future external observers can detect
  "watchdog itself stopped firing" (chicken-and-egg fix). PAUSED short-circuits
  before the marker write (consistent with skipping checks).

### `.gitignore`
- New entry: `.watchdog/` — marker files written by `write_last_run_marker`
  must not be committed.

### Tests (+33 across three files)

| File | Δ tests | Coverage |
|---|---:|---|
| `tests/test_watchdog.py` | +24 | CHECKS registry, TRACKED_JOBS empty invariant, main() marker writes, Group F (Check C scaffold + marker writes: 7), Group G (Check D: 6), Group H (Check F: 8) |
| `tests/test_smartsheet_client.py` | +4 | `get_settings_with_prefix` — prefix filter, workstream narrowing, non-string skip, empty-no-match |
| `tests/test_graph_client.py` | +5 | `fetch_latest_inbound_timestamp` — ISO Z parse, empty mailbox → None, query-string shape ($top=1, $orderby), 403 + 404 propagation |

### `scripts/smoke_test_watchdog.py` (new)
- 6-phase runner with per-phase `try/finally` cleanup. PASS / SKIPPED / FAIL
  reporting. Phase E prints `SKIPPED: deferred to PR #37 — Admin API key
  not provisioned` and is not counted as a failure by the exit code.

### `ITS_Config` rows seeded (1)
- `Setting=mail_intake.safety.max_idle_hours, Value=96, Workstream=global,
  Description=…` — used by Check F.

(The 4 `spend.*` knobs and `system.anthropic_admin_api_keychain_key` row stay
unseeded; they're Check E's responsibility in PR #37.)

## Smoke results

```
ITS watchdog smoke runner — tag ITS-WATCHDOG-SMOKE-2026-05-20
============================================================

[Check A (stale review queue)]
      PASS

[Check B (open criticals)]
      PASS

[Check C (scheduled jobs scaffold)]
      PASS

[Check D (reviewer chain forward scan)]
      PASS

[Check E (spend trend)]
      SKIPPED: deferred to PR #37 — Admin API key not provisioned

[Check F (mail intake silent-disable)]
      observed: safety@evergreenmirror.com has no inbound history
      check result: INFO — All tracked intake mailboxes fresh.
      PASS

============================================================
Summary:
  [PASS   ] Check A (stale review queue)
  [PASS   ] Check B (open criticals)
  [PASS   ] Check C (scheduled jobs scaffold)
  [PASS   ] Check D (reviewer chain forward scan)
  [SKIPPED] Check E (spend trend)
  [PASS   ] Check F (mail intake silent-disable)
```

Each phase cleaned its own artifacts via `try/finally`. Post-smoke
`~/its/.watchdog/` is empty.

**Operational observation from Phase F:** `safety@evergreenmirror.com` has
zero inbound history. The check correctly handles None (empty mailbox is
distinct from silent-disable — could be a brand-new mailbox awaiting its
first message). For the sandbox tenant this is expected; when Safety
Reports intake goes live, the first real inbound message will replace
this state and Check F will start measuring actual idle hours.

## Decisions made during session

- **Defer Check E to PR #37** (per operator direction). Anti-pattern compliance:
  the wrong-key-class issue is an operator-side fix; trying to ship Check E
  with a workspace key would lock in a non-working live path. Better to ship
  five working checks now and one working check later than six checks with
  one silently broken.
- **Check D SLA tier = SUBCONTRACT_DRAFT** (per the rationale-coherent reading
  of the operator's ambiguous directive — see pre-flight finding #3). The
  4-day stale window gives the operator time to triage anomaly rows before
  Check A's stale-WARN auto-fires on them.
- **One ANOMALY row per workstream, not per gap-day.** Each row's payload
  carries the full `gap_dates` list. Keeps `ITS_Review_Queue` clean even when
  the same multi-week PTO arrangement spans many forward-scan days.
- **No deduplication across watchdog runs** (per brief). A persistent gap
  produces one new row per daily run — accepted as Session 2 behavior;
  enhancement candidate if proliferation becomes painful.
- **`write_last_run_marker("watchdog")` runs at end of `main()` even though
  `TRACKED_JOBS` is empty.** Plants the marker for future external observers
  (UptimeRobot, etc.) so the watchdog's own no-run state is detectable.
  PAUSED short-circuits before the marker so a deliberately-paused system
  doesn't look fresh.
- **`get_settings_with_prefix` returns `dict[str, str]`** rather than a list of
  row dicts. Tighter shape for the immediate caller (Check F); other callers
  can switch to `get_rows + filter` if they need the full row dict.
- **`_check_mail_intake_silent_disable` skips empty mailbox (None) as INFO**,
  not WARN. Empty ≠ silently disabled; could be brand new. Surface conversion
  to WARN if pre-launch validation needs it.

## Verification

- `ruff check .` — clean (4 auto-fixes during dev: `timezone.utc` → `UTC`
  per UP017; import-block ordering).
- `mypy .` — 0 errors across 67 source files (Op Stds v9 §28; +1 for the
  smoke runner).
- `pytest -q` — **470 passed, 2 skipped** (was 437; delta +33).
- Live smoke — 5 PASS, 1 SKIPPED (planned), 0 FAIL.

## Out-of-scope notes

- **`shared/anthropic_billing.py` not created** — Check E deferred to PR #37.
- **`spend.*` × 4 `ITS_Config` rows not seeded** — Check E's responsibility.
- **`system.anthropic_admin_api_keychain_key` row not seeded** — only needed
  by Check E; PR #37 will seed it (matching the Resend/Sentry pattern).
- **Procurement / subcontracts / its / voice intake mailboxes not pre-configured**
  per brief anti-pattern #3 — only seed `mail_intake.safety.max_idle_hours`
  until the other mailboxes go intake-bearing. `WORKSTREAM_TO_MAILBOX` has
  only `safety` for the same reason.
- **No dedupe in Check D** per brief anti-pattern #4.
- **Harness untouched** per brief anti-pattern #1 — verified `_run_check`,
  CHECKS-list iteration, kill-switch routing all unchanged.
- **`ReviewReason` enum not extended** with an `ANOMALY` value — matches
  brief anti-pattern §3 (preservation-over-refactor §14). Future PR can add
  the picklist value + enum entry if anomaly rows become operationally
  distinct from `OTHER`.
- **`_no_override` chain-override fetcher** still stubbed — unchanged from
  PR #35's "separate PR when a consumer exercises it" status.
- **Box Layer 2 JWT wait unaffected.**

## Sequencing context

- Completes the 6-check watchdog spec (5 shipped, 1 deferred). Closes the
  R2 half of Excellence Roadmap v2.1 Track 1 except for Check E.
- PR #37 (Check E + `shared/anthropic_billing.py` + 4 spend.* config rows +
  `system.anthropic_admin_api_keychain_key` row + 8-ish new tests + Check E
  live-smoke phase) becomes the next queued workstream once the operator
  provisions a real Admin API key (`sk-ant-admin01-...` prefix). The current
  smoke runner's Phase E becomes a real exerciser at that time.
- **Unblocks the Phase 1.5 30-day-clean-operation gate** — watchdog is now in
  near-final production shape; the 30-day timer can start once the operator
  is comfortable with the deferral discipline (Check E is a "nice-to-have"
  spend alarm, not a system-correctness check).
- **Marker convention now wired** — any future scheduled job appends its slug
  to `TRACKED_JOBS` and calls `write_last_run_marker(<slug>)` on success.
  One line per side.
- Op Stds v9 invariants honored: §13 verify-before-fix (9-item pre-flight
  surfaced the Admin-key issue before any code was written); §27 failure
  isolation (Check F's per-mailbox WARN-and-continue; `write_last_run_marker`
  fail-soft); §28 mypy baseline 0 (unchanged at 67 files).
