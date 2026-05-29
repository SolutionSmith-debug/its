---
type: session_log
date: 2026-05-29
status: closed
workstream: docs
related_prs: [125]
tags: [doctrine-manifest, foundation-mission, operational-standards, kill-switch, tech-debt, ledger-cleanup]
---

# 2026-05-29 — Exec-side ledger cleanup: FM v8→v9 + Op Stds v13→v14 doctrine bump

PR: [#125](https://github.com/SolutionSmith-debug/its/pull/125) — squash-merged 2026-05-29T18:20:09Z, merge commit `837bfd8d30c7583c28388130cc3581cc4705d232`. `pr-landed-verifier` output: **PR #125 — four-part verify clean / state: MERGED / mergedAt: 2026-05-29T18:20:09Z / mergeCommit: 837bfd8d30c7583c28388130cc3581cc4705d232 / main CI on merge commit: SUCCESS (run 26654520630, workflow: ci) + SUCCESS (run 26654519862, workflow: CodeQL)**.

## Purpose

Record the blueprint doctrine bump (Foundation Mission v8→v9 and Operational Standards v13→v14, both landed in blueprint PR #23 commit `29000f1`) into the execution repo's machine-readable doctrine manifest and human-readable CLAUDE.md surface, plus a deferred-mechanism tech-debt entry for an optional fail-closed kill-switch enhancement. No production code changed — three doc/config files only.

## Commits landed

- **`837bfd8`** — `docs(doctrine): FM v8→v9 + Op Stds v13→v14 manifest bump + F07 kill-switch reframe` (PR #125). Updates `docs/doctrine_manifest.yaml` (both version strings + blueprint_head + notes + drift_signal regexes), appends F07 kill-switch security-boundary clause to `CLAUDE.md`, and adds a `[DEFERRED 2026-05-29]` tech-debt entry to `docs/tech_debt.md`.

## CI / verification

```
- pytest: 1141 passed / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

## Pre-flight findings

`brief-validator` verified all version numbers and SHAs against the live blueprint before any edit and caught two material corrections:

1. **Stale SHA `3b7d56d` in memory.** The brief's FM-v8 blueprint SHA (`3b7d56d`) is a stale memory artifact that does not exist in the blueprint log. Both FM and Op Stds were last touched by commit `29000f1` (blueprint PR #23). The old manifest had two different `blueprint_verified_against` values; the correct state is a single SHA (`29000f1`) for both entries. This is the canonical class of error the brief warned about ("don't hardcode memory-derived SHAs").

2. **No test edit needed.** The brief assumed `tests/test_check_doctrine_drift.py` pins version literals (13/8) and would fail CI without an edit. It does not — the 3 tests assert only contract shape (M1–M5 checks, severity set), exit-0 behavior, and the always-printed "DRIFT" header. The checker keys off the manifest `current` field; the `drift_signal` strings are human-facing documentation, not consumed by the checker. The checker exits 0 even with drift (propose-only, not a CI gate). CI passed without a test edit; this was confirmed by reading all 56 test lines and the checker, then observing the passing run.

## Decisions made during session

1. **Both manifest `blueprint_verified_against` values set to `29000f1`, not two different SHAs.** `brief-validator` confirmed both FM v9 and Op Stds v14 were introduced in the same commit. Using the same SHA for both entries is correct; the prior manifest's two-SHA shape was inherited from an earlier state where the docs diverged. Alternative considered: keep separate SHAs per brief's implicit two-SHA assumption. Rejected: `29000f1` is the accurate answer for both, as confirmed against the blueprint log.

2. **`meta.blueprint_head` refreshed to `e7f8764` (beyond the brief's enumeration).** The manifest's own UPDATE DISCIPLINE comment (line 35) instructs "refresh blueprint_head" on every version bump. The brief's item-2 enumeration omitted it, but the convention takes precedence. `meta.its_head` / `generated` / `seeded_by` were left as the original seeding record — those are not refreshed on doctrine bumps. The checker does not read `meta`, so zero functional impact either way. Alternative: follow the brief's literal enumeration, skip `blueprint_head`. Rejected: that would leave the manifest's own stated discipline violated.

3. **`section_42` block left untouched.** The `(Op Stds v13)` comment in that block is a when-introduced reference, not a stale version pointer. `ops-stds-enforcer` independently confirmed that v14 did not renumber or alter §42. Changing it would be actively wrong. Alternative: bump `(Op Stds v13)` → `(Op Stds v14)` mechanically. Rejected: the comment records which version introduced the section; it is not a "current version" citation.

4. **Both `drift_signal` regexes bumped (`1[0-2]`→`1[0-3]` for Op Stds; `[0-7]`→`[0-8]` for FM).** After the bump, the drift checker now surfaces ~20 M1 findings (the v13/v8 citations still present in `CLAUDE.md` and `README.md`). This is the intended backing for the separate OBS-1 sweep — the 20 findings become OBS-1's worklist. Alternative: leave regexes as-is to suppress drift noise until OBS-1. Rejected: the regexes exist to flag stale citations; suppressing them defeats the purpose. The 20 M1 findings exit-0 (propose-only) and do not block CI.

5. **F07 kill-switch clause is a doc reframe, not a behavior change.** The clause appended to the "Kill switch first." bullet clarifies that `@require_active` is an operator-convenience pause and NOT a security control (fail-open by design: sheet-unreachable / row-missing / invalid-value all resolve to ACTIVE-with-WARN). The External Send Gate (Invariant 1), not the kill switch, is the security boundary. `ops-stds-enforcer` confirmed the clause restates v14 §1 verbatim. The behavior description ("PAUSED or MAINTENANCE → exit cleanly") is unchanged. Alternative: leave the kill-switch bullet ambiguous and address it in a future security-review. Rejected: with the F07 clause established in this PR, the OBS-1 sweep (which touches CLAUDE.md) can reference the already-landed framing without a race.

6. **Fail-closed kill-switch mechanism deferred to tech-debt rather than designed.** The `fail_closed_until` concept (an ITS_Config ISO-8601 timestamp letting the operator make the kill switch fail CLOSED until a given time) was raised as a potential enhancement. Deferred because the External Send Gate (Invariant 1) is the real security boundary and a fail-closed kill switch is belt-and-suspenders. Recording in tech-debt keeps the idea retrievable without over-engineering a mechanism the threat model does not require.

## Open items handed off

- **OBS-1 — CLAUDE.md v13→v14 / v8→v9 citation sweep (recommended next PR).** The drift checker now reports ~20 M1 findings (citations in `CLAUDE.md` and `README.md`) with the current manifest in place. Run `doc-reconciliation-auditor` against the new main (with the F07 clause already landed) and reconcile in a dedicated pass. The two PRs deliberately do not overlap — both touch `CLAUDE.md` and should be sequenced.
- **Orphaned `~/its-itest-fix` worktree** (merged keychain-fix branch, current session worktree) pending operator cleanup: `git worktree remove ~/its-itest-fix --force && git worktree prune`. Force-delete is hook-blocked from inside CC; operator runs this manually.

## What was NOT touched

- **No production Python code.** All changes are in `CLAUDE.md`, `docs/doctrine_manifest.yaml`, and `docs/tech_debt.md`.
- **`tests/test_check_doctrine_drift.py`** — deliberately left unchanged after confirming the tests assert only contract shape, not version literals. See Pre-flight findings.
- **`meta.its_head` / `generated` / `seeded_by`** in the manifest — not refreshed; those are the original seeding record, not per-bump fields.
- **The `section_42` block** in the manifest — the `(Op Stds v13)` annotation is a when-introduced reference; v14 did not alter §42.
- **The behavior description of `@require_active`** ("PAUSED or MAINTENANCE → exit cleanly") — unchanged; the F07 clause is documentation of the security boundary, not a code or behavior change.
- **Blueprint-side docs** — this PR is exec-repo only; the blueprint PR #23 that bumped FM/Op Stds was already merged.

## Subagents used

- **`brief-validator`** — verified all version numbers and SHAs against the live blueprint; caught the stale `3b7d56d` artifact and confirmed no test edit was needed (all claims PASS after those two corrections).
- **`ops-stds-enforcer`** — CLEAN across §3/§3.1/§14/§23/§30/§41/§42; independently confirmed `29000f1` as correct SHA for both entries, `blueprint_head` consistency, both drift regexes, and that the F07 clause matches v14 §1 verbatim.
- **`pr-landed-verifier`** — four-part verify clean (quoted verbatim above).

## Cross-references

- Blueprint PR #23 (commit `29000f1`) — introduced FM v9 and Op Stds v14; the upstream event this PR records on the exec side.
- `docs/doctrine_manifest.yaml` — primary file changed.
- `docs/tech_debt.md` — `[DEFERRED 2026-05-29]` fail-closed kill-switch entry added.
- `CLAUDE.md` — F07 kill-switch security-boundary clause appended.
- `docs/operations/pr_merge_discipline.md` — four-part verification protocol referenced.
