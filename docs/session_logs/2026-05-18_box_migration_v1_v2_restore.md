# 2026-05-18 — restore parse_job v1 + v2 (cascade dependency for v3)

Tiny chore PR: commits `box_migration/parse_job.py` (v1) and
`box_migration/parse_job_v2.py` (v2) so that `box_migration/parse_job_v3.py`
becomes importable. Without these, the v3 parser landed at `4b3e5c0`
(2026-05-17) on `main` in a broken state — its top of file does
`from parse_job_v2 import (...)` against modules that didn't exist
anywhere in the repo.

No code changes to v1, v2, or v3. Files copied verbatim from
`~/Downloads/`, where the user retrieved them from the planning Claude.ai
project. Op Stds v8 §14 preservation-over-refactor.

## How the break went unnoticed

`parse_job_v3.py` is in `box_migration/` (a side directory for the Box
folder restructuring workstream, not part of the ITS execution layer),
so no `shared/` consumer imports it. The earlier F841 ruff fix to v3
(commit `1fd6751`, the entry that was already CLOSED in `tech_debt.md`
during today's Phase A) was a static-analysis pass — ruff parses but
does not import, so the missing dependency never surfaced.

The break revealed itself today only when the operator asked Claude
Code to "reconcile" v3 against the 10 active-portfolio Box listings in
`~/Downloads/Box_listings_for_Seth/`. The first `python -c "from
box_migration import parse_job_v3"` raised `ModuleNotFoundError: No
module named 'parse_job_v2'`. Discovery via verify-before-fix; same
discipline as Phase A this morning.

## Cross-check before commit

Did a wishlist-vs-defines audit so we'd know the cascade actually
resolves without trying it. `parse_job_v3.py` imports ~30 named symbols
from `parse_job_v2`; every one is either:

- defined in v2 directly (e.g., `ClosedFolderParse`, `parse_closed_folder`,
  `TEMPLATE_1111A_*`, `BOS_*`, `KNOWN_VENDORS_SEED`, `ChaosFlag`), or
- re-exported by v2 from v1 (the "v1 types" section: `JobIdKind`,
  `FolderKind`, `ParsedFolder`, `PRIORITY_PREFIX`, `MODERN`, `LEGACY`,
  `RANGE`, `SUBJECT_KEYWORDS`, `UTILITY_NAMES`, `SHARED_NAMES`,
  `TEMPLATE_PATTERN`).

Then ran `python -c "import parse_job_v3"` from inside `box_migration/`
and confirmed clean import. `Schema` enum reports all 11 values — 7
v2-era plus the 4 v3 active-side additions (`active_portfolio_modern`,
`active_development`, `active_single_project`, `active_hybrid`).

## Path-resolution quirk worth knowing

Imports inside this directory are top-level (`from parse_job_v2 import …`,
not `from .parse_job_v2 import …`), so they only resolve when
`box_migration/` itself is on `sys.path`. The import test above worked
because `cd box_migration && python -c "import parse_job_v3"` puts the
current directory at the head of `sys.path` by default.

Any reconcile harness or downstream caller will need to either:

1. Live inside `box_migration/`, or
2. Prepend `box_migration/` to `sys.path` at startup, or
3. Be refactored to use relative imports (`from .parse_job_v2 import …`).

Picking #3 would touch all three files and breaks the
preservation-over-refactor principle for code that landed elsewhere.
Default to #1 (the reconcile harness lives in `box_migration/`).

## Gates

- `ruff check box_migration/parse_job.py box_migration/parse_job_v2.py`
  clean. Existing per-file-ignores
  (`box_migration/* = ["I001", "F401", "UP042", "UP045"]`) cover the
  conventions used by v1 and v2.
- `pytest` — 184 passed, 2 skipped (same as post-PR-#11 baseline).
  No test imports `box_migration/*`, so adding the files cannot cause
  a regression; the run confirms it.
- `mypy` not run on these — `box_migration/*` was never under the mypy
  scope. If we add it later, expect a meaningful pass of type-cleanup
  work; v3 in particular leans on `Optional[T]` in older-style
  annotations.

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26063440645](https://github.com/SolutionSmith-debug/its/actions/runs/26063440645) | `9b5cbfd` (PR #12) | green (33s) |

## What's NOT in this PR

- Reconcile harness — separate feature branch, lands after this merges.
- Coverage report — same.
- Any parser patches — same.
- `box_migration/` test coverage — the v1/v2/v3 cascade has a private
  `TEST_CORPUS` and `_run_corpus` smoke-runner inside v2, but no
  `tests/test_parse_job*.py`. Building one is a larger scope discussion
  best had after we see what the reconcile turns up.

## What's NOT touched

- v1, v2, v3 source — no edits. Files committed verbatim from Downloads.
- `pyproject.toml` — existing `box_migration/*` per-file-ignores already
  cover the new files; no entries added.
- `shared/`, `tests/`, `scripts/` — unrelated; this PR is strictly
  box-migration-side.

## Followup that becomes possible

With the cascade restored, the original "reconcile parse_job_v3 against
the 10 active-portfolio Box listings" task is unblocked. Plan:

1. Build `box_migration/reconcile_box_listings.py` that walks every
   line in `~/Downloads/Box_listings_for_Seth/files__*.txt` and
   `folders__*.txt`, runs the appropriate v3 parser entry points
   (`parse_folder`, `parse_active_subjob`, `parse_portfolio_subject`,
   etc.), and tallies coverage per portfolio.
2. Output a markdown report identifying unrecognized paths and chaos
   classifications.
3. Patch v3 to close the gaps, with new test corpus entries upstreamed
   into v2's `TEST_CORPUS`.

The Box listings stay in `~/Downloads/` per the operator's earlier
decision (don't commit customer portfolio names into git history); the
reconcile harness takes the path as a CLI arg or env default.
