---
type: session_log
date: 2026-07-23
status: closed
workstream: infrastructure
related_prs: [664, 665, 666, 667, 668, 669, 670, 671, 672, 674]
tags: [tenant-wipe, standup, migrations, sheet-ids-regen, cutover-rehearsal, config-parity, job-id-doctrine, operator-dashboard]
---

# 2026-07-22 → 2026-07-23 — Full tenant wipe + orchestrated stand-up rehearsal (production-cutover dry run)

## Purpose

Operator directive (2026-07-22): *"nuke everything off Smartsheet and Box, then run the setup
code to mimic the production stand-up"* + *"seed scripts should auto-update
`shared/sheet_ids.py` — circle complete."* The goal was a full-fidelity rehearsal of the actual
production cutover sequence — destroy the mirror tenant down to nothing, then rebuild it purely
from the repo's own builder/migration scripts, proving both that the wipe tooling is safe and
that the stand-up path is complete enough to regenerate a working tenant unattended. Scope was
confirmed via operator Q&A before any destructive action: all 12 Smartsheet workspaces including
the demo workspace, a full Box wipe, discard any pending-review rows outstanding, and leave
Cloudflare/D1 untouched (URL, account, and D1 data all persist across the mirror rebuild).

## Pre-flight findings

- Scope questions resolved before build: wipe is Smartsheet + Box only; D1 stays exactly as-is
  (operator: keep the URL and the Cloudflare account state); pending-review rows are acceptable
  losses (not archived first); the demo workspace is in scope, matching the "everything" framing
  of the directive literally rather than narrowly.
- The three-lens adversarial review workflow (ops-stds-enforcer / attacker / skeptic) run against
  the new tooling pre-merge surfaced 19 issues, including one BLOCKER — a builder-local id pin that
  would have silently diverged from the freshly regenerated `shared/sheet_ids.py` — all fixed before
  `#664` merged.

## Phase 1 — tooling (PR #664, `7f86a13`)

New scripts, all built and adversarially reviewed before any live run:

- `wipe_tenant.py` — name+id double-match allowlist (never a bare name match against a live
  tenant), a typed confirmation phrase (`NUKE THE SANDBOX`), a daemon-down guard (refuses to run
  while any launchd daemon is loaded), and dump-before-delete (every sheet/share/Box manifest
  entry is serialized to disk before it is destroyed).
- `sheet_ids_regen.py` — a declarative `REGISTRY` driving an in-place rewrite of
  `shared/sheet_ids.py` (including `DAEMON_HEALTH_COLUMNS` and its satellite constants), plus a
  `--check` mode that reports parity without writing.
- `standup.py` — the 36-stage orchestrator that runs every builder/migration in cutover order.
- `build_legacy_workspaces.py` — the 4 workspaces with no existing builder, schemas captured from
  the live tenant before this rehearsal.

Live smokes run before the destructive step: the wipe plan matched all 12 workspaces + 3 Box
roots exactly; `sheet_ids_regen --check` correctly flagged the tenant's real pre-existing
divergences (5 duplicate `ITS_Errors` sheets → AMBIGUOUS, a renamed progress Control sheet →
MISSING) rather than silently resolving them; all 7 legacy schemas were created and verified in a
scratch workspace, then deleted, before being trusted for the real run.

## Phase 2 — execution: wipe + stand-up (2026-07-23)

All 20 daemons unloaded, then `wipe_tenant.py --commit`: 231 sheets + their shares + the full Box
manifest dumped to `logs/migrations/prewipe_20260723T030026Z` before deletion. 11 of 12
workspaces and 2 of 3 Box roots were actually deleted — `Forfront IL portfolio`
(`accessLevel=ADMIN`, not owned by the ITS account) and the Box `ITS DATA` root (not owned) were
correctly refused by the allowlist rather than force-deleted; `ITS DATA` surviving turned out to
preserve the 1111A/1111B templates the stand-up needed anyway.

The stand-up ran with 5 resume points; each one exposed a real defect in the never-before-live
create path, fixed via a same-day PR before the run continued:

- **#665** (`7508253`) — `build_system_sheets` column descriptions over 250 characters 400'd
  4 of 5 sheet creates (Smartsheet errorCode 1041). The create path had never been exercised
  live before this rehearsal.
- **#666** (`b7b0e8d`) — a Smartsheet create→read propagation race: `sheet_ids_regen` run
  immediately after a builder stage resolved everything as MISSING, then resolved correctly
  2 seconds later, flipping nothing on the first pass.
- **#667** (`666b198`) — the convergence heuristic from #666 was still too eager (a retry loop
  guessing at "probably converged now"); replaced with a deterministic builder→flip contract
  (`--expect`, a `REGEN_EXPECT` table naming exactly which ids each stage should produce).
- **#668** (`d272a84`) — **operator-caught**: the manual gate treating Smartsheet's `Job ID`
  column as `AUTO_NUMBER` was stale pre-Slice-6 doctrine. Verified 3 independent ways before
  acting — the pre-wipe dump showed the live column typed `TEXT_NUMBER` holding portal-allocated
  values (`JOB-000017`, `JOB-000018`, `JOB-000027`, `JOB-000028`); `active_jobs_writer` writes the
  cell on every upsert; migration `0022`'s own commit notes recorded the retype. `phase3` of
  stand-up now API-creates `Job ID` and `Portal Job Key` as TEXT columns; the manual gate step was
  deleted outright; 11 files carrying the stale doctrine were corrected, including the §43 runbook
  `docs/runbooks/safety_portal_job_management.md`.
- **#669** (`cad7cfe`) — the final `verify_cutover` VC-03 pass failed 15 of 46 rows: 15
  `ITS_Config` rows on the live (pre-wipe) tenant had only ever been hand-created by an operator,
  never by any seeder script. Seeded (gates left dark) plus a new CI test,
  `test_every_vc03_config_row_has_a_seeder`, closing the class so a future hand-created row can't
  silently escape the rebuild again.

Completion: the operator explicitly authorized (§44 high-class, doctrine-adjacent) a full restore
of every pre-wipe gate value rather than leaving the rebuilt tenant dark — 21 gates including the
4 send dispatchers. Post-restore, `verify_cutover --only config --allow-sandbox` PASSed 46/46 and
`sheet_ids_regen --check` reported PARITY OK.

## Phase 3 — landing + verification

- **#670** (`040b7fd`) — landed the regenerated ID surfaces from the rebuild. The wipe
  allowlist's own ids were deliberately kept at their historical (pre-rebuild) values and
  exempted from the remap — the allowlist's job is recognizing the *previous* tenant's sheets on
  a future wipe, not tracking the current one. `doctrine_manifest.yaml`'s id references needed a
  hand-fix in the same PR; `check_doctrine_drift` caught the mismatch before merge.
- **#671** (`5d06a2e`) — seeded 11 more hand-created-only config rows the post-reload sweep found
  (the field-ops row-cap thresholds, whose absence was WARNing on every 90-second cycle — the
  same class of storm identified 2026-07-13).
- Fleet reload: 20/20 daemons back up, 14/14 heartbeats self-provisioned cleanly against the
  rebuilt `ITS_Daemon_Health` sheet, `ITS_Errors` stable at 10 startup WARNs (all resolved
  config-row gaps, not a new failure mode), dashboard returning 200.
- Data restored from the pre-wipe dump: 4 jobs (Job IDs preserved verbatim), 33 vendors,
  24 subcontractors, the master DBs, and the F22 approver share lists.

## Phase 4 — post-rehearsal reviews + parallel sessions

Two review workflows ran against the freshly rebuilt tenant:

- **Optimization review** — 24 findings across 3 lenses; dossiers at
  `logs/reviews/2026-07-23_opt_*.json`.
- **Archive audit** — verdict: archive-on-closure is *defined* for only 4 tracker sheets, and
  *implemented* for only the portal-origin `lifecycle='archived'` path — it has never actually
  been proven, since the Archive workspace held 0 sheets pre-wipe. Dossiers at
  `logs/reviews/2026-07-23_arch_*.json`.

The operator spun up two parallel Opus 4.8 sessions against inline briefs (one per review, fenced
territories to avoid file collisions) — the optimization session landed **#673** (`747e220`),
**#675** (`a4f6d5c`), and **#676** (`bf574a0`) during this session's close; those are that
session's PRs, not this one's, and are recorded here only for cross-reference completeness.

This session additionally landed:

- **#672** (`e74998a`) — `system_map.py` now reads its sheet ids from `shared.sheet_ids` instead
  of carrying its own copies (operator directive: single source of truth). A literal-ban guard
  test enforces it; the regen script's own scope shrank correspondingly.
- **#674** (`249afe9`) — the stand-up ACT fence. A marker file
  (`~/its/state/standup_in_progress.json`) causes `auth.py` to refuse every dashboard ACT verb
  while the marker is fresh (< 6h), fail-open on a stale or corrupt marker;
  `StandupFenceError` subclasses the existing `PinError` hierarchy. Both daemon-down guards
  (wipe and stand-up) explicitly exempt the dashboard process itself — it stays up through a
  future wipe/rebuild window rather than going dark with everything else. Merge required
  resolving a cross-session conflict with #676, where the conftest live-state guard caught the
  one place the two branches' changes genuinely overlapped.

## Decisions made during session

- **Full Box + all-12-workspaces scope, taken literally.** The operator's "nuke everything"
  included the demo workspace and Box in full rather than a narrower reading — confirmed by
  direct Q&A before the destructive step rather than assumed.
- **D1/Cloudflare deliberately untouched.** Operator: keep the URL and account as-is. This leaves
  known, already-documented deviations in place across the wipe/rebuild boundary — D1
  "watermark amnesia" (the D1-side dedupe watermarks don't reset with the Smartsheet/Box wipe),
  sha-dedupe 409s against pre-wipe content hashes, and dead `box_file_id` backrefs in D1 rows
  pointing at now-deleted Box files. None of these were treated as bugs to fix this session —
  they're the expected shape of "only the Smartsheet/Box side was wiped."
- **Trusted-contacts lane kept dormant** through the rebuild, matching its pre-wipe state — no
  new activation decision was made incidentally by the rehearsal.
- **Config gates seeded dark, then operator-authorized to full restore** rather than either
  leaving them all dark (safer default, but would misrepresent an actual cutover rehearsal) or
  restoring them silently (a gate flip is a documented high-capability-class action, §44) — the
  authorization was explicit and named 21 gates including all 4 send dispatchers.
- **Wipe-allowlist ids stay pinned to historical values, not remapped.** The allowlist's purpose
  is recognizing a *previous* tenant on a *future* wipe; remapping it to the just-rebuilt ids
  would defeat that purpose the next time this tooling runs.
- **Deterministic `--expect` contract over a convergence-heuristic retry** (#667, superseding
  #666's first attempt) — a retry-until-it-looks-converged loop is exactly the class of "green
  proves nothing" control HOUSE_REFLEXES §2 warns about; naming the expected id per stage makes a
  silent non-convergence structurally impossible to pass.
- **The AUTO_NUMBER Job ID gate was deleted, not patched** (#668) — once verified three
  independent ways that the live column is TEXT and portal-written, keeping a vestigial
  "convert to AUTO_NUMBER" step would have reintroduced exactly the doctrine-vs-code drift
  HOUSE_REFLEXES §1 exists to prevent.
- **New CI teeth (`test_every_vc03_config_row_has_a_seeder`) rather than a one-time fix** for the
  15 hand-created config rows (#669) — the same gap (an operator hand-creates a row, no seeder
  script ever reproduces it) will recur on the next rebuild without a standing check.
- **Dashboard exempted from both daemon-down guards** (#674) — the operator directive was that
  the dashboard stays observable through a wipe/rebuild window; the ACT-verb fence, not a
  daemon-down shutdown, is the control that keeps ACT operations safe during that window.

## Verification

Overall final full gate (fence branch, #674):
- pytest: 4370 passed / 2 skipped / 49 deselected
- mypy: Success: no issues found in 459 source files
- ruff: clean

**Four-part landing verify, quoted from live `gh` state at drafting time:**

```
PR #664 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T02:42:37Z
- mergeCommit: 7f86a13981e925d211648a253d6fd07a7d4e3039
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #665 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T03:33:46Z
- mergeCommit: 75082531130098adf7778b58f26daf304bd89b8a
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #666 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T03:51:26Z
- mergeCommit: b7b0e8d8abe2c351909cf97ea042a6f26fba7068
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #667 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T04:07:10Z
- mergeCommit: 666b1985da2059977217e20443bdd3da44145032
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #668 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T13:44:04Z
- mergeCommit: d272a84f38885dc165b38c2afeaad88de037c1ab
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #669 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T14:03:05Z
- mergeCommit: cad7cfe90942edfdc36d6f117aff7776d946bf68
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #670 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T14:24:17Z
- mergeCommit: 040b7fdbdca12ba00d8b1f7a76e47e62ad450fd4
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #671 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T14:37:56Z
- mergeCommit: 5d06a2e904f3dd85a5216aaa49b77f00bcfff7fe
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)

PR #672 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T15:32:37Z
- mergeCommit: e74998ad3febc980fe13bce7e901db5a07c43d03
- main-branch CI on merge commit: SUCCESS (ci: success, Push on main: success)
```

**PR #674 — four-part verify clean** (fourth leg confirmed post-draft, same session). The
first three legs: state=MERGED, mergedAt=2026-07-23T16:28:11Z non-null,
mergeCommit=249afe9d879bd7fa4d63b86b016cf646dba9be5e present. The fourth leg was still
`status: in_progress` at drafting time; the orchestrating session's watcher subsequently
recorded, verbatim: `main ci on 249afe9: completed:success`. All ten of this session's PRs
(#664–#672, #674) are therefore four-part verify clean.

**Informational — parallel optimization-session PRs, four-part verify clean at time of this
writing (not authored by this session; recorded for cross-reference only):**

```
PR #673 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T15:53:29Z
- mergeCommit: 747e2207262a1fd57f7cad44cc050e0337f0c768
- main-branch CI on merge commit: SUCCESS

PR #675 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T16:03:19Z
- mergeCommit: a4f6d5c57b313190a3fada8a547c9a91717e20d2
- main-branch CI on merge commit: SUCCESS

PR #676 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-23T16:11:09Z
- mergeCommit: bf574a099272bcd8c45fc61bd2bc6954941c87e4
- main-branch CI on merge commit: SUCCESS
```

## Open items handed off

- ~~Confirm #674's fourth leg~~ — RESOLVED in-session: `main ci on 249afe9:
  completed:success` (recorded above); #674 is four-part clean.
- **Archive-on-closure is unproven, not just incomplete.** The Phase 4 archive audit's verdict —
  defined for 4 tracker sheets, implemented for only the portal-origin `lifecycle='archived'`
  path, never actually exercised (Archive workspace was empty pre-wipe) — is a real gap, not a
  documentation gap. Needs a deliberate test before it can be trusted at the next real archive
  event.
- **24 optimization findings** from the Phase 4 review remain to be triaged (dossiers at
  `logs/reviews/2026-07-23_opt_*.json`); #673/#675/#676 addressed some subset via the parallel
  session — the remainder, if any, needs a follow-up pass.
- **D1-side deviations from the wipe/rebuild boundary** (watermark amnesia, sha-dedupe 409s
  against pre-wipe hashes, dead `box_file_id` backrefs) are known and accepted for this
  rehearsal per the operator's D1-untouched decision, but a REAL production cutover (not a
  rehearsal) will need a decision on whether D1 gets wiped in lockstep or whether these
  deviations are permanently acceptable.

## What was NOT touched

- Cloudflare / D1 — explicit operator decision to leave the Worker, its URL, and all D1 tables
  exactly as they were before the Smartsheet/Box wipe.
- No External Send Gate flip was made as part of this rehearsal itself — the full gate restore
  in Phase 2 restored the 21 gates (including the 4 send dispatchers) to their pre-wipe values,
  it did not newly activate anything beyond what was already live before the wipe.
- No doctrine file was edited this session beyond the mechanical id-reference fix in
  `doctrine_manifest.yaml` (#670), which `check_doctrine_drift` treated as a drift-correction,
  not a doctrine content change.
- Trusted-contacts lane — left dormant through the rebuild, matching its pre-wipe state.

## Lessons captured to memory

- The #668 `Job ID` AUTO_NUMBER-vs-TEXT catch is a clean instance of HOUSE_REFLEXES §1 ("trust
  the live code, never the claim") — a manual gate step in the stand-up orchestrator was itself
  a stale claim about the schema, caught only because the operator questioned it against the
  pre-wipe dump rather than trusting the existing tooling's own assumption.
- The #669/#671 hand-created config rows (15, then 11 more) are a live instance of HOUSE_REFLEXES
  §5's dark-gate-seeding discipline, applied in reverse — a rebuild is the forcing function that
  surfaces every config row an operator ever hand-created outside a seeder, and the fix
  (`test_every_vc03_config_row_has_a_seeder`) makes the gap self-detecting on the next rebuild
  rather than requiring another live tenant destruction to find it again.
- The #666→#667 convergence-heuristic-to-deterministic-contract swap is a small case study for
  HOUSE_REFLEXES §2 ("prove the control bites") — a retry loop that eventually looks converged is
  not the same as a check that names what convergence means and asserts it.

## Cross-references

- `docs/HOUSE_REFLEXES.md` §1 (trust the live code, never the claim — the Job ID catch), §2
  (prove the control bites — the convergence-heuristic replacement), §5 (observable config /
  dark-gate seeding — the hand-created VC-03 rows).
- `docs/runbooks/safety_portal_job_management.md` — corrected in #668 (Job ID is portal-written
  TEXT, not AUTO_NUMBER).
- `docs/operations/pr_merge_discipline.md` — the four-part verify discipline applied above,
  including the explicit "not yet clean" call on #674.
- `scripts/migrations/` — `wipe_tenant.py`, `sheet_ids_regen.py`, `standup.py`,
  `build_legacy_workspaces.py` (new this session, PR #664).
- Immediately preceding session:
  `docs/session_logs/2026-07-22_cutover-migration-builders-and-log-growth-fixes.md` (the
  pre-builder-family gap builders this rehearsal exercised for the first time live) and
  `docs/session_logs/2026-07-22_dashboard-refinement-and-cutover-day1.md` (the operator-dashboard
  work #674's ACT fence builds on).
