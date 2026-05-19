# Session log — 2026-05-19 Cascade Absorb

## Context

Absorbed the 2026-05-19 planning-side cascade into the repo. Driver: white-glove
business-model reframe (Foundation Mission v7) + 23-PR-window state catch-up.
Canonical doc set bumped to Foundation Mission v7 / Op Stds v9 / V&R v7 /
Handover Plan v6 / Excellence Roadmap v2.1 / Foundation Scaffold Update v6.

This session is a documentation-and-doc-references absorption pass; no
behavior changes. Two Phase-3 verifications were planned with the option to
add code fixes — Phase 3-A closed by discovery (the existing fix is a
logging.Filter, not a stdout redirect — the spec's prescription was wrong),
Phase 3-B added a `sys.exit` guard.

## Edits verified (made earlier in chat session via Desktop Commander)

- `shared/sheet_ids.py` — docstring rewritten to white-glove framing
  ("each customer gets a private repo forked from the blueprint"), replacing
  the old "Customer 2 onboarding becomes per-customer configuration" framing.
- `shared/defaults.py` — docstring single-line fix: "this customer" replaces
  "a new customer tenant".
- `docs/tech_debt.md` — intro paragraph: "Op Stds v9 §14" replaces "v7 §14".
- `CLAUDE.md` — product-context paragraph rewritten white-glove; canonical-doc
  list bumped to v7/v9/v7/v6 (multiple call sites); invariants section header
  bumped to FM v7; `error_log.py` row reference bumped to "Op Stds v9 §3 open";
  closing inconsistency-flag paragraph bumped; "Multi-tenant SaaS scale = move
  to PaaS" line replaced with per-customer-repo language.
- `README.md` — opening paragraph rewritten white-glove; canonical-doc refs
  bumped; Phase 1.6 row renamed "Blueprint Generalization" (was
  "Multi-Tenancy Framework"); Phase 0 status row refreshed to reflect the
  23-PR push 2026-05-18/19.

All six edits in CLAUDE.md and all five in README.md verified via grep
spot-checks; old text confirmed absent, new text confirmed present at the
expected lines.

## Edits added in this CC session

- `docs/tech_debt.md` — Mail.app rule silent disable entry [OPEN]. Carries
  forward from Foundation Scaffold v4; mitigation hook is watchdog.py's
  inbound-mail-processed-in-24h check (Excellence Roadmap v2.1 Track 1 R2).
- `docs/tech_debt.md` — PowerShell macOS Gatekeeper deprecation 2026-09-01
  entry [OPEN]. Plan B is Azure Cloud Shell; revisit date 2026-08-15.
- `smartsheet_migration/build_human_review.py` — added a `sys.exit(...)`
  runtime guard after the existing imports. The archival docstring (which is
  more specific than the spec template — records the original folder ID, the
  move targets, and the same-day workspace restructure) was preserved
  verbatim per Op Stds v9 §14. The guard makes non-re-runnability enforceable
  even if `HR_FOLDER` were re-pointed.

## Phase 3 verification findings

### Phase 3-A — Smartsheet 404 stdout filter: filter present (different mechanism than spec assumed)

The spec prescribed adding a `contextlib.redirect_stdout` wrapper because the
Smartsheet SDK "prints raw 404 JSON to stdout". Pulling the actual code per
Op Stds v9 §13 (Diagnostic Discipline) revealed the diagnosis was stale:

- The Smartsheet SDK does **not** print to stdout. It emits the response body
  through Python `logging` at ERROR level on the `smartsheet.smartsheet`
  logger before our `_translate` raises the typed exception.
- A `_Suppress404JSON` `logging.Filter` is already installed at the bottom of
  `shared/smartsheet_client.py` (lines 291–313); it inspects `record.args`
  (unformatted) to survive SDK format-string changes, and is parameterized
  on `_QUIET_STATUS_CODES = frozenset({404})` so additional codes can be
  silenced later. The mechanism is documented in the module docstring
  (lines 25–32).

If the spec's `redirect_stdout` had been applied blindly, it would have added
dead code (would suppress nothing — the SDK isn't writing to stdout) while
leaving the actual ERROR-log emission in place. Op Stds v9 §13 caught this.

No code change. No tech_debt entry needed. Closed by discovery — already
landed in the 23-PR window before this session.

### Phase 3-B — `build_human_review.py` archival header: header present, `sys.exit` guard added

The file already had a rich archival docstring (`[ARCHIVED 2026-05-17]`)
recording the exact provisioning event, the post-provisioning workspace
restructure, and the resulting non-runnability (HR_FOLDER deleted). This
docstring is more historically accurate than the spec's generic template, so
preservation-over-refactor §14 says keep it.

What was missing was the **runtime guard**. The existing docstring states the
script is non-runnable as written, but only because `HR_FOLDER` is invalid;
if someone fixed `HR_FOLDER` (e.g., copy-pasted to a new context) the script
could re-execute. Added one line:

```python
sys.exit("Archived script — do not re-run. See module docstring.")
```

immediately after the existing imports. Belt-and-suspenders runtime
protection without disturbing the existing docstring or imports. No tests
import `build_human_review.py` (verified via grep), so the hard exit at
module load doesn't break anything.

## Verification

- `ruff check .`: clean
- `mypy .`: 0 errors across 64 source files (per Op Stds v9 §28)
- `pytest -q`: 364 passed, 2 skipped

Test count is unchanged from baseline because Phase 3-A's filter was already
present (no new test added), and Phase 3-B added a `sys.exit` line whose
behavior is "not importable / not runnable" — not a unit-test target.

## Canonical doc references after this commit

- Foundation Mission v7
- Operational Standards v9
- Vision & Roadmap v7
- Handover Plan v6
- Excellence Roadmap v2.1
- Foundation Scaffold Update v6
- Permissions Ask v3 (unchanged)
- Smartsheet Handoff v4 (unchanged)
- Smartsheet System + Human Review v5 (unchanged)

## Related artifacts

- `/mnt/project/ITS_Cascade_Unification_Update_2026-05-19.docx` — master input doc
- `/mnt/project/ITS_Cascade_Audit_Errata_2026-05-19.docx` — audit trail of cascade
- `/mnt/project/ITS_Cascade_Implementation_Checklist_v2_2026-05-19.docx` — landing plan

## Next critical-path items (Excellence Roadmap v2.1 Track 1)

- R1: `box_client.py` JWT wiring (one focused session)
- R2: `watchdog.py` real checks — now includes Mail.app silent-disable check
  per `docs/tech_debt.md` entry added this session
- R3: First workstream consumer integration — blocked on Q4/Q5/Q6/Q8

## Open design question (urgent, per Op Stds v9 §3)

Alert-routing dedupe brief. Triple-fire CRITICAL operational means one event
produces three notifications; without dedupe, recurring errors hit operator
with N×3 emails. Brief needs to resolve before first workstream goes into
production-shape operation. Existing pointer is the `error_log.py` row of
the stub/real table in CLAUDE.md (line 104 references "Op Stds v9 §3 open").
