# 2026-05-23 — R3 Session 3: safety_reports/weekly_send.py + weekly_send_poll.py (closes R3 cycle)

PR: [#68](https://github.com/SolutionSmith-debug/its/pull/68) — squash-merged at 2026-05-23T02:03:26Z. Merge commit `5959d251cb3043d8d7ea3290351f0a5013ec0a86`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Send half of the External Send Gate two-process model per Foundation Mission v8 Invariant 1. Zero AI capability. **R3 cycle now closes end-to-end**: intake daemon (PR #57/#59/#60) → WPR generation (PR #63 + #65 retry fix) → WPR send (this session). All three Safety Reports scripts operational + live-validated.

## Purpose

Close the R3 cycle by shipping the send-side scripts that consume `WPR_Pending_Review` rows after Teala (or her backup chain) approves them. The polling-daemon trigger model matches the intake side: approval is a dynamic event (Friday afternoon, Saturday morning, Monday morning are all valid times for Teala to approve), so a 15-min polling cadence matches the operational profile better than a static Monday-9-AM cron.

## Pre-flight findings

- HEAD: `347a9c0` (PR #67 Box 1111B absorb) ✓.
- `graph_client.send_mail` signature confirmed: `*, from_mailbox, to: list[str], subject, body, content_type="Text", cc, bcc, attachments`. Returns None on 202 Accepted. Raises GraphError subclass.
- `GraphError` hierarchy: `GraphError → GraphAuthError, GraphPermissionError, GraphNotFoundError, GraphRateLimitError`. Pipeline catches GraphAuthError distinctly (CRITICAL fire) and generic GraphError narrowly (retry path).
- `graph_client.get_client()` does NOT exist (brief presumed it). Token-acquisition probe uses `graph_client._get_token()` instead (private but exposed for env-probe use cases).
- `smartsheet_client.get_row` did NOT exist — added a small wrapper around `client.Sheets.get_sheet` + filter-by-id. ~25 lines.
- `intake_poll.py` heartbeat helpers (`_load_heartbeat_row_state`, `_persist_heartbeat_row_state`, `_invalidate_heartbeat_row_state`, `_resolve_heartbeat_row_id`, `_write_heartbeat`, `_write_heartbeat_row`, `_log_heartbeat_failure`) replicated VERBATIM into `weekly_send_poll.py` per preservation-over-refactor.
- **`WPR_Pending_Review` live schema** (sheet 3096105695793028, 12 columns inspected via SDK):
  - Present: Customer, Job, Week (DATE type, not TEXT), Draft Body, Recipients, Approved for Send, Approved By (CONTACT_LIST), Approved At, Sent At, Send Status (PICKLIST), Late Send, Notes.
  - **MISSING**: `Last Send Error`, `Send Retry Count`. Both required by the brief but not present in the live schema. Per Op Stds v11 §23.3 (sheet-level columns added via UI, not API), graceful-degraded both to bracketed tags in Notes: `[LAST_SEND_ERROR: <text>]` + `[SEND_RETRY_COUNT: N]`. Operator-side action: add the columns via Smartsheet UI later if native column storage is preferred; code will continue to work either way (tag-encoding is the v0.1.0 ship).
  - **`Send Status` picklist values**: `PENDING`, `SENT`, `FAILED`, `HELD`. **Brief said `SEND_FAILED`** but the picklist enforces these specific values; using actual `FAILED`. `HELD` is reserved (unused this PR).

## Code changes

### New files
- `safety_reports/weekly_send.py` — ~480 lines. `send_one_row(row_id) -> SendResult` 7-stage pipeline. CLI entry point for manual rerun.
- `safety_reports/weekly_send_poll.py` — ~470 lines. Polling daemon `poll_once()`. Inline-copy of heartbeat helpers.
- `scripts/launchd/org.solutionsmith.its.weekly-send.plist` — `StartInterval` with `__POLL_INTERVAL_SECONDS__` substitution.
- `scripts/smoke_test_weekly_send.py` — 6-stage env smoke.
- `tests/test_weekly_send.py` — 34 unit tests.
- `tests/test_weekly_send_poll.py` — 18 unit tests.
- `tests/test_weekly_send_integration.py` — 1 gated integration test.

### Modified files
- `shared/smartsheet_client.py` — new `get_row(sheet_id, row_id) -> dict` helper (~25 lines).
- `scripts/watchdog.py` — `TRACKED_JOBS` extended with `safety_weekly_send_poll`; `TRACKED_JOB_WINDOWS` override 30 min (= 2 poll cycles).
- `tests/test_capability_gating.py` — `SEND_SCRIPTS` extended with both new files (forbidden: `anthropic_client`, `anthropic`).
- `CLAUDE.md` — stub/real table updated with both new module rows.
- `safety_reports/README.md` — three-scripts section flipped to SHIPPED + R3 cycle complete.
- `docs/tech_debt.md` — 4 new entries (heartbeat extraction, HTML rendering, attachment generation, automated mailbox cleanup).

## Decisions made during session

- **`get_row` added to `shared/smartsheet_client.py` rather than inlined.** Brief said `smartsheet_client.get_row` (which didn't exist). Two options: inline the SDK call in `weekly_send.py`, or add a small wrapper. Wrapper preserves the typed-exception translation that existing helpers use; 25 lines is a low refactor cost; cleaner than inlining. Future per-row consumers (the eventual `wpr_notify.py` mentioned in safety_reports/README.md) will reuse.
- **Token-acquisition probe via `_get_token()` (private API).** `graph_client.get_client()` doesn't exist; using `_get_token()` is a defensible deviation in operator-tooling-only contexts (smoke scripts + test fixtures). Production code paths still use the public methods (`send_mail`, `list_inbox`, etc.) which call `_get_token` internally.
- **Picklist drift accepted as `FAILED` not `SEND_FAILED`.** Live Smartsheet picklist enforces values; writing `SEND_FAILED` would silently fail or be rejected. The brief's `SEND_FAILED` reference is a brief-vs-live drift; using the actual picklist value is the safe choice. `HELD` is unused this PR but reserved for future operator-driven manual hold.
- **Schema-degradation: Notes-tag encoding for missing columns.** Brief explicitly anticipated this case and prescribed the approach. `[LAST_SEND_ERROR: ...]` and `[SEND_RETRY_COUNT: N]` tags are regex-parseable on read, regex-replaced on write. Brackets in error messages are sanitized to parens to prevent tag-closing collisions.
- **Bracket sanitization needed to escape `[` AND `]` (not just `]`).** First test pass caught the `[` case where an embedded bracket pair in the error message broke the surrounding tag. Both characters now convert to `(` / `)` respectively.
- **Heartbeat helpers replicated VERBATIM rather than extracted.** Op Stds v11 §14 preservation-over-refactor: defer abstraction until ≥4 reuse cases. This is the 2nd consumer (intake_poll + weekly_send_poll); extraction to `shared/heartbeat.py` is the next consolidation PR's job, tracked in tech-debt.
- **The shared heartbeat-row-state file (`~/its/state/heartbeat_row_ids.json`) is intentionally shared** between intake_poll and weekly_send_poll. Both keyed by `daemon_name`; the JSON merge pattern in `_persist_heartbeat_row_state` handles concurrent updates fine since each daemon writes its own key.

## CI runs

- Build #1 (push to `r3-session-3-weekly-send`) — `test` workflow → SUCCESS. Polled to completion before squash-merge.

## Verification

| Stage         | Result                                                                                  |
|---------------|-----------------------------------------------------------------------------------------|
| pytest -q     | **883 passed, 1 skipped, 13 deselected** (+54 from 829 baseline; brief targeted +30-50). |
| mypy .        | **Success: no issues found in 103 source files**.                                       |
| ruff check .  | **All checks passed!**                                                                  |
| plutil -lint  | **OK** on `scripts/launchd/org.solutionsmith.its.weekly-send.plist` (with substitutions resolved). |
| Capability AST| `tests/test_capability_gating.py` passes for both `weekly_send.py` and `weekly_send_poll.py`. |
| Smoke script  | All 6 stages green: kill switch ACTIVE, ITS_Config keys read OK (defaults), Graph token acquired, WPR_Pending_Review reachable, ITS_Daemon_Health reachable, filter on empty list returns empty. |
| CI            | PR #68 build #1 → SUCCESS.                                                              |

### Manual live smoke (sandbox send)

```
$ python -c "<seed + send_one_row + verify + cleanup>"
Seeded row_id=1481960880799620
2026-05-23T01:59:15.325384+00:00  INFO  safety_reports.weekly_send  sent row_id=1481960880799620 project='_manual_smoke_weekly_send' recipients=1 late=True
send_one_row result: status=sent project=_manual_smoke_weekly_send late=True error=None
Row state: Send Status='SENT' Sent At='2026-05-23' Late Send=True
Notes: '[ITS-MANUAL-SMOKE] manual smoke seed sent=2026-05-23T01:59:12+00:00'
Cleaned up row_id=1481960880799620
```

- **Seeded** an approved sandbox row with Recipients=`["seths@evergreenmirror.com"]`, Approved for Send=True, Send Status=PENDING, Week=`1970-01-12` (far-past Monday so Late Send=True triggers, exercising that branch).
- **Invoked** `weekly_send.send_one_row(row_id)` directly (bypassing the poller).
- **Result**: `status="sent"`, `late=True` (expected — ancient week date is past any deadline). Row state in Smartsheet confirmed `Send Status='SENT'`, `Sent At` populated, `Late Send=True`, `Notes` appended with `sent=2026-05-23T01:59:12+00:00`. Graph returned 202 Accepted (no exception raised); message dispatched to the mirror inbox.
- **Cleanup**: row deleted post-verify. The mailbox-side message stays in `seths@evergreenmirror.com` inbox until manual delete (covered by the `automated mailbox cleanup` tech-debt entry).

## Subtleties found mid-implementation

- **`graph_client.get_client()` doesn't exist** — brief presumed it. Replaced with `_get_token()` probe (token acquisition is the same MSAL path send_mail uses). Defensible deviation in operator-tooling contexts.
- **mypy "Source file found twice under different module names"** — `from scripts import watchdog` triggers it because `scripts/__init__.py` is present AND `tests/test_watchdog.py` already inserts `scripts/` into `sys.path`. Worked around by replicating the sys.path-insertion pattern in `tests/test_weekly_send_poll.py::test_watchdog_job_slug_matches_watchdog_tracked_jobs`.
- **Bracket pair sanitization** in `_update_notes_tags` — first test pass caught that `]` alone wasn't enough to prevent tag-collision when error messages contain `[...]` pairs. Both `[` → `(` and `]` → `)` now.
- **`Week` column is DATE type, not TEXT** — Smartsheet returns DATE cells as ISO strings OR date objects depending on SDK version. `_coerce_week_to_date` handles both shapes.
- **`Approved By` is CONTACT_LIST** — returns a contact dict, not a string. Not read by weekly_send today; noted for future use.

## Operator-side actions remaining

1. **Load both R3 launchd plists** on the production MacBook:
   - `scripts/launchd/install.sh load org.solutionsmith.its.weekly-generate.plist` (pending from PR #63).
   - `scripts/launchd/install.sh load org.solutionsmith.its.weekly-send.plist` (this PR).
2. **(Optional) Seed ITS_Config rows**:
   - `safety_reports.weekly_send.from_mailbox` — sending mailbox value (default: mirror `safety@evergreenmirror.com`).
   - `safety_reports.weekly_send.poll_interval_seconds` — override default 900 s.
   - `safety_reports.weekly_send.send_deadline_local` — override default `MON 12:00`.
3. **Verify Graph App Access Policy** covers the sending mailbox for the Entra app (should already be in place from R3 Session 1 + 2).
4. **(Future, optional) Add UI columns** to `WPR_Pending_Review`: `Last Send Error` (TEXT_NUMBER), `Send Retry Count` (TEXT_NUMBER). Code degrades to Notes tags until present; native columns are cleaner.

## What's NOT touched

- `intake.py`, `intake_poll.py`, `weekly_generate.py` — all untouched (preservation-over-refactor; not part of this PR's scope).
- `shared/runner.py` / `shared/heartbeat.py` — defer extraction; tech-debt entry covers the 2nd-consumer trigger now being met.
- HTML email rendering — defer to v0.2.0 after 30-day calibration with Teala.
- Word-doc / PDF attachment generation — defer to Phase 1.4+ extension.
- Automated mailbox cleanup for integration smoke — defer; tech-debt entry tracks.
- `WPR_Pending_Review` schema changes via API — sheet-level columns are added via UI per Op Stds v11 §23.3.

## Baseline state at session close

- `main` at `5959d25` (PR #68 merge commit).
- pytest **883 / 1 / 13**. mypy **0 / 103**. ruff **clean**.
- safety_reports/intake_poll.py daemon: still running, still healthy (untouched by this PR).
- Both R3 launchd plists STILL not installed on production MacBook — operator-side `install.sh load` is the remaining gate. Once installed, the R3 cycle will run autonomously on its launchd cadences (intake polling every 60 s, weekly_generate every Friday 14:00, weekly_send polling every 15 min).
- R3 cycle complete end-to-end.

## Sequencing context

**R3 closes**. Next critical-path is the **Phase 1.4 pre-Customer-1 security hardening cluster** per V&R v7.2:
1. Picklist-hardening across all bounded-enum Smartsheet columns (Op Stds v11 §35).
2. ITS_Trusted_Contacts sheet + header-forgery detection (Op Stds v11 §33, FM v8 Invariant 2 Layer 1).
3. Attachment screening pipeline Layers 1-3 (Op Stds v11 §34, FM v8 Invariant 2 Layer 6).

All three deliverables are already logged in `docs/tech_debt.md` with full scope. They precede Phase 1.5 cutover (Florida → California hardware handover) per V&R v7.2.

Concurrent (lower-priority) follow-on candidates:
- `shared/heartbeat.py` extraction (2nd-consumer trigger now met).
- Box 1111B materialization (no longer blocked on the retry primitive — PR #65 shipped; held only on operator coordination + SDK→REST swap trigger).
- HTML email rendering for weekly_send (30-day calibration).
- The `weekly_summary.py` DEPRECATED stub deletion (follow-on cleanup PR once R3 plists are confirmed installed).

## Lessons captured to memory

- `project_phase1_status.md` updated to reflect R3 close + new next-critical-path pointer (Phase 1.4 security cluster).
- `MEMORY.md` index entry refreshed to match.
- No new feedback/preservation/reference memories surfaced — the brief was clear, decisions were brief-directed, drift cases (picklist, schema) were the kind of routine pre-flight findings that don't generalize to durable lessons.
