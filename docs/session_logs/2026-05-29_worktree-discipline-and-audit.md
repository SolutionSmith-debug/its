---
type: session_log
date: 2026-05-29
status: closed
related_prs: [121]
workstream: infrastructure
---

# 2026-05-29 — Worktree-isolation fix + agent/workflow optimization audit

Documented the worktree-isolation discipline the exec repo already relied on (and extended
it to the blueprint repo, closing the gap that let two parallel sessions collide on the
single `~/its-blueprint` checkout), and ran a read-only audit of the agent / hook / scaffold
infrastructure across both repos producing ranked, propose-only recommendations.

## Commits landed

- **`f47cb46`** (squash of `96cb05d`) — `docs(operations): worktree-discipline runbook + 2026-05-29 agent/workflow audit` (PR #121). Adds `docs/operations/worktree_discipline.md`, `docs/audits/2026-05-29_agent-workflow-audit.md`, a defensive `.gitignore` `/its-*/` stanza, a one-line `CLAUDE.md` pointer, and the two auto-regenerated `docs/**/README.md` index rows. Doc/config-only — no production code.
- This session log lands as its own follow-up PR (per the #118→#119 convention).

## CI runs

PR #121 — four-part verify clean (via `pr-landed-verifier`):

```
- pytest: 1141 passed / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

- state: MERGED · mergedAt: 2026-05-29T15:59:15Z · mergeCommit: `f47cb469742aaa77f93fe78676815f19a3f1ae93`
- main CI on merge commit: `ci` run 26647719044 SUCCESS + `CodeQL` run 26647716976 SUCCESS.

## Decisions made during session

- **The brief's Part-2 premise was wrong — verified and corrected, not worked-around.** The brief assumed agents/hooks were independent two-repo copies with divergence risk and that the blueprint had no hooks. Direct verification (`readlink` / `git ls-files -s`) showed `~/its-blueprint/.claude/{agents,hooks}` are committed **relative symlinks** into `../../its/.claude/*` — a single source of truth. The audit pivoted to the *residual* risks the symlink introduces rather than the (non-existent) drift. `brief-validator` flagged the premise; I confirmed the mechanism before writing anything.
- **Root cause of the import gotcha was demonstrated, not asserted.** The brief said "imports resolve to `~/its` unless you set `PYTHONPATH`." Probing showed the real rule: `sys.meta_path = [..., PathFinder, _EditableFinder]`, so **CWD wins first** (`sys.path[0]`) and `PYTHONPATH=<worktree>` beats the editable finder only because `PathFinder` precedes `_EditableFinder`. The doc documents the verified mechanism (CWD-first, PYTHONPATH belt-and-suspenders), which is more accurate than the brief's framing. Rejected alternative: just repeating the brief's `PYTHONPATH` instruction without the CWD nuance — would have misled on *why* the failure happens.
- **Audit doc placed in `docs/audits/`, not the brief-suggested `docs/operations/`.** `doc_conventions.md` reserves date-prefixed `YYYY-MM-DD_topic-slug.md` + `type: audit` for `docs/audits/`; `operations/` is evergreen `topic-slug.md` with no date. A dated audit-shaped file in `operations/` would violate the filename convention the lint enforces. Chose convention-conformance over the brief's literal path; flagged the deviation to the operator. (`worktree_discipline.md` itself IS an operations runbook and correctly lives in `operations/`.)
- **Guardrail: doc-over-tooling (Part 1c).** Confirmed `block-dangerous-git.sh` has no worktree pattern (correct — `git worktree` ops aren't destructive like force-push) and that no hook could catch the parallel-session collision (it's a process-topology problem, invisible to a tool-input hook). Recommended *against* a new session-start guard: any clean signal needs a lockfile/PID protocol that is just worktrees reimplemented, and a branch-name heuristic false-positives on normal solo work. Recorded the negative result so it isn't re-attempted.
- **Defensive `.gitignore /its-*/` rather than a no-op.** Sibling worktrees (`~/its-<task>`) live outside the repo so `.gitignore` is technically a no-op for them; added the stanza anyway to guard the one accident that matters — an *accidentally nested* worktree at the repo root. Verified zero-false-positive (no tracked top-level dir is named `its-*`).
- **The adversarial audit lens upgraded my own conclusion.** It partially refuted "divergence risk is structurally zero": the symlink trades content-drift for a **fail-open** — the relative symlink dangles on a clone / CI / non-`~/`-sibling layout, and CC then loads zero agents *and the propose-only security hooks silently vanish*. Folded that caveat into the Part-1 doc (blueprint worktrees must be `~/`-siblings) and made it audit finding 🔴 H1.
- **Did not touch the blueprint tree.** All blueprint-side fixes (the blueprint worktree doc, scaffold findings H2/H3) are drafted in the audit (Appendix B) and deferred to a future blueprint-rooted PR, per the brief's cross-repo sequencing.

## Open items handed off

- **Future blueprint-rooted PR (after `close-out-f02-f22` lands):** lift the blueprint-side worktree doc (audit Appendix B) into the blueprint's operations docs; apply scaffold fixes **H2** (`doctrine-revision.md` Authority-block / in-body prior-version reconciliation step — operator already flagged this) and **H3** (route brief current-state claims through `brief-validator`); optionally **M4** (`session-log.md` exec 7-section pointer) and **C8** (doctrine-revision push-to-main branch-protection note).
- **OBS-1 — doctrine version drift:** exec `CLAUDE.md` still asserts Op Stds **v13** (15 mentions); blueprint is canonically **v14** on `origin/main` (PR #23 `29000f1`). Already tracked as tech-debt on `~/its` @ `36e429e` ("v14/v9 citation lag"). Run `doc-reconciliation-auditor` and reconcile in a dedicated pass. **Not** touched here.
- **M1 — worktree-hygiene coverage:** add a survey line to `session-close-maintainer` (`git worktree list` → assert `~/its` on main + flag stale worktrees). Lightweight; not a new agent.
- **H1 (optional) — symlink-resolve health check:** consider a tiny `SessionStart`/watchdog/CI assertion that `~/its-blueprint/.claude/agents` resolves to an existing dir (fail-open detection). Operator decision; deferred.
- **Operator cleanup (force-delete is hook-blocked inside CC):** stale worktrees `~/its-f16` (`57bdca8`) and `~/its-f02-f22` (`ae7131c`) remain; this session's own `~/its-optimize` worktree + `worktree-discipline-and-audit`/`session-log-worktree-audit` local branches need operator removal. Suggested: `git -C ~/its worktree remove ~/its-f16 --force && git -C ~/its branch -D f16-session-log-final && git -C ~/its worktree prune` (repeat per stale worktree).
- **Reconcile the unpushed-then-pushed `~/its` main:** during the session local `~/its` main held `36e429e` (unpushed) while `origin/main` was `df83713`; both have since landed on origin (`36e429e` is on `origin/close-out-f02-f22-tech-debt`, my docs are `f47cb46` on `origin/main`). `36e429e` touches only `docs/tech_debt.md` (no overlap) so the reconciliation is clean — just `git -C ~/its checkout main && git pull` between tasks.

## What was NOT touched

- **The `~/its-blueprint` working tree** — read-only inspection + `git worktree add` only, per the brief. (It was already clean / committed when the session started — the F02/F22 edits the brief expected to find uncommitted had already landed.)
- **Any production Python code** — Part 1 is doc/config; Part 2 implemented nothing.
- **Agent model assignments** — audited (8 sonnet / 1 opus / 0 haiku confirmed correct; opus on `doc-reconciliation-auditor` justified; `pr-landed-verifier` keep sonnet) but not changed.
- **Agent consolidation** — the `doc-reconciliation-auditor` / `session-close-maintainer` / `session-log-writer` three-way split is principled (§14); no consolidation.
- **`settings.json`** — left as an independent per-repo file (audit M2/C1: legitimate per-repo wiring surface; not symlinked).
- **Doctrine version strings / OBS-1** — flagged, not reconciled (doc-reconciliation-auditor's job; out of scope).
- **No new hooks** — the collision fix is the worktree-discipline doc, not tooling.

## Lessons captured to memory

- Updated auto-memory `exec-host-worktree-daemon-topology.md`: added the canonical-doc pointer (`docs/operations/worktree_discipline.md`), the blueprint `.claude/{agents,hooks}` **relative-symlink** topology, and the **fail-open** consequence (dangles on non-`~/`-sibling layout → CC loads no agents + guard hooks vanish). Takeaway for future sessions: blueprint worktrees must be `~/`-level siblings of `~/its`, and the agent/hook guards are *not* guaranteed present in a bare blueprint clone.
- Reinforced (already in memory): the live launchd daemon runs the `~/its` working tree, so `~/its` should sit on `main` between tasks and real work happens in a worktree.
