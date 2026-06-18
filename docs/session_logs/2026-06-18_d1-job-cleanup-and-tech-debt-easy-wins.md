---
type: session_log
date: 2026-06-18
status: closed
related_prs: [292, 294, 295]
workstream: safety_portal
tags: [session_log, safety_portal, d1-cleanup, prune, purge-job, codeql, tech-debt, doc-closes, live-smoke, clean-slate, worktree, portal-admin, smartsheet-orphan, branch-cleanup]
---

# Session — D1 job cleanup + clean-slate purge + tech-debt easy-wins (later arc, 2026-06-17 evening → 2026-06-18)

Later arc of a long session. The earlier arc (test-artifact cleanup + PDF-naming work, PRs #287-era through #290) is covered in [`2026-06-17_safety-portal-test-artifact-cleanup-and-pdf-naming.md`](2026-06-17_safety-portal-test-artifact-cleanup-and-pdf-naming.md). This log picks up at the D1 job-cleanup brief and runs through the tech-debt easy-wins pass and the operator-run live cleanup that followed.

## PRs landed

### PR #292 — feat(safety-portal): D1 job cleanup — auto-prune inactive jobs + operator purge-job command (merge `22ab1db4`)

**Gap closed:** the daily D1 prune cron (`pruneOldData`, `0 9 * * *`) handled submissions, pdf-cache, and audit rows but never the `jobs` table. Deactivated and test jobs accumulated indefinitely. Additionally, `POST /api/internal/sync` refuses an empty job set, so removing the last `ITS_Active_Jobs` row cannot deactivate a job — it lingers `active=1` in D1.

Three additions:

1. **`pruneOldData` extended** — after the existing prune passes, DELETEs jobs with `active=0` that have no remaining submissions (self-cleaning; a re-add via `/sync` upsert recreates the row). Count threaded into the `scheduled()` cron log entry.

2. **`POST /api/internal/admin/purge-job` (requireAdminToken)** — atomic hard-delete of a given job plus ALL its D1 rows (submissions, `filed_pdfs` cache, `pdf_requests`) in one batch, plus an `audit_log` entry. Idempotent: unknown job → `{found: false}`. Design: D1 stays a transport cache; Box + the week sheet remain the systems of record; send-free; no new imports; no migration needed.

3. **`portal_admin purge-job <JOB-ID>` CLI** — Python thin wrapper over the new endpoint; confirms found/counts before printing the result.

4. **§43 runbook row** added to `safety_reports/README.md` — symptom (test/deactivated jobs accumulate in D1), low-class repair steps (`portal_admin purge-job <JOB-ID>` after verifying Job ID via `portal_admin jobs`), and explicit Tier-3 boundary (do not purge a job with active filings without Seth; purge is irreversible on D1 — Box/Smartsheet are unaffected).

**CodeQL caught 2 real issues in-PR (both fixed at source, neither dismissed):**

- **String-built SQL query** — the cascade DELETEs interpolated a `subSel` const into a template literal → inlined to a full literal SQL string (`job_id` stays bound via `?`). Lesson: never interpolate any variable into a D1 SQL string, even a locally-constructed constant, because CodeQL correctly flags the taint path.
- **Clear-text logging of a sensitive response** — `portal_admin`'s `print` logged `data.get(...)` straight from the admin-API response body (the query taints a response body as possibly-secret) → coerced the counts to plain `int` + dropped the raw `{data}` from the failure message. The other `portal_admin` commands already log only `status` + args, matching the corrected pattern.

Gates:

- pytest: 1835 passed / 44 deselected
- mypy: 0 errors / 202 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS (ci + CodeQL)

PR #292 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-18T02:41:51Z
- mergeCommit: 22ab1db4e973a7e403e9caf01634c65ed94bd4e3
- main-branch CI on merge commit: SUCCESS (ci + CodeQL)

Worker deployed at version `903faeee` after merge.

---

### PR #294 — chore: tech-debt easy-wins pass — 5 code/test fixes + 21 verified-resolved closes (merge `79c96b2b`)

Operator-directed "are there tech-debt items we can cleanly + easily solve right now?" pass. A 20-agent Workflow ran a 7-way parallel assessment of all 122 open `docs/tech_debt.md` items plus an adversarial verification pass on the easy-win candidates.

**Five code and test fixes:**

1. **`scripts/lint_doc_conventions.py`** — added `safety_portal` to `CANONICAL_WORKSTREAMS` (plus the test's expected set). The doctrine_manifest and `doc_conventions.md` already listed `safety_portal`; the omission caused spurious lint warnings on 10+ docs every CI run.

2. **`tests/test_weekly_generate_integration.py`** — added an `autouse` fixture monkeypatching `weekly_generate.WATCHDOG_MARKER_DIR` to a tmp dir. The live integration compile was mtime-touching the real watchdog marker file, causing Check C (staleness floor) and Check I (Friday-crash catch-up) to see a freshly-refreshed marker and fail to detect a stale daemon. The fixture prevents any test run from silently masking those checks.

3. **`safety_reports/publish_daemon.py` `_regenerate_archive`** — replaced the `form_archive_out/` write-into-live-tree pattern with `tempfile.mkdtemp` + `shutil.rmtree` cleanup. The Box upload consumes the in-memory render; writing to the live `~/its` tree was gratuitous and left partial artifacts on failure. Added `import shutil, tempfile`; updated the existing test assertion.

4. **`shared/graph_client.py` `fetch_latest_inbound_timestamp`** — docstring no longer cites the RETIRED watchdog Check F; reworded to "preserved for Email Triage."

5. **`safety_reports/README.md` weekly-send idempotency note** — corrected: the guard keys on `Send Status == SENT` (the authoritative state), not "non-empty Sent At" as the stale note claimed.

**21 tech_debt.md closes** (each with a resolution note):

- Two D1 items closed by PR #292 (inactive-job prune + purge-job command).
- Several "PLANNED not built" items confirmed as actually live: `portal_poll.py`, the intake portal-marker branch, the weekly generate/send rewire, and the D1 dropdown sync.
- `/api/login` disabled-gate confirmed already shipped in `auth.ts`.
- Invariant-2 Layer-5 reword + doctrine-version-lag + ops-stds-enforcer v18 — all current.
- Portal CI job confirmed present; Daily Reports Box-link confirmed.
- M4 bad-HMAC + M5 publish/stamp — already guarded in code.
- `~/its`-stranded already on main; portal-admin Retire-on-retired path confirmed unreachable.
- Half-applied morning publishes resolved by the 2026-06-15 full-archive re-upload.

Gates:

- pytest: 1835 passed / 44 deselected
- mypy: 0 errors / 202 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS (ci + CodeQL)

PR #294 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-18T03:58:24Z
- mergeCommit: 79c96b2b44c8239e82c0f8477835ec22924ed72f
- main-branch CI on merge commit: SUCCESS (ci + CodeQL)

---

### PR #295 — chore: live-cleanup closes — orphan week sheet + duplicate daemon-health entry + merged orphan branches (merge `974b111c`)

Three live-operation closes executed by the operator, then documented as verified-resolved in `docs/tech_debt.md`:

1. **Orphan week sheet `1966431334780804`** ("Bradley 1 — week of 2026-06-06") deleted via `smartsheet_client.delete_sheet`. Verified orphan: zero code references, not the clone template, in a separate workspace from the live filing tree. Name-guarded before deletion.

2. **Duplicate `ITS_Daemon_Health` sheet `3717381690969988`** — live fetch returned 404; the sheet was already gone from a prior restructure. The tech_debt entry was stale; marked resolved with the 404 finding as the evidence.

3. **Orphan remote branches `origin/f02-f22` and `origin/session-log-f02-f22`** deleted via `gh api -X DELETE`. PRs #118 and #119 are state=MERGED; the branches were safe to remove.

tech_debt.md OPEN count: 122 → 98 (across #294 + #295).

PR #295 — four-part verify clean
- state: MERGED
- mergeCommit: 974b111c447db8e87a02808b67c679ba3faa2880
- main-branch CI on merge commit: SUCCESS (test + portal + CodeQL + secrets; docs-only PR)

---

## D1 clean-slate purge (operator-approved, not a PR)

After PR #292 deployed (Worker version `903faeee`), the operator approved "purge all 13" — a clean-slate removal of all D1 test/placeholder jobs accumulated since the mirror stood up.

**Procedure:**

1. Live-smoked `purge-job` against JOB-000015 ("Placeholder test") first: `job=1 submissions=2` — validated the just-deployed endpoint on real data before batch-purging.
2. Purged the remaining 12 jobs via `portal_admin purge-job`. Total removed: 13 jobs, 27 submissions.
3. JOB-000015 re-mirrored into D1 from its `ITS_Active_Jobs` row within the next 60s sync cycle (the sync working as designed — D1 mirrors the Smartsheet source). Operator chose a fully-empty slate → deleted the source row (`smartsheet_client.delete_rows`, sheet `ITS_Active_Jobs` `6223950341164932`, row `8641525072461700`) + re-purged D1.
4. Re-verified `jobs:0` after a full 60s sync cycle: empty source → `/sync` refuses empty → no re-mirror.

**Final state:** ITS_Active_Jobs 0 rows; D1 `jobs` / `submissions` / `filed_pdfs` / `pdf_requests` all 0; `users` = 6 + `audit_log` kept (14 purge-job entries).

**PYTHONPATH gotcha:** `portal_admin` must be invoked with `PYTHONPATH=/Users/sethsmith/its` (i.e., `PYTHONPATH=/Users/sethsmith/its python -m safety_reports.portal_admin ...`); bare `-m safety_reports.portal_admin` without `PYTHONPATH` fails `ModuleNotFoundError`. Recorded for future operator use.

## Decisions made during session

1. **Auto-prune + operator purge command (not manual-only) for D1 job accumulation.**
   - Decision: implement both a cron-driven auto-prune (inactive jobs with no submissions) and an explicit `purge-job` command.
   - Alternative considered: manual-only — document a purge procedure with raw D1 SQL.
   - Rationale: auto-prune handles the steady-state (deactivated jobs drain themselves once their submissions are cleared); the explicit command handles the clean-slate / point-in-time case and gives the operator a safe, idempotent tool that doesn't require raw SQL access to D1. The §43 runbook documents both paths.

2. **Fix CodeQL SQL-taint issue by inlining the full literal (not dismissing).**
   - Decision: inline the `subSel` constant into a full SQL string literal instead of dismissing the CodeQL alert as a false positive.
   - Alternative considered: `// codeql-ignore` suppression comment (the constant is not user-controlled).
   - Rationale: the fix is trivially correct and removes any taint path argument, making the intent unambiguous for future maintainers. Suppressing a fixable real issue conflicts with the repo's CodeQL discipline — the codeql-fp-triager agent exists for genuine FPs, not as a convenience exit.

3. **Fix CodeQL clear-text-log issue by coercing counts to int + dropping raw response body from failure log.**
   - Decision: coerce admin-API response counts to `int` before printing; remove `{data}` from the failure message.
   - Alternative considered: dismiss as a false positive (the response body is ITS-internal, not a credential).
   - Rationale: same rationale as above — the fix is correct and small, and it brings `portal_admin` into line with the existing pattern (other commands log only `status` + args, not raw response dicts). Even if the response body is internal, logging it verbatim is not the intended behavior; the counts are the only useful signal.

4. **Purge all 13 D1 jobs (clean slate) after confirming the live smoke on JOB-000015.**
   - Decision: after live-smoking the purge endpoint on JOB-000015 (real data, `job=1 submissions=2`), purge all 13 jobs and the ITS_Active_Jobs source row to reach a fully-empty state.
   - Alternative considered: keep JOB-000015 as a sentinel / smoke anchor for future tests.
   - Rationale: the operator's explicit goal was a clean slate with no test artifacts in the live mirror. The live smoke on JOB-000015 served its purpose (validates the endpoint); retaining the placeholder after validation creates exactly the accumulation problem the feature was built to prevent. The `portal_poll` / `weekly_generate` full end-to-end path remains testable against any real submission going forward.

5. **Skip 5 flagged tech-debt items (orphan folder, scheduled_send_local WARN, box_smoke_folder_id, duplicate ITS_Errors sheets).**
   - Decision: left OPEN after the adversarial pass flagged them.
   - Rationale (per item):
     - Orphan Box folder JOB-000013: `Folders.delete_folder` is recursive; same-named folders cannot be safely distinguished; deletion could wipe a populated week tree, contradicting a recorded decision.
     - `scheduled_send_local` WARN: the proposed fix would write an `ITS_Errors` row on every `weekly_send_poll` 15-min cycle for every PENDING scheduled row — a spam loop. Safe surfacing requires once-at-config-read-time, a separate change.
     - `box_smoke_folder_id` seed: needs live Box Keychain credentials + creates a folder Claude Code cannot verify without operator-run confirmation.
     - 5 duplicate ITS_Errors sheets: live records likely 404 from the 2026-05-17 restructure; delete-as-written would error on missing sheets, and the tech_debt entry contradicts itself. Needs a fresh live survey before acting.

6. **Operator runs irreversible Smartsheet deletions and `gh api -X DELETE` branch removals directly (not via agent).**
   - Decision: the operator used the `!` session-command prefix (or a direct shell session) for the `delete_sheet` and `gh api -X DELETE` calls, rather than asking the agent to run them.
   - Alternative considered: add a Bash permission rule to allow agent-run irreversible deletes.
   - Rationale: the auto-mode permission classifier correctly gates workflow-derived irreversible targets (Smartsheet sheet deletes) and `gh api -X DELETE` (reads as bypassing the git-guardrails hook). The right resolution is operator-run, not a broader permission grant for a one-off operation. The agent correctly identifies and flags the boundary; the operator makes the call.

## Open items / next session

1. **PR-3 — `shared/heartbeat.py` extraction** (`feat/pr3-heartbeat-extraction`, foundation `546537c`). The `HeartbeatReporter` class is committed; the thin-wrapper rewire of the 4 daemons + watchdog `TRACKED_JOBS` + `tests/test_heartbeat.py` + mandatory live daemon smoke remain. Still unblocked.

2. **`scheduled_send_local` WARN surfacing fix.** Safe approach: once-at-config-read-time, not per polling row. Needs a separate brief.

3. **5 duplicate ITS_Errors sheets** — needs a fresh live survey (`smartsheet_client.get_sheet(id)` on each) before acting; the tech_debt entry describes contradictory states.

4. **Orphan Box folder JOB-000013** — requires a safe-delete approach that verifies the folder is genuinely empty before any recursive delete. Escalate-to-Seth class if the folder contains any week-tree data.

5. **tech_debt OPEN count: 98** (down from 122 entering this session).

6. **ITS_Active_Jobs is empty; D1 is empty.** The mirror is a clean slate. The next real safety-form submission or operator-seeded job will be the first post-cleanup end-to-end test.

## What was NOT touched

- **`~/its-blueprint`** — exec-repo-only session. No doctrine, mission, brief, or reference files modified.
- **Invariant 1 (External Send Gate)** — `purge-job` is send-free; D1 is a transport cache; Box and Smartsheet (systems of record) are not modified by the purge endpoint. `tests/test_capability_gating.py` is unchanged.
- **Invariant 2 (Adversarial Input Handling)** — no external-content processing paths modified.
- **Python daemon code for `portal_poll.py`, `weekly_send.py`, `weekly_send_poll.py`, `weekly_generate.py`** — the tech-debt fixes in PR #294 are confined to lint configuration, test fixtures, `publish_daemon.py` temp-dir cleanup, a docstring, and a README note. No behavioral change to any intake or send path.
- **Worker routing, HMAC, D1 migrations** — PR #292 adds a new endpoint and extends an existing cron handler; no existing routes or migration schema changed.
- **`tests/test_capability_gating.py`** — no new generation or send scripts added; gate is unchanged.

## Cross-references

- Prior arc session log (test-artifact cleanup + PDF-naming): [`2026-06-17_safety-portal-test-artifact-cleanup-and-pdf-naming.md`](2026-06-17_safety-portal-test-artifact-cleanup-and-pdf-naming.md)
- Memory entry `project_safety_portal_state.md` — updated this session: clean-slate purge COMPLETE; D1 jobs/submissions 0; ITS_Active_Jobs 0; Worker `903faeee`.
- `docs/tech_debt.md` — 122 → 98 OPEN items.
- `safety_reports/README.md` — §43 runbook row for `purge-job` (PR #292).
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI on merge commit.
- `docs/operations/worktree_discipline.md` — worktrees `~/its-d1cleanup` and `~/its-techdebt` used and cleaned up.
- FM v11 Invariant 1 (External Send Gate — send path unchanged; purge-job is send-free).
- Op Stds v18 §43 (successor-remediation runbook entry for purge-job in `safety_reports/README.md`).
- Op Stds v18 §30 (SDK-vs-Live discipline; live smoke on JOB-000015 before batch purge).
- `~/its-blueprint/references/claude-code-info-gap.md` §4 — session-log verbatim-quote discipline (the "PR #34 ghost" cure).
