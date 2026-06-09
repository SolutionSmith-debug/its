---
type: session_log
date: 2026-06-09
status: closed
related_prs: [236, 241, 242, 244, 245]
workstream: safety_portal
tags: [safety-portal, publish-pipeline, publish-daemon, form-archive, sys-executable, publish-monitor, retire, wsr, abstract-datetime, pacific, smartsheet-schema-migration, live-mirror-op]
---

# Session log — Publish pipeline bugfix chain + WSR Approved At / Sent At datetime (PRs #236, #241–#242, #244–#245)

Afternoon session. Operator opened on a Publish Monitor screenshot showing a stack of FAILED
publish requests. Root-caused a chain of five distinct publish-pipeline bugs — archive naming
collision, bare-`python` subprocess failure in launchd, incorrect monitor step labels for Retire
operations, redundant-retire git-commit crash — then closed with an operator-requested WSR schema
change promoting Approved At and Sent At from DATE to ABSTRACT_DATETIME (time-carrying, Pacific).
Five PRs landed to main (final merge commit `2aa2061`), plus a live Smartsheet column retype in
the mirror performed outside git.

## PRs landed

### PR #236 — Key blank-form archive PDFs by definition id, not form_name (`8b29ee9`)

Two form definitions sharing the same `form_name` (a version bump or same-named variant) caused
`publish_daemon._regenerate_archive` to produce identical output filenames and the second write
silently overwrote the first. `test_form_archive`'s count assertion then failed, blocking every
such publish because `publish_daemon` gates on full-repo CI passing.

Fix: name each archived blank PDF by the unique definition id (stem), not the human `form_name`.
All archive consumers read the manifest (which stores the id→filename mapping); no caller relied
on the human-name-based path.

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #236 — four-part verify clean
- state: MERGED
- mergeCommit: 8b29ee9
- main CI on merge commit: SUCCESS

---

### PR #241 — Run the publish archive step with sys.executable, not bare "python" (`880c535`)

`publish_daemon._regenerate_archive` launched the archive generation subprocess as bare
`"python"`. launchd PATH does not include a `python` binary (only `python3`), causing a
`FileNotFoundError` at the `archived` stage — after the form had already been written live to
`catalog.json`. The form went live but the archive step then crashed, leaving publish in a
half-committed state.

Fix: replace `"python"` with `sys.executable` in the subprocess call so the subprocess inherits
the exact interpreter running the daemon. Consistent with `portal_poll.py`'s subprocess
convention and with Op Stds v16 §31 (launchd path isolation).

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #241 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-09T16:28:08Z
- mergeCommit: 880c535
- main CI on merge commit: SUCCESS

---

### PR #242 — Operation-aware publish-monitor stepper labels (Retire != Publish) (`ff249f4`)

The Publish Monitor's progress-stepper rendered the same step labels (including "Live" and
"Archived") for both Publish and Retire operations. A Retire's terminal states are "Removed" and
"Done," not "Live" and "Archived." The mislabelling caused operator confusion when reading the
FAILED stack.

Fix: exported a `stepsForOp(op)` function from the monitor module that returns operation-specific
step labels. Retire's last two steps now read "Removed" and "Done." The existing label rendering
path consumes `stepsForOp()` directly; no change to monitor state logic.

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #242 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-09T16:38:50Z
- mergeCommit: ff249f4
- main CI on merge commit: SUCCESS

---

### PR #244 — Redundant retire fails clean, not on an empty git commit (`241cd64`)

Re-retiring an already-retired form produced a no-op manifest mutation: `apply_publish` wrote the
same JSON, `git add` found nothing new, and `git commit` exited with status 1 ("nothing to
commit"), crashing the daemon with an unhandled subprocess error. The publish request stayed FAILED
permanently with no actionable error message.

Two-layer fix:

1. **`apply_publish` validation gate:** reject a Retire for a definition that is already absent
   from the live catalog, surfacing a `PublishValidationError` before any git operation. This is
   the authoritative guard — a Retire should never reach git if there is nothing to retire.
2. **`_actuate` backstop:** after `git add`, check `git diff --cached --quiet`; if the diff is
   empty, skip `git commit` and treat the step as a no-op success. Prevents the crash if any
   future caller bypasses the validation gate.
3. **`gitignore` addition:** `form_archive_out/` added to `.gitignore` so transient archive build
   artefacts are never staged accidentally.

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #244 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-09T17:02:43Z
- mergeCommit: 241cd64
- main CI on merge commit: SUCCESS

---

### PR #245 — WSR Approved At / Sent At carry time (ABSTRACT_DATETIME, Pacific) (`2aa2061`)

Operator request: Approved At and Sent At in `WSR_human_review` should display as date+time
(Pacific), not date-only. The columns were typed DATE in Smartsheet.

**Schema:** Smartsheet user columns can be ABSTRACT_DATETIME (the "Date/Time" user-facing type)
but not plain DATETIME. The `update_column` API retypes DATE → ABSTRACT_DATETIME in place
without data loss. The column accepts naive `YYYY-MM-DDTHH:MM:SS` strings and rejects ISO-8601
offsets or `Z`-suffixed values (`errorCode 5536`). Existing date-only cells coerce to midnight on
retype. All established by a live throwaway-column smoke in the mirror (Op Stds v16 §30
SDK-vs-live discipline).

**Code changes:**

- `safety_reports/wsr_review.py`: new `to_wsr_datetime()` helper — `datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")` (naive Pacific). Replaces the `[:10]` date-slice on Approved At; Sent At now similarly populated with time.
- `safety_reports/weekly_send.py`: calls `to_wsr_datetime()` for Sent At (previously date-only).
- `schemas/wsr_schema.json`: column type annotations updated from `DATE` to `ABSTRACT_DATETIME` for both columns.

**Sequencing:** operator retyped the two live mirror columns (`Approved At` col `7944658226548612`,
`Sent At` col `5129908459442052` on sheet `5035670127988612`) before merging the code change, so
the running `weekly_send_poll` daemon never wrote an offset-bearing value into a DATE column.

**Live mirror op (not in git):** `update_column` called directly against Smartsheet mirror via
the MCP to retype both columns. 2 existing rows survived (coerced to midnight). This op is not
reversible via a code rollback; the schema is now ABSTRACT_DATETIME in production mirror.

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #245 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-09T17:20:16Z
- mergeCommit: 2aa2061
- main CI on merge commit: SUCCESS

---

## Overall final state (main `2aa2061`)

four-part verify clean (all 5 PRs: state=MERGED + mergedAt + mergeCommit + main-branch CI SUCCESS)

- pytest: 1650 passed / 44 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit 2aa2061: SUCCESS

## Decisions made during session

1. **Archive PDFs keyed on definition id, not form_name (PR #236).**
   - Decision: use the unique definition id as the archive filename stem.
   - Alternative considered: keep `form_name` keying and enforce uniqueness in `apply_publish`
     validation (reject two defs with the same name).
   - Rationale: names are human-visible labels — version bumps and same-named variants are a
     normal lifecycle pattern. Keying on an opaque id (already unique by construction) is
     structurally correct; the manifest provides the id→human-name mapping for display.

2. **`sys.executable` for all daemon subprocesses (PR #241).**
   - Decision: replace bare `"python"` with `sys.executable` in `_regenerate_archive`.
   - Alternative considered: add a `python` → `python3` symlink in the launchd PATH.
   - Rationale: `sys.executable` is the canonical pattern in this codebase (Op Stds v16 §31;
     consistent with `portal_poll.py`). Relying on PATH resolution for an interpreter invoked
     from a launchd-launched process is fragile and defeats the venv isolation guarantee.

3. **Two-layer redundant-retire fix: validation gate + git backstop (PR #244).**
   - Decision: add the `apply_publish` validation guard as the authoritative rejection and the
     `git diff --cached --quiet` check as a backstop, rather than the backstop alone.
   - Alternative considered: only add the git backstop (skip commit on empty diff) and leave
     the validation gate for a future pass.
   - Rationale: the backstop prevents the crash but silently accepts a semantically invalid
     operation (retiring a form that was never live). The validation gate surfaces a meaningful
     error to the operator — "nothing to retire" — and stops the daemon from attempting git
     operations against a no-op change. Defense in depth: both layers protect from different
     failure modes (caller bypasses validation; future callers miss the gate).

4. **ABSTRACT_DATETIME, not DATETIME, for WSR time columns (PR #245).**
   - Decision: type the columns ABSTRACT_DATETIME (the Smartsheet "Date/Time" user-facing
     column type).
   - Alternative considered: DATETIME (a different Smartsheet column type).
   - Rationale: established by live smoke in the mirror — `update_column` with type `DATETIME`
     is rejected by the Smartsheet API for user-column retypes; ABSTRACT_DATETIME is the
     correct type for user-visible date+time columns. Plain DATETIME is a system-column type
     not applicable here (Op Stds v16 §30 SDK-vs-live discipline).

5. **Naive Pacific strings, no UTC offset (PR #245).**
   - Decision: write `YYYY-MM-DDTHH:MM:SS` (naive, Pacific local) rather than a UTC-offset or
     `Z`-suffixed ISO-8601 string.
   - Alternative considered: write UTC-offset strings (e.g., `2026-06-09T17:20:16-07:00`).
   - Rationale: Smartsheet ABSTRACT_DATETIME rejects offset-bearing strings with
     `errorCode 5536`. Naive strings coerce correctly; the column's timezone display is
     controlled by the Smartsheet user's sheet settings, so Pacific is the right local source
     to match the operator's display preference.

6. **Retype mirror columns before merging code, not after (PR #245 sequencing).**
   - Decision: operator retyped the live mirror columns first, then the code PR was merged.
   - Alternative considered: merge the code PR first and retype the columns after.
   - Rationale: `weekly_send_poll` runs on a 15-minute cycle against the live mirror. Merging
     the code first would create a window where the daemon writes a naive-Pacific string into
     a DATE column — Smartsheet stores the value but silently truncates the time component,
     potentially producing a CRITICAL double-send row if the cell value shifts on a re-read.
     Code-first retype closes that window. Sequence: retype → merge → daemon picks up new
     writer on next cycle.

## Open items / next session

- **Frontend guard — Retire on already-retired form:** the portal admin UI currently surfaces
  the "Retire" action for forms already in retired state; the backend now rejects cleanly
  (`PublishValidationError`). A UX improvement (disable/hide the action in the frontend when
  the form is already retired) is a cosmetic follow-on; backend is correct.
- **`README.md` idempotency doc-drift (line 111):** the safety portal README claims idempotency
  keyed on "Sent At"; the live code keys on `Send Status == SENT`. Doc should be corrected;
  low-urgency.
- **Portal deploy:** `cd ~/its/safety_portal && npm run deploy`. PRs from the prior session
  (D3 timestamp fix, A1 stable UUID, A3 prune cron) are code-merged but the Worker is not yet
  redeployed. Operator action.
- **Load the compile-now daemon (Part B):** carried from prior session — until loaded, watchdog
  Check C WARNs on the `safety_compile_now_poll` marker.
  ```
  bash ~/its/scripts/launchd/install.sh load org.solutionsmith.its.compile-now-poll
  ```
- **CSP enforce flip** (carried from `2026-06-08_admin-dashboard-audit-and-security-hardening.md`):
  still held pending a live signature-capture smoke + zero console-violation confirm.
- **Stale worktrees** (`~/its-*` from prior sessions): operator cleanup; force-delete is
  hook-blocked in CC.

## What was NOT touched

- **`~/its-blueprint`:** exec-repo-only session. No doctrine, mission, brief, or reference
  files touched.
- **Invariant 1 (External Send Gate):** `weekly_send.py` change is in the send half
  (time-formatting only; no new send capability); `intake.py` and `portal_poll.py` unchanged.
  `tests/test_capability_gating.py` confirms gating unaltered.
- **Invariant 2 (Adversarial Input Handling):** no external-content processing paths modified.
- **`compile_now_poll.py` / Orphaned Reports (Parts B + C):** completed in the prior session
  (PRs #232–#235). Not re-touched.
- **`weekly_generate.py`:** not touched this session.
- **`portal_poll.py` / intake HMAC path:** unchanged. Publish pipeline bugs are all in
  `publish_daemon.py` / `publish_monitor.py` / the archive subprocess; intake is unrelated.
- **Evergreen production tenant:** schema migration applied to the mirror only
  (`5035670127988612`). Production cutover deferred pending Evergreen go-live.

## Live ops (outside git)

- **Smartsheet column retype — mirror `WSR_human_review` sheet (`5035670127988612`):**
  - `Approved At` col `7944658226548612`: DATE → ABSTRACT_DATETIME
  - `Sent At` col `5129908459442052`: DATE → ABSTRACT_DATETIME
  - 2 existing rows survived; date-only values coerced to midnight.
  - Op performed via Smartsheet MCP `update_column` before the PR #245 merge. Not reversible
    by code rollback — the schema is now ABSTRACT_DATETIME in the mirror.

## Cross-references

- Prior session log (Part B: Compile Now + Part C: Orphaned Reports, PRs #232–#235):
  [`2026-06-09_part-b-compile-now-part-c-orphaned-reports.md`](2026-06-09_part-b-compile-now-part-c-orphaned-reports.md)
- Prior session log (Publish CI gate hardening + Part A, PRs #222, #224, #227, #228, #230):
  [`2026-06-09_publish-ci-gate-hardening-and-part-a.md`](2026-06-09_publish-ci-gate-hardening-and-part-a.md)
- `safety_portal/safety_portal/publish_daemon.py` — archive naming fix, `sys.executable`,
  redundant-retire validation gate, `git diff --cached --quiet` backstop
- `safety_portal/safety_portal/publish_monitor.py` — `stepsForOp()` export
- `safety_reports/wsr_review.py` — `to_wsr_datetime()` helper (new)
- `safety_reports/weekly_send.py` — Sent At time-carry update
- `schemas/wsr_schema.json` — ABSTRACT_DATETIME column type annotations
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- Op Stds v16 §30 (SDK-vs-live smoke discipline; ABSTRACT_DATETIME discovery)
- Op Stds v16 §31 (launchd PATH isolation; `sys.executable` convention)
- Op Stds v16 §1 (External Send Gate — no send-path capability changes)
