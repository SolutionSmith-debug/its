---
type: audit
date: 2026-05-29
status: active
related_prs: []
workstream: infrastructure
tags: [agents, hooks, scaffolds, worktree, audit]
---

# Agent + Workflow Optimization Audit (2026-05-29)

## Purpose

Read-only audit of the Claude Code agent / hook / scaffold infrastructure across the
execution repo (`~/its`) and the planning repo (`~/its-blueprint`). It produces **ranked
recommendations for operator decision** — nothing in this doc was auto-implemented. The
companion Part-1 deliverable (`docs/operations/worktree_discipline.md`, this PR) is the one
exception: it implements the worktree-discipline fix that several findings here point to.

## Headline verdict

**The agent/hook/scaffold infrastructure is healthy and the roster is correctly sized and
modeled — but the brief's central premise was wrong (agents and hooks are a single source of
truth via symlink, not drift-prone copies), and that symlink trades content-drift risk for a
quieter fail-open risk that is the single most important thing to know going forward.**

## Scope & method

- **Audited:** the 9 agents in `.claude/agents/`, the 4 PreToolUse guard hooks in
  `.claude/hooks/` + their wiring, both repos' `.claude/settings.json`, and the 10 scaffolds
  in `~/its-blueprint/prompts/scaffold/`.
- **Method:** `brief-validator` ran first against the brief's current-state claims (it
  caught the premise error); the orchestrator then verified topology directly (`readlink`,
  `git ls-tree`/`ls-files -s`, `diff`, import-resolution probes); then a 4-lens read-only
  workflow ran in parallel — scaffold drift (A), an **adversarial refutation** of the
  topology conclusion (B), roster health (C), and hook coverage (D). Findings below are the
  synthesis. Evidence is quoted inline.

## Status legend

| Severity | Meaning |
|----------|---------|
| 🔴 high | Real risk or a known-recurring failure the infra doesn't prevent; decide soon. |
| 🟡 med | Worth fixing; not urgent; recoverable if left. |
| ⚪ cosmetic | Confirmed-healthy assessment or low-value polish — recorded so a future pass doesn't re-litigate. |

## Corrected premise (read this first)

The brief assumed the agent files are **independent copies in two repos** with **divergence
risk**, and that the **blueprint has no hooks**. All three are **false**:

- `~/its-blueprint/.claude/agents` and `.../hooks` are **git-tracked relative symlinks**
  (mode `120000`, blobs `../../its/.claude/agents` / `../../its/.claude/hooks`) that resolve
  to the **same inodes** the exec repo uses. There is exactly **one** copy of each agent and
  hook. Two-copy content drift is structurally impossible.
- The blueprint therefore **has** the full hook set (via the symlink) plus a byte-identical
  copied `settings.json` — it is a complete mirror, not a hook-free partial subset.
- The 4 hooks are wired **3 ways**: `block-dangerous-git.sh` globally (in `settings.json`,
  matcher Bash); `block-codeql-dismiss.sh`, `block-doc-reconciliation-write.sh`, and
  `block-doctrine-write.sh` each **agent-scoped** in their respective agent frontmatter.

Someone already solved the divergence problem the brief feared. The audit therefore pivots
to the *residual* risks the symlink design introduces — see 🔴 H1.

## Findings (ranked)

| ID | Area | Repo | Sev | Finding | One-line recommendation | Decision | Timing |
|----|------|------|-----|---------|--------------------------|----------|--------|
| H1 | topology | blueprint | 🔴 | Committed **relative** symlink fails **open**: on a clone / CI checkout / non-sibling layout it dangles → CC loads **zero agents** and the propose-only guard hooks silently vanish. | Document the load-bearing `~/`-sibling assumption (done in Part-1 doc); consider a tiny health-check that asserts the symlink resolves. | operator | doc now / check defer |
| H2 | scaffolds | blueprint | 🔴 | `doctrine-revision.md` has **no Authority-block / body-supersession check** — the exact v13/v14 self-contradiction class that slipped past and was caught only by adversarial review. | Add a Procedure step: `grep` the doc body for the prior version string; reconcile every in-body self-reference. | operator | implement-now (blueprint PR) |
| H3 | scaffolds | blueprint | 🔴 | Brief-authoring scaffolds (`cc-implementation.md`, `session-orientation.md`) never reference `brief-validator`; stale code-shape claims keep recurring (F04 `-w`, F02 importers, **this brief's** symlink premise). | Add one line telling the brief author to run current-state claims through `brief-validator` before CC acts. | operator | implement-now (blueprint PR) |
| H4 | hooks | both | 🔴 | **No hook could have caught** this session's collision (two sessions, one blueprint checkout) — hooks see command/file input, not process topology. | Worktree **discipline** is the right layer, not a hook. **Addressed by the Part-1 doc in this PR.** | operator | done (Part 1) |
| M1 | coverage | both | 🟡 | No agent/check covers **worktree hygiene** — `~/its` being off-main + stale worktrees were found only by manual `git worktree list`. | Add a survey line to `session-close-maintainer` (`git worktree list` → assert `~/its` on main, flag stale worktrees); **not** a new agent. | operator | defer |
| M2 | hooks | both | 🟡 | `settings.json` is a **copied, non-symlinked, unguarded** file (byte-identical today, blob `9d674a1`) with **no CI/lint** enforcing sync in either repo. | Keep it independent (per-repo wiring is legitimate — exec has no `doctrine/`); if drift-avoidance wanted, add a **warn-only** CI diff-check (not a symlink). | operator | defer |
| M3 | hooks | blueprint | 🟡 | Doctrine-write protection is **only agent-scoped** (`session-close-maintainer`); an ordinary blueprint session editing `doctrine/*` has no guard. | **No hard global block** (operator must edit doctrine intentionally); rely on CI-lint + version-gate + `doc-reconciliation-auditor`. Optional warn-only notice. | operator | defer |
| M4 | scaffolds | blueprint | 🟡 | `session-log.md`'s execution-side guidance reuses the planning template's section skeleton instead of the canonical 7-section order in `docs/session_logs/README.md` (the 4-part CI block itself is correct). | Replace "structure is the same" with an explicit pointer to the exec 7-section order. | operator | defer |
| C1 | hooks | both | ⚪ | Keeping `settings.json` independent (not symlinked) is **correct** — it's the one legitimate per-repo policy surface. | No change. | operator | — |
| C2 | agents | both | ⚪ | `doc-reconciliation-auditor` on **opus is justified** (semantic drift judgment; mechanical tier already offloaded to a deterministic script). | Keep opus. | operator | — |
| C3 | agents | both | ⚪ | `pr-landed-verifier` is the most mechanical agent and a **defensible haiku candidate**, but the asymmetric downside (silently certifying a ghost merge via a mis-parsed leg-4 + the load-bearing verbatim phrase) argues against it. | Keep sonnet; record the rationale so a cost pass doesn't re-litigate. | operator | — |
| C4 | agents | both | ⚪ | The 8-sonnet / 1-opus / 0-haiku split **matches operator convention exactly**; no judgment agent is mis-tiered. | No change. | operator | — |
| C5 | agents | both | ⚪ | `doc-reconciliation-auditor` vs `session-close-maintainer` vs `session-log-writer`: the 3-way split is **principled and explicitly reasoned** (heavy/light drift-guard halves; subagents-can't-spawn-subagents forces the maintainer/writer split). | No consolidation (§14 preservation-over-refactor). | operator | — |
| C6 | agents | both | ⚪ | No dead/speculative agents; each cites the concrete origin failure-case that justified it. | No change. | operator | — |
| C7 | hooks | blueprint | ⚪ | **Negative result:** no clean low-false-positive `SessionStart` collision guard exists — any guard needs a lockfile/PID protocol (= worktrees reimplemented, worse) or a branch heuristic that false-positives on normal solo work. | Do not build one; recorded so it isn't re-attempted. | operator | — |
| C8 | scaffolds | blueprint | ⚪ | `doctrine-revision.md` steps 7–8 instruct a bare "push to main" with no landed-verify / branch-protection reference, unlike code-side scaffolds. | Add a one-line branch-protection note. Low priority (blueprint CI surface is lighter). | operator | defer |
| OBS-1 | doctrine | both | (obs) | **Cross-repo content drift, already tracked:** blueprint `operational-standards.md` is **v14 on `origin/main`** (PR #23 `29000f1`), but exec `CLAUDE.md` asserts "canonically at v13" (15 mentions). Already logged as tech-debt on `~/its` @ `36e429e` ("v14/v9 citation lag"). | Run `doc-reconciliation-auditor`; reconcile in a dedicated pass. **Not** this audit's scope. | operator | defer |

## High-finding detail

### 🔴 H1 — The symlink fails *open*

**What.** `~/its-blueprint/.claude/{agents,hooks}` are committed **relative** symlinks
(`../../its/.claude/…`). They resolve correctly only when `~/its-blueprint` sits as a
`~/`-level sibling of `~/its`. A clone of the blueprint *alone* (a CI runner, a fresh
machine), or any non-sibling worktree (e.g. `~/work/wt`), leaves them **dangling** — verified
dangling from both `/tmp/clone/its-blueprint` and `~/work/wt`. When they dangle, Claude Code
finds **no agents**, and because the three propose-only PreToolUse guards
(`block-codeql-dismiss`, `block-doc-reconciliation-write`, `block-doctrine-write`) are wired
through those same agents/hooks paths, they **silently disappear**.

**Why it matters.** This is a fail-*open*, not fail-closed: the structural backstops that
exist precisely so a misfire can't silently dismiss a CodeQL alert / rewrite doctrine / edit
during a reconciliation pass would be *absent* with no error. The symlink converts the
brief's feared content-drift into a resolution/availability risk. Probability is low on the
operator's stable local sibling layout; impact is high (security guards silently off).

**Recommended fix.** Two layers, both light: (1) **document** the load-bearing `~/`-sibling
assumption — **done** in `docs/operations/worktree_discipline.md` (this PR), including the
explicit "do not assume the guards are present in a bare blueprint clone" warning. (2)
*Optionally* add a small health-check (a `SessionStart` notice or a watchdog/CI line) that
asserts `~/its-blueprint/.claude/agents` resolves to an existing dir. **Operator decision;
defer the check.** Do not "fix" by de-symlinking — the single-source-of-truth property is
worth keeping.

### 🔴 H2 — `doctrine-revision.md` has no Authority-block reconciliation step

**What.** The scaffold's only supersession mechanic is a frontmatter `supersedes:` pointer
(line 36) — a cross-doc link. Nothing tells the author to confirm the **doc body** no longer
asserts the prior version's rules. That is exactly how a v13/v14 self-contradiction reached a
doctrine doc and was caught only by adversarial review.

**Why it matters.** Doctrine is canonical; a doc that bumps its version header but leaves
`v(N-1)` assertions in its body is internally contradictory and silently authoritative.

**Recommended fix.** Add a Procedure step under "If revising":
`grep -n "v{N-1}" doctrine/{name}.md` and reconcile every in-body self-reference to `v{N}`
or frame it explicitly as superseded history. **Operator already flagged this line.**
Mechanical doc edit, but lands in a **future blueprint PR** (this session does not touch the
blueprint tree). **Implement-now** once that PR opens.

### 🔴 H3 — Brief scaffolds don't route current-state claims through `brief-validator`

**What.** `grep -rniE 'brief-validator|agent'` across all 10 scaffolds returns **zero hits**.
The brief-authoring scaffolds push verification onto CC at *execution* time, not onto the
*author* up front — even though `brief-validator` exists specifically to catch stale
code-shape claims before CC acts.

**Why it matters.** Stale claims recur and are expensive: F04's wrong `-w` ordering, F02's
missed importers, and **this very session's** brief ("independent copies / blueprint has no
hooks" — false). The agent layer and the scaffold layer are disconnected; wiring the
cross-reference is the highest-leverage scaffold improvement (the same gap applies to
`pr-landed-verifier` for the four-part verify and `ops-stds-enforcer` for invariant checks).

**Recommended fix.** Add to `cc-implementation.md` (and a cross-ref in
`session-orientation.md`): *"Before finalizing a brief that names specific files / functions
/ line-ranges or makes current-state claims, run those claims through the `brief-validator`
agent."* **Future blueprint PR; implement-now** when it opens.

### 🔴 H4 — No hook catches the parallel-session collision (and that's fine)

**What.** The session's root problem — two sessions sharing one `~/its-blueprint` checkout —
is a runtime process-topology problem. PreToolUse hooks only see proposed tool input; they
are structurally blind to "another session is live in this directory." A `SessionStart`
guard can't know either without a lockfile/PID protocol that amounts to worktree discipline
reimplemented, more fragile (see C7).

**Why it matters / fix.** The correct layer is **worktree discipline**, extending the pattern
the exec repo already uses (`~/its` has 4 worktrees on distinct branches) to the blueprint
(which had **0** worktrees). **This is addressed by the Part-1 doc in this PR** — no new hook.

## Implement-now vs defer (explicit call per finding)

- **Implement now (this PR):** H4 — done, via `docs/operations/worktree_discipline.md`.
- **Implement now (a future *blueprint*-rooted PR — blueprint tree is occupied this session):**
  H2, H3. Both are small doc-edits to scaffolds; bundle with the blueprint-side worktree doc
  (appendix below).
- **Document now / tooling deferred:** H1 — the sibling-layout warning is in the Part-1 doc;
  the optional resolve-check is deferred and operator-decided.
- **Defer (operator decides if/when):** M1 (session-close survey line), M2 (settings.json
  warn-only check — optional), M3 (no global doctrine guard — likely never; correct as-is),
  M4 (session-log scaffold pointer), C8 (push-to-main note), OBS-1 (doctrine reconciliation
  via `doc-reconciliation-auditor`).
- **No action (confirmed healthy):** C1–C7.

## Cross-repo sequencing

The blueprint half of the worktree discipline (and the scaffold edits H2/H3/M4/C8) **must not
land in this session** — `~/its-blueprint` is on `close-out-f02-f22` and that close-out's work
should land first. Recommended order:

1. (this PR) Exec-repo Part 1 + this audit doc land on `~/its` main.
2. The blueprint `close-out-f02-f22` branch merges (separate session).
3. A future **blueprint-rooted** session (in its own blueprint worktree) lands: the
   blueprint-side worktree doc (appendix), the scaffold edits (H2, H3, M4, C8), and reconciles
   OBS-1 via `doc-reconciliation-auditor`.

## Appendix A — Adversarial residual-risk analysis (Lens B)

The symlink topology was stress-tested by an adversarial agent instructed to **refute** the
"zero divergence risk" conclusion. Verdict: **partially refuted** — the topology genuinely
eliminates two-copy content drift (verified: both paths are mode-`120000` blobs resolving to
one inode), but it undercounts residuals. The full residual set:

1. **Fail-open on clone / non-sibling layout** (H1) — dangling symlink → no agents, guards
   silently off. Verified dangling from `/tmp/clone/its-blueprint` and `~/work/wt`.
2. **Working-tree branch coupling** — the symlink targets the `~/its` *working tree*, not a
   pinned ref. When this audit's ground truth was captured, `~/its` was on
   `close-out-f02-f22-tech-debt` (not main); it returned to `main` before session close (a
   live illustration of the hazard *and* of the discipline self-correcting). The mechanism
   is the durable point: any feature-branch or uncommitted edit to an agent/hook propagates
   to every blueprint session and sibling exec worktree with **no review gate**. Same class
   as the documented daemon-reads-working-tree topology — a second reason to keep `~/its` on
   main between tasks.
3. **Unguarded `settings.json` copy** (M2) — byte-identical today, no sync check anywhere.
4. **Security-hook transitive dependency** — the agent-scoped guards invoke
   `"$CLAUDE_PROJECT_DIR"/.claude/hooks/block-*.sh`, which in a blueprint session resolves
   only by traversing the hooks symlink. So the guards inherit residuals 1 and 2.
5. **No structural detection** for any of the above — consistent with the deliberate
   "no automated cross-repo divergence check" design.

## Appendix B — Draft: blueprint-side worktree discipline (for a future blueprint PR)

Lift this into the blueprint's own operations docs (e.g. `its-blueprint/references/` or an
operations equivalent) in a future blueprint-rooted session. It is the blueprint's view of
the same discipline `docs/operations/worktree_discipline.md` documents from the exec side.

> **Worktree discipline (blueprint).** The blueprint is markdown-native — no venv / PYTHONPATH
> concern — but it has the same checkout-collision hazard as the exec repo, and until
> 2026-05-29 it ran as a single shared checkout (two doctrine-touching sessions collided on it
> that day). Rule: **each doctrine/reference/scaffold-editing session gets its own worktree**,
> created as a `~/`-level sibling:
>
> ```bash
> git -C ~/its-blueprint fetch origin
> git -C ~/its-blueprint worktree add ~/its-blueprint-<task> -b <branch> origin/main
> # cd ~/its-blueprint-<task> && claude
> ```
>
> **Never run two doctrine-touching sessions against the same blueprint checkout.** The
> `.claude/{agents,hooks}` symlinks require the `~/`-sibling layout — a worktree placed
> elsewhere, or a bare clone without `~/its` beside it, leaves them dangling and the guard
> hooks silently absent (fail-open). Cleanup is an operator action (force-delete is
> hook-blocked inside CC): `git -C ~/its-blueprint worktree remove ~/its-blueprint-<task>
> --force && git -C ~/its-blueprint branch -D <branch> && git -C ~/its-blueprint worktree
> prune`. Fallback if you'd rather not manage worktrees: serialize — one blueprint session at
> a time, land before the next.

## Owner / next action

`@solutionsmith`. This audit is propose-only; each finding is the operator's call. The
recommended next actions are the three sequencing steps above. Re-run this audit (or invoke
`doc-reconciliation-auditor` for the doctrine half) after the blueprint close-out lands.
