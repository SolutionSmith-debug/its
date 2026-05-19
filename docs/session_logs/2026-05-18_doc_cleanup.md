# 2026-05-18 — doc cleanup: CLAUDE.md doc-version refs and stub/real table

Phase A of the three-phase doc-cleanup / SDK-404-investigation / error_log-
Smartsheet-write plan. Doc-only chore; intentionally separated from the
Phase C feature PR (different commit types, different rollback semantics).

This session is also a worked example of the verify-before-fix discipline —
the original brief operated on two stale premises that pre-flight reading
exposed, and the work scope adjusted before code landed.

## What changed in tech_debt.md

Nothing. The brief instructed me to close the F841 entry for
`parse_job_v3.py:659`. Pre-flight read of `docs/tech_debt.md` showed the
entry is already CLOSED (line 7), dated `2026-05-17`, referencing commit
`1fd6751` — the exact closure metadata the brief said to add. The brief
was working from a snapshot of the Cascade Update text that pre-dated
the earlier ruff/doc-refresh session's close. No-op confirmed; file
untouched. DATETIME and AUTO_NUMBER entries remain OPEN as instructed.

## What changed in CLAUDE.md

Two kinds of edits, both narrow:

**1. Canonical doc-version pointers — 4 versions across 3 sites.**

| Location | Was | Now |
|---|---|---|
| Line 24 (canonical-docs sentence under "Architectural model") | Foundation Mission v5, Operational Standards v7, Vision & Roadmap v6, Handover Plan v4 | Foundation Mission v6, Operational Standards v8, Vision & Roadmap v6.1, Handover Plan v5 |
| Line 31 (heading "## System-wide invariants") | (Foundation Mission v5) | (Foundation Mission v6) |
| Line 180 (closing reminder paragraph) | (Foundation Mission v5, Operational Standards v7) | (Foundation Mission v6, Operational Standards v8) |

The brief listed 9 doc-version pointers to refresh; only those 4 families
(Foundation Mission, Op Stds, V&R, Handover Plan) appear in CLAUDE.md at
all. The other 5 — Smartsheet Handoff v4 + System+HR v5, Permissions Ask
v3, Project Organization & Descriptions v5, Excellence & Productization
Roadmap v1.1, Foundation Scaffold Update v5 — are not referenced in
CLAUDE.md, so there is nothing to update for them. The brief author
appears to have assumed CLAUDE.md contained a single canonical-doc-
versions table; it does not. Adding such a table is a separate, larger
edit and was not in scope for this chore.

**2. "What's stubbed vs. real" table — two rows flipped to Working.**

| Row | Was | Now |
|---|---|---|
| `shared/kill_switch.py` | Stub (returns ACTIVE) — read-by-Setting refactor lands with smartsheet_client.py wiring | Working, tested — reads system.state via smartsheet_client.get_setting; fail-open on three modes |
| `shared/smartsheet_client.py` | Stub — awaiting ITS_SMARTSHEET_TOKEN in Keychain | Working, tested — SDK wrapper with title-keyed reads/writes, typed exception hierarchy, lazy keychain-backed client |

Both flipped from real Stub status to real Working status as a direct
consequence of the two PRs that landed earlier this week (smartsheet_client
on 2026-05-18 morning; kill_switch + ITS_Config seed 2026-05-18 afternoon).

`shared/sheet_ids.py` was already listed as Working in the table (line 113)
from the 2026-05-17 evening provisioning session — no entry added.

Entries that remain unchanged because their real status hasn't changed:
- `shared/error_log.py` — still "Local file + decorator working;
  Smartsheet ITS_Errors write pending". Phase C of this plan flips it.
- `shared/review_queue.py` — still Stub.
- `shared/quarantine.py` — `is_allowlisted` working, logger still stub.
- `shared/box_client.py` — still Stub.
- `shared/scheduling.py` — partial (PTO lookup still stubbed).
- `scripts/watchdog.py`, `safety_reports/*` — still Stub / not yet created.

## Why both bundled

Both are pure doc edits with zero behavioral risk. Bundling avoids the
churn of two separate PRs for files that nobody runs and that only Claude
Code reads as launch context. The brief explicitly bundled them under
Cascade Update 2026-05-18 §7 with the same rationale. The Phase C feature
PR stays separate because it carries real rollback semantics.

## Worked example: verify-before-fix

This session is a clean example of the discipline saved in memory as
`feedback_verify_ci_diagnosis_before_fix`. The brief was the
authoritative work-item, but pre-flight reading of both `tech_debt.md`
and `CLAUDE.md` showed:

1. The F841 closure was already done — the brief was working from a
   stale Cascade Update snapshot.
2. CLAUDE.md doesn't have the doc-version status table the brief
   described — only inline references to four of the nine doc families.

The right move was to pause, surface both findings, and ask whether to
proceed narrowly (Option 1) or to also refresh the stub/real table that
my own recent PRs had silently invalidated (Option 2). Operator picked
Option 2 with a precise scope: bump the four refs that exist, flip the
two stub/real rows that have actually changed, do not touch anything
else. That is what landed.

The cost of pausing was a few minutes. The cost of not pausing would
have been either (a) inventing a doc-version status table that doesn't
exist or (b) silently leaving the kill_switch and smartsheet_client rows
stale.

## Tests / gates

No code changes — pytest is a regression check only.

- `ruff check .` clean.
- `pytest -q` — expected to remain 160 passed / 2 skipped (no behavior
  changed; only `CLAUDE.md` edited).

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26060825031](https://github.com/SolutionSmith-debug/its/actions/runs/26060825031) | `babd69f` | green (33s) |

## Open items handed off

- **CLAUDE.md doc-version coverage gap.** The 5 doc families listed in
  the brief but not referenced in CLAUDE.md (Smartsheet Handoff,
  Permissions Ask, Project Org & Descriptions, Excellence &
  Productization, Foundation Scaffold) may warrant an explicit canonical-
  docs section in CLAUDE.md so Claude Code sees them on launch. That is
  a larger restructuring decision that should be made deliberately, not
  bolted onto a doc-version refresh.
- **Phase B (SDK 404 investigation) starts next, no commit.** Phase C
  (error_log Smartsheet write + 404 filter feature PR) waits on Phase B
  findings.

## What was NOT touched

- `docs/tech_debt.md` — F841 entry was already closed; DATETIME and
  AUTO_NUMBER entries remain OPEN as instructed.
- CLAUDE.md outside the 6 edited lines — no restructure of the
  stub/real table; no edits to invariant sections, operational
  conventions, observability stack, what-not-to-do, or references.
- All source / test / script files — this PR is doc-only.

## Lessons captured to memory

None. The verify-before-fix rule that drove this session's scope
adjustment is already saved (`feedback_verify_ci_diagnosis_before_fix`);
this log serves as the worked example for that rule.
