# 2026-05-18 — post-PR-#15 followup (memory + mypy + chore close)

Three lightweight followups from the PR #15 close-out review.
Bookkeeping work, no behavioral changes to production code paths.

Sequencing:
1. **Task 1 (no commit)** — updated my own working memory file at
   `~/.claude/projects/-Users-sethsmith/memory/project_phase1_status.md`
   to reflect post-PR-#15 reality. Not user-visible, not in this repo.
2. **Task 2 (this PR)** — mypy baseline inventory report + 4 new
   `docs/tech_debt.md` entries.
3. **Task 3 (separate PR #16, merged before this one)** — fix the
   mypy error in `reconcile_box_listings.py:127` I introduced in
   PR #13.

## Commits landed

| SHA | Title | Purpose |
|---|---|---|
| `7b292da` (PR #16) | fix(box_migration): narrow reconcile_box_listings type annotation | Task 3 — walrus-narrowed the double `.match()` call site |
| _(this PR)_ `7a08809` | docs: mypy baseline inventory post-PR-#15 | Task 2 — report + 4 tech_debt entries |

Task 1 is intentionally not a commit — it's working memory, not repo content.

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26069320511](https://github.com/SolutionSmith-debug/its/actions/runs/26069320511) | `7b292da` (PR #16, Task 3 merge) | green (35s) |
| [26069496189](https://github.com/SolutionSmith-debug/its/actions/runs/26069496189) | `2abac1c` (PR #17, Task 2 merge) | green (29s) |

## Decisions made during session

### Task 2 classification methodology

For each of the 8 errors at HEAD (`3a97061`, post-PR-#15), ran `mypy .`
at four historical snapshots — `1d7cb80` (start of 2026-05-18 work),
`343b84b` (post-#11, pre-#12), `9b5cbfd` (post-#12, pre-#13), and
`89f7bf7` (post-#14, pre-#15). For each error, noted presence and line
number at each snapshot to identify "first seen" point.

Methodology turned out important: it caught that `parse_job_v3.py`'s
`matched` annotation error shifted line 692 → 767 across PR #13's
`parse_subsubject` addition (same error, different line). Without
that, the error could have read as "introduced by PR #13" — wrong.
Tracking by error type + file (not just file:line) avoided the false
positive.

Full lifecycle table in `docs/reports/2026-05-18_mypy_baseline.md`.

### Why the brief said "5"

The 2026-05-18 sanity-check brief quoted "5 errors" as the mypy
baseline. Real `mypy .` was 9 at start of day. The discrepancy: prior
session logs ran `mypy shared/ scripts/ tests/` — a narrower scope
that excluded `box_migration/` and `smartsheet_migration/`. Those two
directories contribute 3 of the 9 errors. Recorded explicitly in the
inventory report so the same undercount doesn't recur. Going forward,
this session adopts `mypy .` as the canonical baseline.

### Did any of the 8 errors warrant "fix now, not tech debt"?

One: `reconcile_box_listings.py:127`. My own miss from PR #13, the
single error introduced today. Fixed in Task 3 of this session (PR
#16). The other 7 are all either:
- Vendor-SDK import-untyped noise (4 errors, 2 files) — config fix,
  not a code bug. Captured as one combined tech_debt entry.
- Preservation-code errors in `box_migration/parse_job_v3.py`,
  `smartsheet_migration/ss_api.py`, `smartsheet_migration/migrate_fl.py`
  (3 errors, 3 files). All preservation-over-refactor (Op Stds v8 §14)
  scoped. Each captured as its own tech_debt entry with regex sketch,
  suggested fix, and expected delta.

### Task 3 fix shape — why walrus

Previous form:

```python
key=lambda p: int(PORTFOLIO_FILE_RE.match(p.name).group(1))
              if PORTFOLIO_FILE_RE.match(p.name)
              else 9999,
```

The two `.match()` calls aren't bound to each other. mypy can't
propagate truthy-narrowing from the condition's call to the
expression's call. Runtime was safe because both `.match()` calls
return the same result for the same input, but:

1. mypy is right to flag it (the two calls are not semantically tied).
2. The double call is wasteful — twice the regex evaluation per file
   in the sort.

Walrus form binds the match once with `(m := PORTFOLIO_FILE_RE.match(p.name))`,
truthy-checks it (narrowing `Match[str] | None` to `Match[str]`), then
uses `m.group(1)` safely. Same behavior, half the work, mypy clean.
Minimum-diff fix. One added comment explaining the narrowing intent.

## Open items handed off

These all carry over from PR #15's close-out queue; this session's
inventory work made each more concrete.

- **Vendor-SDK import-untyped silencing** (new tech_debt entry from
  Task 2). Should land BEFORE any mypy-in-CI integration so signal-
  to-noise is acceptable. Small chore — one `pyproject.toml` block
  + `types-requests` dev-dep.

- **`parse_job_v3.py:767 matched annotation`** (new tech_debt entry).
  Bundle with next `parse_job_v3` touch if possible.

- **`ss_api.py:79 body arg`** (new tech_debt entry). Bundle with the
  smartsheet_migration import-time-side-effects entry from PR #15
  (M4).

- **`migrate_fl.py:176 warnings annotation`** (new tech_debt entry).
  Same bundling rationale as `ss_api.py`.

- **mypy-in-CI decision.** Task 2's inventory is the input for this
  decision. Recommendation in the report: silence vendor noise
  first; otherwise persistent warnings will train operators to
  ignore mypy. Decision not made here.

- All 4 `box_migration/parse_job_v3.py` workstream tech_debt entries
  from PR #13/#14 (V/S vendor-sub, ISO date prefix, person_tag
  over-match) and the `smartsheet_migration` import-time entry from
  PR #15 — still OPEN.

- `_alert_critical` Resend wiring (Op Stds v8 §3 triple-fire) —
  remains the next natural feature work; deliberately a fresh
  session start per the brief.

## Lessons captured

The mypy-error swap during PR #13 (one fixed by #12, one introduced
by #13) was silent across both PRs because nobody ran `mypy .` before
either merge. The brief framed this as cause #2: "PRs introduced new
mypy errors that nobody surfaced at the time." Confirmed.

The narrower `mypy shared/ scripts/ tests/` baseline used in earlier
session logs was a reasonable working choice for shared/ refactor
work, but it masked the picture. Going forward, session-log mypy
quotes should use `mypy .` and explicitly note the count.

If mypy lands in CI (open queue #3), it would catch silent additions
like the one PR #13 made. That's the structural argument for the
in-CI decision: not the absolute error count, but the prevention of
silent drift.

## What was NOT touched

Per the brief, deliberately out of scope:

- `_alert_critical` Resend wiring.
- The 4 pre-existing `box_migration` / `smartsheet_migration`
  tech_debt entries from prior PRs.
- mypy-in-CI decision (only fed input via the inventory report).
- Any test additions beyond what Task 3's fix required (none — the
  walrus form's behavior is unchanged, covered by existing tests).
- User memory edits beyond Task 1 (the one explicitly approved).

## Sequencing context

Lands directly after PR #15 (`3a97061`) which closed the 2026-05-18
sanity-check audit. This session resolves PR #15's three close-out
items (memory update, mypy baseline confusion, my own PR #13 miss).
Closes the seven-PR work block from today's session: PRs #9 through
#16, plus this PR #17.

Stopping point per the brief: no rolling into `_alert_critical`
work. That gets a fresh session.
