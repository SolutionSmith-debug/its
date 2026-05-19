# 2026-05-18 — Sentry triple-fire complete + Phase 1 critical-path unblock

Two-phase session per the brief. Phase A closed the third leg of the
Op Stds v8 §3 CRITICAL triple-fire (Sentry wiring). Phase B wired
`shared/review_queue.py` and `shared/quarantine.py` to their
respective Smartsheet sheets — the Phase 1 critical-path dependencies
that workstream code has been waiting on.

## Commits landed

| PR | SHA | Title |
|---|---|---|
| #23 | `a86840c` | feat(shared): wire _alert_critical to Sentry — triple-fire complete |
| #24 | `e0186095` | feat(shared): wire review_queue to ITS_Review_Queue |
| _(this PR)_ | _t.b.d._ | feat(shared): wire quarantine logger to ITS_Quarantine |

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26072739141](https://github.com/SolutionSmith-debug/its/actions/runs/26072739141) | `a86840c` (PR #23) | green (29s) |
| [26072950046](https://github.com/SolutionSmith-debug/its/actions/runs/26072950046) | `e0186095` (PR #24) | green (28s) |

This PR's run lands when it's pushed; URL to be backfilled on a future
chore touch per the established convention.

## Decisions made during session

### Phase A — Sentry SDK version pin

Pinned `sentry-sdk>=2.0,<3.0`. Version 2.x is current and stable;
Sentry has a habit of breaking-changes in major releases (saw the 1.x
→ 2.x transition land breaking renames around `Hub` / `scope`). Pinning
under 3.x means we deliberately re-test before a major bump. Pinning
at-least 2.0 ensures we get the modern `push_scope()` context-manager
shape used in `capture_exception`.

### Phase A — failure-isolation pattern

Refactored `_alert_critical` from a single Resend-leg function into
two helpers (`_fire_resend_leg`, `_fire_sentry_leg`) called
unconditionally and independently from the top-level function. Each
leg has its own:
- Recursion guard (`_in_resend_alert`, `_in_sentry_capture`).
- try/except with broad `Exception` catch.
- Distinguishable marker line (`[resend-alert-failed]` vs
  `[sentry-capture-failed]`).

Key guarantee verified by 4 dedicated dual-leg tests in
`tests/test_error_log.py`: failure of one leg never prevents the other
from running. The brief was explicit about this; the test names
(`test_sentry_called_even_when_resend_fails`,
`test_resend_called_even_when_sentry_fails`,
`test_both_legs_failing_still_does_not_raise`,
`test_sentry_guard_and_resend_guard_are_independent`) lock the
contract.

Order chosen: Resend first, Sentry second. Rationale in the function
docstring — Resend is the higher-stakes wake-up leg; if Sentry hangs
during init we want the operator email already out. In the happy path
both legs return in milliseconds and the order is invisible.

### Phase A — live Sentry smoke ran

`ITS_SENTRY_DSN` was in Keychain at preflight (the operator added it
before the session). Live `scripts/smoke_test_sentry.py` ran green:
SDK init, direct `capture_exception`, full `_alert_critical` path. 2
Sentry events delivered + 1 Resend email. Zero failure markers in the
local log.

Operator-side verification (Sentry web dashboard event arrival) is
hand-off per the script's docstring.

### Phase A — autouse test-isolation fixture discovery

First run of `pytest` after wiring `_alert_critical` to Sentry showed
"Sentry is attempting to send 8 pending events" — the existing
decorator-CRITICAL tests in `test_error_log.py` were firing live
Sentry events into the operator's dashboard! Same isolation gap that
surfaced when Resend was wired in PR #21.

Fix mirrored that earlier session's response: added a second autouse
fixture (`sentry_capture_mock`) parallel to `send_alert_mock`. Both
mocks fire on every test by default; the dual-leg tests fetch them
to assert against. Generalizes the pattern noted in PR #21's session
log: when a side-channel leg lands, parallel autouse mock in the
caller's test file is the right defensive shape.

### Phase B — ITS_Review_Queue schema verification

Live schema inspection found 3 deltas from the brief's documented
schema:

1. **`Reason` is PICKLIST (9 options), not TEXT_NUMBER.** Added a
   `ReviewReason` enum mirroring the live picklist:
   `low-confidence-extraction / ambiguous-classification /
   structured-output-edge / zero-data-window / mismatched-reference /
   security-trigger / policy-edge / manual / other`. Changed `add()`
   signature from `reason: str = ""` to `reason: ReviewReason =
   ReviewReason.OTHER`.

2. **`Notes` is actually `Resolution Notes`.** Operator-fill at
   resolution time — we don't write it, so the rename is invisible to
   the wiring. Documented in module docstring.

3. **Two columns the brief didn't mention:** `Severity` (PICKLIST
   matching the `Severity` enum exactly) and `Source File`
   (TEXT_NUMBER, optional). Added matching parameters to `add()`:
   `severity: Severity = Severity.WARN` and `source_file: str | None =
   None`. Reused the existing `Severity` enum from `error_log` rather
   than defining a parallel one — picklist values match exactly and
   the import is one line.

Schema drift was the same failure mode as PR #11's brief which said
"Workstream column" when live had "Script". Verify-before-fix
discipline caught it; adapted code to match live schema rather than
silently writing wrong cell values.

### Phase B — ITS_Quarantine schema (was TBD)

Live inspection documented:
- Quarantined Message (TEXT_NUMBER, primary)
- Received At (DATE)
- Sender (TEXT_NUMBER)
- Subject (TEXT_NUMBER)
- Summary (TEXT_NUMBER)
- Workstream (PICKLIST: safety_reports / po_materials / subcontracts /
  email_triage / ai_employee / **other**)
- Reviewed (CHECKBOX)
- Added to Allowlist (CHECKBOX)
- Reviewed By (CONTACT_LIST)
- Reviewed At (DATE)
- Notes (TEXT_NUMBER)

Existing `log_quarantined_message` signature (`sender, subject,
timestamp, summary, workstream`) maps cleanly. Set the primary
`Quarantined Message` cell to a short operator-facing label
(`f"quarantined: {sender}"`) mirroring the ITS_Errors `Error` column
convention.

**Subtle picklist divergence between the two sheets:** ITS_Review_Queue
uses `global` as the catch-all workstream value; ITS_Quarantine uses
`other`. Different sheets, different picklists. Locked in by
`test_log_quarantined_message_rejects_global_workstream` so a future
copy-paste from review_queue doesn't break quarantine writes.

### Phase B — no new tech_debt entries

Schema drift was adapted-to, not papered-over. None of the deltas
required schema changes to the live sheets or function-signature
compromises that would warrant tech_debt entries. The drift surfaces
are documented here in the session log for archaeology, but the
modules themselves match live reality cleanly.

### Phase B — Item ID generation choice

Chose timestamp-based format `<workstream>-<YYYYMMDD>-<HHMMSS>` in UTC
per the brief's suggestion. Rationale:
- Operator-friendly: sortable in the Smartsheet UI; humans can read
  the workstream + date at a glance.
- Stable: never regenerated; safe as a long-lived reference.
- Unique enough for sandbox volume: collisions only if the same
  workstream enqueues two items within the same second. Acceptable
  for a human-review queue where item rate is dozens per day at
  worst.
- UTC chosen (not local) for deterministic test behavior — locked in
  by `test_item_id_uses_utc`.

UUID was the alternative; rejected because UUIDs aren't operator-
readable and sortability isn't a free property.

## Open items handed off

- **Sentry web dashboard verification** — operator action. Two events
  should be visible in the ITS Sentry project's Issues view from
  smoke step 2 (direct `capture_exception`) and step 3 (via
  `_alert_critical`). The CRITICAL decorator path is also exercised
  by integration tests, but those events go to the live Sentry
  project too if the autouse mock isn't loaded (pytest with `-p
  no:mocker` would be the catastrophic case — don't run that).

- **Alert-routing dedupe across the three legs** — Op Stds v8 §3 open
  item. With triple-fire now complete and operational, the design
  question becomes "when Sentry + Smartsheet + Resend all fire on
  one CRITICAL event, how does the operator triage without three
  parallel notification streams?" Separate session.

- **Workstream consumer integration** — `safety_reports/intake.py`,
  `safety_reports/weekly_generate.py` (not yet created), etc. don't
  yet call `review_queue.add()` or `quarantine.log_quarantined_message`.
  Helpers are ready; consumers land when the workstreams build out.
  Deliberately deferred per the brief.

- **Vendor-SDK import-untyped tech_debt entry** — still OPEN. mypy
  baseline unchanged at 4 errors throughout this session. Should
  land before any mypy-in-CI integration.

- **CI URL backfill** — Phase A and Phase B PRs' CI runs are now
  citable. The session log's table cites PRs #23 and #24; this PR's
  URL backfills naturally when somebody runs a doc chore.

- **4 older box_migration tech_debt entries** (V/S vendor-sub, ISO
  date prefix, person_tag over-match, smartsheet_migration import-
  time side effects) — unchanged.

- **mypy-in-CI decision** — input is PR #17's inventory report;
  still open.

## What was NOT touched

Verbatim from the brief's out-of-scope list:

- Workstream consumer code (`safety_reports/intake.py`, etc.).
- The 4 older `box_migration` tech-debt items.
- Alert-routing dedupe design.
- mypy-in-CI decision.

## Lessons captured

**Triple-fire failure-isolation pattern now appears 3 times.**
`_smartsheet_log`, `_fire_resend_leg`, and `_fire_sentry_leg` all
implement the same shape: recursion guard flag + try/finally + broad-
Exception catch + marker-line fallback via `_local_log`. PR #21's
session log flagged this for future extraction; tonight makes it
concrete — three instances of the same boilerplate.

Not extracting tonight per Op Stds v8 §14 preservation-over-refactor:
the rule of thumb is ≥4 cases before abstraction. We're at exactly 3
now. The next side-channel (Sentry was supposed to be the last leg of
the triple, but if Better Stack / log aggregation ever lands, that
would be the 4th). Worth a `_protected_sidechannel(name, fn)` helper
at that point — flagged here for the next person who touches
`error_log.py`.

**Autouse-mock-the-new-side-channel pattern.** Second time this
session (Resend in PR #21 → Sentry now). When a function adds a new
side channel that uses external state, the caller's test file needs
a parallel autouse mock — otherwise the new path leaks through into
previously-isolated tests and pollutes whatever external system the
side channel writes to. Pattern is now load-bearing in
`tests/test_error_log.py` (two autouse fixtures: `send_alert_mock`,
`sentry_capture_mock`). If a third leg lands, expect a third
autouse fixture.

**Picklist divergence across sheets is a real source of bugs.**
ITS_Review_Queue's `Workstream` picklist has `global`; ITS_Quarantine's
has `other`. Same conceptual column, different sheets, different
catch-all value. Locked in test
(`test_log_quarantined_message_rejects_global_workstream`). If a
future workstream needs `global` quarantine logging, the picklist
needs to change on the sheet first, not in code.

## Sequencing context

Lands at the end of the second 2026-05-18 work block. Sentry
completes the Op Stds v8 §3 triple-fire (legs 1 + 2 + 3 all wired
and tested live). Review_queue and quarantine unblock Phase 1
workstream code — the next natural session is wiring those into a
workstream consumer (e.g., `safety_reports/intake.py`'s anomaly
routing).
