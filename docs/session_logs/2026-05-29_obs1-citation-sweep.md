---
type: session_log
date: 2026-05-29
status: closed
workstream: docs
related_prs: [127]
tags: [doctrine-citations, foundation-mission, operational-standards, kill-switch, anomaly-logging, doc-reconciliation, obs1]
---

# 2026-05-29 — OBS-1: CLAUDE.md + README.md citation sweep (Op Stds v13→v14 / FM v8→v9)

PR: [#127](https://github.com/SolutionSmith-debug/its/pull/127) — squash-merged 2026-05-29T19:50:01Z, merge commit `aeca725551ef9823c8bf082166f8bcb50d3bb3ea`. `pr-landed-verifier` output, verbatim:

```
PR #127 — four-part verify clean
- state: MERGED
- mergedAt: 2026-05-29T19:50:01Z
- mergeCommit: aeca725551ef9823c8bf082166f8bcb50d3bb3ea
- main CI on merge commit: SUCCESS (run 26658780463, workflow: ci — conclusion: success) / (run 26658779840, workflow: CodeQL — conclusion: success)
```

(The `Push on main` dynamic workflow on the same merge commit also completed `success` — observed directly during the merge-commit CI poll; it is the leg `pr_merge_discipline.md` Step 4 calls out as the PR #68→#73 red-main propagator.)

## Purpose

The OBS-1 follow-on that PR #125 explicitly deferred. PR #125 bumped `docs/doctrine_manifest.yaml` and the `check_doctrine_drift.py` `drift_signal` regexes to Op Stds v14 / FM v9 — which then surfaced ~20 M1 drift findings: the v13/v8 citations still living in `CLAUDE.md` and `README.md`. This PR reconciles those citations so the exec-repo conversational docs match canonical doctrine (blueprint PR #23, commit `29000f1`). The bumped drift checker is both the worklist and the verification. **Docs only — no code, no manifest (done in #125), no doctrine (done in blueprint #23).** Sequenced after #125 because both touch `CLAUDE.md`, and #125 landed the F07 kill-switch clause this sweep reconciles around.

## Commits landed

- **`aeca725`** (squash of `102878c`) — `docs(doctrine): reconcile CLAUDE.md + README.md citations to FM v9 / Op Stds v14 (OBS-1)` (PR #127). 16 citation findings in `CLAUDE.md` + 4 in `README.md`, plus the semantic rewrite of the governing-version block. 27 insertions / 22 deletions across the two files.

## CI / verification

```
- pytest: 1141 passed / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- check_doctrine_drift.py: 0 drift (was 20 M1 findings pre-sweep)
- main-branch CI on merge commit: SUCCESS
```

## What got reconciled

- **All 20 mechanical M1 findings** — `Op Stds v13`→`v14` (×16: lines for the Architectural-model header, Invariant-2 §31/§33/§34 inline cites, the §3.1/§18/§34/§31/§23.3/§31/§3.1 table-and-prose cites, and the contradiction-check footer) and `Foundation Mission v8`/`FM v8`→`v9` (×4: the §-invariants heading, two `FM v8 Invariant 1` table cites, the footer). Every inline section number is unchanged (`§31`/`§33`/`§34`/`§3.1`/`§18`/`§32`/`§23.3`); only the version prefix moved — confirmed against the v14 changelog's "Sections §§2–42 carry forward from v13 with cross-reference refresh only."
- **Two checker-blind items fixed by judgment, not the regex.** The drift checker's `(?:Op Stds|Operational Standards)\s+v(\d+)` pattern only matches when the version number immediately follows the doctrine name, so it missed (a) the line-wrapped `Foundation Mission` / `v8` straddling two lines in the "Canonical docs:" list, and (b) the governing-version block, which phrases it as "Operational Standards is canonically at **v13**". Both are current-doctrine citations and were bumped.
- **Governing-version block rewritten to v14 + history extended `v12 → v13 → v14`.** Appends the v14 kill-switch §1 reframe (operator-convenience pause, fail-open by design, **not** a security control; audit F07; External Send Gate / FM Invariant 1 remains the real boundary; mechanism unchanged) and the companion FM v9 Invariant 2 Layer 5 reframe (anomaly logging recategorized from co-equal defense layer to post-hoc detection tripwire; audit F13; mechanism unchanged). History entries use **bare** version numbers (`v12 added …`, `v13 added …`, `v14 reframed …`) so they don't trip the checker's drift regex.

## Decisions made during session

1. **History entries kept as bare version numbers, not `Op Stds vN`-prefixed.** When extending the version-history block, writing e.g. `Op Stds v13 added §42` would newly trip the drift checker (the regex matches a doctrine-name-then-version pattern; `_near_historical` only exempts a citation when an `earlier|previously|superseded|…` marker sits in the ±40/80-char window, and "added" is not such a marker). Bare `v13 added §42` matches the existing house style and stays invisible to the checker. Alternative: prefix the history entries for symmetry. Rejected — it would manufacture new M1 findings.
2. **F07 kill-switch bullet (CLAUDE.md `**Kill switch first.**`) left byte-identical.** It already cited `Op Stds v14 §1` from #125; OBS-1 was sequenced *after* #125 precisely so this sweep could reconcile around an already-landed clause without a race. `ops-stds-enforcer` + the accuracy/F07 critic both confirmed it is absent from every diff hunk vs origin/main.
3. **Used "operator-convenience pause" (no "suggested") in the new prose.** The blueprint v14 changelog reads "operator-convenience *suggested* pause." `brief-validator` flagged the omission. Chose the landed F07 bullet's exact phrasing ("operator-convenience pause") over the doctrine verbatim so the two descriptions of the same concept inside CLAUDE.md read identically. Alternative: quote the doctrine verbatim. Rejected — internal consistency within the file outweighs verbatim-matching a changelog that the in-file F07 clause already paraphrases.
4. **F13/v9 framed as a "companion reframe" inside the *Op Stds* block.** `brief-validator` noted the v14 changelog does not itself name F13/FM v9 — the pairing is the manifest's framing. Adopted it anyway because it is accurate in substance (both are 2026-05-29 forensic-audit reframes) and the manifest (`doctrine_manifest.yaml` notes) is the in-repo canonical derivation that already pairs them.
5. **Invariant 2 Layer 5 *prose* (the "Six-layer defense" bullet 5) NOT reworded.** FM v9 reframes Layer 5 to a detection tripwire, but rewording the Invariant 2 section is a doctrine-characterization change, not a citation bump — out of scope for OBS-1 and carrying no version string the checker flags. Deferred to a tech-debt entry (below). Alternative: reword it here. Rejected — scope discipline; the brief scoped this PR to citations + the governing-version block only.

## Open items handed off

- **Invariant 2 Layer 5 characterization reword (tech-debt added this PR).** CLAUDE.md still lists Layer 5 under "Six-layer defense" as "Output validation and anomaly logging." FM v9 reframed it to a post-hoc detection tripwire (not a co-equal defense layer). The governing-version block now *states* the v9 reframe, but the Invariant 2 bullet itself still uses the pre-v9 framing — a mild internal tension to resolve in a future Invariant-2 doc pass. See `docs/tech_debt.md` `[OPEN 2026-05-29]`.
- **Worktree cleanup (operator).** This session ran in `~/its-obs1` (branch `obs1-citation-sweep`, then `obs1-session-log`). Force-delete is hook-blocked from inside CC; operator runs from `~/its`: `git worktree remove ../its-obs1 --force && git worktree prune`.

## What was NOT touched

- **No code, no `docs/doctrine_manifest.yaml`, no blueprint doctrine.** The manifest was bumped in #125; the doctrine in blueprint #23.
- **The F07 `**Kill switch first.**` bullet** — already v14-correct; byte-identical to origin/main.
- **`Op Stds v4 … superseded` (Invariant 1 prose)** and **`v12 added §§37–41, v13 added §42`** — historical-provenance refs, preserved as correct history.
- **Version-less `Op Stds §30` cites** (agent-section + SDK-vs-Live notes) — no version to bump.
- **`Op Stds §N` inline section numbers** — unchanged; v14/v9 renumbered nothing.
- **The Invariant 2 Layer 5 bullet prose** — deliberately left for a future characterization pass (see Open items + tech-debt).

## Subagents / workflow used

- **`brief-validator`** (session start) — verified Op Stds v14 / FM v9 canonical + the F07/F13 reframe characterizations against live blueprint frontmatter; caught the "suggested" wording nuance and confirmed §-numbers unchanged. All claims PASS.
- **`obs1-citation-sweep-verify` workflow** (3 reviewers, parallel) — `doc-reconciliation-auditor` (drift 0, historical preserved, manifest matches blueprint, no renumber), `ops-stds-enforcer` (§14 clean, §41 n/a, F07 untouched), and an accuracy/F07 critic (v14/v9 prose accurate vs changelogs, F07 byte-identical, no new drift). All three **PASS, zero blockers**.
- **`pr-landed-verifier`** — four-part verify clean (quoted verbatim above).

## Cross-references

- Predecessor: [`2026-05-29_exec-ledger-cleanup.md`](2026-05-29_exec-ledger-cleanup.md) (PR #125) — the manifest/regex bump + F07 clause that produced this sweep's worklist.
- Blueprint PR #23 (commit `29000f1`) — introduced FM v9 + Op Stds v14 upstream.
- `docs/doctrine_manifest.yaml` — the v14/v9 facts + `drift_signal` regexes the checker keys off.
- `scripts/check_doctrine_drift.py` — the worklist + verification tool.
- `docs/operations/pr_merge_discipline.md` — four-part verification protocol.
