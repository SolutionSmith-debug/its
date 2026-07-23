---
type: report
date: 2026-07-23
status: draft
related_prs: [676, 679, 687]
workstream: null
tags: [standup, finish, run-branch, drill, cutover, rehearsal, seth-owned]
---

# Maintenance-window drill — first live run of `finish`, run-branch mode, and the NONINTERACTIVE orchestration

> **Status: PREPARED PLAN — Seth schedules and attends; nothing here has been executed.**
> Three controls that the Aug-3 production cutover depends on have **never executed against a
> live tenant**: `standup.py finish` (either posture), run-branch mode (#687), and the
> `STANDUP_NONINTERACTIVE` orchestration path under a real stage. This ~20-minute attended
> drill on the mirror exercises all three harmlessly. The typed confirms and the fleet
> reload are operator moments (§44) — a CC session may drive, but Seth types the phrases.

## Pre-reqs (5 min, before the window)

1. `~/its` clean on `origin/main`, pulled to latest (the drill assumes PR #690's fixes are
   merged — the run-branch dirty gate otherwise refuses on the untracked prewipe dump).
2. A per-task worktree with its own venv for the stand-up half
   (`worktree_discipline.md`): `git worktree add -b drill/standup-final-verify
   ../its-drill origin/main && cd ../its-drill && python3 -m venv .venv-wt &&
   .venv-wt/bin/pip install -e '.[dev]'`.
3. **UptimeRobot**: the whole fleet (including the watchdog) is down during the window; the
   external dead-man's switch fires if the watchdog stays silent **> ~35 min**. Either keep
   the window under ~30 min, or pre-set an UptimeRobot maintenance window (the Aug-7
   transport step already uses that pattern). Decide before booting anything out.
4. Dashboard stays up throughout (both daemon-down guards exempt it); its ACT verbs will
   fence while the stand-up marker is fresh — expected, this is #674 working.

## Part A — the drill (~20 min in-window)

| # | Step | What it proves | Expected output |
|---|------|----------------|-----------------|
| 1 | `python3 scripts/migrations/standup.py finish --verify-only` (from `~/its`) | verify-only is safe any time; read-only legs | preconditions pass; heartbeat/error/gate report print; no reload |
| 2 | Boot out the fleet: every loaded `org.solutionsmith.its.*` label except the dashboard (`launchctl list | grep solutionsmith`; `launchctl bootout gui/$UID/<label>` each) | the finish daemons-down precondition has something to refuse on until this is done | `grep` shows only `…its.dashboard` |
| 3 | From `../its-drill`: `.venv-wt/bin/python scripts/migrations/standup.py --dump ~/its/logs/migrations/prewipe_20260723T030026Z --start-at final-verify` | run-branch creation on a clean tree; the NONINTERACTIVE contract on a real orchestrated stage; run-state + streamed `[stage/script]` output + per-run transcript; marker write→clear on completion | master confirm (Seth: `y`); `standup/run-<UTC>` branch created; `final-verify` streams `sheet_ids_regen --check` + `verify_cutover --only config --allow-sandbox`; `[ok] Stand-up complete`; marker file GONE (`ls ~/its/state/standup_in_progress.json` → absent) |
| 4 | Observe the run-branch epilogue | zero-diff completion behavior | 0 checkpoint commits; the branch push succeeds (empty branch); the printed `gh pr create` command will refuse (“no commits”) — **expected** for a no-change re-verify; do not run it |
| 5 | `python3 scripts/migrations/standup.py finish --posture full` (from `~/its`, on main) | the full epilogue: preconditions → state cleanup → typed `--posture full` confirm (Seth) → fleet reload → bounded heartbeat wait → error sweep → gate report → dashboard restart LAST | on the **mirror**, `full` restores the current posture (all send lanes are live here — go-lives 07-16/17/20); heartbeat wait names the 15-min pollers as expected laggards; `[ok] finish clean` or a named laggard list |
| 6 | `python3 -m scripts.verify_cutover --only launchd --allow-sandbox` | independent post-drill fleet check | note: VC-02's dark expectation (`po-send`/`rfq-send` UNLOADED) is the **production cutover** posture — on the mirror with those lanes live this leg FAILS by design; read it, don't chase it |

Capture for the record: the per-run transcript (`standup_<runid>.log` beside the dump),
the `finish` summary block, and the gate report — attach to the session log.

**Cleanup:** delete the empty remote `standup/run-<UTC>` branch (operator — remote branch
deletion is hook-blocked in CC); remove the `../its-drill` worktree; confirm
`launchctl list | grep -c solutionsmith` matches the pre-drill count.

## What this drill does NOT cover (known, deliberate)

- The dark-posture bridge at the real cutover: after a production `finish` (dark), the three
  established send plists (weekly-send / progress-send / subcontract-send) must be loaded
  per-plist before VC-02 passes — see the cutover checklist stand-up callout.
- A real `--resume` mid-run fix-PR merge (needs a real failure; Symptom 1/2 of
  `docs/runbooks/tenant_standup.md` cover it).
- The heartbeat-laggard and CRITICAL-sweep failure paths (finish only *reports* them; the
  drill proves the reporting runs, not the failures).

## Part B (optional, same window) — the §51 archive live-smoke

The archive path's prove-the-control-bites debt (its#462, closed on mocked tests) rides the
same window if Seth wants both: the §30 move smoke
(`pytest -m integration -k move_sheet_to_folder`) then one disposable portal job through
`lifecycle='archived'`, watching the four standing trackers move to Closed Projects. The
prepared plan lives in `docs/session_logs/2026-07-23_archive-path-closure-docs.md` (Brief
B); it is attended-only and independent of Part A — run it after step 5 while the fleet is
back up (the archive move rides `fieldops_sync`, which must be running).
