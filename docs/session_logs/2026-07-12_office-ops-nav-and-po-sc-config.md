---
type: session_log
date: 2026-07-12
status: closed
workstream: subcontracts
related_prs: [541, 542, 546, 544, 548]
---

# Session — Office Operations nav + PO/SC Configuration subcontract editors (PRs #541/#542/#546 landed; #544/#548 held for operator; PR-B2 mapped, not started)

Executed the 2026-07-12 next-session brief (`docs/cc-brief_office-ops-nav-and-po-sc-config.md`) — Feature A
(home-nav reorg) then Feature B (subcontract config editor), both SPA-only against an already-live backend
(the subcontract generator shipped 100% built + deployed on `seths@evergreenmirror.com` per PRs #529–#540).
Two add-on fixes surfaced beyond the brief's scope during the session (#542's trade-overwrite bug, discovered
live-testing the builder; #544's attach-kind fence, discovered exercising a `negotiated_msa` subcontract) plus
one operator-requested feature (#548, Site-address auto-fill). A concurrent, unrelated documentation-
reconciliation thread (WP1–WP5, PRs #543/#545/#547/#549 + blueprint #65) landed interleaved on `main` during
this session with no file collision — see its own log, `docs/session_logs/2026-07-12_documentation-
reconciliation.md`; not re-narrated here beyond the session-close cross-reference.

## Commits landed

- **#541** `f57559c` — `feat(portal): Office operations home-nav section + PO/SC Configuration card rename`.
  `HomePage.tsx` gains a new "office" `HOME_SECTIONS` entry between the existing field and admin sections;
  Purchase Orders / Subcontracts / Checklists / Materials Catalog / Vendors / Subcontractors all move into it.
  The `po-config` card is renamed "PO Configuration" → "PO/SC Configuration", anticipating #546's content.
  SPA-only, no worker/migration touch.
- **#542** `226f509` — `feat(portal): subcontract builder UX — trade overwrites Exhibit A + calendar date
  pickers`. Found live-testing the builder after #541: `onTradeSelect`'s only-if-empty guard left stale
  Article II text from a previous trade selection when the operator switched trades mid-draft. Now
  unconditionally overwrites on every trade change (a failed template fetch still does not clobber). Start/
  Completion date inputs switched from free-text to native `<input type="date">`.
- **#546** `cdd38a8` — `feat(portal): PO/SC Configuration — subcontract Contractor + terms editors (v1)`. The
  session's largest build, two things at once: (1) a §14 extraction of the terms-library editor (add_version
  / make-current / create_profile + the profiles display) out of `PoConfigPage` into a shared, workstream-
  parameterized `components/TermsProfilesEditor.tsx`, rendered twice (`po_materials` and `subcontracts`) —
  net −280 lines in `PoConfigPage`, existing PO terms tests pass unchanged through it, the regression net
  that proves the extraction didn't silently change PO behavior; (2) the subcontracts "coming soon"
  placeholder replaced with a Contractor-identity json editor (payload matches the actuator's existing
  `_apply_contractor_edit`) + the shared terms editor. Both gated on `cap.subcontracts.manage`. Payment-terms
  editing and Exhibit-A editing deliberately deferred to PR-B2 (needs a worker deploy — building the SPA now
  would let the operator submit a payload the actuator can validate but the SPA can't source current values
  for). Gate: `npm run typecheck` (3 tsconfigs) clean, `test:spa` 636/636, `build` clean.

**Held, not merged — both `gh pr view`-verified `OPEN`/`MERGEABLE`, both behind `main`:**

- **#544** (`feat/attach-kind-reference`, worktree `~/its-attach`) — fixes a real fence: an `attach`-kind
  terms profile (`negotiated_msa`) had no library text to load, so a valid negotiated-MSA subcontract could
  never file. `render_body_text` now branches on `terms.get_profile(kind)`; `attach` renders a one-page
  reference from a new sha-pinned `subcontracts/terms/attach_reference.md` — PURE VERBATIM fragments (the
  standard body's preamble + §2.1 Contract Price + signature block) plus the profile's manifest
  `render_line`, no library-text load, no legal-review gate (correctly — the fragments are already covered
  by `standard_subcontract`'s cleared `legal_review`). An ops-stds review BLOCKED an earlier draft with
  paraphrased/invented clauses; rewritten pure-verbatim, re-review CONFIRMED-RESOLVED. `manifest.json`'s
  `negotiated_msa` description + ADR-0003 decision #9 updated. HELD because it touches ADR-0003 + the
  manifest design description (doctrine-adjacent docs), not because of any review failure — ready to merge
  on the operator's review.
- **#548** (`feat/subcontract-job-address`, worktree `~/its-c1`) — auto-fills the builder's Site address from
  the `ITS_Active_Jobs` Smartsheet SoR (operator-requested "C1"). `portal_poll._push_active_jobs` adds
  `address` to the existing `/api/internal/sync` payload (no new sync call, no migration — `jobs.address`
  exists from migration 0021); the sync route accepts + bounds (`>512` rejects the batch) + stores it; new
  `GET /api/subcontracts/jobs/:job_id/site-address` (`cap.subcontracts.manage`) mirrors the PO ship-to-
  address pattern; `SubcontractBuilderPage.onJobSelect` fetches and fills, degrading to manual on blank/404
  (never clobbers an operator-typed value). `portal-worker-security-reviewer` verdict CLEAN 13/13. HELD for
  the operator's worker deploy + a live down-sync smoke.

**Mapped, not built — PR-B2 (Exhibit-A versioned+gated editing + subcontract payment-terms editing):**
operator-directed ("build Exhibit-A now, versioned + gated") but deliberately left for the operator's
presence — an Explore agent scoped it as one large, atomic Python+worker+SPA change needing both a worker
deploy and a Layer-A legal-attestation seed (the 7 existing trade templates need `legal_review=cleared`, the
same operator attestation already applied to `standard_subcontract` v1). Full scope recorded in
`docs/tech_debt.md` under **PR-B2** — read that entry before picking this up, not this log.

## CI runs

Four-part verify run directly against the merge commit for each of this session's three landed PRs (not
just `gh pr view`'s state/mergedAt/mergeCommit.oid triad):

| PR | mergeCommit | mergedAt | `test` | `portal` | `secrets` | CodeQL ×3 | Verdict |
|---|---|---|---|---|---|---|---|
| #541 | `f57559c9` | 2026-07-12T17:36:47Z | success | success | success | success | four-part clean |
| #542 | `226f5097` | 2026-07-12T17:56:30Z | success | success | success | success | four-part clean |
| #546 | `cdd38a81` | 2026-07-12T18:49:58Z | success | success | success | success | four-part clean |

Verified via `gh api repos/SolutionSmith-debug/its/commits/<sha>/check-runs` on each merge commit directly.
`#544` and `#548` were NOT re-checked for CI on their feature branches beyond `mergeable: MERGEABLE` — they
are open, behind `main`, and need `gh pr update-branch` before their own CI is meaningful post-merge-with-
`main`.

**Local re-verify at current HEAD (`fbd77a0`, post-#549 — see "concurrent landing" below):**

```
- pytest: 3259 passed / 0 failed / 33 deselected (dot-count verified — the final summary line is
  suppressed by the same pytest-cov ordering quirk already named in the 2026-07-10 log; both the
  local run and the CI `test` job's own coverage TOTAL line print, but neither prints "N passed")
- mypy: 0 errors / 360 source files
- ruff: clean
- main-branch CI on merge commit fbd77a0 (#549): SUCCESS
```

## Decisions made during session

1. **#542 and #544 were built as separate PRs from #541/#546, not folded in.** Both were bugs discovered
   live-testing the surfaces #541/#546 had just shipped (trade-overwrite; the attach-kind fence), not part of
   the original brief — kept as their own PRs so each has an isolated, reviewable diff and its own CI/review
   trail rather than silently growing an already-merged PR's scope.
2. **#544 was HELD for operator merge rather than self-merged**, even though the fix itself is code +
   green CI — it touches ADR-0003 + the `manifest.json` design-description text, which is doctrine-adjacent
   documentation outside this session's autonomous-merge authorization. The distinction: the RENDER logic
   change (`render_body_text`, `terms.py`) would have been in autonomous-merge scope on its own; it was the
   accompanying ADR/manifest-description edit that moved the whole PR to HELD.
3. **#548 was HELD for a live worker deploy + smoke, not for review reasons** — `portal-worker-security-
   reviewer` cleared it 13/13. The boundary here is operational (a worker deploy is a step this session
   doesn't take unattended for a change touching Smartsheet-SoR-derived data flowing to a live worker), not
   a code-quality gate.
4. **Payment-terms editing and Exhibit-A editing were both deferred to PR-B2 rather than partially built.**
   Building the subcontracts payment-terms SPA form now would let the operator submit a well-formed payload
   the actuator (`_apply_payment_terms_edit`) can validate, but the served `/api/subcontracts/config` route
   doesn't expose `application_for_payment_day`/`progress_payment_day` yet — there'd be no source of truth to
   pre-fill or round-trip against. Rather than ship a form that always submits blank/guessed values, the
   whole feature (plus Exhibit-A, which needs its own worker route + manifest-schema versioning + a legal-
   attestation seed) was scoped as one deploy-gated follow-up.
5. **The §14 terms-editor extraction was verified with a regression net, not just built and trusted.**
   `components/TermsProfilesEditor.tsx` is used by BOTH `po_materials` and `subcontracts` now; the existing
   PO terms tests were run against the extracted component unchanged (not rewritten to match the new shape)
   specifically to prove the extraction preserved PO behavior rather than just visually resembling it.
6. **A second `git fetch` was run mid-maintenance-pass rather than trusting the first.** Per the standing
   "fetch first, then survey against origin/main — never the stale local tree" discipline, a routine re-check
   partway through this session-close pass caught PR #549 landing on `main` from the concurrent doc-
   reconciliation thread — re-verified four-part clean and folded into the archive/info-gap entries rather
   than closing out against a tree that had already gone stale by the time of writing.

## Open items handed off

- **PR-B2 (Exhibit-A versioned+gated editing + subcontract payment-terms editing)** — full 7-point scope
  in `docs/tech_debt.md` under **PR-B2**; needs a worker deploy + a Layer-A legal-attestation seed (7 trade
  templates → `legal_review=cleared`), so it needs the operator present. Suggested next-session opening move:
  read the PR-B2 tech-debt entry, then the `exhibit.py`/`config_apply.py` files it names, before drafting a
  build brief.
- **#544 — operator merge.** `gh pr update-branch 544` (currently behind `main`), review the ADR-0003 +
  manifest-description diff, merge. No code concerns outstanding — ops-stds review already confirmed clean
  on a re-review after the pure-verbatim rewrite.
- **#548 — operator deploy + smoke.** `gh pr update-branch 548`, merge, `npm run deploy` from
  `safety_portal/`, then confirm a real `ITS_Active_Jobs` address round-trips `portal_poll` → Worker →
  `SubcontractBuilderPage`'s Site-address field.
- **CE-7 (payment-terms editing gap)** — filed in `docs/tech_debt.md` under the existing "Config editor (§50)
  — deferred follow-ups" section; folds into PR-B2.
- **SC-CFG-1 (`attach_reference.md` v2-divergence, informational)** and **SC-CFG-2 (`index.ts` hardcodes
  `512` instead of importing the shared `MAX_ADDRESS` constant, cosmetic)** — both filed in a new
  `docs/tech_debt.md` section, "Subcontracts — PO/SC Configuration + builder follow-ups."

## What was NOT touched

- **No worker deploy this session** — #548 needs one and it deliberately was not run unattended.
- **No doctrine edit** — #544's ADR-0003 + manifest-description change is staged in the HELD PR, not applied
  ahead of operator review.
- **No Exhibit-A manifest-schema change** — PR-B2 was scoped, not started; `subcontracts/exhibit/
  manifest.json` is untouched this session.
- **No legal-attestation seed applied** — the 7 Exhibit-A trade templates remain without a `legal_review`
  field; PR-B2 needs that seed before its render-path gate can go live.
- **The concurrent doc-reconciliation PRs (#543/#545/#547/#549) were not authored by this thread** — reviewed
  only far enough to confirm no file collision and to fold their landing into this session's own
  session-close survey; their own content is that thread's session log, not re-derived here.

## Lessons captured to memory

- **`docs/tech_debt.md` §"Config editor (§50) — deferred follow-ups"** — new entry **CE-7**: subcontract
  payment-terms editing is blocked on the served `/api/subcontracts/config` route not yet exposing the
  actuator's required day-fields; folds into PR-B2.
- **`docs/tech_debt.md` new §"Subcontracts — PO/SC Configuration + builder follow-ups"** — **SC-CFG-1**
  (attach_reference.md's sha-pinned verbatim fragments won't auto-flag as diverged against a future
  `standard_subcontract_v2`, informational only) and **SC-CFG-2** (`worker/index.ts`'s inline `512` address
  bound duplicates the `MAX_ADDRESS` constant already defined independently in three other worker files,
  cosmetic) — plus the full **PR-B2** scope write-up.
- **`~/its-blueprint/references/memory-archive.md` §G63** — full narrative of #541/#542/#546 (built) +
  #544/#548 (held) + PR-B2 (mapped, not built), plus §G63.1 folding in the concurrent WP1/WP2/WP1.5/WP5/
  blueprint-#65 doc-reconciliation batch that resolved the §G62 "needs its own close pass" gap, and §G63.7
  documenting the mid-pass PR #549 collision caught by a second `git fetch`.
- **`~/its-blueprint/references/claude-code-info-gap.md`** — §8 "Recently landed" dated paragraph + "Open
  queue" bullet added for this session; frontmatter `last_verified_against` moved to `fbd77a0`.
- **Auto-memory `project_subcontracts-workflow.md` and `project_config-editor-build.md`** — both updated
  with this session's PRs, the two HELD PRs' operator-gated next steps, and the PR-B2 scope, so a fresh
  session picks up the subcontracts-config thread without re-deriving it from PR bodies.

## Cross-references

- `docs/cc-brief_office-ops-nav-and-po-sc-config.md` — the originating brief (Feature A / Feature B).
- `docs/session_logs/2026-07-12_documentation-reconciliation.md` — the concurrent, unrelated WP1–WP5 doc-
  reconciliation thread that landed interleaved on `main` this session with no file collision.
- `docs/tech_debt.md` — CE-7, SC-CFG-1, SC-CFG-2, PR-B2 (all new this session).
- `docs/adr/0003-subcontract-generation-workflow.md` — decision #9, updated by the HELD #544 (not yet
  applied to `main`).
- `~/.claude/projects/-Users-sethsmith-its/memory/project_subcontracts-workflow.md` and
  `project_config-editor-build.md` — auto-memory topic files updated this session.
- `~/its-blueprint/references/memory-archive.md` §G63 — full narrative detail for this session.
