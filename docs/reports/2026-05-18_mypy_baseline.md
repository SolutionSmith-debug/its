# mypy baseline inventory — post-PR-#15 (2026-05-18)

Eight `mypy .` errors at `HEAD = 3a97061`. This report classifies each
against historical snapshots so the next mypy-related decision (in-CI
vs local-only) starts from a complete inventory rather than the
narrower-scope undercount the 2026-05-18 sanity-check brief assumed.

## Methodology

For each of the 8 current errors, ran `mypy .` at four historical
commits and noted presence/absence + line number:

- `1d7cb80` — start of 2026-05-18 work (pre-PR-#9)
- `343b84b` — post-PR-#11 (pre-#12)
- `9b5cbfd` — post-PR-#12 (pre-#13)
- `89f7bf7` — post-PR-#14 (pre-#15)
- `3a97061` — HEAD (post-#15)

"First seen" = oldest snapshot where the error appears.

## Lifecycle table

| # | Error (truncated) | File:Line | First seen | Status |
|---:|---|---|---|---|
| 1 | `Skipping analyzing "msal"` | `scripts/smoke_test_graph.py:32` | pre-1d7cb80 | pre-existing baseline |
| 2 | `Library stubs not installed for "requests"` | `scripts/smoke_test_graph.py:33` | pre-1d7cb80 | pre-existing baseline |
| 3 | `Need type annotation for "matched"` | `box_migration/parse_job_v3.py:767` (was 692) | pre-1d7cb80 (line 692) | pre-existing baseline; line shifted +75 by PR #13's `parse_subsubject` addition |
| 4 | `Argument "body" to "api" has incompatible type` | `smartsheet_migration/ss_api.py:79` | pre-1d7cb80 | pre-existing baseline |
| 5 | `Item "None" of "Match[str] \| None" has no attribute "group"` | `box_migration/reconcile_box_listings.py:127` | post-9b5cbfd (PR #13) | **introduced by PR #13** — mine |
| 6 | `Need type annotation for "warnings"` | `smartsheet_migration/migrate_fl.py:176` | pre-1d7cb80 | pre-existing baseline |
| 7 | `Skipping analyzing "smartsheet.exceptions"` | `tests/test_smartsheet_client.py:16` (was 15) | pre-1d7cb80 (line 15) | pre-existing baseline; line shifted +1 by PR #11's `import logging` addition |
| 8 | `Skipping analyzing "smartsheet"` | `tests/test_smartsheet_client.py:16` (was 15) | pre-1d7cb80 (line 15) | pre-existing baseline; same shift as #7 |

## Composition summary

- **7 pre-existing-baseline** errors carried in from before today's session.
- **1 introduced by PR #13** (`reconcile_box_listings.py:127`) — my own
  miss, captured in PR #15's close-out as queue item #4. Fixed in
  Task 3 of this same followup session, not added as a tech_debt
  entry (action and resolution land together).

## Net delta across today's PRs

| PR | Net mypy effect | Reason |
|---|---|---|
| #9 (kill_switch + seed_its_config) | 0 | Code added under per-file-ignores / lazy paths |
| #10 (CLAUDE.md refresh) | 0 | Docs only |
| #11 (error_log + 404 filter) | 0 | Line numbers shifted but no error added or removed |
| #12 (parse_job v1+v2 restore) | −1 | Resolved `parse_job_v3.py:62 parse_job_v2 import-not-found` |
| #13 (parse_subsubject + reconcile) | +1 | New `reconcile_box_listings.py:127 None.group` |
| #14 (reconcile follow-up) | 0 | Docs only |
| #15 (sanity-check sweep) | −1 | Resolved `smartsheet_client.py:221 get_setting return type` |

Day net: **−1 (started 9, ended 8)**.

## Why the brief said "5"

The 2026-05-18 sanity-check brief's mypy baseline of 5 errors was
quoted from earlier session logs that ran `mypy shared/ scripts/
tests/` — a narrower scope that excluded `box_migration/` and
`smartsheet_migration/`. Those two directories contributed 3 of the
9 errors at the time the brief was drafted (`parse_job_v3 matched`,
`ss_api body arg`, `migrate_fl warnings`). The narrower scope was a
reasonable working choice for the shared/ refactor work; this report
adopts the broader `mypy .` scope as the canonical baseline going
forward.

## Tech_debt entries added in the same commit

Three new `docs/tech_debt.md` OPEN entries, one per pre-existing
error not yet captured elsewhere:

- `parse_job_v3.py: matched type annotation [OPEN]`
- `smartsheet_migration/ss_api.py: api body arg type mismatch [OPEN]`
- `smartsheet_migration/migrate_fl.py: warnings list type annotation [OPEN]`

The four import-untyped errors (msal, requests, smartsheet ×2) are
captured collectively under a single new entry,
`mypy: import-untyped noise from vendor SDKs without stubs [OPEN]`,
since they share root cause and fix (either install `types-*` stubs
where available or add `[[tool.mypy.overrides]]` blocks in
`pyproject.toml`).

The reconcile_box_listings error is intentionally NOT added —
Task 3 of this same session is fixing it; tech_debt would be
duplicate bookkeeping.

## Recommendation for the mypy-in-CI decision (input, not commitment)

The 8 errors split cleanly into two cohorts:

1. **4 import-untyped from vendor SDKs.** Trivially fixable with a
   one-time `pyproject.toml` override block. No ongoing cost.
2. **3 preservation-code errors + 1 my-own (now fixed).** Real
   tech debt but contained to `box_migration/` and
   `smartsheet_migration/`, both governed by preservation-over-
   refactor (Op Stds v8 §14).

If mypy lands in CI as non-blocking annotations, the 4 vendor
import-untyped will be persistent noise unless silenced. Silencing
them first (as a separate small chore) is probably the prerequisite
to a clean CI integration. Otherwise the signal-to-noise ratio is
poor and the warnings will be ignored.

This report doesn't pre-commit to the in-CI decision; that's a
separate call.
