---
type: session_log
date: 2026-07-17
status: closed
workstream: subcontracts
related_prs: [596, 597, 599]
tags: [subcontracts, po_materials, operator_dashboard, config_actuator, external-send-gate, go-live, four-part-verify]
---

# Session — config_actuator fail-soft, dashboard mark-errors-resolved verb, SC-S4 subcontract send lane, and PO + subcontract external-send LIVE (2026-07-14 → 2026-07-17)

## Purpose

Three PRs landed across a multi-day span (2026-07-14 build, 2026-07-15 merge, 2026-07-17
operator go-live): an alert-hygiene fix to `config_actuator`, the dashboard's
`mark-errors-resolved` verb (closing an item explicitly captured-but-not-built in the prior
2026-07-14 session log), and the SC-S4 subcontract send lane — the last piece of the
subcontracts workstream (ADR-0003). The session closes with both PO and subcontract external-send
lanes brought fully live by the operator, verified end-to-end against real Graph send + the
Exchange Application Access Policy scope.

## Pre-flight findings

- The 2026-07-14 dashboard session log (`2026-07-14_dashboard-backnav-configseed-errorclear-alerting-gap.md`)
  had already named "mark-errors-resolved" as captured-but-not-built (its open item 3) — #597 is
  that exact follow-through, not new scope invented this session.
- An early filter-composition bug on my own side, applying the new `mark_errors_resolved` verb to
  the backlog cleanup: I first searched for `po_send.*`-prefixed rows and found none, which read as
  "no PO-send errors exist." Self-corrected before acting on it — the real prefix in `ITS_Errors` is
  `po_materials.po_send.`, not `po_send.`. Caught before any wrong resolution was stamped.
- The 07-14 brief's "out-of-band alerting down / rotate Resend+Sentry keys" open item is
  **RETRACTED** here per §55.4 (faithful reporting): a 2026-07-15 error-flood diagnosis (recorded in
  memory, not this session's own work) established the apparent outage was phantom pytest test
  pollution reaching `ITS_Errors`, not a real Resend/Sentry credential failure. Flagging the
  retraction here rather than silently letting the stale open item propagate.

## Code changes

### PR #596 — `config_actuator` fail-soft on transient `SmartsheetError`
`_read_str_setting` now catches the broader `SmartsheetError` (was a narrower exception class) and
WARNs (`config_actuator.config_read_error`) instead of propagating to an unhandled-exception
CRITICAL, mirroring the pattern already used by `shared.required_config.resolve_and_log`. Stops a
transient Smartsheet token flap from paging as an "unhandled" CRITICAL when it's actually a
recoverable read. Prove-it-bites: 3 new tests confirmed RED against the unmodified code before the
fix, then green after.

### PR #597 — dashboard `mark_errors_resolved` verb
New Class-B `operator_dashboard/act/errors_ops.mark_errors_resolved`: stamps `Resolved At` on open
CRITICAL `ITS_Errors` rows matching a required Script + Error-code filter, moving them to terminal
so the existing `clear_error_log` verb (PR #594) can sweep them on its next pass. Guard: refuses an
unfiltered mass-resolve (filter is mandatory, not optional) — added per an ops-stds-style review
pass, along with a preview/dry-run button before committing the stamp, and the §43 successor-
remediation runbook entry. Also fixed a stale CLAUDE.md line during this PR — the dashboard's
"What's stubbed vs. real" row still said "No launchd plist yet," which had been stale since PR #570
(2026-07-13) added the KeepAlive service plist; the 2026-07-14 log had already flagged this as due
for correction and this PR carried it out.

**Backlog cleanup (using the new verb, same session):** marked 83 definitively-dead open
CRITICALs resolved — 50 `intake_poll` rows (the retired 2026-07-03 email-intake daemon; see
CLAUDE.md's `safety_reports/intake_poll.py` "DELETED" entry) plus 33 smoke/test rows. Open
CRITICALs dropped 215 → 132, correcting watchdog Check B's inflated count. 132 rows remain for
genuine operator triage — not touched by this pass (no filter was broad enough to justify auto-
resolving them, and the guard would have refused an unfiltered sweep regardless).

### PR #599 — SC-S4 subcontract send lane (subcontracts workstream, ships dark)
The send half of the subcontracts pipeline (ADR-0003), mirroring `po_send`/`po_send_poll` — the
last unbuilt piece of the workstream per the 2026-07-12 subcontracts-workflow memory entry. ~16
touched surfaces:
- `subcontracts/subcontract_send.py` + `subcontracts/subcontract_send_poll.py` — Invariant-1 send
  half. Recipient resolved at send time as the subcontractor's Contact Email (`ITS_Subcontractors`
  lookup by Sub Key), **empty CC** (no distribution list, unlike PO's invoice-routing CC), from
  `procurement@` (reused, not a dedicated mailbox — operator decision). F22 approval-attestation
  verified against `WORKSPACE_SUBCONTRACTS`.
- The subcontractor receives **one combined `Subcontract Package.zip`** (Subcontract body +
  Exhibit A + Annex C SoV) via a new deterministic `subcontract_docx.zip_package`, filed by
  `subcontract_poll` and linked from the review row's "Compiled PDF" column.
- **Only shared-engine change:** `weekly_send._attachment_content_type` is now filename-derived
  (`.pdf` → the existing content-type, byte-identical for safety/progress/PO; `.zip` →
  `application/zip`) instead of hardcoded — the one place this PR touches code shared by every
  other send-gated workstream.
- Migration `scripts/migrations/seed_subcontracts_send_config.py` seeds the send-lane config rows
  (dark by default); new launchd plist; `scripts/watchdog.py` `TRACKED_JOBS` entry for
  `subcontract_send_poll`; `verify_cutover.py` VC-03 registration; config-dictionary regen; the 9th
  interval daemon in `daemon_ops`; §43 runbook `docs/runbooks/subcontract_send.md`; a smoke script.
- `ops-stds-enforcer`-style review returned WARN-only (no BLOCK); the full test/mypy/ruff suite was
  re-run green after addressing the WARNs, not merged on the strength of the first pass.

### Both external-send lanes brought LIVE (2026-07-17, operator-actioned)
The operator flipped both `polling_enabled` gates (PO send, subcontract send) and loaded both
plists. Post-activation checks: both daemons healthy — fresh Check-C markers, fresh
`ITS_Daemon_Health` heartbeats, zero new errors, no unintended sends (no rows were in an approved
state at flip time, so neither daemon had anything queued to act on). The send path itself was
verified end-to-end separately: a real Graph self-send test from `procurement@` returned SENT OK,
and the operator independently confirmed the Exchange Application Access Policy scope via
`Test-ApplicationAccessPolicy` (PowerShell), closing the loop on whether Graph's app-only send
permission is actually scoped correctly in the live tenant. Both lanes are now fully operational
and human-approval-gated (F22), matching the PO lane's existing operating model.

## Decisions made during session

1. **Combined zip vs. multi-attachment for the subcontract package.** Chose a single
   `Subcontract Package.zip` over adding multi-attachment support to the shared send engine.
   Multi-attachment would have required a riskier change to `weekly_send`/`graph_client` shared by
   safety, progress, and PO sends; the zip keeps the blast radius of the new workstream to its own
   files plus one narrow, filename-derived engine change.
2. **`weekly_send._attachment_content_type` made filename-derived, not workstream-derived.**
   Minimal surface change, proven byte-identical for the three existing PDF workstreams by a
   prove-it-bites test (reverting to the old hardcoded content-type RED-lights only the new `.zip`
   cases, nothing else).
3. **Empty CC for subcontracts.** No distribution-list equivalent to PO's invoice-routing CC exists
   for subcontractor-facing sends; left empty rather than inventing a CC target.
4. **Reused `procurement@` for the subcontract send lane** rather than provisioning a dedicated
   mailbox — an operator decision, consistent with treating subcontracts as a PO-mirror workstream
   throughout its build.
5. **Retraction of the 07-14 alerting-gap claim (§55.4).** Rather than carrying forward a stale
   "Resend/Sentry down" open item into this log, recorded it as retracted per a later diagnosis
   already captured in memory — faithful reporting over silently dropping or silently repeating an
   unverified claim.

## Verification

Four-part verify (`pr-landed-verifier`), quoted verbatim:

PR #596 — four-part verify clean
- state: MERGED · mergedAt: 2026-07-14T22:39:55Z · mergeCommit: a4a9985fef178dca7cb219c876cbd7bcb728fed5
- main CI on merge commit: SUCCESS (run 29373729519, workflow: ci) + CodeQL SUCCESS

PR #597 — four-part verify clean
- state: MERGED · mergedAt: 2026-07-14T22:55:18Z · mergeCommit: 94edcc951ecf044c111f6d0c2738c4c6d9ac71ca
- main CI on merge commit: SUCCESS (run 29374539064, workflow: ci) + CodeQL SUCCESS

PR #599 — four-part verify clean
- state: MERGED · mergedAt: 2026-07-15T14:13:43Z · mergeCommit: fb906b2ff1cc3798f2913fdceccfc1ea8de2b6c6
- main CI on merge commit: CANCELLED (concurrency-cancelled by #600 merging immediately after) — verified via the substitute rule against containing green tip 39f9dc01972e93fda184000b556634643a2382e2: SUCCESS (run 29422682926, workflow: ci) + CodeQL SUCCESS; `git merge-base --is-ancestor fb906b2 39f9dc0` confirms ancestry.

Gate numbers (#599's final local run in the worktree venv — the session's peak numbers, all three
PRs' work integrated):
```
- pytest: 3479 passed / 49 deselected
- mypy: 0 errors / 384 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

## Live smoke / go-live verification

- **PO send lane:** `polling_enabled` flipped, plist loaded, daemon healthy post-flip (marker +
  heartbeat fresh, 0 errors).
- **Subcontract send lane:** same flip + load; same healthy post-activation signature.
- **End-to-end send path:** a real Graph self-send from `procurement@` returned SENT OK.
- **Exchange Application Access Policy scope:** operator-confirmed via `Test-ApplicationAccessPolicy`
  (PowerShell) — the app-only Graph send permission is correctly scoped in the live tenant, not just
  assumed from configuration.
- Neither daemon had an approved row queued at flip time, so no send actually fired during
  activation itself — the "no unintended sends" claim is a negative-evidence check (nothing to send),
  not a positive test of the approval gate under load. The approval gate (F22) itself was exercised
  and passed during PR #599's own test suite, not by this go-live step.

## Out-of-scope notes

- **`docs/enablement/subcontracts.md` residual internal contradiction** — a parallel, concurrent
  session (docs corpus Tranches A–E, PRs #598/#600–#604) updated the top-of-file "Sending ships
  dark" callout (lines 43–46) to reflect SC-S4 as built, but left a second, older line further down
  (`## What's not built yet` / `## It ships dark` section, "there's no send code yet") uncorrected —
  the two sections of the same doc now disagree with each other. Not fixed by this session (I stayed
  out of `docs/enablement/` to avoid colliding with their in-flight edits); flagged here as a
  tech-debt candidate for whoever next touches that file.
- No fix attempted on the 132 remaining genuine open CRITICALs left after the #597 backlog cleanup —
  they need real operator triage, not a blanket filter.
- No credential rotation performed for Resend/Sentry — the 07-14 claim about this is retracted (see
  Decisions #5), not acted on.

## What was NOT touched

- No doctrine (`~/its-blueprint`) edits.
- No `docs/enablement/` edits (left for the concurrent docs-corpus session; see Out-of-scope notes).
- No Keychain/secret writes.
- No changes to the PO send lane's code — only its `polling_enabled` gate and plist state changed at
  go-live.

## Parallel-session note

A second session ran concurrently against the documentation corpus / dashboard troubleshooting tree
(#598, #601–#604, plus its own session log at #600/#605). No file collisions: #599 merged cleanly
over their docs PRs, and I deliberately stayed out of `docs/enablement/` for the duration. See
Out-of-scope notes above for the one residual doc-drift item their pass left behind.

## Cross-references

- `docs/session_logs/2026-07-14_dashboard-backnav-configseed-errorclear-alerting-gap.md` — the log
  that first captured `mark_errors_resolved` as an open item (its open item 3); #597 closes it.
- `docs/operations/pr_merge_discipline.md` — the four-part landing verify definition applied above.
- `docs/HOUSE_REFLEXES.md` §2 ("prove the control bites") — the #596 fail-soft tests and #597's
  filter-required guard both follow this discipline.
- `docs/runbooks/subcontract_send.md` — new §43 successor-remediation entry for the SC-S4 send lane.
- `docs/runbooks/config_actuator.md` — covers the #596 fail-soft behavior.
- CLAUDE.md `subcontracts/` "What's stubbed vs. real" row — due for an update reflecting SC-S4 as
  built and both send lanes live (not performed by this log; flagged for the next doc-reconciliation
  pass).
- `~/its-blueprint/references/memory-archive.md` — a new `§G` entry naming both the SC-S4 build and
  the PO+subcontract external-send go-live is recommended at session close.
