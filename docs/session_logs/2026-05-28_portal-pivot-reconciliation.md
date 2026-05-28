---
type: session_log
date: 2026-05-28
status: closed
workstream: security
related_prs: [98, 99, 100]
tags: [portal-pivot, high-2-supersession, attachment-screening, invariant-2, email-triage, layer-6, cross-repo-drift, verify-before-fix]
---

# 2026-05-28 — Portal-pivot reconciliation + HIGH-2 supersession

PRs (all **four-part PR-landed verify clean** — state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on merge commit = SUCCESS):
- [#98](https://github.com/SolutionSmith-debug/its/pull/98) — squash-merged 2026-05-28T22:32:08Z, `bf2a94a`. Task 0 (undo stale HIGH-2 artifacts).
- [#99](https://github.com/SolutionSmith-debug/its/pull/99) — squash-merged 2026-05-28T22:33:25Z, `a1fe04b`. Task A (exec reconcile to portal pivot).
- [#100](https://github.com/SolutionSmith-debug/its/pull/100) — squash-merged 2026-05-28T22:34:39Z, `8c09a6b`. Tasks B+D (coverage sweep + cross-repo drift guard).
- Blueprint [its-blueprint#15](https://github.com/SolutionSmith-debug/its-blueprint/pull/15) — squash-merged 2026-05-28T22:32:11Z, `133afb8`. Task C (Email Triage Layer 6).

## Purpose

The 2026-05-28 forensic audit (#95/#96) correctly surfaced HIGH-2 (Invariant 2 Layer 6 attachment screening) *as of the audit*. Since then the safety-report intake model pivoted to a form-fill **Safety Portal**, already canonical in the blueprint (`workstreams/safety-portal/mission.md` v1, 2026-05-25). That pivot supersedes HIGH-2 *for safety reports*. This session undid the now-stale HIGH-2 artifacts from #96, reconciled the execution repo's docs to the pivot, reassigned Layer 6 to its true owner (Email Triage), and added a guard against the cross-repo divergence that caused the staleness. Makes the execution repo consistent with already-canonical blueprint doctrine — does not invent doctrine.

## Verify-before-fix / drift found

State verified against `~/its` HEAD `09f8c02` and `~/its-blueprint` HEAD `003b56a` before editing; a read-only verification fan-out re-confirmed every edit location. Findings:

- **Agent-availability divergence (flagged to operator).** The prompt assumed running "from inside ~/its" would let this session reach the repo-local `.claude/agents/` (`session-close-maintainer`, `session-log-writer`, `brief-validator`, `ops-stds-enforcer`). It does **not** — the CC session is rooted at `/Users/sethsmith` and the agent registry is fixed at session start, so those agents were unreachable. Manual equivalents were used: frontmatter validated via `lint_doc_conventions` / `lint_frontmatter` directly; this session log authored to spec; four-part verification run as direct `gh` queries; memory-archive + auto-memory updated by hand.
- **Stub safe to delete.** `shared/attachment_screening.py` had zero importers (its docstring instructed deletion if not built for safety reports). Confirmed before `git rm`.
- **safety-portal citations live in mission §7, not brief §8.** The "SVG vector, not raster" / "PMs cannot attach arbitrary files" ruling is mission.md §7 (Layer 6 N/A); the HMAC shim (`portal-noreply@` → unified `safety@`) is brief §8 Step 3, and the PLANNED portal-marker stages (1.5 / 8' / 13') are brief §8 Step 4. Don't conflate the portal **mission v1** with **Foundation Mission v8** (the portal inherits FM v8).
- **Email Triage had NO Layer 6 section** — it had to be added, not strengthened. Both email-triage docs were version-stale (cited Foundation Mission v4 / Operational Standards v5, "five defense layers"); reconciled the refs the Layer-6 addition directly contradicts and bumped mission v4→v5, brief v5→v6.
- **No exec drift across workstreams.** All 6 blueprint workstreams are accounted for in the exec repo; only `safety_reports` is built, the rest are coherently planned (reserved enum/folder/cap-gating templates). AI Employee + PO/contract drafting confirmed NOT stranded. Naming aliases `purchase_orders`↔`po_materials`, `ai_employee_capabilities`↔`ai_employee` are stable (used by the briefs themselves), not drift.

## Decisions

- **Undo, don't rebuild (Task 0).** Deleted the stub; rewrote the `tech_debt.md` HIGH-2 entry to `[SUPERSEDED 2026-05-28]` citing the portal mission/brief; marked the audit-doc HIGH-2 finding SUPERSEDED with a one-line note (finding preserved as recorded, not rewritten).
- **Docs-only exec reconcile (Task A).** Added portal-pivot notes to CLAUDE.md (Layer 6 line + intake.py/intake_poll.py rows) and the mailbox seed comment. **No `intake.py` logic changes; seeded mailbox value unchanged.** The legacy ITS_Config `allowed_senders` fallback was left untouched (per #96 drift note 2 — removing it before the trusted-contacts sheet is seeded would quarantine all real reports).
- **Layer 6 → Email Triage (Task C).** Applied the existing FM v8 §34 framework-default to the workstream that owns the arbitrary-attachment surface, mirroring FM v8 sub-layers (a)–(d) verbatim + clamd operator prerequisite (VirusTotal sub-layer d deferred to Phase 2+).
- **Minimum drift guard (Task D).** No new field/script/linter (an automated cross-repo check would have to read both repos and is deliberately not built). Added a recurring "Cross-repo supersession check" to `session-close-maintainer` (both directions) + a `doc_conventions.md` note pointing at the existing `last_verified`/`last_verified_against` + audit-snapshot mechanisms.

## Preserved (Op Stds §14)

No refactors; no portal build; no doctrine invented (blueprint wins). Did not touch `intake.py` logic, the legacy allowlist fallback, or #97 (prior session log — left for the operator to merge when ready).

## Operator / follow-on

- **HIGH-2 is closed for safety reports.** Layer 6 implementation lands when the **Email Triage** build begins (Phase 3); clamd is its operator prerequisite.
- **Residual version staleness** in the email-triage blueprint docs beyond the Layer-6 context (e.g. a stray "Operational Standards v5" parenthetical) was left for a dedicated workstream-doc refresh, not expanded here.
- **Blueprint living docs** normally maintained by `session-close-maintainer` (`references/claude-code-info-gap.md` §8 snapshot) were not updated this session — that agent was unreachable (see drift note). The `memory-archive.md` §G8 append + this session's planning-side log were done manually in blueprint PR (companion to #15).
