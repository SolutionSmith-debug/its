# 2026-05-22 — Follow-on fix: transient-404 retry + GENERATION_FAILED placeholder

PR: [#65](https://github.com/SolutionSmith-debug/its/pull/65) — squash-merged at 2026-05-23T01:30:48Z (~2026-05-22 18:30 PT). Merge commit `911996572edf36387c46397c447c61ff17c5b98d`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Closes the silent-gap risk in `safety_reports/weekly_generate.py`'s per-project fence. Both 2026-05-22 smoke runs (PR #63 session log) hit the SDK in-process staleness pattern: one transient `SmartsheetNotFoundError` per run on the first project to need a fresh scaffold create. Before this PR, the affected project got zero `WPR_Pending_Review` rows — indistinguishable on Teala's queue from "project deliberately skipped." After: every active project in `PROJECT_NAME_BY_FOLDER_ID` gets exactly one row per cycle, regardless of mix (real draft / ZERO_DATA / GENERATION_FAILED).

## Purpose

Operator-facing guarantee: the **6-rows-per-week invariant**. After this PR, a weekly_generate cycle ALWAYS lands one row per active project — even when the pipeline hits a transient SDK error. The two new code paths (single-shot retry + GENERATION_FAILED placeholder) close the silent gap without forcing the durable SDK→REST swap, which stays deferred per `docs/tech_debt.md` until the retry counter shows it's needed in production.

## Pre-flight findings

- `SmartsheetNotFoundError IS a subclass of SmartsheetError` (line 60 inherits from line 48 in `shared/smartsheet_client.py`). So catching `SmartsheetNotFoundError` explicitly above the generic `SmartsheetError` branch is the correct ordering — `except` blocks evaluate in source order.
- `error_log.log` does NOT support an `extra=` kwarg (signature: `severity, script, message, *, error_code, exc_info, correlation_id`). The brief's draft `extra=` payload was folded into the message string.
- `iter_active_projects()` is pure — no I/O, just `sorted(PROJECT_NAME_BY_FOLDER_ID.items())`. No decoupling needed.
- `import time` was not yet present in `safety_reports/weekly_generate.py`. Added.
- Existing retry pattern in repo is `copy_with_lock_retry` in `scripts/migrations/box_clone_1111a_to_projects.py` — Box-specific, 30s wait, 40 attempts. Different domain and different staleness shape (Box lock vs Smartsheet SDK cache); confirmed brief's call to hand-roll the Smartsheet retry rather than pull in `tenacity` for one usage.
- `time` is stdlib, not on any capability-gating forbidden-substring list. Confirmed `tests/test_capability_gating.py` still passes after import addition.

## Code changes

| Helper                              | Lines    | Role                                                                                              |
|-------------------------------------|----------|---------------------------------------------------------------------------------------------------|
| `_process_one_project`              | new      | Pure extract-method of the existing per-project loop body. Behavior parity.                       |
| `_process_with_retry`               | new      | Wraps `_process_one_project` with single-shot retry on `SmartsheetNotFoundError` after 500 ms.    |
| `_write_generation_failed_placeholder` | new   | Writes/updates a `WPR_Pending_Review` row with `[GENERATION_FAILED: <Class>]` Notes tag. Respects existing approved-row contract. |
| `_safe_write_placeholder`           | new      | Defensive outer catch around the placeholder write — placeholder-write failure logs + continues. |
| `_run_pipeline` per-project fence   | rewrite  | Replaces inline `try/except` with `_process_with_retry` + `_safe_write_placeholder` on both `SmartsheetError` and bare `Exception` branches. |
| `RunSummary`                        | +2 fields | `drafts_failed: int = 0`, `retries_attempted: int = 0`. Documented as the watchdog signal.        |
| `RETRY_SLEEP_SECONDS = 0.5`         | new const | Doc comment cites PR #51 evidence for the ~1-second staleness window.                              |
| `import time`                       | new      | Stdlib; not capability-gated.                                                                      |

## Tests

- **7 new unit tests** in `tests/test_weekly_generate.py`:
  - `test_transient_404_retried_succeeds` — first call raises 404, retry succeeds, no placeholder.
  - `test_persistent_404_writes_failure_placeholder` — both calls raise 404, retry exhausts, placeholder writes.
  - `test_non_404_smartsheet_error_does_not_retry` — generic `SmartsheetError` skips retry entirely.
  - `test_generic_exception_writes_placeholder_without_retry` — `RuntimeError` triggers broad fence; placeholder Notes tag is `[GENERATION_FAILED: RuntimeError]`.
  - `test_placeholder_respects_existing_unapproved_row` — Notes appended, Draft Body untouched, no add_rows.
  - `test_placeholder_respects_existing_approved_row` — no write at all; INFO log entry.
  - `test_placeholder_write_failure_does_not_crash_run` — placeholder add_rows raises, run continues, two ITS_Errors entries written.
- **1 new gated integration test** in `tests/test_weekly_generate_integration.py`: `test_weekly_generate_writes_one_row_per_project_regardless_of_outcome` — runs `weekly_generate.main()` against a fresh future Monday (`2030-01-21`), asserts exactly one `WPR_Pending_Review` row per active project, deduplicates by Job, validates no missing projects + no duplicates. Cleanup deletes all 6 rows + 6 week folders.
- The 36 prior unit tests still pass unchanged — the `_process_one_project` extract is pure refactor.

## Verification

| Stage         | Result                                                                                  |
|---------------|-----------------------------------------------------------------------------------------|
| pytest -q     | **829 passed, 1 skipped, 12 deselected** (+7 from baseline 822; matches brief target exactly). |
| mypy .        | **Success: no issues found in 97 source files**.                                        |
| ruff check .  | **All checks passed!**                                                                  |
| Capability AST| `tests/test_capability_gating.py` still passes for `safety_reports/weekly_generate.py`. |
| CI            | PR #65 build #1 → SUCCESS.                                                              |

## Manual live smoke (option-b 6-rows invariant)

```
$ python -m safety_reports.weekly_generate --week-start 2030-01-21
2026-05-23T01:27:46.132451+00:00  INFO  safety_reports.weekly_generate  started
2026-05-23T01:28:23.303614+00:00  INFO  safety_reports.weekly_generate  completed
```

Resulting `WPR_Pending_Review` rows (post-run):

| Project     | body_len | Notes                                                  |
|-------------|----------|--------------------------------------------------------|
| Bradley 2   | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:27:51+00:00` |
| Rockford    | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:27:56+00:00` |
| Brimfield 2 | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:28:01+00:00` |
| Brimfield 1 | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:28:08+00:00` |
| Bradley 1   | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:28:14+00:00` |
| Huntley     | 115      | `[ZERO_DATA_WEEK] generated=2026-05-23T01:28:20+00:00` |

**6 projects, 6 rows, no missing project, no GENERATION_FAILED placeholder fired.** Invariant holds.

Notable: **no 404 fired this run** (transient pattern is sporadic — observed twice in 2026-05-22 smokes, zero times here). The retry + placeholder paths were NOT exercised live this session; they're exercised by the unit-test suite. When the transient pattern next surfaces in production, the retry/placeholder path will exercise — and `summary.retries_attempted` will increment, giving the watchdog signal for the durable SDK→REST swap trigger.

Cleanup deleted all 6 rows + all 6 `Week of 2030-01-21` folders.

## Subtleties found mid-implementation

- **`_safe_write_placeholder` wrapper.** The brief described the defensive outer catch inline (nested try/except in the fence body). I extracted it to its own helper so the fence body stays clean and the test surface is sharper — the `test_placeholder_write_failure_does_not_crash_run` test patches `add_rows` to raise and verifies the wrapper absorbs without bubbling. Same behavior; better readability.
- **Notes append vs. replace semantics for unapproved rows.** Brief said "append `[GENERATION_FAILED: ...]` to the existing Notes; do NOT touch Draft Body." Implementation reads the existing row's Notes via the placeholder helper's own `get_rows` lookup (separate from `_resolve_existing_wpr_row` which doesn't return the Notes value), appends the failure tag, and updates only the Notes column. Test `test_placeholder_respects_existing_unapproved_row` confirms the existing `[LOW_CONFIDENCE: 0.50]` tag survives intact alongside the new `[GENERATION_FAILED: ...]` tag.
- **Counter semantics.** `drafts_failed` increments on EVERY failure path (the project failed regardless of whether the placeholder physically wrote). `drafts_written` increments only when a row was added or updated (mirroring the existing counter semantics from `_handle_standard_project` / `_handle_zero_data_week`). For the approved-row-skip path: `drafts_failed += 1`, `drafts_written` unchanged. Test `test_placeholder_respects_existing_approved_row` locks this.
- **`projects_processed` was moved into `_process_one_project`.** It now increments only on success paths (real draft, ZERO_DATA placeholder, approved-skip return). Failure paths in the fence do NOT increment it — the project did not complete. Returning to the brief's instruction: "Intentionally do NOT increment `projects_processed` — this project did not complete." Confirmed via inspection of the result dict assertions across the 7 new tests.
- **Sporadic transient 404.** Two observations during 2026-05-22 smokes; zero observations during the 2026-05-22 follow-on smoke against `2030-01-21`. The pattern is real but flaky — exactly why it's a tech-debt SDK staleness signature rather than a hard SDK regression. The retry + placeholder closes the operator-visible gap; the SDK→REST swap closes the root cause.

## Out of scope (deferred)

- **SDK→REST swap on `ensure_current_week_folder` / `get_rows`.** Durable root-cause fix per tech-debt entry. Trigger condition shipped: `retries_attempted >= 3` in any 4-week window OR first user-visible `GENERATION_FAILED` placeholder in a real Friday cycle.
- **Retry on Box uploads.** Different domain — `copy_with_lock_retry` in `scripts/migrations/box_clone_1111a_to_projects.py` handles that with its own (30s × 40-attempt) policy.
- **Multi-retry loops.** Bounded to one retry per project per Op Stds v11 §30 + PR #51 evidence (~1 second SDK staleness window). Multi-retry adds latency without fixing the underlying staleness.
- **Changes to `intake.py`, `intake_poll.py`, or any other workstream.** SDK-vs-Live class-of-bug discipline applies everywhere, but other workstreams haven't observed the pattern. Preservation-over-refactor (Op Stds v11 §14).
- **`tenacity` or similar retry library.** Hand-rolled `time.sleep(0.5)` + single retry is correct for a one-shot, single-call-site case.

## Sequencing context

This PR is a follow-on to **R3 Session 2 (PR #63)** — it closes the silent-gap risk that smoke runs surfaced but the original ship didn't address. It unblocks **R3 Session 3 (`weekly_send.py`)** by ensuring `weekly_send` can rely on the 6-rows-per-week invariant when it reads `WPR_Pending_Review` rows — every active project will have a row (even if it's a placeholder), so the absence of a row no longer needs to be a special case in `weekly_send`'s logic.

After R3 Session 3 lands, the next downstream is the **Phase 1.4 pre-Customer-1 security hardening cluster** per V&R v7.2 (picklist-hardening, ITS_Trusted_Contacts, attachment screening) — all already logged in `docs/tech_debt.md`.

## Operator-side actions remaining

- **No new operator actions for this PR.** The launchd plist from PR #63 already runs `weekly_generate` Friday 14:00; the retry + placeholder code paths fire automatically when needed.
- **Watchdog signal monitoring**: when the next real Friday cycle (or post-install smoke) fires a `retries_attempted >= 1`, watch the ITS_Errors row stream for the `weekly_generate.transient_404_retry` INFO entries. If they accumulate to 3+ in a month, that's the SDK→REST swap trigger.
- **No GENERATION_FAILED row currently in WPR_Pending_Review** — cleanup of the 2030-01-21 smoke confirmed empty. First production GENERATION_FAILED row in a real Friday cycle is also a SDK→REST swap trigger (one-off failure is signal enough since it means the retry exhausted).

## Baseline state at session close

- `main` at `9119965` (PR #65 merge commit).
- pytest **829 / 1 / 12**. mypy **0 / 97**. ruff **clean**.
- safety_reports/intake_poll.py daemon: still running, still healthy (untouched by this PR).
- weekly_generate plist STILL not installed on production MacBook — the operator-side `install.sh load` from R3 Session 2 still pending. This PR doesn't change that.
- R3 Session 3 (`weekly_send.py`) remains the immediate-next critical-path target with zero new code-side prereqs. The 6-rows-per-week invariant established by this PR simplifies weekly_send's row-iteration logic.

## Tech-debt entries

Updated the existing 2026-05-22 entry "Smartsheet transient 404 on first-project sheet/folder create" — status flipped `[OPEN]` → `[PARTIALLY MITIGATED 2026-05-22]`. Mitigation language added; durable-fix trigger condition now explicit (`retries_attempted >= 3` in any 4-week window OR first real-cycle `GENERATION_FAILED`).

No new tech-debt entries surfaced during implementation.
