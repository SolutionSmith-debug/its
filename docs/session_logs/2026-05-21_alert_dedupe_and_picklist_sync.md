# Alert-routing dedupe ship + picklist sync foundation + V1 fix — 2026-05-21

Long session spanning 2026-05-20 evening into 2026-05-21 early morning UTC. Shipped the alert-routing dedupe design end-to-end (PR α + PR β), the cross-sheet PICKLIST sync foundation (per the picklist-sync brief), and a verify-before-fix hardening pass that uncovered V1 — a MAINTENANCE-bypass bug in Check G. Total: 11 PRs landed on `main` (PRs #42 → #52).

## Commits landed

| SHA | PR | Purpose |
|---|---|---|
| `b9aeb1e` | #42 | PR α — alert-dedupe core: correlation-ID threading + Resend-leg dedupe (`shared/alert_dedupe.py`, `_fire_resend_leg` gate, `Correlation_ID` ITS_Errors column) |
| `e1c1c3a` | #43 | chore: fix `smoke_test_alert_dedupe.py` to exercise all three triple-fire legs (was bypassing `log()` via direct `_alert_critical`) |
| `0888395` | #44 | PR β — watchdog Check G (alert-dedupe summary sweep) with two-phase deletion |
| `6a5f8ff` | #45 | prep PR: `smartsheet_client` helpers (`list_columns_with_options`, `update_column_options`, `find_sheet_by_name_in_folder`, `create_sheet_in_folder`) |
| `7a3cdc9` | #46 | feat: cross-sheet PICKLIST sync from master DBs (`shared/picklist_sync.py`, CLI runner, migration, 44 tests, runbook) |
| `3b6632c` | #47 | fix: drop `id` from `update_column_options` body (errorCode 1032) |
| `0b48eab` | #48 | fix: include `column.type` in body (errorCode 1090) |
| `ef2973e` | #49 | fix: unwrap `EnumeratedValue` → str in `list_columns_with_options` (actual root cause; SDK silently stripped wrapped type) |
| `1cf2c7b` | #50 | chore: picklist sync hardening (kill switch via `@require_active`; integration test infrastructure; 5 sandbox tests; cadence 15min → hourly) |
| `8ec4c80` | #51 | fix: integration-test followups — SDK `Folders.get_folder()` swap to REST (stale-cache bug), sandbox name length (≤50), MULTI_PICKLIST quirk |
| `56fac6c` | #52 | V1 fix: Check G respects MAINTENANCE via `alerts_suppressed` parameter (defer pattern); smoke harness bundled for §2 consistency |

PR #43 was the "smoke harness mirrors the wrong path" finding mid-session. PRs #47/#48/#49 were three iterations of the same class of bug surfacing different live-API constraints. PR #51 caught real production bug (SDK same-session caching) plus two test-fixture issues. PR #52 closed V1 surfaced by the multi-agent sanity audit.

## CI runs

All 11 PRs: GitHub Actions `test` job × 2 matrix, both green, ~35–44 sec per run. No CI flakes; no failed runs that required re-running. Hyperlinks omitted (PR URLs at `https://github.com/SolutionSmith-debug/its/pull/<N>` for `N` in 42–52).

## Decisions made during session

### Alert dedupe (PR α / #42)

- **Locking idiom:** `fcntl.flock(LOCK_EX | LOCK_NB)` with bounded retry (5 attempts × 50 ms). Alternative considered: signal-alarm timeouts — rejected as threading-unsafe and not used elsewhere in repo. Single-host single-writer means contention is essentially impossible; the retries are defensive courtesy.
- **Fail-open contract everywhere:** any state-read or state-write exception → marker line + safe default. Reasoning: false positives (extra emails) are acceptable; false negatives (missed operator wake-ups) are not. Locked into module docstring.
- **Correlation ID generation in the decorator, not `_alert_critical`:** so `log()` (writes Smartsheet row) and `_alert_critical` (Resend + Sentry) share the same UUID. Alternative considered: generate inside `_alert_critical` and pass to `log()` after — rejected because `log()` is called first via the existing decorator flow.
- **Body format preservation:** kept existing 4-space `Script:    {script}` / 3-space `Message:   {message}` alignment in `_alert_critical` body composition to avoid breaking existing tests' substring assertions. Added `Correlation: {uuid}` as a new line rather than rewriting all alignment.
- **`_alert_critical` accepts `correlation_id: str | None = None`:** the decorator passes the shared UUID; smoke tests can omit and accept an internal-generated UUID. Avoids forcing every existing caller to thread the new arg.
- **Threshold validation in `_resolve_size_thresholds`:** all-or-nothing fallback on any invalid configured value plus a single WARN to ITS_Errors naming both raw values. Rejected per-value mixing (configured `warn=300` alongside default `halt=400` after halt rejected) because subtle inconsistency is worse than uniform fallback.

### Smoke-harness gap (PR #43)

- **Pattern divergence noted:** `smoke_test_sentry.py` and `smoke_test_resend.py` call `_alert_critical` directly (intentionally narrow per-leg coverage). `smoke_test_alert_dedupe.py` must use the `@its_error_log` decorator to exercise the Smartsheet leg, otherwise ITS_Errors rows never get written. Tech-debt entry filed for future smokes that claim "full triple-fire" coverage.
- **Brief draft had two inaccuracies** corrected during implementation:
  - "5 ITS_Errors rows all sharing one Correlation_ID" → actually 5 distinct UUIDs (decorator generates per-CRITICAL).
  - "Prior smoke's window suppresses re-run" → false: prior smoke used `error_code="smoke_dedupe"`, new smoke via decorator uses `"uncaught_exception"`. Different dedupe keys → independent windows.

### Watchdog summary sweep / PR β (#44)

- **Two-phase deletion for crash safety:** phase 1 (sweep N) — fire summary + `mark_summarized`; phase 2 (sweep N+1) — delete `summarized=true`. A crash between Resend send and `mark_summarized` causes the next sweep to re-fire (duplicate email is acceptable; silent loss is not). Alternative considered: single-phase fire-and-delete in one sweep — rejected because no recovery path on partial failure.
- **Summary body lists ITS_Errors filter criteria, not inline correlation IDs.** Reasoning: state file aggregates only `suppressed_count` + timestamps; per-event UUIDs live in ITS_Errors. Operator pulls detail from the sheet. Cheaper, simpler, and the sheet is the source of truth. Upgrade-if-friction filed in tech_debt.
- **Resend leg only, per Op Stds v9 §27:** summary email is push, not record. Does NOT write ITS_Errors (rows already exist from PR α) and does NOT fire Sentry (not an exception event).

### Picklist sync core (PR #46)

- **Pure-function core + driver:** `extract_unique_values`, `compute_diff`, `compute_hash`, `_resolve_size_thresholds`, then `sync_one_mapping` / `sync_all`. Lets tests cover the math without mocking Smartsheet.
- **Reference-checked removals:** before removing a picklist option, count live target cells using it. Non-zero → keep option + log Review Queue row (`Reason=mismatched-reference`, `Severity=WARN`). Read failure during reference check fails-SAFE (returns 1 to block removal — silent destruction is worse than a noisy Review Queue row).
- **Two-stage size guardrails:** 200 WARN, 400 HARD-HALT-that-mapping. Configurable via ITS_Config; defaults in `shared/defaults.py`. Operator directive added the hard-halt branch on top of the brief's WARN-only ask.
- **Triple-fire on ≥3 mappings failed in one run:** explicit `log(CRITICAL) + _alert_critical()` with shared correlation_id rather than raising (which would lose other-mapping results). Sub-threshold failures stay at ERROR per §3 push-vs-record.
- **SHA-256 of sorted unique source values as `last_run_hash`:** idempotency short-circuit. Unchanged source → matching hash → zero API writes. Stored in `Picklist_Sync_Config` as TEXT_NUMBER (not DATE — operator directive: 15-min cadence at the time needed time-of-day; later changed to hourly but TEXT_NUMBER rationale still holds).
- **Smartsheet client helpers split into prep PR #45:** the four helpers totaled 83 non-blank lines, exceeding the operator's 50-line threshold for in-PR shared/* extensions. Sequential merge: prep PR first, then picklist sync depends on the merged helpers.

### SDK-vs-live body-shape iteration (PRs #47/#48/#49)

- **Three live-API constraints surfaced one at a time** by the picklist smoke test:
  - PR #47: `id` MUST NOT appear in `update_column` body (errorCode 1032; column ID lives in URL path).
  - PR #48: `type` IS required in body when `options` is changing (errorCode 1090).
  - PR #49: `type` value must be plain string, not the SDK's `EnumeratedValue` wrapper — SDK silently strips wrapped values and the API rejects with the same 1090.
- **Decision: forward fixes, no history rewrite.** Each fix is a separate PR with its own regression-guard test asserting the SDK-side serialized body shape. The iterative arc is intentionally preserved in the commit history as a teaching artifact for the value of integration tests.
- **Mock-only testing at SDK boundary doesn't catch live contract drift.** The pattern is fundamentally insufficient for typed columns + body shapes. Integration tests landed in PR #50 to close the class.

### Picklist sync hardening (PR #50)

- **7-item brief; verify-before-fix discipline.** Found 5 of 7 already-resolved in shipped code (with citations); 2 needed surgical fixes; 1 (cadence) was BLOCKED-ON-OPERATOR.
- **Kill switch via `@require_active`** (outermost decorator). Alternative considered: explicit `check_system_state()` matching watchdog's pattern — rejected because at 96 runs/day (the pre-decision cadence), `started`/`completed` INFO noise for paused runs would dominate the local log. The decorator short-circuits before any its_error_log noise.
- **Cadence: 15min → hourly** per operator decision. Cuts 96/day → 24/day; matches construction-workflow tempo (vendors added today don't need to appear in dropdowns within 15 minutes).
- **Integration test infrastructure:** `@pytest.mark.integration` marker registered in `pyproject.toml`, default `pytest -q` adds `-m 'not integration'` to addopts. Operator runs `pytest -m integration` on demand pre-deployment. CI doesn't have live Keychain access, so default-skip keeps CI green.
- **`shared.review_queue.ReviewReason.MISMATCHED_REFERENCE` already in the enum** — pre-flight surface from PR #50 brief turned out to be stale; no enum addition needed.

### Integration-test followups (PR #51)

- **The integration tests caught their own design issue immediately on first run.** Three surfaces:
  - Test-fixture bug: `_sandbox_name` generated names up to 59 chars; Smartsheet limit is 50 (errorCode 1041). Fixed; added `len() <= 50` assert as forward-protection.
  - **Real production bug:** `find_sheet_by_name_in_folder` used `Folders.get_folder()` which is deprecated AND returns stale folder data within a single SDK client session — newly-created sheets don't appear in subsequent `get_folder` calls from the same client. Direct REST sees them immediately. Refactored helper to REST + `requests`-based mocks for 5 unit tests. Picklist migration's earlier success was a happy accident (fresh Python process per run).
  - Smartsheet API quirk: MULTI_PICKLIST columns read back as TEXT_NUMBER after sheet creation. Tech-debt entry filed; integration test simplified to PICKLIST-only.

### V1 fix — Check G honors MAINTENANCE (#52)

- **Audit-driven finding:** the multi-agent sanity audit caught that Check G's per-entry loop calls `resend_client.send_alert()` directly inline, sidestepping the severity-downgrade path other checks use via `log()`. MAINTENANCE was supposed to suppress operator alerts; Check G fired anyway. PAUSED was handled correctly (whole CHECKS loop skipped).
- **Fix: defer pattern (option a)** per operator spec. Phase 1 (send + mark) DEFERS during MAINTENANCE; phase 2 (delete) PROCEEDS (no push side-effect, suppressing it would grow state unboundedly). Bounded delay = MAINTENANCE window + one watchdog cadence; no information loss. Alternative considered: carve out Check G from MAINTENANCE entirely — rejected because it breaks the operator's "don't page me right now" invariant.
- **Signature inspection in `_run_check`** to thread `alerts_suppressed` only to checks that accept it. Alternative considered: change all checks' signatures to take `**kwargs` — rejected as gratuitous. Other approach considered: typed `Protocol` for two flavors of check — rejected as over-engineering for one check that needs the param. `inspect.signature(check_fn).parameters` is minimal and self-documenting.
- **Smoke harness bundled into PR #52** per operator directive. The dedicated smoke harness was itself the same class of bug as V1 — defaulted to `alerts_suppressed=False` and ignored MAINTENANCE. Leaving it would re-introduce V1 in a different file once Op Stds v10 §2 codifies the contract. ~3 lines of new logic plus output-shape updates.

### Hybrid live verification

- **Skipped the explicit `smoke_test_alert_dedupe.py` reseed step** in the (a) path. The existing `::uncaught_exception` state entry was already in the seed-equivalent state (expired, `summarized=false`, `suppressed_count=4`). Re-running the seeder would have OVERWRITTEN that entry with a fresh 60-min-future window, destroying the verification surface for "deferred entry fires on ACTIVE re-run."
- **Forensic state-file injection for (b) MAINTENANCE defer test.** State was empty after (a) consumed the existing entry; could not seed a new expired entry in real time (`record_fire` opens a 60-min-future window). Hand-wrote a synthetic entry directly to `~/its/state/alert_dedupe.json` with `window_ends_at` in the past + `summarized=false` to exercise the defer path through `python3 scripts/watchdog.py`. Not a production code change — it's the only way to compress 60 min of natural test setup into <1 sec.

## Open items handed off

- **Op Stds v10 §2 amendment** (planning-project doc cascade): codify Check G's MAINTENANCE carve-out. Suggested sentence: *"Check G respects MAINTENANCE via the `alerts_suppressed` parameter — summary emails defer during the window; state entries persist; first post-MAINTENANCE sweep fires the deferred digest. Phase-2 deletion proceeds during MAINTENANCE because it has no push side-effect."*
- **Op Stds v10 §3 amendment** (alert-routing dedupe full design closeout): now that PR α + PR β + Check G MAINTENANCE behavior are all shipped and verified in-prod.
- **Doc drift chore PR** identified by the sanity audit: CLAUDE.md stub/real table missing rows for `alert_dedupe.py` and `picklist_sync.py`; watchdog row says "5 of 6 checks" (actually 6 of 7 with E deferred); `docs/picklist_sync.md` lines 116 & 139 broken cross-ref to nonexistent "Smartsheet UI-only constraints" entry; `docs/tech_debt.md` "Alert-dedupe state file grows unboundedly until PR β lands" still `[OPEN]` despite PR β shipped; README test count stale (says 137→364, actually 663); memory `project_phase1_status.md` enumeration stops at PR #38 (now PR #52).
- **PR #40 lacks a session log** but was a doc-only chore the brief didn't require one for; acceptable gap.

## What was NOT touched

- `shared/box_client.py` — OAuth flow shipped PR #39, untouched this session.
- `shared/graph_client.py`, `shared/anthropic_client.py`, `shared/keychain.py` — no changes.
- `safety_reports/*` — Phase 1 workstream still pending Q4/Q5/Q6/Q8 mirror inspection.
- `shared/scheduling.py` — chain-override fetcher still stubbed per planning decision D-i.1a.
- `box_migration/*`, `smartsheet_migration/*` — preservation layer untouched per §14.
- `tests/test_capability_gating.py` — registry intentionally unchanged; `run_picklist_sync.py` is neither generation nor send (writes to system-of-record only, no AI step, no customer email).
- Watchdog Check E (Anthropic spend trend) — still deferred to Phase 1.5; no Admin API key provisioned.
- All planning-project docs (Foundation Mission, Op Stds, Permissions Ask, Handover Plan, Excellence Roadmap, V&R, Foundation Scaffold) — reserved for separate operator-led cascades.
- README.md, CLAUDE.md, MEMORY.md — left at start-of-session state pending the doc-drift chore PR identified by the audit (deliberate; not in-session scope).

## Lessons captured to memory

- `project_phase1_status.md` — updated `error_log.py` paragraph to reflect dedupe shipped (PR #42) and noted PR β queued status (later closed by PR #44). Added `alert_dedupe.py` bullet. NOT updated: test counts, PR enumeration in frontmatter, watchdog Check G coverage — these are git-history-derivable and will be refreshed in the doc-drift chore PR rather than ad-hoc here.
- No new feedback memories added this session. The "audit-found-V1" pattern reinforces an existing lesson (`feedback_verify_ci_diagnosis_before_fix`) about not trusting prescribed fixes without verification — but the existing memory already captures the principle; no new entry needed.
- The SDK-vs-live body-shape iteration (PRs #47–#49) was costly enough to warrant a class-of-bug entry in `docs/tech_debt.md` (filed) plus the integration-test pattern as the canonical mitigation. This is captured at the project level (tech_debt) rather than memory because it's a per-module engineering practice, not a user/feedback preference.
