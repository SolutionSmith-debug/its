# Session logs

Durable narrative record of Claude Code sessions that change the repo. Each log
captures the decisions made *during execution* — the context that doesn't
survive in commit messages, doesn't belong in the Claude.ai planning project,
and would otherwise have to be reconstructed by re-reading transcripts months
later.

## Why these exist

Three records describe the project, and each loses something the others keep:

- **Claude.ai planning project** — decisions *about* the system. Foundation
  Mission, Operational Standards, mission files, the Master Checklist. Stable,
  version-numbered, owner-facing.
- **Commit history** — what changed in the code. Atomic, machine-readable, but
  the *why* gets compressed into one or two paragraphs per commit.
- **Session logs** — decisions made *during* execution: which of two valid
  approaches got picked and the reasoning, what was deliberately left
  untouched, what was flagged for the planning project, what failed before
  succeeding. Bridges the planning project (system-level) and the commit
  history (code-level).

A session log is the answer to "why did 2026-05-17 land this particular set of
commits and not the obvious alternative." Re-reading a transcript six months
later is expensive; re-reading a 50-line log is cheap.

## When to write one

Write a session log when **both** are true:

1. The session lands ≥1 commit.
2. The session involved at least one non-obvious decision — a choice between
   valid alternatives, a deliberate carveout from a project rule, an item
   handed off to the planning project, or a diagnosis that didn't match the
   initial brief.

Don't write one for pure mechanical commits (typo fix, dependency bump,
formatting-only). The commit message is sufficient there.

If unsure: write it. The cost of an unnecessary log is one extra commit; the
cost of a missing log is reconstructing context from transcripts.

## Filename convention

`YYYY-MM-DD_short-slug.md` — date first so chronological sort works in any
file browser; slug short enough to read at a glance.

Examples:
- `2026-05-17_ruff_and_doc_refresh.md`
- `2026-05-20_safety_reports_intake_wiring.md`

If a single date has multiple distinct sessions, append `_2`, `_3`, etc.

## Section ordering

Every log uses this section order. Skip a section only if it would be empty.

1. **Date + session focus** — one sentence at the top, after the H1.
2. **Commits landed** — SHA, title, one-line purpose per commit.
3. **CI runs** — URL, duration, result per run.
4. **Decisions made during session** — one line each. Include the alternative
   that was rejected and the reasoning, not just the choice.
5. **Open items handed off** — anything flagged for the planning project's
   Master Checklist, future sessions, or external systems. Include the
   suggested wording when possible so the recipient can copy-paste.
6. **What was NOT touched** — explicit list. The negative space matters: it
   documents that the omissions were deliberate, not oversights.
7. **Lessons captured to memory** — which memory files were updated and what
   the takeaway was. Cross-references the persistent-memory system so future
   sessions can find the rule even without re-reading this log.

## Planning project vs. session log — what goes where

**Planning project (Claude.ai)** — decisions about the system itself:
canonical doc versions, invariants, architectural choices, workstream missions,
Master Checklist items, schemas, the things that outlive any one session.

**Session log (this directory)** — decisions made during a session, including
the ones that *didn't* reach the planning project: which lint rule to suppress,
which precedent to mirror, why option (b) beat option (a) on a doc link, what
the regex bug was that caused a tally miss. If the decision is about a
specific commit or set of commits, it lives here.

If a session-log decision turns out to be load-bearing for the system
(e.g., "we now have a session-log convention"), the next session that has a
natural reason to touch the planning surface (CLAUDE.md, an Op Std, a mission
file) carries the decision up. Session logs are the staging area; the
planning project is canonical.

## First entry

[`2026-05-17_ruff_and_doc_refresh.md`](./2026-05-17_ruff_and_doc_refresh.md) —
ruff exemption for `box_migration/*` and the v5/v6/v7/v4 doc pointer refresh
PR #8 left half-done. Also the session that established this convention.
