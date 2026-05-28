---
type: session_log
date: 2026-05-28
status: closed
workstream: security
related_prs: [95, 96]
tags: [forensic-audit, injection, untrusted-content, attachment-screening, invariant-2, verify-before-fix, brief-drift, hygiene]
---

# 2026-05-28 — Forensic-audit remediation (HIGH-1 injection fix + LOW hygiene + HIGH-2 surfacing)

PRs: [#95](https://github.com/SolutionSmith-debug/its/pull/95) — squash-merged 2026-05-28T21:57:04Z, merge commit `dce7158b442fda629a1d35d11f320df7f9e9fc01`. [#96](https://github.com/SolutionSmith-debug/its/pull/96) — squash-merged 2026-05-28T21:58:37Z, merge commit `09f8c02b982c16aecbb23546d2729f8bec829843`. Both **four-part PR-landed verify clean** (state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on merge commit = SUCCESS for `ci` + `CodeQL`).

Remediation of the 2026-05-28 forensic evaluation (`docs/audits/2026-05-28_forensic-evaluation.md`, added in #96). Split per the audit's sequencing: HIGH-1 injection fix isolated in #95; the LOW hygiene batch + audit doc + HIGH-2 surfacing in #96.

## Purpose

Close the audit's actionable findings before Phase 1.5 cutover: fix the tag-breakout injection in `untrusted_content.wrap()` (HIGH-1), batch the three LOW hygiene items, land the audit doc, and surface the decision-gated HIGH-2 (attachment screening) without building it. MEDIUM-1 and the NIT are operator/elsewhere actions — verified, not coded.

## Verify-before-fix — the audit's line numbers had drifted

The audit was written against `eab6cfc`; HEAD was `4431bae` (PRs #92/#93/#94 landed since — all CC-tooling/session-log, none touching the audited paths). A parallel read-only verification pass re-confirmed every finding against HEAD before any edit. Drift / corrections found:

- **HIGH-1 fix-pattern path was wrong in the audit.** The mirrored delimiter-neutralization idiom is in `safety_reports/weekly_send.py:250` (`_update_notes_tags`, `[`→`(`/`]`→`)`), **not** `shared/weekly_send.py` (no such file). The two call sites (`intake.py:663-664`, `weekly_generate.py:456-457`) matched the audit exactly.
- **MEDIUM-1 mechanism claim was inaccurate.** The audit said `SHEET_TRUSTED_CONTACTS=0` makes `check_scope` quarantine *every* sender. In reality `intake.py:1007-1013` branches on `_load_contacts()` emptiness and falls back to the legacy ITS_Config `allowed_senders` allowlist (`_check_legacy_allowlist`), so allowlisted senders still proceed; only genuinely-unknown senders quarantine (as `LEGACY_ALLOWLIST_MISS`). Net posture is still fail-safe-closed, but via a different path. **Do not remove the legacy fallback before the operator seeds the new sheet** — that would silently quarantine all real safety reports. No code change (operator action).
- **NIT artifact is not in this repo.** `Evergreen_Contacts.pdf` does not exist anywhere in `~/its` (no PDFs, not in git history). The PK-header concern must be acted on wherever the file actually lives (customer-data export / its-blueprint). Note only.
- **LOW-2 had two stale spots beyond the audit's four** — `test_capability_gating.py:129` ("even when empty") and the false "lists are currently empty / hasn't landed yet" note (the two-process refactor landed; `GATED_SCRIPTS`/`SEND_SCRIPTS` are populated). Both corrected.

## Decisions

- **HIGH-1 — Option 1 (zero-width-break), not the nonce approach.** Per the audit's recommendation: neutralize any embedded `</untrusted_content>` by inserting U+200B into the tag token before interpolation. Implemented as `chr(0x200B)` (not a `​` escape and not a raw invisible char in source — both are error-prone) so the substitution is unambiguous and the neutralized tag stays human-readable. Centralized in `wrap()`, covering all four call sites with no call-site edits. The existing `test_wrap_preserves_content_verbatim` stays green because its payload carries no closing sentinel; regression test added for the breakout case.
- **HIGH-2 — surfaced, not built.** Decision-gated (Option A build vs Option B documented exception) + external prerequisite (clamd socket). Left a `docs/tech_debt.md` entry (blocked-on-decision, both options restated) and a **NOT-WIRED** stub `shared/attachment_screening.py` that pins the `screen(filename, content, mime_type) -> ScreenVerdict` signature and raises `NotImplementedError` (fails closed). The stub is not imported by intake and is marked "delete if Option B." Recommended as its own dedicated session.
- **LOW-3 concurrency group keyed `ci-${{ github.ref }}`** — matched the audit's exact acceptance snippet rather than the `${{ github.workflow }}-${{ github.ref }}` variant a verifier suggested; both work, the audit is the binding spec.
- **Audit doc published as-is to the public repo** (operator call) — established practice (two prior audits already in `docs/audits/`). HIGH-1 fix (#95) landed before the doc (#96); HIGH-2 remains an openly-documented, decision-gated gap.
- **Merge discipline.** #96 required a branch update after #95 landed (protection enforces up-to-date branches); repo auto-merge is disabled, so the re-run CI was awaited (no `--admin` bypass) before squash-merging — honoring the four-part discipline that exists to prevent post-merge reds.

## Preserved (per Op Stds §14)

No refactors. The audit's "What's working — preserve" list was untouched: External Send Gate two-process model, triple-fire alerting, kill switch, `header_forgery.py` + `trusted_contacts.py` parsing, secret hygiene. The HIGH-1 fix kept the module's defense-in-depth framing intact ("the system prompt boilerplate remains the primary defense").

## Operator / follow-on

- **MEDIUM-1** (Phase 1.5 cutover prerequisite): run `scripts/migrations/build_its_trusted_contacts_sheet.py`, paste the real ID into `shared/sheet_ids.py:84` (replacing `0`), seed contacts with `["*"]` scopes, confirm a seeded ACTIVE sender resolves to `allowed`. Pairs with the open M365 admin-credential item.
- **HIGH-2**: operator picks Option A (build) or B (documented exception). See the tech_debt entry.
- **NIT**: re-export / correctly rename `Evergreen_Contacts.pdf` wherever it lives before using it as trusted-contacts seed input.
