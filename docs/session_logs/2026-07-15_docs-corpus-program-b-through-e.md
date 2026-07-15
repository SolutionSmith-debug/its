---
type: session_log
date: 2026-07-15
status: closed
related_prs: [601, 602, 603, 604]
workstream: docs
tags: [documentation-corpus, troubleshooting-tree, tranches-b-e]
---

# 2026-07-15 — Documentation corpus, Tranches B–E (tree · dashboard · currency · distribution)

## Purpose

Complete the system-documentation-corpus + interactive-troubleshooting-tree program (executes the
`feedback_documentation-program` directive). Tranche A (Tier-1 references) landed earlier this
session (PR #598, session log `2026-07-15_docs-corpus-tranche-a-tier1-references.md`); this log
covers B → C → D → E, which finish the program. Extraction-first throughout: every factual claim
verified against live code.

## Code changes (by tranche)

- **B — troubleshooting tree (PR #601, `5e45581`).** `docs/troubleshooting/tree.yaml` (10 workflows:
  safety/progress reports, field-ops sync, PO, subcontract, email intake, config-change §50 rail,
  dashboard ops, daemon plane, publish/Box) + `schema.md`. A shared `troubleshooting/` loader package
  (raises `TreeError`; the dashboard boots fail-soft on it). `scripts/build_troubleshooting_guide.py`
  — deterministic `tree.yaml` → `troubleshooting_guide.md` (manifest-registered). CI-blocking coverage
  tests (`tests/test_troubleshooting_tree.py`) are the completion meter: floors extracted from LIVE
  code (17 daemons · 20 watchdog checks · 6 HELD states · 37 runbooks) so a new daemon/check/runbook
  RED-lights the tree until covered.
- **C — dashboard tree (PR #602, `62105c8`).** `operator_dashboard/troubleshoot.py` — `/troubleshoot`
  (htmx workflow→step→failure-mode drill-down + `?q=` filter) and `/doc/{path}` (path-allowlisted,
  traversal-rejected, html-escaped markdown viewer). Class badges (green Operator-resolvable / gold
  Escalate-to-Seth). Fail-soft boot. NO mutation routes (grep-proof + a GET-only test).
- **D — currency pass (PR #603, `19b618f`).** Drift fixes verified against live HEAD; idempotent
  `scripts/build_runbook_xrefs.py` added reverse tree cross-link blocks to the 29 referenced runbooks.
- **E — distribution (PR #604, `972a0a9`).** `build_docs_pdfs --upload --dry-run` (INDEX-first plan,
  fail-soft); `scripts/migrations/build_docs_index_sheet.py` (idempotent `ITS_Documentation_Index`
  builder — THE one authorized live write, mock-tested, operator-run); dashboard `/docs` corpus page.

## Non-obvious decisions

- **Coverage tests as the completion meter (B).** Rather than assert a memorized count, the tests
  enumerate daemons/checks/runbooks/HELD-states from live code — the tree cannot silently fall behind
  the system. This immediately caught D's drift (a 17th daemon).
- **Idempotent generators, not manual edits (D/E).** The runbook xrefs (29 files) and the index-sheet
  rows are generated + `--check`-gated, so they stay current as the tree/manifest grow.
- **Deferred the one live Smartsheet write (E).** Smartsheet was returning HTTP 401 this session (see
  the parallel error-flood diagnosis — Storm A). Rather than burn the single authorized live action on
  a failing call, the index-sheet builder is mock-tested and its live create is an operator step
  (verify-after built in). Faithful-reporting over a forced green.

## Drift table (D — Tier-2 currency)

| Doc | Was | Now |
|-----|-----|-----|
| `daemon_reference.md` | "16 launchd agents"; no subcontract-send | 17 agents + subcontract-send section; 13/11 Check-C/heartbeat split |
| `subcontracts.md` | "sending is not built yet (SC-S4)" | sending built (#599 SC-S4), ships dark |
| 29 runbooks | no reverse tree link | marker-bounded xref block |
| Other guides ("materials M2 coming soon", "health card unavailable") | — | verified legitimate designed-state, left as-is |

## Verification (four-part, per tranche)

All four PRs: `state=MERGED`, `mergedAt` non-null, `mergeCommit` present, required `ci.yml` trio
(test/portal/secrets) green. #602 landed on `UNSTABLE` (CodeQL umbrella check infra-flaked at 3s
while all three Analyze jobs passed — HOUSE_REFLEXES §3); #601/#603/#604 landed clean.

Final tranche (E) local gate:
- pytest: 3523 passed / 0 failed (CI-equivalent `-m 'not integration'`)
- mypy: clean / 393 source files
- ruff: clean
- build_docs_pdfs --check: green (22 docs) · troubleshooting-guide + runbook-xref --check green
- main-branch CI on merge commits: SUCCESS (ci.yml trio)

## Live smoke

Dashboard `/troubleshoot`, `/doc`, and `/docs` were Playwright-driven in-browser (0 console errors):
the index rendered all 10 workflows; clicking a card htmx-loaded its step chain (daemon facts shown);
filtering `held_no_recipient` surfaced the matching failure mode + class badge; the doc viewer and the
corpus page rendered branded. `--upload --dry-run` printed the correct INDEX-first plan (gate DARK).

## Operator-side actions remaining

1. **Index sheet** — `python3 scripts/migrations/build_docs_index_sheet.py --dry-run` then live to
   create + seed `ITS_Documentation_Index` (once Smartsheet auth is healthy — 401 this session).
2. **Box publish** — on the production host, `build_docs_pdfs --upload --dry-run` → `--upload` after
   seeding `docs_pdf.upload.box_folder_id` + flipping `docs_pdf.upload.enabled` (cutover punch-list).
3. **Seth 15-min review** of `escalation_matrix.md` + `security_trust_model.md` before publishing.
4. `/troubleshoot` + `/docs` go live on the next dashboard daemon restart (dashboard ships dark-until-PIN;
   read pages are PIN-free).

## Sequencing context

Completes the corpus program. The manifest now carries 22 docs; the corpus is self-describing
(Tier-1 references + tree) and self-troubleshootable (tree → dashboard + guide). Distribution is
built and dark, awaiting operator activation at cutover.
