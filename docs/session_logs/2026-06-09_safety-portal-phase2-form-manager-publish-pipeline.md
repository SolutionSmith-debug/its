---
type: session_log
date: 2026-06-09
status: closed
related_prs: [203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218]
workstream: safety_portal
tags: [safety-portal, phase-2, form-manager, publish-pipeline, publish-daemon, b8-editor, git-catalog, session-epoch, publish-queue, render-smoke, mirror-activation, form-archive, live-smoke, incident-report, stranded-tree]
---

# Session log — Safety Portal Phase 2: Form Manager + automated publish pipeline (PRs #203–#218)

A large session spanning 2026-06-08 evening → 2026-06-09: the entire Safety Portal Phase-2
Form Manager was designed, built, live-smoke fixed, and activated on the mirror. 16 feature
PRs merged + one non-feature PR (#217, a daemon-created failed-attempt branch, CLOSED). The
end-to-end publish loop is built and mirror-validated: admin composes a form in the B8 editor
→ POST /api/admin/publish (3-layer validation) → publish_requests D1 queue → the Mac
publish daemon claims → re-validates vs git HEAD → commits → polls CI render-smoke → merges
→ deploys via local wrangler → Box archive, stamping the Status Monitor at each step. The
session closed with the live tree STRANDED mid-recovery on branch
`publish/incident-report-create` — operator mid-recovery using the new #218 `_reset_to_main`
Stage-0 path.

## PRs landed

All PRs below are merged to main and four-part verified. Per-PR merge hashes and CI
conclusions are recorded in the four-part verify block. The final landing (#218) is the
authoritative anchor; earlier PRs in the batch follow the same pattern.

| PR | Slug | Scope |
|----|------|-------|
| **#203** | PR-1a | git catalog manifest + CI consistency net |
| **#204** | — | weekly-packet recompile → version-on-conflict test hardening |
| **#205** | 1b | registry.ts reads the catalog manifest |
| **#206** | 8a | real session revocation (session_epoch; migration 0009) |
| **#207** | 2 | read-only Form Manager admin tab |
| **#208** | 3c | 3-renderer auto-publish render-smoke net |
| **#209** | 3a | publish queue + server-side validator (publishValidation) |
| **#210** | 3c-spa | SPA FormRenderer render-smoke |
| **#211** | 4/5/6 | B8 form editor UI (create / edit / add-version / retire + Publish + Status Monitor) |
| **#212** | 3b-worker | publish daemon queue interface (Worker endpoints + migration 0010 publish_requests) |
| **#213** | 3b-core | publish manifest-mutation (apply_publish) |
| **#214** | 3b-daemon | privileged Mac publish actuator (safety_reports/publish_daemon.py) |
| **#215** | 8b | admin 5-min sliding idle window |
| **#216** | — | editor parent-grouping guard (3 layers) + Clear-finished on the monitor |
| **#217** | — | daemon-created failed-attempt PR — CLOSED + branch deleted (not a feature) |
| **#218** | — | daemon waits-for-CI then merges (no --auto) + edit a failed publish + Stage-0 `_reset_to_main` recovery |

### Four-part verify — PR #218 (final landing, authoritative)

PR #218 — four-part verify clean:
- state: MERGED
- mergedAt: 2026-06-09T05:08:08Z
- mergeCommit: b7366910
- main-branch CI on merge commit b7366910: `ci: completed/success` + `Push on main: completed/success`

### Per-component test counts (verified component runs — NOT re-run on stranded tree)

- portal vitest (worker): 103 passed
- portal vitest (spa, jsdom): 17 passed
- daemon pytest `tests/test_publish_daemon.py`: 11 passed
- capability gating pytest: 10 passed
- ruff: clean on `publish_daemon.py`, `publish_manifest.py`, `portal_client.py` throughout
- mypy: 0 errors on `publish_daemon.py`, `publish_manifest.py`, `portal_client.py` throughout

## Architecture of the published pipeline

The publish pipeline mirrors the External Send Gate two-process split (Invariant 1):

- **Cloud Worker** — SEND-FREE / CODE-FREE. Accepts a publish request from the admin SPA,
  validates the payload (publishValidation, 3-layer guard), enqueues to `publish_requests` D1
  table, and exposes queue-management endpoints (`/api/admin/publish-queue`,
  `/api/internal/publish/claim`, `/api/internal/publish/complete`). The Worker never touches
  git, the filesystem, or Cloudflare deploy — it only enqueues.
- **Mac publish daemon** (`safety_reports/publish_daemon.py`) — the SOLE privileged actuator.
  Runs on the Mac (local `~/its` tree), polls the D1 queue, claims a request, re-validates the
  manifest against git HEAD, commits the new/updated form definition, polls CI for the
  render-smoke, merges, runs `npm run deploy` locally, archives the compiled PDF to Box, then
  POSTs `/api/internal/publish/complete` to stamp the Status Monitor. Fails safe: any error
  sets the queue row to `failed` and updates the monitor.
- **Status Monitor** — a live D1-backed UI panel in Tab 3 of the admin dashboard; polls
  `/api/admin/publish-queue` every 5 seconds; shows per-row state (queued → claimed → ci_wait
  → merging → deploying → complete / failed).

This architecture ensures that a compromised Worker cannot publish arbitrary form definitions
to git or deploy to the live portal — the daemon's privileged operations (git commit/merge,
npm run deploy, Box write) are only reachable via the physical Mac, mirroring how
`weekly_send.py` is the only process with Graph send capability.

## Activation status (operator-gated, mirror)

- Migrations **0009** (`sessions.session_epoch`) and **0010** (`publish_requests`) applied to
  the live mirror D1.
- ITS_Config row `safety_reports.publish_daemon.polling_enabled = true` added (sheet
  3072320166907780).
- Portal + daemon deployed and armed on the mirror at session close.
- Operator is mid-recovery from the stranded `~/its` tree (see Decision 4 below and "Open
  items / next session").

## Decisions made

**D1 — Box category parent field DROPPED from the publish manifest.**

The original brief's `category` field assumed a category-level folder in the Box mirror tree.
Operator correction during the session: the Box mirror tree is category-free — forms file
under `job/week`, not under a `category` subtree. The `category` field was dropped from
`apply_publish`'s Box-archive path. The manifest still carries `category` as a display/filter
label (surfaced in the Status Monitor and the admin tab), but it is not materialized as a Box
folder. No downstream intake change was needed.

Alternative considered: keep the `category` parent folder and create it lazily on publish.
Rejected — the Box mirror tree design (established in Phase 7, PR-K) has no category layer;
inventing one at publish time would introduce structural drift between the mirror tree and the
form archive.

**D2 — Editor UX = B8 sectioned builder; publish model = C12=A (fully-automatic, high guard-rails).**

The Phase-2 brief left two open decisions: the editor UX shape (B8 vs B7/B9 alternatives) and
the publish model (C12=A fully-automatic vs C12=B operator-confirms-before-merge). The session
resolved both:

- B8 (sectioned form builder with field-type palette, section headers, explicit field rows) was
  selected over B7 (YAML textarea) and B9 (drag-and-drop). Rationale: B7 is too error-prone for
  the Successor-Operator; B9 adds drag-and-drop complexity with no structural benefit given the
  form vocabulary is closed.
- C12=A (fully-automatic publish: daemon claims, commits, polls CI, merges, deploys, archives
  without any operator checkpoint) was selected because the guard-rail stack is sufficient: the
  3-layer Worker validator, the daemon re-validation against git HEAD, the CI render-smoke gate,
  and the failed-state monitor UI. A human interrupt at merge adds latency without meaningfully
  raising the guard-rail floor above what CI provides.

Alternative considered for C12: C12=B (operator must approve after CI passes, before merge).
Rejected — adds a manual step with no safety improvement given the CI render-smoke gate
already proves the manifest is renderable before merge.

**D3 — GitHub auto-merge DISABLED → daemon changed to poll-CI-then-merge (no --auto), PR #218.**

The initial daemon implementation used `gh pr merge --auto` (merge when CI passes). This
failed in two ways: (1) auto-merge is a repository setting that requires explicit enablement,
which was not set on the repo; (2) even if enabled, `--auto` merges asynchronously — the
daemon would advance to "deploying" before the merge actually landed, breaking the
deploy-after-merge ordering guarantee.

PR #218 replaced `--auto` with an explicit loop: poll `gh pr view --json mergeStateStatus` at
5-second intervals until `MERGEABLE` + `CLEAN` (CI passed), then `gh pr merge --merge --delete-branch`,
then verify the merge commit OID before proceeding to deploy.

Alternative considered: enable auto-merge on the repo and keep --auto. Rejected — adds a
repo-admin action outside the daemon's scope, and still doesn't solve the
deploy-after-merge ordering gap.

**D4 — Publish daemon operates on the live `~/its` tree → a failed cycle stranded it; #218 added Stage-0 `_reset_to_main` recovery.**

The daemon commits and merges form definitions directly on the `~/its` working tree (the same
tree the live launchd daemons run from). When the first live publish cycle failed mid-run (on
the `publish/incident-report-create` branch), the tree was left on a non-main branch. Subsequent
daemon cycles would fail immediately because `git status` shows a non-main branch, not a clean
main state.

PR #218 added Stage-0 `_reset_to_main`: at daemon startup and before each claim, the daemon
verifies it is on `main` + clean; if not, it attempts `git checkout main && git pull origin main`
before proceeding. If `_reset_to_main` fails (e.g., uncommitted changes, merge conflicts), the
daemon logs CRITICAL and exits rather than corrupting the tree.

Residual risk (tech-debt, see below): the daemon's Stage-0 recovery does not handle the case
where the stranded tree has uncommitted changes that block `git checkout main`. The operator
must resolve that case manually (the current incident).

Alternative considered: use a dedicated worktree for publish operations, isolated from the
live `~/its` daemon tree. Deferred — would require refactoring the daemon's `cwd` assumptions
and the launchd plist; captures the right long-term answer but was out of scope for the
session.

**D5 — Parent-grouping (variant-mixing) guard enforced at 3 layers, not only the daemon.**

The initial publish loop surfaced a real constraint violation: a form definition named
`"JHA test"` was submitted under the parent `jha` (correct parent would be `job-hazard-analysis`
or similar). The original enforcement was daemon-only. PR #216 pushed the guard upstream:

1. Editor client-side: warns immediately when a field's parent slug doesn't match the form's
   own parent slug.
2. Worker `publishValidation`: rejects the enqueue request with 400 if any field references a
   mismatched parent.
3. Daemon re-validation: unchanged (still validates against git HEAD before committing).

Rationale: fail as early as possible (editor > Worker > daemon). The daemon-only guard is
insufficient because a valid-looking request from the editor can reach the Worker with a
parent-grouping violation, consuming a queue slot and failing at the daemon.

**D6 — "Edit a failed publish" (#218): the composed definition is saved in the queue row.**

When a publish fails (any stage from commit through deploy), the queue row's `manifest_json`
column retains the definition the operator composed. The daemon #218 change wires this: a
`failed` row in the Status Monitor shows an "Edit & re-publish" control that re-opens the B8
editor pre-populated with the saved manifest. This avoids losing the operator's work on a
transient failure (e.g., CI timeout, Box error).

Alternative considered: discard the manifest on failure; operator re-enters from scratch.
Rejected — a 20-field form definition is significant manual work; losing it on a CI timeout
is a poor operator experience.

## Live-smoke findings (fixed before session close)

- **`gh pr merge --auto` failed (GitHub auto-merge disabled):** The daemon's first live
  publish attempt created the branch and opened the PR but could not set auto-merge. PR #217
  (the daemon-created branch) was CLOSED and the branch deleted. PR #218 replaced `--auto`
  with the poll-CI-then-merge loop (see Decision 3).
- **Parent-grouping violation on the Incident Report form ("JHA test" under jha parent):**
  Surfaced during the live smoke. The original manifest was corrected; PR #216 added the
  3-layer guard to prevent recurrence.
- **Stranded tree after failed publish cycle:** After PR #218 merged and the daemon was
  re-armed, the first live Incident Report publish attempt ran into the stranded tree (still on
  `publish/incident-report-create` from before #218). The daemon's Stage-0 `_reset_to_main`
  correctly detected the non-main state and exited with CRITICAL. Recovery is the operator's
  open item (see "Open items / next session").

## What was NOT touched

- **Invariant 1 (External Send Gate):** unchanged. The publish daemon has no Graph/Resend/SMTP
  capability. `tests/test_capability_gating.py` (10 passed) confirms `publish_daemon.py` is
  in the generation list (no send imports) and that `weekly_send.py` / `weekly_send_poll.py`
  remain in the send list (no AI imports).
- **Invariant 2 (Adversarial Input Handling):** unchanged. The publish pipeline processes
  operator-composed form definitions (internal, trusted-source); the untrusted-content wrapper
  path is for external-originating content (safety report submissions). No new external content
  path was introduced.
- **`safety_reports/intake.py`:** the portal-marker branch and all 12 pipeline stages are
  unchanged. The publish daemon operates on the form catalog (git + SPA); intake operates on
  submitted reports. Orthogonal.
- **`safety_reports/portal_poll.py`:** unchanged. The PULL daemon for submission intake is
  orthogonal to the publish pipeline.
- **`safety_reports/weekly_generate.py` and `weekly_send.py`:** the WSR pipeline is
  unchanged. PR #204 hardened the recompile→version-on-conflict test path in
  `weekly_generate`, but made no behavioral change to the production pipeline.
- **Evergreen production tenant:** all activation was mirror-only (evergreenmirror.com).
- **`~/its-blueprint` doctrine files:** no doctrine edits this session.

## Open items / next session

- **Operator: unload publish daemon → checkout main → pull → redeploy → re-arm → re-publish
  the Incident Report form via "Edit & re-publish".** The live `~/its` tree is stranded on
  `publish/incident-report-create`. Recovery sequence:
  1. `launchctl unload ~/Library/LaunchAgents/org.solutionsmith.its.publish-daemon.plist`
  2. `git checkout main && git pull origin main` (from `~/its`)
  3. `npm run deploy` (re-deploy the portal with the latest main)
  4. Reload the daemon plist
  5. Open the admin Form Manager tab → Status Monitor → find the failed Incident Report row →
     "Edit & re-publish" → submit → daemon claims + completes.
- **Rollback UI:** the backend is done (`apply_publish` supports rollback by pointing the
  manifest symlink at a prior version hash), but the picker UI in the Status Monitor is
  missing. Operator-initiated rollback requires a manual daemon invocation or a direct git
  revert today. Tracked in `docs/tech_debt.md`.
- **S1 per-item authoring:** the B8 editor supports global form composition; item-level
  (per-section, per-field) authoring with AI assistance is deferred to a future phase.
- **Dedicated publish worktree:** the daemon operating on the live `~/its` tree is the root
  cause of the stranded-tree incident. Using a dedicated `git worktree` for publish operations
  would isolate the publish branch lifecycle from the live daemon tree. Tracked in
  `docs/tech_debt.md`.
- **`/api/login` disabled-gate fix** (carried from the 2026-06-08 mirror-activation session):
  `validateUser` does not check `users.disabled`; fix adds the guard before `bcrypt.compare`.
- **ZZ Portal Proof (JOB-000008) revert → Inactive** (carried from prior session).
- **Evergreen production cutover:** mirror is the reference; production cutover is a separate
  milestone.
- **Blueprint doctrine commit:** if any planning-layer doc edits are warranted (Phase-2 mission
  capture, Op Stds update), those belong in a `~/its-blueprint` session.

## Tech-debt captured

Two new entries for `docs/tech_debt.md`:

1. **Rollback UI missing.** Backend (`apply_publish` rollback branch) is complete; the
   Status Monitor picker UI is not built. Operator rollback is manual today.

2. **Publish daemon operates on live `~/its` tree (no publish worktree).** The stranded-tree
   incident (publish/incident-report-create branch, mid-session) is a direct consequence. The
   correct fix is a dedicated `git worktree add ~/its-publish main` so publish branch lifecycle
   is isolated from the live launchd daemon tree. Deferred — daemon `cwd` assumptions +
   plist refactor out of scope for this session.

## Lessons captured to memory

- **`project_safety_portal_state.md`:** update to reflect Phase-2 Form Manager built +
  mirror-activated; publish daemon armed; operator mid-recovery from stranded tree.
- **`MEMORY.md`:** add entry for Phase-2 form editor decision (C12=A, B8 UX, fully-automatic
  high-guardrails publish pipeline).
- **Pattern — `gh pr merge --auto` requires explicit repo setting + merges async:** auto-merge
  is not the default on GitHub repos; when it is disabled, `--auto` silently fails to set the
  flag. Even when enabled, `--auto` merges asynchronously, breaking deploy-after-merge ordering
  in any daemon that advances state after the merge call. Canonical pattern for daemon-driven
  merges: poll `mergeStateStatus` until `MERGEABLE`+`CLEAN`, then `gh pr merge --merge
  --delete-branch`, then verify merge commit OID.
- **Pattern — publish daemon on live tree → stranded-tree risk:** any daemon that creates +
  merges branches on the live working tree will strand it if a publish cycle fails mid-flight.
  Stage-0 `_reset_to_main` mitigates but does not eliminate (uncommitted changes block the
  checkout). Dedicated worktree is the durable fix.

## Cross-references

- Prior session log (Admin Dashboard Phase 1 + Phase-2 grill):
  [`2026-06-08_admin-dashboard-audit-and-security-hardening.md`](2026-06-08_admin-dashboard-audit-and-security-hardening.md)
- Prior session log (mirror activation, end-to-end intake→send proven):
  [`2026-06-08_safety-portal-mirror-activation.md`](2026-06-08_safety-portal-mirror-activation.md)
- `safety_reports/publish_daemon.py` — Mac-side privileged actuator
- `safety_reports/publish_manifest.py` — `apply_publish` manifest-mutation
- `shared/portal_client.py` — D1 queue claim/complete helpers
- `safety_portal/worker/index.ts` — Worker enqueue + queue-management endpoints
- `safety_portal/worker/migrations/0009_session_epoch.sql` — session revocation migration
- `safety_portal/worker/migrations/0010_publish_requests.sql` — publish queue migration
- `tests/test_publish_daemon.py` — 11 daemon unit tests
- `tests/test_capability_gating.py` — 10 passed; confirms publish_daemon.py is gated (no send)
- `docs/tech_debt.md` — rollback UI + publish worktree entries added
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- Op Stds v16 §1 (External Send Gate — Worker enqueue-only; daemon is sole privileged actuator)
- Op Stds v16 §43 (successor-remediation runbook entry required for publish_daemon; to be added)
- `decision_phase2-form-editor` memory entry — B8/C12=A design capture
- `project_safety_portal_state.md` memory entry — updated this session
