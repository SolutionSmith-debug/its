# 2026-05-18 — sanity-check sweep

Coordinated chore-sweep PR closing five findings from the 2026-05-18
post-PR-#14 sanity-check audit. Three commits on `main` plus one
operational live-row sync plus one new `docs/tech_debt.md` entry. No
behavioral changes to production code paths; all edits mechanical and
reversible.

## What's in this PR

| Commit | Subject | Files |
|---|---|---|
| `ed706d4` | chore(docs): refresh canonical doc-version pointers + CI run backfill | 16 |
| `c787435` | docs(claude-md): refresh stub/real table — error_log, scheduling, anthropic_client | 1 |
| `f173703` | fix(smartsheet_client): narrow get_setting return type to str \| None | 2 |

Plus, in this session-log commit:
- `docs/tech_debt.md` — new `smartsheet_migration: import-time side effects in three scripts [OPEN]` entry covering finding M4.

Out-of-band (no commit):
- Task 3 — live `ITS_Config` row sync for the
  `safety_reports.external_send_gate` Description.

## Verify-before-fix findings

Brief premise vs. reality, surfaced during preflight per Op Stds v8 §13.

- **Task 5 brief said all 3 target session logs lack the `## CI runs`
  section.** Reality: `2026-05-18_kill_switch_and_config_seed.md`
  already had the section with a `Pending push.` placeholder (line 20,
  written when the PR was opened before CI completed). Treated as
  in-place text replacement rather than section addition. Same end
  result, different mechanic. The other two logs (`doc_cleanup.md`,
  `error_log_smartsheet_write.md`) lacked the section entirely; both
  got new headings.

- **Brief mypy baseline of "5 errors" did not match reality.** The
  audit and brief both quoted 5, but `mypy .` (broader scope than
  `mypy shared/ scripts/ tests/`) reports 9 errors pre-sweep, 8
  post-sweep. Net drop of 1 still matches the intent (Task 4
  fixes one error). The discrepancy comes from `box_migration/` and
  `smartsheet_migration/` having their own pre-existing baseline
  noise that didn't show up in the narrower scope the audit used.
  Composition recorded in Commit 3's message.

- **Brief Task 2 substitution table omitted `Handoff v3 → Handoff v4`.**
  `smartsheet_migration/migrate_fl.py` lines 44 and 123 cite
  "Handoff v3 §3" and "Handoff v3" for column-index and subtotal-row
  behavior. Skipped per the brief's "only edit matches that hit the
  table" guidance. These are historical-content citations — the §3
  number and behavioral claims describe what was in the v3 doc at
  the time the migration ran. Mechanically updating to "v4" without
  re-verifying the corresponding section in v4 would silently revise
  history of what informed that code. Preserved.

- **`shared/anthropic_client.py` Task 1 row label.** Brief said label
  should be "Working, unconsumed | ... No production consumers yet —
  first generation script (safety_reports weekly_generate.py) will be
  the integration test. No dedicated test file." Applied verbatim.
  The "unconsumed" framing is more accurate than "Working" alone — at
  audit time, grep confirmed zero importers in the codebase (the
  apparent matches were inside a docstring example and three
  commented-out test-capability-gating entries).

## mypy baseline before/after Task 4

Per `mypy .` against the full repo, output filtered to the count line:

```
Before:  Found 9 errors in 7 files (checked 52 source files)
After:   Found 8 errors in 6 files (checked 52 source files)
```

Net delta: −1 (the `shared/smartsheet_client.py:221` `get_setting`
return-type error, dropped). Composition of remaining 8 errors:

1. `scripts/smoke_test_graph.py:32` — `msal` import-untyped (vendor SDK without stubs)
2. `scripts/smoke_test_graph.py:33` — `requests` library stubs not installed
3. `box_migration/parse_job_v3.py:767` — `matched` variable needs annotation
4. `smartsheet_migration/ss_api.py:79` — `body` arg type mismatch
5. `box_migration/reconcile_box_listings.py:127` — `Match[str] | None` `.group` access (my own code from PR #13; the ternary guard handles it at runtime but mypy doesn't narrow through the ternary)
6. `smartsheet_migration/migrate_fl.py:176` — `warnings` needs annotation
7. `tests/test_smartsheet_client.py:16` — `smartsheet.exceptions` import-untyped
8. `tests/test_smartsheet_client.py:16` — `smartsheet` import-untyped

All pre-existing baseline; nothing introduced by this sweep.

## Task 3 — live ITS_Config row sync

Inline one-shot Python via `python << 'EOF'` (not committed). Updated
the Description cell on `ITS_Config` row `2660940342820740` for the
`safety_reports.external_send_gate` setting:

```
Before: 'External send gate mode. MANUAL = human approval required for
         every send (Foundation Mission v5 Invariant 1).'
After:  'External send gate mode. MANUAL = human approval required for
         every send (Foundation Mission v6 Invariant 1).'
```

Verified by re-reading the row immediately after `update_rows()` —
new value present.

**Why this was needed:** `scripts/seed_its_config.py:84` originally
held the `v5` string (correct at write time, since PR #9 landed before
PR #10's CLAUDE.md refresh established `v6`). When `seed_its_config.py`
got its `v6` substitution in Commit 1, the code matched reality but
the live Description still held the `v5` string. Re-seed would
classify the row as `SKIPPED` (Value match) and leave Description
untouched — `classify()` only compares Value, not Description. One-shot
sync was the surgical fix.

## Tasks skipped or scope-reduced

- **`smartsheet_migration/migrate_fl.py` Handoff v3 refs** — skipped
  per verify-before-fix above. Captured here as a deliberate choice;
  not added to tech_debt because the references are correct as
  historical citations.
- **Test file for the import-time-side-effect fix** — drop-in test
  snippet included in the new `tech_debt.md` entry for M4 but not
  committed as a real test file. Including it would have required
  applying the fix (otherwise the test would fail), which is
  out-of-scope per the brief's narrow Task 4 framing.

## Open items handed off

- **`smartsheet_migration/` import-time side effects (M4).** New
  `docs/tech_debt.md` OPEN entry added in this PR with regex sketch,
  affected files, suggested fix, drop-in test snippet, and expected
  delta. Status: focused follow-up PR; low priority because the
  scripts are one-off migration tools; worth bundling with any other
  `smartsheet_migration/` touch.

- **`shared/anthropic_client.py` test coverage (M3).** Captured in
  Commit 2's CLAUDE.md row label change ("Working, unconsumed | ...
  No dedicated test file"). Defer until the first real consumer
  (`safety_reports/weekly_generate.py`) lands; that script will be
  the integration test driver.

- **`reconcile_box_listings.py:127` mypy narrow.** My own miss from
  PR #13. The ternary's `if PORTFOLIO_FILE_RE.match(p.name)` check
  protects runtime but mypy doesn't propagate that narrowing through
  the `.group(1)` call site. Tiny fix — assign the match to a local
  then check truthiness — but out of scope for this PR. Captured in
  the mypy baseline composition above.

- **mypy-in-CI decision.** `.github/workflows/ci.yml` currently runs
  `ruff` + `pytest` only. Mypy drift is silent — only catches when
  someone runs it locally. Two paths:
  1. Add mypy as a non-blocking CI step (annotations / warnings only)
     so drift is at least visible in PR checks.
  2. Document mypy as local-only in `CLAUDE.md` "## Operational
     conventions" and treat the baseline-error list as the contract.
  Separate decision; not made in this PR.

## What was NOT touched

- `box_migration/*` — per-file-ignores in `pyproject.toml` hold. The
  138 `person_tag_in_subject` over-match in `tech_debt.md` stays OPEN.
- `tests/test_capability_gating.py` `GATED_SCRIPTS` / `SEND_SCRIPTS`
  lists — empty until `weekly_generate.py` + `weekly_send.py` land.
  Doc-version ref at line 1 was updated as part of Task 2.
- `safety_reports/weekly_summary.py` removal — legacy file is the
  launchd plist reference until the two-process refactor lands.
  Bumped its `v4` ref to `v6` in Task 2; kept the file.

## Sequencing context

Lands directly after PR #14 (`89f7bf7`). The audit that surfaced these
findings was performed against `89f7bf7` as `HEAD`. Verified PR #14's
scope before starting this sweep — `docs/tech_debt.md` and
`docs/session_logs/2026-05-18_box_migration_reconcile.md` only;
no overlap with Tasks 1-5.

This is the seventh PR landing today and the third chore PR in the
post-feature cleanup pattern (after PR #10 and PR #14). Closes the
2026-05-18 work session's audit loop.
