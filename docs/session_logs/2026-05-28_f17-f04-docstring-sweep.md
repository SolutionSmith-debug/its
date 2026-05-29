---
type: session_log
date: 2026-05-28
status: closed
workstream: security
related_prs: [113]
tags: [forensic-audit, watchdog, observability, keychain, secret-hygiene, sdk-vs-live, brief-deviation, verify-before-fix, worktree]
---

# 2026-05-28 — Phase 1.4 sweep: F17 (intake_poll watchdog tracking) + F04 (keychain stdin write) + watchdog docstring drift

PR: [#113](https://github.com/SolutionSmith-debug/its/pull/113) — squash-merged 2026-05-29T02:38:57Z, merge commit `9ef0a66a19dc2a89e7192d84358a6d91fcca42f9`. **Four-part PR-landed verify clean** (`pr-landed-verifier`): state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on the merge commit = SUCCESS (`ci` run 26614667156 + `CodeQL` run 26614666787, both completed/success).

Verification gates:
- pytest: 1097 passed / 0 skipped / 16 deselected
- mypy: 0 errors / 134 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

## Purpose

Three independent, parallel-safe blocker-bucket items from the 2026-05-25 forensic audit §3, batched into one PR (cheap items; keeps four-part-verify overhead proportional per audit §7). Sweep PR 1 of the pre-Safety-Portal hardening cluster. F16 (UptimeRobot/Healthchecks ping) is its own operator-side follow-on (worktree `~/its-f16`); F18/F03 already shipped (#95) and were not touched.

## Verify-before-fix — brief-validator GO, one cosmetic anchor drift

`brief-validator` re-checked every code-shape claim against live HEAD `5bb6486`: all file paths, function names, constants, imports, call sites, test names, and audit findings VERIFIED. One drift: the brief located the "empty by design" Check C prose at a "function docstring (~L24-29)"; it actually lives in the module-header `Checks shipped:` comment block (L26) — the text exists, just not as a function docstring. No code-change impact.

## The F04 finding — the brief's prescribed fix was broken against the live CLI (Op Stds §30)

This is the session's non-obvious decision. The brief/audit prescribed the keychain stdin write as `subprocess.run([... "-w", "-U"], input=value)`. A live create→read-back round-trip against the real `security` CLI (throwaway entry, deleted after) showed that shape is **broken**:

- `-w` immediately followed by `-U` makes `security` swallow `-U` as the **password value** — the stored secret became the literal `"-U"`.
- `security add-generic-password -h` states "Specify `-w` as the last option to be prompted"; `-w` reads from stdin **only when it is the terminal option**, and then issues a password + **retype** confirmation prompt (two line-reads). Feeding the value once → "passwords don't match" → stores empty.

Corrected, live-verified shape (create + read-back + idempotent `-U` rotation all pass, special chars included):

```python
subprocess.run(
    ["security", "add-generic-password", "-U", "-a", account, "-s", service, "-w"],
    check=True, capture_output=True, text=True,
    input=f"{value}\n{value}\n",   # password + retype; never in argv/ps/EDR
)
```

Feeding the value twice is robust whether a future CLI build prompts once or twice (a single-prompt build reads line 1 and discards the rest). This still meets F04's actual objective — the secret is no longer in `ps` / `/proc/<pid>/cmdline` / EDR argv capture — and preserves the idempotent `-U` rotation the Box-OAuth refresh-token path depends on. The deviation was flagged in the PR for operator review before merge (approved). Textbook SDK-vs-Live (mocks pass, live API rejects): the original unit test had asserted `cmd[cmd.index("-w")+1] == "-U"`, i.e. it *encoded* the broken shape — rewired to assert `-w` is last, `-U` precedes it, secret absent from argv, value on `input`.

## F17 — live-validated in production, not just unit-tested

`intake_poll._write_watchdog_marker()` mirrors `weekly_send_poll`'s helper (fail-soft per §3.1) and is called after `_write_heartbeat()` **only inside `_poll_inside_lock`** (the completed-cycle path), never on the `skipped_disabled` / `skipped_locked` early-exits in `poll_once()`. The live launchd daemon `org.solutionsmith.its.safety-intake` — which runs the `~/its` working tree every 60s — picked up the (then-uncommitted) edit and wrote the real `~/its/.watchdog/safety_intake.last_run` on an actual cycle, confirming the marker fires on the production completed-cycle path.

## Decisions

- **Skip-path divergence is deliberate (diverges from weekly_send_poll).** The marker is written only on completed cycles, NOT on disabled/lock-held skips, because a stalled/disabled 60s intake poller is exactly the state Check C SHOULD surface (marker goes stale → WARN). `weekly_send_poll` marks even on its SkippedWeeklyOther path because that's a normal weekly state. Mandatory §42 rationale comment at the call site; two test assertions lock the divergence so a future reader can't "fix" it by adding the call to the skip paths.
- **5-minute freshness window** (~5 poll cycles) for `safety_intake`, per the high-frequency-poller convention. Tight relative to the watchdog's own once-daily 7 AM cadence (so in practice a stall surfaces at the next 7 AM sweep regardless), but 5 min is the semantically-correct "high-frequency poller" value and false-positive-free because the marker refreshes every 60s when healthy.
- **Slug `"safety_intake"` (brief), not `"safety_intake_poll"` (audit's suggestion).** The brief was explicit and self-consistent across the helper constant, TRACKED_JOBS entry, window, tests, and operator-smoke steps; §1c's real requirement is internal consistency (slug = TRACKED_JOBS entry = `{slug}.last_run` filename = marker dir). A cross-module consistency test pins it. The audit's `safety_intake_poll` (which would match the `safety_<module>` convention) was noted but the brief is the operative, more-recent instruction.
- **Empty-branch summary string reconciliation.** The `git grep "empty by design"` gate also catches the runtime summary in `_check_scheduled_jobs`' empty branch; the empty-branch test asserts `"empty by design" OR "No scheduled jobs"`. Dropped "by design" from the summary (`"...is empty)."`) — satisfies the gate and keeps the test green via the "No scheduled jobs" arm, with the branch behavior intact.

## Preserved (per Op Stds §14)

No refactors. The marker helper is duplicated verbatim from `weekly_send_poll` rather than extracted — `shared/heartbeat.py` consolidation remains the separately-tracked tech-debt item (not triggered here). `get_secret`'s read path (`-w` no-value read form) was left untouched (F04 is write-only). `ops-stds-enforcer` review of the diff: CLEAN across §42 / §3.1 / §14 / §30 / Send-Gate / secret-leak.

## What was NOT touched

- `shared/untrusted_content.py` — F03/F18 shipped in #95.
- No F16 ping code (separate operator-side follow-on, `~/its-f16`).
- No `shared/heartbeat.py` consolidation (tracked tech-debt).
- No broader watchdog refactor; only the three prescribed prose spots + the empty-branch summary.

## Process note — worktree topology

The operator pre-creates per-task git worktrees (`~/its-sweep` for this branch, `~/its-f16` for F16) alongside `~/its` (main). The brief said to launch rooted at `~/its`, so the work was authored there, then transferred onto `f17-f04-docstring-sweep` in `~/its-sweep` via `git stash` (push in ~/its → pop in worktree), leaving `~/its`/main clean — which also stops the live daemon from running uncommitted feature code. Leftover after merge (operator cleanup, optional): the stale `~/its-sweep` worktree and its squash-merged local branch (needs `-D`; the guardrail hook blocks force-delete, so left for the operator).

## Operator / follow-on

- **F17 disabled-cycle smoke** (optional live confirmation): flip `safety_reports.intake.polling_enabled` false → confirm no marker refresh → flip back. Covered by `test_poll_once_skips_when_disabled`.
- **F16** (UptimeRobot/Healthchecks heartbeat) — its own PR from `~/its-f16`.
- **`shared/heartbeat.py`** consolidation — existing tech-debt; would dedupe the now-3 copies of the marker/heartbeat helpers.

## Lessons captured to memory

- `exec-host-worktree-daemon-topology` (project) — per-task worktrees + the live launchd intake daemon running the `~/its` working tree (uncommitted edits go live in 60s). The F04 SDK-vs-Live gotcha and the corrected `security -w`-last + double-feed shape are recorded in code comments + commit + this log (repo-recorded; not duplicated to memory).
