---
type: session_log
date: 2026-07-06
status: closed
workstream: field_ops
related_prs: [483]
tags: [session_log, field-ops, progress-reporting, m3, material-incidents, append-only-ledger, section51, one-way-up, brief-validator, ops-stds-enforcer, portal-worker-security-reviewer, live-smoke, four-part-verify, deploy, config-gate-activation, dark-ship, forensic-class-7, forensic-class-3, doctrine-gate-hold]
---

# Session — M3 Slice 2 Material Incidents ledger: build → deploy → activate (PR #483), plus close-out prep

One continuous session that took M3 Slice 2 from an execution brief all the way to **live in the
sandbox**: built the per-job Material Incidents append-only ledger (PR #483), landed it four-part
clean, then — at the operator's direction — pulled the daemon tree, deployed the Worker, and
**activated the incidents gate** (verified end-to-end). Closed with config-observability seeding
and a doctrine-respecting HOLD on materials.

## Commits landed

- **#483** `814c9cf` — `feat(fieldops): M3 Slice 2 — Material Incidents append-only ledger up-sync (dark)`.
  Worker `GET /api/internal/fieldops/material-incidents` (`requireFieldopsToken`, read-only over
  `submissions` `box_verified=1`, active-job join, `line_status` LEFT JOIN via
  `json_extract($.line_uuid)`, display-name-only reporter; **no migration**) + `shared/portal_client`
  method + `progress_reports/material_incidents.py` (find-or-create + CHANGE-ONLY upsert + §51 A5
  row-cap watchdog; APPEND-ONLY — no retire, no On List) + the `fieldops_sync` incidents pass (gated
  `field_ops.fieldops_sync.incidents_enabled`, ships OFF) + §51 archive-on-closure + §43 runbook
  Symptom F + tech_debt activation queue. 10 files, +1507/−15.

## CI runs

- PR #483: `test`, `portal`, `secrets`, CodeQL/Analyze all SUCCESS; merged on `CLEAN`.
- Four-part verify (pr-landed-verifier ritual): `state=MERGED` · `mergedAt=2026-07-06T17:57:33Z` ·
  `mergeCommit=814c9cf5d3d6de22b9496030e8f6ca6521d106eb` · **main-branch CI on the merge commit =
  SUCCESS** (`ci` success + `CodeQL` success). Local gate before merge: pytest full suite green (exit
  0, 2553 collected) · ruff clean · mypy clean (256 files) · Worker typecheck clean · vitest 857
  passed (9 new, against real miniflare D1) · **live Smartsheet write smoke GREEN** (sandbox schema
  accepted, DATE round-tripped, change-only + no-op idempotency verified, sheet+folder cleaned up).

## Decisions made during session

- **Brief validated before building (forensic class #3).** The execution brief named
  `progress_reports/fieldops_sync.py`; the real file is `field_ops/fieldops_sync.py`. The
  brief-validator + an Explore agent caught four drifts before any edit: (1) that path; (2) the auth
  tier is `requireFieldopsToken` (the daemon's field-ops bearer), **not** `requireInternalToken`; (3)
  the brief's "Value = qty × unit_cost" column is **un-buildable** — `unit_cost` isn't on
  `job_expected_materials`, the per-line dollar value lives only in the downstream Smartsheet; (4) the
  data source is filed submissions (retained on `mark-filed`, never deleted).
- **Data source: Option (i) form-anchored** (the brief's recommendation), over Option (ii)
  line-anchored. (ii) required zero new surface (filter the existing material-list snapshot's
  `status=='incident'`) but carries no narrative/issue-type/photos and leaves Slice 1's `line_uuid`
  unused — a degenerate "incidents" view. (i) mirrors the filed `material-incident-v1` detail
  referencing its line — the true M3 intent, and the payoff of Slice 1.
- **Model: APPEND-ONLY event ledger, not a re-projected snapshot** — the one real refinement of the
  brief. An incident is an immutable historical event; it is never removed. This is *more correct*
  than a snapshot-with-retire (which would destroy the incident audit trail the moment a shortfall is
  resolved) AND it **structurally eliminates the #468 zero-drop class** — there is no retire path to
  wrongly zero. The only mutable field is the live `Line Status` (via the Slice-1 join), which the
  change-only upsert tracks. ops-stds-enforcer confirmed this matches the v20-folded §51 low-volume
  clause (single standing sheet + row-cap-watchdog split, no calendar period-split).
- **Skipped a dedicated mocked integration test** — the brief's clone target
  (`test_material_list_integration.py`) doesn't exist; the SDK primitives are covered by the shared
  `test_smartsheet_client_integration.py`, and a **real live Smartsheet write smoke** (stronger than a
  SimpleNamespace mock) was run instead. Flagged for the operator; offered to add the mocked test if
  preferred.
- **Reviews: both CLEAN.** ops-stds-enforcer (8/8 clauses — §51 send-free/AI-free, Invariant 1/2,
  §14 clone-is-right, §42/§43, #336, Reflex §5, dark-ship seeding) + portal-worker-security-reviewer
  (13/13 — send-free, `requireFieldopsToken`, bound SQL, no row-multiplication via the unique
  `line_uuid` index, no `actor_username` leakage, `box_verified=1`/active-job/`form_code LIKE`
  filtering). Only an informational note (the append-only SELECT grows monotonically) → added a
  forward `LIMIT`/cursor note to the endpoint docblock.

## Deploy + activation (same session, operator-directed)

- **Pulled `~/its` to origin/main** (was 2 behind: #483 + the #485 docs close). A real conflict:
  the local untracked `2026-07-06_doctrine-v20…` session log **differed** from origin — inspection
  showed origin is a strict superset (adds a "Follow-on" section that itself documents #483), so the
  stale local subset was removed, losing nothing. Live venv import-smoke confirmed the incidents pass
  loads clean and stays inert while gated off.
- **Deploy** (operator-run): Worker deployed; all three fieldops internal routes probe `401
  application/json` (deployed + gated). No migration for #483.
- **Gate flipped: `incidents_enabled → true`.** Post-flip daemon cycle (19:06:15) ran the incidents
  pass clean: `incidents upserted=0 reviewed=0 errors=0` (0 = no filed incidents on active sandbox
  jobs yet; `errors=0` proves the daemon→Worker-endpoint→mirror path is healthy end-to-end). **M3
  Slice 2 is LIVE.**
- **Config observability seeded (forensic class #7):** explicit `ITS_Config` rows for
  `smartsheet.sheet_count_ceiling`=1500 / `…margin`=50 (workstream `global`, Business-plan Description
  — advisory WARN, never blocks, tunable) closing the silent-hardcoded-default gap (M-1); plus the
  four `progress_reports.*.row_cap_warn_threshold` rows (=15000) that the #336 startup pass was
  WARNing as NO-ROW every cycle. `ITS_Daemon_Health` M-2 cleanup was a **no-op** — the live sheet
  already has exactly the 6 healthy self-provisioning daemon rows, zero stale placeholders (inspected
  before deleting; the tech_debt claim was stale).

## Open items handed off

- **Materials (`materials_enabled`) HELD dark — doctrine gate.** The operator (Seth) verbally chose
  **one-way-up portal→Smartsheet** for the Material List, which unblocks the §51 divergence in
  substance. But the gate's own in-cell Description is an explicit guardrail — *"Do NOT set true until
  a Seth-ratified v19.x phased-delivery rider is merged"* — and doctrine is a fixed high-capability
  class (§44 both-rule) that isn't actioned autonomously. Flipping it now would introduce a
  code-vs-doctrine drift the auditor would flag. **Next step: merge the §51 one-way-up rider in the
  blueprint (Path B of the two ready drafts, `docs/audits/2026-07-05_section51-materials-rider-proposal.md`),
  then flip `materials_enabled → true` (Worker route already live; migration 0039 applied at deploy).**
- **`progress@evergreenmirror.com` mailbox (#460)** — the one remaining progress-SEND blocker (sends
  HELD at approval until then). NOT a terminal command: create the shared mailbox → add it to the
  security group the `safety@` Application Access Policy scopes → confirm the EXO ServicePrincipal
  (Exchange-admin/PowerShell). Address must be exactly `progress@evergreenmirror.com`.
- **Slice 3 (M3 incident photos): recommend DROP** — incident photos are already §34-screened by the
  inline pipeline and reachable via the ledger's `Report` (Box PDF) column. Only real work if the
  operator wants incident photo bytes off the inline payload onto the Option-D pool (consistency, not
  a gap). Mark M3 done at Slice 2.
- **B2 (Seth):** incident → Weekly Progress Report presentation + `material-incident-v1`
  required-content floor + `category:progress` placement (two in-file confirmation flags) — separate
  downstream external-send decision, not needed for the send-free ledger.

## What was NOT touched

- **Materials activation** — deliberately reverted to dark (see hand-off); doctrine rider is Seth's.
- **No doctrine merged, no code changes made in the close-out** — the §51 rider, the #460 mailbox,
  and the equipment/checklist/recurring/hours activations are all operator/Seth-owned and left for
  them per direction ("the rest I'll handle after the deploy").
- **No risky autonomous builds** while unsupervised (e.g. #462 archive-on-closure turned out already
  built; jobs.progress column drop, the "Same as stakeholder" button, etc. deferred).

## Lessons captured to memory

- **Read a gate row's full Description before flipping it.** The `materials_enabled` in-cell
  guardrail ("do not set true until the §51 rider is merged") was only visible in the update response;
  the flip was reverted immediately. A gate flip on a doctrine-divergent capability is a doctrine
  action — verify the row's documented precondition first. (Updated the field-ops program auto-memory
  + this log.)
- Reinforces existing memories: [[feedback_prove-the-control-bites]] (live smoke over mocks),
  [[reference_pdf-three-delivery-surfaces]]/[[feedback_multi-surface-fan-out]] (enumerate surfaces —
  a datum has N homes), the append-only-vs-snapshot choice for any immutable-event mirror, and the
  dark-ship seeding discipline (a missing config row reads as `false` — seed it visible).
