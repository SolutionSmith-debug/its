---
type: session_log
date: 2026-05-29
status: closed
workstream: security
related_prs: [118]
tags: [forensic-audit, send-gate, capability-gating, approval-attestation, smartsheet-cell-history, fail-closed, sdk-vs-live, brief-deviation, adversarial-review, cutover, worktree, doctrine-drift]
---

# 2026-05-29 — F02 (network-capability allowlist) + F22 (approval-attestation verification)

PR: [#118](https://github.com/SolutionSmith-debug/its/pull/118) — squash-merged 2026-05-29T14:59:42Z, merge commit `a3efca700fef31f133812be8bb934a059ecefa7a`. **Four-part PR-landed verify clean** (`pr-landed-verifier`): state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on the merge commit = SUCCESS (`ci` run 26644697818 + `CodeQL` run 26644696583, both completed/success).

Verification gates (local, pre-push):
- pytest: 1141 passed / 0 skipped / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

## Purpose

The two audit §5 item-5b "gate before any new workstream" findings, paired in one cluster PR. Both protect Foundation Mission v8 Invariant 1 (External Send Gate):
- **F02** — additive repo-wide network-library allowlist so a module that should never touch the network *can't* import `requests`/`socket`/`subprocess` undetected. A second, orthogonal layer on top of the landed `GATED_SCRIPTS`/`SEND_SCRIPTS` send-gate.
- **F22** — verifies a `WPR_Pending_Review` "Approved for Send" value was set by an authorized actor (per Smartsheet **cell history**) before the weekly send dispatches. Fail-CLOSED.

## Verify-before-fix — `brief-validator` surfaced material F02 drift; F22 clean

`brief-validator` re-checked every code-shape claim against live HEAD `eea3553`. **F22 claims all VERIFIED** (`approval_verification.py` absent, no existing history wrapper, `get_setting(key, *, workstream)`, dispatch point `_filter_dispatch_candidates`→`send_one_row`, `seed_its_config` 4-column schema, `heartbeat_client` present). **F02 claims DRIFTED materially:**

- The brief asserted "the 5 allowlisted `shared/` modules are the only network importers today, so a repo-wide check passes." **False.** The live scan found **15 network/subprocess imports across 11 files** — the 5 `shared/` clients PLUS 8 operational scripts (`scripts/migrations/` seeders ×5, `setup_box_oauth`, `run_picklist_sync`, `smoke_test_graph`) and `smartsheet_migration/ss_api.py`. The brief also named 3 source dirs; there are 5 (`box_migration/`, `smartsheet_migration/` too).
- A literal "repo-wide, 5-entry allowlist" check would have **failed on 8 legitimate operational scripts**. This was the brief's own flagged "surface it and PAUSE" judgment call.

## The scope decision (operator-resolved)

Surfaced the drift via `AskUserQuestion`. **Operator chose Option 1: walk `shared/` + `safety_reports/` only** — the actual Invariant-1 untrusted-content surface — with the 5-entry allowlist (passes today, since `safety_reports/` has zero direct network imports). Operational/migration scripts are deliberately **excluded with documented per-dir rationale** (they legitimately hit REST, aren't in the injection path, and the audit scoped migration code out). Future workstream dirs (`po_materials/`, …) get appended to `WALKED_ROOTS` as they land — same per-workstream discipline as `GATED_SCRIPTS`. Dotted-segment matching (`socket` ≠ `socketserver`, `http.client` ≠ `http.server`) and `subprocess`-as-capability calls confirmed.

## F22 design — email match because the cell-history API exposes no user ID

Empirically inspected the Smartsheet SDK before coding: `Cells.get_cell_history(...)` returns `IndexResult[CellHistory]`; `CellHistory.modified_by` is a `User` whose **only populated fields are `name` + `email`** — `User.id_` comes back `None` (the documented `modifiedBy: {name, email}` payload omits the ID). So per the brief's design recommendation, **matching is on email** (no stable user ID is available), and the verdict carries `actor_user_id` opportunistically for forensics/future-proofing. This is precisely why the cutover approver-swap is fail-closed-fragile and got its own checklist doc.

Key design choices (all in the §42 module docstring):
- **Total function — `verify_approval` never raises.** The history read *and* the deciding-event selection run under one fail-closed guard; any failure folds to a `verified=False` verdict. Deliberately chosen over the typed-error hierarchy used by sibling clients: a raising API has a fail-OPEN footgun (a caller that forgets `try/except` sends anyway). A verdict the caller must inspect cannot be silently bypassed.
- **Most-recent-modifier rule.** The deciding event is the newest by `modified_at` (not list position); if an unauthorized actor re-touches an authorized approval, that's caught (UNAUTHORIZED_ACTOR).
- **Empty/missing allowlist ⇒ fail-CLOSED** ("no one is authorized", block all + page), never fail-open to "allow all."

## Code changes

- **`tests/test_capability_gating.py`** (F02, additive) — `NETWORK_LIB_ALLOWLIST` (5 `shared/` clients incl. `keychain.py`/`subprocess`), `NETWORK_NEEDLES`, `WALKED_ROOTS=("shared","safety_reports")`, dotted-segment `_import_matches_needle`, `test_no_unallowlisted_network_imports`, and `test_network_allowlist_has_no_stale_entries` (the allowlist can't rubber-stamp a deleted/non-importing entry). Existing send-gate untouched.
- **`shared/approval_verification.py`** (NEW) — `verify_approval`, `parse_authorized_actors`, `ApprovalVerdict`, `VerdictReason`. §42 docstring.
- **`shared/smartsheet_client.py`** — `get_cell_history(sheet_id, row_id, column_title)` + `CellHistoryEvent` (egress stays inside the audited `*_client` boundary, keeping the F02 allowlist honest).
- **`safety_reports/weekly_send_poll.py`** — per-row gate before `send_one_row`; `_load_authorized_approvers`, `_handle_unverified`, `_WAKE_REASONS`, `PollStats.blocked`. Block → forensic `approval_unverified` event with a threaded `correlation_id` (CRITICAL+triple-fire for unauthorized/empty-allowlist; WARN benign race; ERROR infra). Per-row independence preserved.
- **`scripts/seed_its_config.py`** — `safety_reports.authorized_approvers` row (3 validation-phase sandbox approvers; config-driven for cutover swap).
- **`docs/operations/cutover_checklist.md`** (NEW) — F22 stands it up; fail-closed approver swap (7 production approvers, incl. the `renwables`→`renewables` typo fix) + the broader `evergreenmirror.com`→`evergreenrenewables.com` domain-flip enumeration.
- Tests: `tests/test_approval_verification.py` (unit), `tests/test_approval_verification_integration.py` (operator-gated §30, via `sdk-integration-test-scaffold`), F22 wiring tests in `tests/test_weekly_send_poll.py`, count fixes in `tests/test_seed_its_config.py`.

## Adversarial review — 12 raw → 7 confirmed (all low/nit) → all fixed

Ran a 5-lens adversarial review workflow (17 agents, incl. an `ops-stds-enforcer`-typed lens; every finding independently re-verified to refute). **5 findings correctly dismissed** (static-analysis dynamic-import limit, out-of-set egress libs, three confirmations-of-correctness). **7 confirmed, all low/nit, none a send-gate breach — all fixed in-PR:**

1. **(low) `_latest_event`/`.isoformat()` ran outside `verify_approval`'s try** → the documented "never raises" invariant wasn't *structural* (held only because the SDK returns uniform tz-aware timestamps). Fix: brought event-selection under the fail-closed guard.
2. **(low) §3 `correlation_id` not threaded** in `_handle_unverified` → ITS_Errors row and Resend/Sentry legs got different IDs. Fix: one `uuid` threaded to both `log()` and `_alert_critical` (the `picklist_sync` precedent).
3–5. **(low/nit) poller test gaps** — NO_HISTORY/HISTORY_READ_FAILED ERROR-branch untested, WARN/ERROR forensic severities asserted only indirectly, `_alert_critical` `error_code` kwarg unchecked, `_latest_event` untimestamped-fallback unexercised. Fix: added parametrized ERROR-branch test, WARN-severity assertion, dedupe-key kwarg assertion, all-None-timestamp unit test (+3 tests net).
6. **(nit) doctrine v13→v14** — see below.

## Doctrine drift — Op Stds bumped v13 → v14 the same day

The review's `ops-stds-enforcer` lens flagged that `../its-blueprint/doctrine/operational-standards.md` is now **v14** (canonical, `last_verified: 2026-05-29`, `supersedes: @v13`), bumped the same day this work was authored against v13. Verified independently: v14's only substantive change is the **§1 kill-switch reframe** (operator-convenience pause, NOT a security control — audit F07, "no mechanism change"). **Every clause F02/F22 rests on (§3, §3.1, §30, §42) carries forward verbatim.** Per the cross-repo rule (planning project wins), updated the 3 new in-code citations to v14; left pre-existing grandfathered v11/v13 references alone (§14 preservation). No behavioral impact.

## Preserved (per Op Stds §14)

No refactors of adjacent working code. The existing `GATED_SCRIPTS`/`SEND_SCRIPTS` send-gate is untouched (F02 is purely additive). The `weekly_send_poll` heartbeat helpers (the `shared/heartbeat.py` consolidation tech-debt) were not touched. `get_cell_history` was added as a sibling `*_client` method rather than calling the SDK raw from `approval_verification.py`, keeping the network boundary single.

## Out-of-scope (deliberately not touched)

`shared/heartbeat.py` consolidation (tech-debt #116), F08/F09 circuit-breaker work, any doctrine reconciliation beyond the v14 citation bump. The CLI `weekly_send.main` manual-send path is intentionally NOT behind the F22 gate (it is a deliberate operator-in-the-loop action; the autonomous poller is the threat surface F22 targets).

## Sequencing context

This was the audit §5 5b gate: **F02 + F22 must land before any new workstream's generation/send script ships.** With both landed, that gate is cleared. F22's prerequisite (the authorized-approver set) was satisfied by the operator-provided validation list, so the cluster landed as one PR (no split needed).

## Operator-side actions remaining

1. **Run the F22 §30 integration test** against the sandbox: `cd ~/its-f02-f22 && pytest -m integration tests/test_approval_verification_integration.py` (needs `ITS_SMARTSHEET_TOKEN` in Keychain). Confirms cell-history actor extraction + the AUTHORIZED/UNAUTHORIZED verdicts against live Smartsheet.
2. **F22 fail-closed manual smoke** (per `prompts/scaffold/manual-smoke.md`): create a `WPR_Pending_Review` row approved by an authorized actor → confirm `weekly_send_poll` dispatches; create one approved by an unauthorized account (or hand-edited approval cell) → confirm the send is **blocked** + an `approval_unverified` forensic event is recorded.
3. **Seed the config row in the live sandbox**: `python scripts/seed_its_config.py` adds `safety_reports.authorized_approvers` (the 8th row) if not present.
4. **Delete the merged remote branch `f02-f22`** — the `gh pr merge --delete-branch` skipped it (worktree topology: `gh`'s local `checkout main` fails because `main` is in `~/its`). The GitHub-API ref-delete is blocked by the git-guardrail hook, so it needs a manual `git push origin --delete f02-f22` (or the GitHub UI).
5. **Cutover (delivery-critical, tracked in `docs/operations/cutover_checklist.md`)**: swap `safety_reports.authorized_approvers` from the 3 `evergreenmirror.com` validation accounts to the 7 `evergreenrenewables.com` production approvers. Fail-closed — getting this wrong silently blocks every safety-report send.

## Merge verification quartet output

```
- pytest: 1141 passed / 0 skipped / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```
(`pr-landed-verifier` on PR #118 / merge commit `a3efca7`: state=MERGED, mergedAt=2026-05-29T14:59:42Z, mergeCommit.oid present, `ci` run 26644697818 + `CodeQL` run 26644696583 both completed/success.)
