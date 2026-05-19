# 2026-05-18 — kill_switch reads ITS_Config; initial seven-row seed

Refactors `shared/kill_switch.py` from a stub returning hardcoded ACTIVE to
a real read of `system.state` from ITS_Config via
`shared.smartsheet_client.get_setting`. Ships alongside
`scripts/seed_its_config.py`, which idempotently populates the seven seed
rows from Handover v5 §ITS_Config. Bundled per the locked brief — the seed
and the consumer that depends on it land together so the smoke test can
exercise both fail-open (pre-seed) and happy path (post-seed) in one
session.

## Commits landed

| SHA | Title | Purpose |
|---|---|---|
| (this commit) | feat(shared): kill_switch reads system.state from ITS_Config; seed initial config | The refactor + the seeding script + tests + this log |

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26059482891](https://github.com/SolutionSmith-debug/its/actions/runs/26059482891) | `1dc8cbb` | green (28s) |

Local gates:
- `ruff check` clean on all five new/edited files.
- `pytest` green: 160 passed, 2 skipped (was 137 + 2 baseline → +23 new
  tests across `test_kill_switch.py` and `test_seed_its_config.py`;
  `test_helpers.py` got one line mocked).
- `mypy` clean on the three new source files (`shared/kill_switch.py`,
  `scripts/seed_its_config.py`, `scripts/smoke_test_kill_switch.py`).
  The 5 mypy errors in the baseline (4 import-untyped, 1 in
  `shared/smartsheet_client.py:209`) all pre-date this PR — verified by
  stashing my changes and rerunning mypy on `main`.

## Decisions made during session

- **Fail-open distinguishability via three separate WARN messages.**
  `check_system_state()` returns `SystemState.ACTIVE` on any of three
  modes — Smartsheet unreachable / row missing / value not in enum — and
  logs a distinct message for each. Per Op Stds v8 §1 the system must
  never silently halt on a config read failure; the morning log scan needs
  to be able to tell *which* mode tripped without guessing. Distinct
  substrings (`"read failed"`, `"row missing"`, `"invalid value"`) are
  asserted in `test_kill_switch.py`.
- **WARN goes through `shared.error_log.log()`, not `@its_error_log`.**
  The decorator wraps caller `main()` functions for uncaught exceptions;
  the kill switch's fail-open paths are *handled* exceptions inside a
  shared library, so direct `log()` is right. `log()` is local-file-only
  today and auto-promotes to ITS_Errors when the error_log Smartsheet
  write lands in the next PR.
- **Reviewer chain Value pulled live from `shared.defaults.DEFAULT_REVIEWER_CHAINS`.**
  The brief explicitly called out that some planning docs have wrong
  emails (missing last-initial suffixes). Sourcing the value from
  `shared.defaults` at seed time guarantees parity between the seed and
  the in-repo default fallback. `test_reviewer_chain_value_roundtrips_to_defaults`
  asserts the JSON round-trip equals the canonical dict so this can never
  drift silently.
- **STALE rows surfaced, never overwritten.** When the seed script sees
  an existing key with a divergent value, it flags it as STALE in the
  dry-run plan and refuses to overwrite. The operator's intent is
  ambiguous in that case (manual edit? prior typo? half-finished migration?)
  — refusing keeps the seed strictly additive. Confirmed by
  `test_main_stale_sheet_does_not_overwrite`.
- **Key match is case-sensitive with no whitespace trim.** A `Setting`
  named `"system.state"` does not match `"System.State"` or `" system.state"`.
  This is deliberate — Smartsheet picklists are case-sensitive, and a
  whitespace-tolerant match would mask data-entry errors that the operator
  should see and resolve. Documented in the module docstring and asserted
  by `test_classify_setting_match_is_case_sensitive`.
- **Patched the existing helpers test rather than leaving it as a live
  integration test.** Before the refactor,
  `tests/test_helpers.py::test_check_system_state_returns_enum` exercised
  the hardcoded-ACTIVE stub. After the refactor it silently became a
  live Smartsheet call (visible via the `smartsheet/session.py` SSL
  deprecation warning during pytest). Added a one-line mock to keep it a
  unit test. Full kill-switch coverage now lives in
  `tests/test_kill_switch.py`; the helpers entry stays as a thin
  smoke-of-the-import sanity check.
- **Build-and-pass design for seed_its_config.** `_build_seed_rows` is a
  pure function; `classify` is pure; `main` is the only impure boundary.
  Lets the test suite cover both build correctness (the JSON round-trip
  test) and the classify branches (empty / matching / stale / workstream-
  scoped / case-sensitive) without going near the Smartsheet client.

## Live smoke scope (to run by hand, in order)

```bash
# 1. Pre-seed — exercises the row-missing fail-open branch.
python scripts/smoke_test_kill_switch.py
# Expect: state=ACTIVE; log tail shows
#   WARN  shared.kill_switch  system.state row missing in ITS_Config — defaulting to ACTIVE

# 2. Seed.
python scripts/seed_its_config.py
# Expect dry-run plan: 7 ADDED. Confirm y; summary: "Added 7 / Skipped 0 / Stale 0"

# 3. Post-seed — exercises the happy path.
python scripts/smoke_test_kill_switch.py
# Expect: state=ACTIVE; no new WARN.

# 4. Idempotency.
python scripts/seed_its_config.py
# Expect dry-run plan: 7 SKIPPED, 0 ADDED, 0 STALE; no y/N prompt
# (nothing to write); returns without calling add_rows.
```

## Open items handed off

- **Smartsheet 404-on-stdout noise persists.** Carried from the
  smartsheet_client session log — the SDK prints raw 404 JSON to stdout
  before our translation runs. The kill_switch fail-open path on
  pre-seed will trigger this. Slated for the next PR (error_log
  Smartsheet wiring) along with the SDK-level silencing pass.
- **Pre-existing mypy error on `shared/smartsheet_client.py:209`** —
  `get_setting`'s return annotation is `str` but the implementation
  returns `rows[0].get("Value")` which is `Any | None`. Not introduced
  here, not in scope for this PR; will get a typed-cast fix in the
  next pass.
- **`system.heartbeat_url`** seeded with the placeholder string
  `PLACEHOLDER_uptimerobot_heartbeat_url`. Operator needs to swap in the
  real UptimeRobot URL before the watchdog wires up to read it. Until
  then, watchdog should treat that placeholder value as "no heartbeat
  configured" rather than emitting it as an HTTP target.
- **`docs/tech_debt.md` DATETIME and AUTO_NUMBER entries** are unrelated
  to this PR and should be retained.

## What was NOT touched

- `shared/error_log.py` — `log()` and `@its_error_log` unchanged. The
  Smartsheet ITS_Errors write path lands in the next PR.
- `shared/smartsheet_client.py` — unchanged. `get_setting()` was already
  shaped for this consumer last session.
- `shared/sheet_ids.py` — unchanged. `SHEET_CONFIG` was already
  defined.
- `shared/defaults.py` — read-only consumption only. `DEFAULT_REVIEWER_CHAINS`
  stays the source of truth.
- `shared/review_queue.py`, `shared/quarantine.py`,
  `shared/scheduling.py` real fetchers — separate follow-on PRs.

## Lessons captured to memory

None this session. The decisions taken here are PR-local (fail-open
distinguishability, JSON-from-defaults seeding, STALE-not-overwrite) and
live in this log rather than persistent memory. The "preservation over
refactor" rule was honored — the helpers test patch is the smallest
possible change to preserve isolation, not a broader refactor.
