---
type: session_log
date: 2026-07-06
status: complete
workstream: field_ops
related_prs: [478, 479, 480, 481, 482]
tags: [session-log, field_ops, safety_portal, doctrine, opstds-v20, required-config, progress-reporting, material-incidents, section51, workflow-agent, adversarial-review, four-part-verify]
---

# Session 2026-07-06 (extended, operator-driven continuation) — Op Stds v20 consolidation + 2B progress-logging + #336 REQUIRED_CONFIG + M3 Slice 1 (PRs #478–#482, blueprint #62–#63)

**Continuation note.** This is a distinct arc from the morning's autonomous run, already logged at
[`2026-07-06_recurring-checklists.md`](./2026-07-06_recurring-checklists.md) (PR #476, plus the
follow-on doc-index-regen commit referenced there as #477). Operator returned and drove this session
hard: "Operator drove the session hard after 2A. origin/main `3f6c07d`→`29df2d9`[→`eca4c64`]. All
merged + verified." Five exec PRs + two blueprint PRs landed, covering a dependency chain: a
security/hygiene patch, a doctrine ratification pair (materials rider → full v20 consolidation), the
exec-side propagation of that doctrine, the first buildable the doctrine unblocked (2B progress
logging), a standing forensic-audit follow-up (#336 REQUIRED_CONFIG), and the opening slice of a
newly-scoped M3 (Material Incidents).

## Commits landed

Exec (`~/its`), in landing order:

- `987f4f4` — **#478 chore(deps): patch npm audit vulns — hono 4.12.28 + safe dev-toolchain fixes.**
- `65ef7a5` — **#479 docs(doctrine): propagate Op Stds v20 to exec references + seed §52
  `narrated_controls` ledger.**
- `29df2d9` — **#480 feat(fieldops): checklist/inspection completion → weekly progress-report logging
  (#17, Seam A, dark).**
- `c04f4cd` — **#481 feat(shared): #336 REQUIRED_CONFIG — observable ITS_Config resolution across all
  daemons.**
- `eca4c64` — **#482 feat(fieldops): M3 Slice 1 — material incident references its expected-materials
  line (`line_uuid`).**

Blueprint (`~/its-blueprint`):

- `0690aa7` — **#62 doctrine(v19.x): §51 Material List one-way-up rider + §23/§24 seventh-workspace
  sync.**
- `33fce61` — **#63 doctrine(v20): consolidation — its#341 §§52–54 + §31/§43 hardening + §23 seventh
  workspace + fold 3 riders.** Tag `operational-standards-v20` (`33fce61a`, 2026-07-06).

All seven landings were independently four-part verified this session (`state=MERGED` · `mergedAt`
non-null · `mergeCommit` present · main-branch CI on the merge commit `SUCCESS`) — see the per-PR
verify block below each summary.

---

## #478 — npm vuln patch

All 8 `npm audit` findings cleared → `found 0 vulnerabilities`, with **no breaking `--force` bumps**.
`hono` 4.12.23→4.12.28 (the one runtime dep; its 5 advisories were confirmed inapplicable to this
deployment — 4 AWS-Lambda/Lambda@Edge-specific and this is a Cloudflare Worker, 1 Windows
`serve-static` and the Worker uses none of hono's `cors`/`serveStatic`/`bodyLimit`, grep-verified).
`npm audit fix` (non-force) resolved the dev/build/test-only transitives (esbuild, undici, ws,
miniflare, workerd) — none ships to the deployed Worker.

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T12:21:18Z` · `mergeCommit=987f4f411d330dbda02a2a32cd97f2c05a776fe3`
- main-branch CI on the merge commit: run `28791106293`, conclusion `success`
- worker vitest: 828 passed (55 files) · SPA vitest: 560 passed (45 files) · build OK (SPA bundle
  byte-identical) · `npm audit`: 0 vulnerabilities
- ruff: clean (`All checks passed!`) · mypy: no issues in 252 source files (unrelated Python surface,
  unaffected by this dep bump; confirmed clean on the CI run regardless)
- pytest: full suite green (job `test` = `success`); the exact "N passed" summary line is not present
  in the captured GH Actions log for this repo's `pytest -q --cov=...` invocation (the coverage table
  replaces it) — stating this rather than inventing a count. Coverage total: 7573 stmts, 89% covered.

---

## Blueprint #62 — §51 Material List one-way-up v19.x rider

Operator ratified: "the materials list is shipping as one way for now" — Path 1 / Reading A of
`docs/audits/2026-07-05_section51-materials-rider-proposal.md` (drafted the prior session with two
honest framings, per the session-log-writer's don't-edit-doctrine boundary). §51 named the Material
List as bidirectional; the rider phases delivery: M2 (shipped PR #470) is a one-way-up snapshot —
**strictly more conservative** than the bidirectional posture (never writes operator-owned columns,
never reads operator edits back) — every §51 protective guard is still met. M2b (bidirectional
receive) is deferred, not abandoned. Because no protective claim was weakened, this qualified as a
same-major v19.x rider, not a version bump (same test as the 2026-07-04 low-volume-split and
2026-07-03 Sentry-leg riders). Also synced §23/§24's stale workspace enumeration to include the
seventh (`ITS — Progress Reporting`) workspace. **Effect: `field_ops.fieldops_sync.materials_enabled`
is now doctrine-unblocked** — the operator can flip it once ready (it was NOT flipped this session).

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T12:18:31Z` · `mergeCommit=0690aa7ff74a5e5e5967dd0ee861e793ae02a195`
- main-branch CI on the merge commit: run `28790952053` (blueprint `lint` job), conclusion `success`

---

## Blueprint #63 — Op Stds v19→v20 consolidation

Operator: "do the consolidation now" — rather than trickle the its#341 forensic §-adds in as further
v19.x riders, this folds them into a proper major bump. **New §§52–54:**

- **§52 narrated-not-enforced** — a control that doctrine claims as built must resolve to either
  code-evidence (a binding test) or a *dated exception*. Seeds a curated `narrated_controls` ledger
  (8 entries) in `docs/doctrine_manifest.yaml`, plus the citation-integrity leg of the drift gate.
- **§53 sandbox-masks-production** — the cutover checklist must be gated by mechanical pre-cutover
  verification, not narration.
- **§54 runtime secret/PII-leak backstop** — redact/no-secret-in-logs coverage on the error_log
  triple-fire path, migrations, and `security_events` PAT scope.

**Amendments:** §31 (daemon-scaffold DoD hardened) and §43 (coverage audited) both hardened. **Folded:**
§23/§24 seventh-workspace topology change, plus the §51 Material List one-way-up + low-volume
period-split riders — all three prior v19.x riders retained in the doctrine file for provenance,
marked `[FOLDED INTO v20]`. Version mechanics: frontmatter 19→20, changelog + Authority v20 entry, v20
trigger realized + v21 slot added, tag `operational-standards-v20`. Every prior §1–§51 carried forward
verbatim except the §31/§43 hardening and the four folds.

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T12:35:51Z` · `mergeCommit=33fce61a9d8cf1620807bd6903b031ff5586515d`
- main-branch CI on the merge commit: run `28792752111` (blueprint `lint` job, ×2 required checks),
  conclusion `success`
- Tag `operational-standards-v20` confirmed on `33fce61a` (blueprint repo, `git tag -l`).

---

## #479 — exec-side v20 propagation + §52 ledger seed

Cross-repo propagation of the v20 consolidation into the exec-facing surfaces the drift scanner
covers: current-version citations bumped v19→v20 in `CLAUDE.md`, `README.md`,
`docs/HOUSE_REFLEXES.md`, `docs/tech_debt.md`, and `.claude/agents/ops-stds-enforcer.md` (historical
"added at v19" / "v19.x rider" references deliberately left intact — this is citation currency, not a
rewrite of history). `docs/doctrine_manifest.yaml`: `current` 19→20, `max_section` 51→54,
`blueprint_verified_against` → `33fce61`, `drift_signal` widened to flag v19-and-below. **Seeds the
§52 `narrated_controls` ledger** — 8 curated entries covering its#341 binding tests and known
narrated-not-enforced controls as *dated exceptions* (honest per §52 itself), with kill-switch,
send-gate, and state-io marked `enforced` (test-evidence-backed).

Verified `check_doctrine_drift.py` M1 (version-cited-stale) CLEAN and the manifest YAML valid before
merge; the two pre-existing M2 tech-debt findings are unrelated and pre-date this PR.

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T12:49:26Z` · `mergeCommit=65ef7a512238e3aac93657e005708d2cd0e74429`
- main-branch CI on the merge commit: run `28792718301`, conclusion `success` (`test`, `secrets`,
  `portal` all completed/success)
- ruff: clean (`All checks passed!`) · mypy: no issues in 252 source files · pytest: full suite green
  (job `success`; exact pass-count line not captured in the retrieved CI log for the reason noted under
  #478) · doctrine-drift check (blocking, M1/M4/M7): passed · worker vitest 828 passed · SPA vitest 560
  passed (docs-only PR; portal job runs regardless as a required check, unaffected)

---

## #480 — 2B: checklist/inspection completion → weekly progress-report logging (#17, Seam A, dark)

Operator resolved the Seam-A-vs-B decision that had blocked this since the morning session, and chose
the low-friction path: **"just require a signature on the checklists/inspections."** This mattered
because the signature satisfies required-content's default `required_signature_inputs_min:1` floor —
**no Seth-owned `required-content.json` edit was needed**, and a signature is a genuine attestation
rather than a rubber-stamp workaround.

**What it does:** on a complete assigned inspection, the assignee signs off and the Worker synthesizes
a `category:"progress"` `checklist-completion-v1` submission that rides the **existing**
intake → progress-week-sheet → weekly-compile pipeline — a standard submission the built pipeline
already files. No new §51 SoR write-route; zero Python changed (the pipeline is form-agnostic over
`form_code`). Ships **dark** behind `CHECKLIST_PROGRESS_LOGGING_ENABLED`.

**Multi-surface build:** migration `0041` (`emitted_submission_uuid` one-shot marker +
`completion_signature`/`_at`); new `worker/submission.ts` — a §14 extraction of the `/api/submit`
submission-creator (`canonicalPayload` + `buildSubmissionInsert`) giving both producers one
byte-identical HMAC/INSERT path (the existing `submit-as` regression lock stayed green); new
`POST /api/fieldops/checklist/instance/:id/submit` (fail-closed on `cap.tasks.own`, ownership,
completeness, job+date, the dark gate, and a bounded+un-logged signature) plus a new form definition, a
catalog `launch:"synthesized"` parent, and a SPA "Sign & log to progress report" action reusing
`SignaturePad`.

**Built via Workflow (1 build agent + 3 parallel adversarial reviews); both BLOCKs folded before merge:**
1. **[security] concurrency strand** — the `submissions` INSERT is now guarded
   (`INSERT … WHERE emitted_submission_uuid IS NULL`, `guardInstanceNotEmitted`), so a concurrent
   double-emit's loser writes zero rows — no stranded duplicate. Test-locked.
2. **[form-def] manual forgery** — the general `/api/submit` route now rejects
   `checklist-completion*` forms outright (`403 forbidden_synthesized` — the real Invariant-2
   boundary), and the parent is `launch:"synthesized"` (hidden from the manual-submit picker).
   Test-locked.

`ops-stds-enforcer` review: clean (§51 rides the existing pipeline rather than adding a new SoR
surface, §14, §43, §52, never-silent).

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T14:31:32Z` · `mergeCommit=29df2d9d40ffbc116867bbd73418fe9a30931f2d`
- main-branch CI on the merge commit: run `28799281929`, conclusion `success`
- typecheck: clean · worker vitest: 840 passed (56 files) · SPA vitest: 566 passed (45 files) · build
  OK · Python form tests: 191 passed
- ruff: clean · mypy: no issues in 252 source files · pytest (full suite): job `success` (exact count
  not captured in CI log, per the #478 note)

**Activation (2 flips, ships dark until both):** `CHECKLIST_PROGRESS_LOGGING_ENABLED="true"` (Worker
var + deploy — high-class, Seth/Developer-Operator) *and* ITS_Config
`progress_reports.intake_enabled=true` (a Tier-2 bounded config flip per §44 — routes to the progress
week-sheet; the eventual send still needs the `progress@` mailbox, its#460). Neither flip happened this
session.

---

## #481 — #336 REQUIRED_CONFIG: observable ITS_Config resolution across all daemons

Closes the standing forensic-class-#7 gap: "a daemon that silently falls back to a hardcoded default on
a missing/malformed `ITS_Config` value hides a real misconfiguration." This is the exact 2026-07-05
pain (the operator hunted for `equipment_enabled`/`materials_enabled` rows that didn't exist).

**What it does:** new `shared/required_config.py` (§42) — `ConfigKey(setting, workstream, default,
kind)` plus a fail-open `resolve_and_log(script, keys)`: logs each resolved setting with its **source**
(`ITS_Config` vs `default`) at INFO; WARNs `config_row_missing` distinctly on a `SmartsheetNotFoundError`
(the missing-declared-row case); INFO on a blank row; WARN `config_read_error` on any other
`SmartsheetError`; wrapped in a terminal `except` so a config-observability failure can never itself
crash a daemon. Additive per §14 — the runtime `_read_*_setting` reads are unchanged. Wired into every
daemon entry point after `@require_active`: `fieldops_sync` (all 3 of its workstream passes),
`portal_poll`, `intake` (once-per-process guard — not per-message, so an unseeded key doesn't spam N
WARNs/cycle), `compile_now_poll`, `weekly_generate`/`weekly_send`/`weekly_send_poll`, the progress-report
equivalents, `publish_daemon`, `watchdog`, `run_picklist_sync`. New `tests/test_required_config.py` is
the §52 evidence test (every branch RED-lightable) — this flips the `narrated_controls` ledger entry
`required_config_observable_resolution` from `dated_exception` to **`enforced`**, verified live in
`docs/doctrine_manifest.yaml` (line 143–147: `status: enforced`, evidence
`shared/required_config.py + tests/test_required_config.py`).

**Notable: the build agent died mid-stream.** This was built via Workflow; the build agent hit a
transient API stall after wiring the mechanism into the first ~12 daemons and did not resume. CC
finished the remaining wiring from the partial state rather than restarting the build, then folded two
review BLOCKs:
1. **[ops-stds]** `run_picklist_sync` had been left unwired — notable because that daemon's own
   `_resolve_size_thresholds` docstring literally documents the "both keys missing → silent fallback"
   anti-pattern this PR exists to close. Now declares and logs both threshold keys.
2. **[correctness]** the send-path `from_mailbox` key had been declared only on the debug `main()`
   entry point, invisible on the production `send_one_row` path actually driven by the poll daemons.
   Moved the declaration to `weekly_send_poll` + `progress_send_poll`; corrected a misleading comment.

`intake` logs once per process rather than per message (avoids an unseeded-key WARN flood); the
shared-sub-helper scoping (`kill_switch`/`sheet_capacity`/`alert_dedupe` own their own keys) is
documented rather than re-declared.

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T16:00:57Z` · `mergeCommit=c04f4cdd1f10d0e68febe286c4e08bb0e9dd7a9b`
- main-branch CI on the merge commit: run `28805137082`, conclusion `success`
- Gate run in an isolated worktree venv per the Python-source-edit discipline: mypy clean (254 source
  files) · ruff clean (`All checks passed!`) · **599 daemon+evidence tests pass** (author-reported,
  isolated worktree run — the CI run's own coverage table shows 7657 stmts / 89% covered, consistent
  with this) · doctrine-drift check exit 0 · **live smoke**: `resolve_and_log` run against the real
  ITS_Config sheet produced `INFO … (source: ITS_Config)` for the live `worker_base_url` key.
- pytest (CI job): `success`; exact "N passed" summary not captured in the retrieved CI log for the
  reason noted under #478 — the 599-test figure above is the author-reported isolated-worktree count,
  distinguished here from the full-suite CI total.

---

## #482 — M3 Slice 1: material incident references its expected-materials line (`line_uuid`)

The net-new "an incident references a specific M2 Material-List line" behavior — the foundation the
dedicated Material Incidents Smartsheet (Slice 2, queued) will consume. `line_uuid` rides as a
**validated submission value**, not a form field — no meta-schema change.

**What it does:** in `worker/index.ts`, a trust-boundary gate in `/api/submit` (after the job-exists
check): for any `form_code` starting with `material-incident`, a present + non-empty `values.line_uuid`
is shape-checked (string, ≤64 chars) then validated with a bound query
(`SELECT line_uuid FROM job_expected_materials WHERE job_id=?1 AND line_uuid=?2 AND active=1`) — no
matching row, or a malformed value, fails closed with `422 unknown_material_line` **before any INSERT**.
An absent/empty `line_uuid` is still allowed (a manual, unlinked incident) — no existing guard was
weakened, and the change is scoped to the material-incident form family. `worker/fieldops_expected_materials.ts`
+ `wire-types.ts` now return `line_uuid` from `GET /api/fieldops/expected-materials` (single-sourced,
re-exported to the SPA). `src/components/DailyReportTab.tsx`'s "Report a problem →" deep-link folds
`line_uuid` into the prefill — an in-memory value preserved through edits by the renderer's
merge-update, but dropped on a page refresh (documented as a graceful degrade to a valid unlinked
incident, not a bug). New `test/material-incident-line-ref.test.ts` (8 cases, control-bites proven —
bypass attempts RED before the fix).

**Reviews (built via Workflow, 2 adversarial passes):** `portal-worker-security` — clean (fail-closed
ordering, bound SQL, a real cross-job check, no guard weakened, the SPA doesn't misplace trust);
correctness — clean (the value reaches the submission and survives edits; the refresh-drop degrade is
graceful and documented; tests are non-vacuous).

**Four-part verify — CLEAN:**
- `state=MERGED` · `mergedAt=2026-07-06T16:40:26Z` · `mergeCommit=eca4c647a363b6e6a4519f1ecf61fdaef81dcbc3`
- main-branch CI on the merge commit: run `28807615092`, conclusion `success`
- typecheck: clean · worker vitest: 848 passed (57 files) · SPA vitest: 567 passed (45 files) · build
  clean
- ruff: clean · mypy: no issues in 254 source files · pytest (full suite): job `success` (exact count
  not captured, per the #478 note)

Ships dark on the existing `progress_reports.intake_enabled` gate — no new config surface for this
slice.

---

## Non-obvious decisions (the why)

1. **Consolidate to v20 now rather than accumulate further v19.x riders.** Rejected: keep landing
   its#341's forensic §-adds as individual same-major riders. Reasoning (operator call): the §-adds
   (§52–54) are genuinely new sections, not clarifications of existing ones — the honest test the prior
   riders passed (no protective claim weakened) doesn't apply to net-new controls. A dedicated
   consolidation PR also let the three already-landed v19.x riders fold in cleanly with full provenance
   preserved, rather than leaving the doctrine file as a trail of riders a reader has to chase.
2. **2B ships on a signature requirement, not a `required-content.json` edit.** Rejected: add an
   explicit new required-content rule for checklist-completion submissions (would have needed Seth's
   sign-off on the content schema itself). Reasoning: the default `required_signature_inputs_min:1`
   floor already exists and a signature is a genuine attestation of completion — reusing it cleared the
   last blocker on 2B without crossing into doctrine-owned content-schema territory.
3. **M3 scoped to "Full M3" only after discovering it was under-specified and partly shipped.**
   Rejected: build M3 directly off the blueprint mission as written. Reasoning: the mission is a
   `status: draft` (2026-06-28) that specifies a `material_list` table that was never built (the
   as-built table is `job_expected_materials`, migrations 0031+0039), and the `material-incident-v1`
   form plus its `/flag-incident` route already shipped in the earlier M2 arc (PR #428). The actual net-new
   gap was narrower than the mission implied: an incident didn't reference a specific material line.
   Operator was asked (via `AskUserQuestion`) rather than the scope being silently narrowed or silently
   inflated, and chose **Full M3** — Seam A + a dedicated Smartsheet (Slice 2, queued) + a photo
   deep-screen pass (Slice 3, queued) — with this session landing only Slice 1.
4. **A dead build-agent partial is recoverable, not a redo.** On #481, the Workflow build agent died
   mid-stream from a transient API stall after wiring the mechanism into ~12 daemons. Rejected:
   restart the build from scratch. Reasoning (reinforces a standing lesson): CC finished the remaining
   wiring from the partial state, folded the two review BLOCKs, re-ran the full gate, and live-smoked —
   the stall cost no work, only attention. This is the same "prove the control bites" + "mandatory live
   smoke" discipline applied to agent-authored work, not just human-authored.
5. **#336 REQUIRED_CONFIG built now, not deferred to phase 1.6 as the morning session had recommended.**
   Rejected: leave it parked per the earlier session's "operator-parked to phase 1.6, a partial sweep
   would be half-baked" framing. Reasoning: the operator directed it explicitly ("finish the required
   config") after the morning session surfaced it as unblocked-but-parked; building it now, in one pass
   across every daemon, was the full sweep rather than the half-baked partial the earlier framing had
   worried about.

## Open items handed off

- **§51 materials rider decision (Seth) — RESOLVED this session** (Path 1/Reading A ratified via
  blueprint #62). `materials_enabled` is now doctrine-unblocked but **still not flipped** — that remains
  an operator config action, not done this session.
- **2B activation (2 flips, Seth/Developer-Operator + Tier-2 config)** — `CHECKLIST_PROGRESS_LOGGING_ENABLED="true"`
  (Worker var + deploy) and ITS_Config `progress_reports.intake_enabled=true`. Neither flipped.
- **its#341 dated exceptions in the §52 ledger** — the §52 binding test itself (item #1), §53's gated
  cutover verification (item #4), §54's secret/PII-log backstop test (item #6), and a `security_events`
  PAT scope bump are committed exec follow-ups, honestly tracked as `dated_exception` rather than
  claimed done.
- **M3 Slices 2 and 3 (queued, next session)** — Slice 2 is a new `progress_reports/material_incidents.py`
  daemon modeled on `material_list.py` (§51 pattern: send-free/AI-free/gated, non-clobbering,
  period-split, zero-drop reconcile guard) behind a to-be-seeded `field_ops.fieldops_sync.incidents_enabled`
  row (seed it `=false`, per the dark-gate-row-absence house reflex). This is flagged as a fresh-context,
  mandatory-live-smoke item, not a tack-on. Slice 3 is a fenced `portal_poll` incident-photo deep-screen
  pass (§34/§12) with an open inline-vs-Option-D-pool design choice still to make.
- **its#460 progress@ mailbox** — still open; blocks the actual progress-report send, independent of
  everything landed this session.
- **Live `~/its` daemon tree is behind main again** (was pulled to `29df2d9` mid-session for the
  checklist deploy sequence, now behind by the #481/#482 merges) — operator re-pulls before the next
  deploy. Everything in this session's landings ships dark until config/Worker-var flips happen, so
  this is a deploy-ordering note, not a live-behavior risk.

## What was NOT touched

- No config flips were made — `materials_enabled`, `CHECKLIST_PROGRESS_LOGGING_ENABLED`, and
  `progress_reports.intake_enabled` all remain at their pre-session values. Everything landed ships
  dark.
- No `progress@` mailbox work (its#460) — unrelated, still open.
- M3 Slices 2 and 3 were not built — deliberately queued for a fresh-context session per the mandatory-
  live-smoke discipline for new shared daemon infrastructure.
- No bidirectional Material List receive path (M2b) — still deferred; the one-way-up rider explicitly
  phases it out to a later slice, not this session.
- The four its#341 dated exceptions in the §52 ledger (binding test, §53 gated-cutover, §54
  secret/PII backstop, PAT scope bump) were not built this session — recorded as dated exceptions, not
  silently skipped.

## Lessons captured to memory

- Reinforces (no new memory file) **"a dead build agent's partial is recoverable"** — already tracked
  via the Pit-Wall use-doctrine and prove-the-control-bites memory entries; #481's transient API stall
  is a fresh instance of the same pattern (CC-finish + fold reviews + re-gate + live-smoke), worth
  citing here as the concrete example for the next time a Workflow agent stalls.
- Confirms **§34 Option-D screened photo pool** (`reference_section34-option-d-photo-pool.md`) remains
  the open design question for M3 Slice 3's incident-photo deep-screen — flagged for the next session
  rather than decided here.
- No new `docs/HOUSE_REFLEXES.md` entry this session — the decisions above are session-specific
  (doctrine-version judgment calls, scope-narrowing via `AskUserQuestion`) rather than a recurring
  execution-discipline lesson distinct from what's already captured.
