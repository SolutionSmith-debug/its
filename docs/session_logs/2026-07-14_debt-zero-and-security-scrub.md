---
title: "2026-07-14 — Debt-Zero + Security-Scrub autonomous session"
type: session_log
date: 2026-07-14
status: active
workstream: null
tags: [security, gitleaks, tech-debt, debt-zero, po_materials, anomaly_logger, docs_pdf, safety-portal, triage]
---

# 2026-07-14 — Debt-Zero + Security-Scrub

Unattended autonomous session against the DEBT-ZERO + SECURITY-SCRUB brief. **Seven PRs
landed** (six code/doc + the triage); the brief's most-weighted items collapsed under
verify-first, and four Block-C items re-parked with concrete findings rather than shipped.

## Landed (four-part verify clean; part-4 covered by the tip main-CI `de83852d`)

| PR | What |
|----|------|
| **#584** | **Block A security scrub.** Removed every `@evergreenrenewables.com` production identity from guard-scoped code (`.py`/`.ts`/`.tsx`) — 25 substitutions across 6 pytest + 2 SPA test files + 2 seed comments; personal identities (tealap@, benf@, teala@) gone, role addresses → `@example.com`. New `.gitleaks-identity.toml` + a `gitleaks dir` CI step (working-tree scan — **not** history, which immutably holds the domain in ~hundreds of past commits) prevents re-entry. Prove-it-bites: guard clean on scrubbed tree, fires on an injected email, history scan stays clean under the new `[[allowlists]]`. |
| **#585** | **C1 — `po_send_poll` `DEFAULT_POLLING_ENABLED` True→False.** A missing `polling_enabled` row now fails SAFE (send daemon disabled), not fail-open to sending. Prove-it-bites regression (approved row `skipped_disabled`, not SENT, on a missing row). Operator's written send-path co-resolution, one-line only. |
| **#586** | **C5 — anomaly_logger FP narrowing (§553).** Broad `^system_`/`^role_`/`^ignore_` prefix globs → anchored injection-control names; `system_version`/`role_description`/… no longer false-flag. Tested both directions (FP fixed + detection preserved), inject-confirm-revert. |
| **#588** | **C3 — docs_pdf `--upload` Box publish leg (D2-3).** Real render→upload via `box_client.upload_bytes_or_new_version`, **dark-gated** on `docs_pdf.upload.enabled` (+ `box_folder_id`); mock-only tests (no live Box). CLI self-documents activation → no phantom gate. |
| **#589** | **item-7 — portal tab title + inline-SVG favicon.** `Evergreen ITS Portal` + a British-Racing-Green/gold "ITS" data-URI favicon (CSP-clean: `img-src 'self' data:`). Rides the deploy. |
| **#590** | **SC-CFG-2 — hoist `MAX_ADDRESS` to a shared Worker `constants.ts`.** The 512 bound was 3 local defs + 1 hardcoded `512` in index.ts; now one source. tsc + 1063 worker vitest green; `portal-worker-security-reviewer` CLEAN. Rides the deploy. |
| **#592** | **Block D — 2026-07-14 debt-zero triage.** All 123 open `tech_debt.md` entries triaged vs HEAD (8-agent workflow): **12 verified resolved/stale → moved** to the archive; **110 re-parked** under a new owner-bucketed dated-disposition index; the C2/C4/C6/C7 findings recorded. |

## Verify-first collapses / re-park decisions (the real intellectual work)

- **Block A.1 (fingerprint-pin the 2 fixtures):** the full-history `gitleaks git .` scan was **already clean** on the pinned 8.30.1 — the fixtures pass under the default example-value allowlist. No findings to pin; added tight forward-protection instead.
- **C2 (scheduled_send fail-closed) → RE-PARK (Seth).** Touches the External Send Gate's *shared* scheduled-window logic across 3 daemons. C1 got explicit "nothing else on the send path" co-resolution; C2 did not. The triage's "DO" (observability WARN-log + seeder) is folded here — don't split a send-path entry.
- **C4/item-9 (fail-closed guard-hook) → RE-PARK (Seth).** The exec-repo hooks are **real files**, not the vulnerable relative-symlink shape — DR-D1's real target is the blueprint's symlink hooks (doctrine-fenced). A fail-closed SessionStart assertion has a chicken-and-egg (can't run if `.claude` dangles) + a brick-CC blast radius needing operator-present validation; Check M detects post-hoc today.
- **C6/§404 (hours_log indexed lookup) → RE-PARK (phase-1.5).** Premature optimization on a low-volume path (don't-harden-dormant); revisit at the 20-job scale point.
- **C7/§466 (smartsheet SDK pin) → RE-PARK (operator), de-risked.** The "dropped `smartsheet.exceptions`" claim is **STALE**: 4.2.0 restores it (all 3 used names present) and the **full mocked suite passes on 4.2.0** (experiment run in an isolated venv). But it is a MAJOR 3→4 bump + the suite mocks the SDK → the operator must run `pytest -m integration` against 4.2.0 (§30 / mandatory-live-smoke) before the one-line pin bump to `<5.0.0`.

## Verify (four-part, session-level)

- pytest: targeted subsets green per PR (po_send_poll 7, anomaly_logger 20, docs_pdf 41, 1063 worker vitest, 47 SPA vitest, 101 scrub tests); CI `test` job (ruff + mypy blocking + pytest) green on all 7 PRs
- mypy: clean on every touched module (ran locally per PR)
- ruff: clean (CI)
- main-branch CI on merge commit: SUCCESS (tip `de83852d`; intermediate per-PR runs concurrency-cancelled by the next merge, the benign supersede pattern — the tip covers all ancestors)

## Open / handed to the operator

- **Single deploy manifest** (rides one `npm run deploy`, no migration): **#589** (portal title/favicon) + **#590** (MAX_ADDRESS hoist). Both SPA/Worker, already merged to main.
- **SC3c-1 (Block B.1) SURFACED, not landed** — the supersede double-submit dup-guard is check-then-act; the fix (fold into the clone `INSERT…SELECT`'s WHERE via `AND NOT EXISTS`) is a **shared po.ts+subcontract.ts** change on money/legal routes needing a joint PO re-review + a live double-submit smoke (deploy-gated). Low severity (damage ceiling: a human cancels one draft). Better in a focused Worker session with the deploy present.
- **C2 / C4 / C6 / C7** re-parked with the findings above (see the tech_debt debt-zero index).
- **C7 one-liner:** after the operator runs `pytest -m integration` against `smartsheet-python-sdk==4.2.0`, bump the `pyproject.toml` pin `<3.10.0` → `<5.0.0`.
- **docs_pdf `--upload` first activation:** operator seeds `docs_pdf.upload.box_folder_id` + flips `docs_pdf.upload.enabled=true` (+ a live Box smoke) when ready.
- **Pre-existing doc-index drift** (`docs/README.md` + `session_logs/README.md` missing the 2 tech_debt rows) left as a warn-only follow-up — `python -m scripts.regen_doc_indexes` fixes it.
- **Worktrees:** operator removes the merged `../its-dz-*` worktrees + the pre-existing `~/its-auto` (worktree_discipline; force-delete is hook-blocked inside CC).
