---
type: session_log
date: 2026-07-10
status: closed
workstream: null
related_prs: [514, 511, 518, 520]
tags: [session_log, purchase_orders, po_materials, config-editor, section50, terms-editing,
  layer-a-legal-gate, self-defeating-ci-test, multi-surface-fan-out, four-part-verify,
  adr-0002, adversarial-review, house-reflexes]
---

# Session — Config-editor follow-ups (fix the self-defeating CI test, CE-3) + terms editing (Step 2, slices T1/T2)

Same-day continuation of the earlier 2026-07-10 session that built and live-activated the generic
§50 config editor (PRs #504–#512). That session's first live purchaser edit (PR #511) hit a
self-defeating CI test and stranded — diagnosed live, banked as tech_debt **CE-3**, and handed off
as "the next session's first move." This session lands that fix first (#514), closes the stranded
PR (#511), then builds the two-slice terms-editing vertical (T1 pre-fill, T2 make-current + the
Layer-A legal gate), resolving tech_debt **CE-2** in the same pass. All work landed in ISOLATED
worktrees, PR-merged to `main`; nothing committed in the live `~/its` tree. Three PRs merged, one
closed (not merged); every merge four-part-verify clean.

## Commits landed

- **#514** `ca9c776` — `fix(config): assert config SHAPE not CONTENT so §50 editor edits pass CI +
  reconcile ADR-0002`. Converts every editable-config content pin to shape / round-trip /
  served-equals-source / derive-from-source so any purchaser/tax(/terms) edit passes CI without
  weakening what the tests verify. An exhaustive enumeration found **two** merge-blockers where the
  initial brief had named one:
  - `tests/test_config_apply.py` — was byte-copying the live config files as fixtures (inheriting
    the live `config_version` → an absolute `==2` assert); now seeds FIXED fixtures at a
    non-1 sentinel and asserts versions RELATIVE (`new == seed + 1`).
  - `tests/test_po_terms.py` — was asserting the exact live purchaser/tax content (entity, address,
    phone, email, cc list, exact `rates_bp` table); now asserts shape only. **This was the blocker
    the initial brief missed.**
  - `safety_portal/test/po.test.ts` — imports the SAME bundled config the Worker serves, derives
    EXPECTED tax math from `taxConfig.rates_bp.IL` (still independently checked, but now tracks any
    tax edit rather than pinning it), and asserts served-config == imported source.
  - Confirmed SAFE and left untouched: `test_po_generate.py` (local `RATES_BP` + Worker-parity
    pins), all SPA `__tests__` (mocked), `config.test.ts` (edit-request payloads), `po.money.test.ts`
    (own input).
  - Guard banked to `docs/HOUSE_REFLEXES.md` §5 — "Never pin EDITABLE-config CONTENT in a test —
    assert shape / round-trip / served-equals-source" — naming the recurrence (form-publish catalog
    counts #222/#228, now config-editor #511) so a third §50 instantiation doesn't hit it blind.
  - **Step 1b** — `docs/adr/0002-po-config-editor-privileged-actuation.md` reconciled: records
    fully-automatic (C12 = A) as the CHOSEN actuation per the prior session's live operator decision,
    propose-mode preserved as the superseded initial recommendation; `status: proposed` → `active`;
    `related_prs` extended to include 508/509/510/512.
  - **Prove-the-control-bites**: both suites pass on the real config AND under a SIMULATED edit
    (purchaser entity/to/cc-count + tax IL 900→750 + CA + terms `add_version`); a synthetic
    wrong-entity serve-route bug makes served-equals-source RED — non-vacuity confirmed both ways.
- **#511** — `chore(po-config): purchaser: Evergreen Renewables LLC -> config_version 2 (req 1)` —
  **CLOSED, not merged.** This was the stuck smoke-test purchaser edit from the prior session (both
  `test` and `portal` checks permanently `FAILURE` against the pre-#514 tests). With #514 landed on
  `main`, a fresh submission of the same edit via the portal now passes CI clean — #511 itself was
  closed rather than force-merged or retested as-is.
- **#518** `047965c` — `feat(po-config): terms edit-text pre-fill — serve the current clause body
  (Step 2, slice T1)`. The editor already mints a NEW terms version (`op:add_version`), but the
  "edit text" textarea started empty. `safety_portal/worker/po.ts` uses
  `import.meta.glob<string>(".../po_materials/terms/*.md", {query: "?raw"})` to bundle every terms
  clause body at build time (auto-discovers versions `add_version` mints — a static per-file import
  would silently miss new ones), plus a new read-only `GET /api/po/terms/:profile_id/text`
  (`gates.requireSession` + `cap.po.manage`) that resolves the profile's `current_version` → file →
  glob map, strips the leading `<!-- -->` provenance header (a TS port of
  `terms._strip_header_comment`), and serves `{profile_id, version, text}`. Library profiles only;
  attach/unknown/absent → 404. No mutation, no audit row, no send. Typed via
  `/// <reference types="vite/client" />` + the generic `glob<string>` form. New tests assert
  shape/derived values (version from `termsManifest.current_version`; text non-empty, no leading
  `<!--`), never pinned clause content — HOUSE_REFLEXES §5 applied on first use. Does NOT touch the
  legal gate or `current_version` — that is T2.
- **#520** `564dfa7` — `feat(po-config): terms make-current + Layer-A legal gate (Step 2, slice T2)`.
  The legal-activation half of the terms editor. `po_materials/terms.py::_version_entry` now RAISES
  for a library version whose `legal_review != "cleared"` — the single choke point shared by
  `load_terms_text` + `required_tokens`, firing on an explicit pin OR the `current_version` default;
  at render this fences the PO (`po_poll` → Review Queue) rather than silently rendering un-cleared
  contract language. Shipped in lockstep with its two required predecessors: (1)
  `po_materials/terms/manifest.json` backfills both shipped versions (`standard_17_v1`,
  `chint_vendor_v1`) to `legal_review: "cleared"` (operator-confirmed clearance) in the SAME change —
  without the backfill the gate would fence every live PO; (2) a new `set_current` config op
  (`po_materials/config_apply.py::_apply_terms_set_current`) atomically clears `legal_review` and
  repoints `current_version` in one manifest write — the ONLY writer that advances
  `current_version`. Multi-surface fan-out applied deliberately in one PR: `safety_portal/worker/
  config.ts` `CONFIG_OPS += set_current` + `target_version` validated; migration `0046` widens the
  `config_requests.op` CHECK (SQLite table-recreate) in lockstep with the Worker + `config_apply`
  dispatch; `GET /api/po/terms/:id/versions` (curated `{version, legal_review}` + `current_version`,
  `cap.po.manage`, read-only, own-property guarded) feeds the make-current picker; `lib/po.ts` +
  `PoConfigPage.tsx` add a confirmable "Make a version current" UI gated on a REQUIRED "I have
  reviewed this version's legal text" checkbox before the `op:set_current` submit, plus the
  exhaustive `Record<ConfigOp>` status-monitor label extended for the new op. Realizes the deferred
  **CE-2** render-side `legal_review` refusal (Layer A now built). Merge conflict against #519
  (parallel WS2 operator-dashboard PR touching the same doc index) resolved by regenerating the
  auto-index rather than hand-merging.

## CI runs

Four-part verify run per PR (`gh pr view --json mergedAt,mergeCommit,state` then
`gh run list --branch main --commit <sha>`), and confirmed directly against `gh` at session-log-write
time — `~/its` HEAD (`origin/main`) is exactly `564dfa7`, the last of these three merges:

| PR | mergeCommit | state/mergedAt | `ci` job | `Push on main` | Verdict |
|---|---|---|---|---|---|
| #514 | `ca9c7765...` | MERGED @ 2026-07-10T17:23:11Z | success | success | clean |
| #511 | — | **CLOSED** @ 2026-07-10T17:33:04Z (not merged) | FAILURE ×2 (pre-#514 tests; not re-run) | — | not applicable — closed, not landed |
| #518 | `047965ca...` | MERGED @ 2026-07-10T18:01:18Z | success | success | clean |
| #520 | `564dfa7b...` | MERGED @ 2026-07-10T19:11:04Z | success | success | clean |

Verbatim per-PR verify results:

- **PR #514 — four-part verify clean.** `state=MERGED`, `mergedAt=2026-07-10T17:23:11Z`,
  `mergeCommit.oid=ca9c77651652eb6efee64dd884f975b3041a2d53` present; `gh run list --branch main
  --commit ca9c776...` returns `ci: success` / `Push on main: success`.
- **PR #518 — four-part verify clean.** `state=MERGED`, `mergedAt=2026-07-10T18:01:18Z`,
  `mergeCommit.oid=047965cab95cfaaeec297f167c8626ac8d9ef8a9` present; `gh run list --branch main
  --commit 047965c...` returns `ci: success` / `Push on main: success`.
- **PR #520 — four-part verify clean.** `state=MERGED`, `mergedAt=2026-07-10T19:11:04Z`,
  `mergeCommit.oid=564dfa7ba6e048e67229907dc44d63b6db89ff50` present; `gh run list --branch main
  --commit 564dfa7...` returns `ci: success` / `Push on main: success`.

**Cumulative local re-verify at current HEAD (`564dfa7`, post-#520) — the four-part block:**

```
- pytest: 3002 passed / 0 skipped / 48 deselected
- mypy: 0 errors / 326 source files
- ruff: clean
- main-branch CI on merge commit 564dfa7: SUCCESS
```

(`.venv/bin/python -m pytest -v` → `3002 passed, 48 deselected, 2 warnings in 38.34s`;
`.venv/bin/mypy .` → `Success: no issues found in 326 source files`; `.venv/bin/ruff check .` →
`All checks passed!`. Note: the CI `test` job's own `pytest -q` step did not print its final summary
line in the captured GitHub Actions log text — the same pytest-cov ordering quirk already named in
the prior 2026-07-10 log — so the count above is a local re-run against the identical merge commit,
after refreshing the local `.venv` per the "new package → refresh live venv" reflex, since
`pyproject.toml` had gained `fastapi`/`uvicorn`/`python-multipart` from the parallel WS2
operator-dashboard PRs (#516/#519) interleaved on `main` between #514 and #518.) Worker vitest and
SPA vitest counts, confirmed from the #520 `ci` run log: `Test Files 60 passed (60)` / `Tests 933
passed (933)` (worker); `Test Files 49 passed (49)` / `Tests 612 passed (612)` (SPA); `vite build`
succeeded.

## Decisions made during session

1. **The exhaustive enumeration found more blockers than the handed-off diagnosis named — fixed all
   of them in one PR, not incrementally.** The prior session's CE-3 write-up named
   `po.test.ts:222-223` and `tests/test_config_apply.py` as the blockers; the actual fix pass found a
   THIRD file, `tests/test_po_terms.py`, independently pinning the same live purchaser/tax content.
   Converted all three in #514 rather than landing a partial fix and re-diagnosing a second stranded
   edit.
2. **The remedy is a written HOUSE_REFLEXES guard, not just a one-off fix.** This is the second
   recurrence of the self-defeating-CI-test class (form-publish catalog counts #222/#228, then
   config-editor #511) — banked as a durable rule ("assert shape / round-trip / served-equals-source,
   never editable-config content") so a third §50 instantiation doesn't hit it blind, per the prior
   session's own memory-worthy-lesson flag.
3. **#511 was closed, not force-merged or retested in place.** Once #514 fixed the tests on `main`,
   re-running the SAME stuck PR branch against the new tests wasn't attempted — the operator's
   original edit is trivially re-queueable via the live portal, so closing #511 and letting the next
   submission retest clean was simpler and avoided rebasing a daemon-generated PR branch by hand.
4. **ADR-0002 was reconciled in #514 (Step 1b), not deferred again.** The prior session flagged
   ADR-0002's text as stale (still describing propose-mode as default) as an open item; this session's
   CI fix PR was a natural touch point, so the ADR was updated in the same PR rather than opened as a
   separate doc-only change.
5. **Terms editing was split into two slices (T1 pre-fill, T2 make-current+legal-gate), not built as
   one PR.** T1 (serve the current clause body so the operator edits rather than retypes) ships
   independent read-only value with no exposure to the legal gate; T2 (the money/legal-risk half —
   activating a version and enabling the render-side refusal) was reviewed and merged separately,
   consistent with the config editor's existing pattern of small, adversarially-reviewed slices.
6. **Layer A (render-side `legal_review` refusal) shipped in the SAME PR as the two-version
   backfill to `cleared`, never as two separate changes.** Landing the refusal without the backfill
   first would fence every live PO (both shipped versions predate the `legal_review` field); the
   backfill was operator-confirmed clearance, not an assumption, and is git-history-visible in #520's
   diff to `terms/manifest.json`.
7. **`set_current` is the ONLY writer that advances `current_version`,** kept as a single atomic
   manifest write (clears `legal_review` + repoints `current_version` together) rather than two
   separate config ops — avoids a window where a version is marked cleared but not yet current, or
   vice versa.
8. **The make-current UI requires an explicit attestation checkbox ("I have reviewed this version's
   legal text"), and the underlying legal judgment stays a §44 high-class call.** The confirmable
   control is the *mechanism*, not a re-delegation of the legal decision itself — the §43 runbook
   language for `config_actuator` was updated to say so explicitly (see doc-currency below), keeping
   "who may attest" a training-enforced §44 boundary rather than a code-enforced one.
9. **A merge conflict between #520 and the parallel WS2 operator-dashboard PR (#519) on the shared
   doc auto-index was resolved by regenerating the index, not hand-editing the conflict markers** —
   the canonical `scripts/regen_doc_indexes.py` output is the source of truth for that block.

## Open items handed off

- **`docs/tech_debt.md` CE-3 is still marked `HIGH, blocks the editor entirely` / undated-resolution
  in the checked-out tree, even though #514 landed the fix on `main`.** Unlike CE-2 (which #520's
  commit body explicitly marks "RESOLVED 2026-07-10 (slice T2)" in the tech_debt entry itself), #514's
  diff did not touch `docs/tech_debt.md` — the CE-3 entry text was never updated to record the
  resolution. Suggested wording for the next doc-touching session: prefix CE-3 with `RESOLVED
  2026-07-10 (PR #514)` and summarize the shape/round-trip/served-equals-source fix, mirroring the
  CE-2 entry's own resolution note.
- **Make-current authorization boundary — NOT decided this session.** Who besides Seth may hold
  `cap.po.manage` and legitimately check "I have reviewed this version's legal text" remains an open
  §44 high-class judgment call, flagged again (was already open before this session) rather than
  resolved. Decide before any wider `cap.po.manage` grant is made.
- **Migration `0046` is on `main` but not yet applied to the live D1 database; the editor (including
  terms make-current) still ships dark behind `config_actuator.polling_enabled`.** Activation requires
  `wrangler d1 migrations apply` + deploy, per the existing config-editor activation runbook — unchanged
  from the prior session's open item (b).
- **`docs/enablement/purchase_orders.md` remains stale** ("read-only, not a portal edit") — carried
  forward unresolved from the prior session; still deferred because editing it trips the enablement
  sha-manifest recompute (`docs/enablement/manifest.yaml`).
- **CE-1 (LOW, §54 redact parity on `config_actuator._fail` vs. `publish_daemon._fail`)** — untouched
  this session, still open in `docs/tech_debt.md`.
- **Doc-conventions workstream-taxonomy gap (`po_materials`/`purchase_orders` absent from
  `CANONICAL_WORKSTREAMS`)** — untouched this session, still open in `docs/tech_debt.md`; this log
  itself uses `workstream: null` for the same reason the prior session's log did.
- **Merged worktree cleanup** — `its-config-pins`, `its-terms-edit`, `its-terms-t2` (this session's
  three worktrees) plus the earlier `its-close` still need operator cleanup; carried forward from the
  prior session's open item (d), now with three additional worktrees added to the list.

## What was NOT touched

- **No re-diagnosis of #511 as-is** — it was closed rather than rebased/retested against the new
  tests; a fresh portal submission is the path forward, not resurrecting the old PR branch.
- **No doctrine bump.** Both the CI-test fix and the terms-editing slices are refinements of the
  existing §50 gate and its already-approved fully-automatic actuation (ADR-0002) — no new invariant,
  no `docs/doctrine_manifest.yaml` change.
- **`docs/tech_debt.md` was not edited** to mark CE-3 resolved — flagged as an open item rather than
  silently left inconsistent without a note.
- **The make-current authorization boundary question was not resolved** — the UI control was built,
  but "who may attest" stays an explicit open item, not a default answer.
- **Migration `0046` was not applied to the live D1 database and the editor was not activated** — the
  build stays dark, consistent with the existing "operator watches first activation" precedent from
  the prior session.
- **`docs/enablement/purchase_orders.md` was not updated** — carried forward as stale, not silently
  patched around.

## Lessons worth folding into memory (next session-close pass)

- **A handed-off diagnosis can still undercount the blast radius.** The prior session's CE-3 write-up
  named two files; the actual fix found three. Treat a diagnosed-not-patched handoff as a starting
  enumeration, not a complete one — re-run the "enumerate every implementation" reflex at fix time
  even when a prior session already did the diagnosis pass.
- **A parallel session's PRs can add new top-level dependencies mid-session, silently staling a local
  venv.** #516/#519 (a different, WS2 operator-dashboard thread) landed on `main` between #514 and
  #518/#520 and added `fastapi`/`uvicorn`/`python-multipart` to `pyproject.toml`; a local `pytest`
  run against the post-#520 HEAD failed on `ModuleNotFoundError` until `.venv` was refreshed via
  `pip install -e ".[dev]"` — matches the existing "new package → refresh live venv" memory entry,
  now observed to bite even when the dependency-adding PRs were from an unrelated concurrent thread,
  not the current session's own work.
- **Doc-currency fan-out inside a single PR is not automatic just because the PR's OWN prior sibling
  did it well.** #520's commit body explicitly marks CE-2 "RESOLVED" inline in `tech_debt.md`;
  #514, landed two hours earlier in the same session, fixed CE-3 just as completely but never touched
  `tech_debt.md` — leaving the tracker itself inconsistent with `main`'s actual state. Worth a
  standing checklist item ("did this PR's tech_debt entries get their resolution stamp") alongside
  the existing doc-currency review pass, not just at PR-review time but as a session-close check.

## Cross-references

- Prior session log (same day, earlier): `docs/session_logs/2026-07-10_po-followups-and-config-editor-vertical.md`
  — originated CE-2, CE-3, the ADR-0002 drift note, and the stuck-#511 diagnosis this session resolves.
- `docs/tech_debt.md` — CE-2 (marked RESOLVED by #520), CE-3 (fix landed by #514; tracker text not yet
  updated — see Open items).
- `docs/HOUSE_REFLEXES.md` §5 — new guard: "Never pin EDITABLE-config CONTENT in a test — assert
  shape / round-trip / served-equals-source" (added by #514).
- `docs/adr/0002-po-config-editor-privileged-actuation.md` — `status: proposed` → `active` (by #514,
  Step 1b).
- `~/.claude/projects/-Users-sethsmith-its/memory/project_config-editor-build.md` — auto-memory topic
  file for the full config-editor arc; this session closes the "3 slices — DONE" build's first-edit
  smoke-test blocker and adds the terms-editing vertical on top.
- Migration `0046` (`safety_portal/migrations/`) — widens `config_requests.op` CHECK for `set_current`;
  not yet applied to live D1 (see Open items).
