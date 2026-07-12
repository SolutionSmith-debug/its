---
type: session_log
date: 2026-07-12
status: active
related_prs: [536, 537, 538]
workstream: subcontracts
tags: [subcontracts, generation, s3c, s5, exhibit-a, worker, daemon, ui, trade-templates, corpus, ultracode, workflow, overnight, autonomous]
---

# Subcontract generator COMPLETE — SC-S3c (backend) + SC-S5 (UI) + SC-S3b Exhibit A

## Purpose

Finish the deterministic subcontract generator: the Worker + poll daemon + review twin (S3c, the smokeable
capstone + bidirectional sync runner), the SPA (S5, subcontractor admin grouped by state + subcontract
builder), and the Exhibit A trade templates from the corpus (S3b Exhibit A, the last remaining piece). Run
under ultracode — each slice as an understand → build → verify Workflow, adversarially reviewed. **NO AI in
the generation path.** All ships dark.

## Landed (all four-part verified: MERGED · mergedAt · mergeCommit · main-CI SUCCESS)

### #536 — SC-S3c Worker + poll daemon + review twin + `sub:v1` HMAC — `0394429`
- `worker/subcontract.ts` (~1075L ← po.ts): subcontractor CRUD + the §51 down/up-sync routes (the
  bidirectional runner's worker half); draft/generate with the SOV-sums-to-price gate + `sub:v1` signing;
  `/internal/pending`+`mark-filed`+`status-sync` (+the wet-signature `executed` terminal). New
  privilege-separated `requireSubToken`/`PORTAL_SUB_API_TOKEN`.
- `subcontract_poll.py` (~890L ← po_poll.py): 4-pass daemon (drafts render→Box→log→review; subcontractor
  down+up sync = the §51 runner; status +executed). `agreement_ymd` from immutable `created_at` (§47).
- `subcontract_review.py` (WSR twin), `sub:v1` in portal_hmac/portal_client, naming, launchd, §43 runbook.
- **The TS↔Python HMAC canonical is PROVEN byte-identical** (31 keys + null + float qty + non-ASCII), pinned
  as a matched cross-language vector on both sides.
- 3 reviews CLEAN/WARN no-BLOCK. The one moderate finding (a supersede dup-guard TOCTOU) is verbatim
  inherited from live po.ts → shared follow-up in tech-debt, not a new regression.

### #537 — SC-S5 the UI — `47c8a23`
- SPA-only (job picker rides the capability-free GET /api/jobs). `lib/subcontracts.ts` +
  `SubcontractorsPage.tsx` (admin GROUPED BY STATE — PO is flat, this adds it) + `SubcontractBuilderPage.tsx`
  (pick sub→job→fields→SOV lines w/ live subtotal===price gate→draft→generate) + router/App/HomePage wiring
  (2 views gated cap.subcontracts.manage).
- **Integration fix:** the lib's TRADES was invented → corrected to the canonical picklist (else the §51
  up-sync fences). Review CLEAN on all 14 lib↔Worker routes; fixed 3 low findings (double-submit guard,
  sort consistency, retainage fallback).

### #538 — SC-S3b Exhibit A trade templates (from the corpus) + render + serve + pre-fill — `eb6fe9d`
- **The corpus already held canonical per-trade Exhibit A templates** (`05_Subcontracts` Kendall 2025.112 /
  Steger 2025.364, `Sub Name - Project Name_<trade>` files, project-identical). Built `subcontracts/exhibit/`:
  manifest (9 trades → 7 Art II templates; AC/MV/DC share electrical; sha-pinned) + tokenized skeleton (fixed
  Art I/III/IV/V/VI, 8 record-backed tokens) + `art2/<trade>.md` (VERBATIM corpus; Specialty = honest
  placeholder). `exhibit.py` loader + `render_exhibit_a_docx` → render_package now emits **3 files**
  (Subcontract.docx + Exhibit A.docx + Annex C .xlsx) → poll files + inline-attaches all 3 → cap-gated serve
  route → SPA pre-fills Article II from the selected trade (never clobbers operator edits).
- **ops-stds caught a real BLOCK:** the skeleton had a fabricated Contract-Documents recital (in no corpus
  source) — reverted to the verbatim corpus header, sha re-pinned; the §43 runbook 2→3-file update. Both
  re-verified CONFIRMED-RESOLVED.

## Gate (final, on `eb6fe9d`)
- pytest 3232 passed · mypy clean · ruff clean
- TS typecheck clean (3 tsconfigs) · worker vitest 1003 · SPA vitest 630 · vite build ✓
- main-branch CI on merge commits: #536 SUCCESS · #537 SUCCESS · #538 SUCCESS

## The generator is 100% built. Only the operator fold remains.
The deliverable package = **Subcontract.docx + Exhibit A.docx + Annex C SOV.xlsx**, Article II pre-filled per
trade from the corpus templates, editable from there. Everything ships DARK. The operator fold/deploy sequence
(migrations 0049–0052 → 4 Smartsheet builders + flip ids → seed subcontractors → make-current terms →
provision `PORTAL_SUB_API_TOKEN`/`ITS_PORTAL_SUB_TOKEN` → flip the daemon gates → live-smoke) is in the
2026-07-11 session log + the fold-checklist chat. No build work remains.

## Method notes (ultracode)
Each slice ran as phased Workflows: understand (parallel deep-readers → precise PO-mirror specs) → build
(waves of agents into a worktree) → verify (parallel adversarial reviewers). Every gate was re-run by the
main loop, not trusted from agent reports — which is how the real bugs surfaced (HMAC canonical agreement,
OOXML determinism, the invented-TRADES §51 fence, the fabricated Exhibit A recital). Residual for Seth:
the `exhibit.py` no-Layer-A-gate question (intentional; one-line confirm).
