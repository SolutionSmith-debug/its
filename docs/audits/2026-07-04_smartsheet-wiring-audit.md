---
type: audit
date: 2026-07-04
status: active
related_prs: []
workstream: field_ops
tags: [smartsheet, wiring-audit, sor, field-ops, progress-reporting, config, picklist, daemon-health, capacity, section46]
---

# Smartsheet System-of-Record Wiring Audit (2026-07-04)

## Purpose & scope

Task B of the Field-Ops / Progress-Reporting overnight handoff: **prove the Smartsheet
system-of-record is wired correctly and behaves exactly as expected end-to-end.** Method:
fan out the code-side "expected" maps (3 read-only subagents + `brief-validator`), pull the
**live** Smartsheet truth via the repo's own `shared/smartsheet_client` (the same client the
daemons use — more faithful than the MCP), then reconcile. All live reads were read-only;
no writes were made during the audit.

**Bottom line: the SoR is wired correctly and behaves as expected.** Every critical wiring
path — config keys + workstream scoping, picklist REGISTRY three-way parity, sheet topology,
Active-Jobs bridge columns, the six live daemons — is present and correct. The findings below
are **hygiene / observability** items (a silent capacity default, five stale daemon-health
placeholder rows, an interval mismatch), not correctness breaks. Deploy verified live
(migration 0038 applied to remote D1; Worker serving the hours routes; 4 pending hours rows).

Live snapshot: ITS HEAD `71feb62`, blueprint `12024188` (Op Stds v19 + both v19.x riders),
UTC ≈ 2026-07-05T02:56 (Pacific 2026-07-04 ~19:56).

---

## Method / evidence sources

- `brief-validator` — all 12 handoff code-shape claims VERIFIED against live HEAD, zero discrepancies.
- Code-side subagent maps: (a) every `ITS_Config` key read + its Workstream scope + fallback; (b) full 11-daemon launchd inventory + heartbeat self-provision.
- Live Smartsheet pulls (read-only, via `shared/smartsheet_client`): `ITS_Config` full dump (46 rows); `list_columns_with_options` on all gated picklist sheets; `get_sheet` topology resolution of all 9 sheet-id constants; `ITS_Daemon_Health` full rows w/ timestamps; `count_workspace_sheets` + `list_workspace_share_emails` on safety/progress/archive; WSR/WPR column types; Active-Jobs column titles; archive-folder existence.
- `tests/test_picklist_validation.py` + `tests/test_capability_gating.py` + `tests/test_intake_capability_gating.py` — **all pass**.

---

## VERIFIED CLEAN (no action required)

- **Deploy is live (handoff §2.1 DONE).** `/api/internal/fieldops/hours-pending` returns `401 application/json` (the `requireFieldopsToken` gate); an authenticated read returned **4 pending rows** with the mirror-aware schema → migration `0038` (`time_entries.mirrored_at`) is applied to remote D1 and the Worker is deployed. (The program-file note "live tree still at cb58ca8, held" is stale.)
- **B1 critical wiring present + correctly scoped:** `progress_reports.intake_enabled = true` under `Workstream=safety_reports` (intake's own workstream — the documented footgun, correctly seeded); `safety_reports.portal.worker_base_url = https://safety.evergreenmirror.com` under **both** `safety_reports` **and** `progress_reports` (the rollup dup, fixing the silent skip); `field_ops.fieldops_sync.sync_enabled = true` under `field_ops`.
- **B2 picklist three-way parity CLEAN.** `review_queue.VALID_WORKSTREAMS` == `picklist_validation._WORKSTREAM_VALUES_GLOBAL` (identical 7-value sets, both include `progress_reports`); live `ITS_Review_Queue.Workstream` includes `progress_reports`. `fieldops_sync._route_to_review` writes `workstream="progress_reports"` (not `field_ops`) → no latent violation. WSR `Workstream=['safety']`, WPR `Workstream=['progress']`, both `Send Status` include `SENDING`, both Active-Jobs `Active=['Active','Inactive','Archived']`. Parity test PASSES.
- **B3 topology CLEAN.** All 9 sheet-id constants resolve to live sheets of the expected name/schema (ITS_Config, ITS_Errors, ITS_Quarantine, ITS_Review_Queue, ITS_Daemon_Health, ITS_Active_Jobs, WSR_human_review, WPR_human_review, ITS_Active_Jobs_Progress). **WSR/WPR date columns are live `DATE`** (Week Of / Approved At / Sent At) — the ABSTRACT_DATETIME errorCode-1142 latent bug is NOT present in the live sheets (it lives only in the fresh-create builder path, masked by idempotency; tracked tech-debt). Archive folder `Closed Projects` (1034553964947332) + progress `00_Progress_Reporting` folder exist.
- **B4 Active-Jobs CLEAN.** Both `ITS_Active_Jobs` and `ITS_Active_Jobs_Progress` carry the `Portal Job Key` TEXT bridge column + their respective contact columns (Safety vs Progress Reports Contact Email/Name) + CC 1–5 + Active lifecycle. `active_jobs.get_job` OR-matches Job ID then Portal Job Key (verified).
- **B5 (the six live self-reporters) HEALTHY.** `portal_poll`, `weekly_send_poll`, `compile_now_poll`, `progress_send_poll`, `publish_daemon`, `field_ops.fieldops_sync` all have fresh heartbeats (~02:5x UTC) and `Last Cycle Status=OK`. Transient Smartsheet HTTP-500s (code 4000) and a portal circuit-open blip were observed and self-healed (per-job fenced) — no chronic ERROR/STALE.
- **B6 §46 CLEAN.** Safety, progress, and archive workspaces are each shared to `seths@evergreenmirror.com` (non-empty approver set → sends approvable; matches the resolved "seths@ only suffices" decision). Capacity headroom vast: safety workspace 11 sheets, progress 8.

---

## MECHANICAL findings (deterministic code-vs-live mismatches)

### M-1 · MEDIUM · `smartsheet.sheet_count_ceiling` / `_margin` absent from live ITS_Config
- **Code:** `shared/sheet_capacity.py:68` reads `smartsheet.sheet_count_ceiling` (default `1500`) and `smartsheet.sheet_count_margin` (default `50`) under `Workstream=global`, **silent fallback** (no WARN). Consumed by `check_create_headroom`, called live from `safety_reports/week_sheet.py:321` and `progress_reports/hours_log.py:173`.
- **Live:** `ITS_Config` (46 rows) has **neither** row.
- **Impact:** the capacity tripwire runs on a **guessed** 1500/50 ceiling, not the real Smartsheet plan cap, and does so silently (forensic class #7). Harmless today (11/8 sheets ≪ 1450) but uncalibrated — this is the ROADMAP Track-3 "unverified quota" open item.
- **Fix:** operator confirms the Smartsheet plan tier and sets `smartsheet.sheet_count_ceiling` (+ optionally `_margin`) under `Workstream=global`.

### M-2 · MEDIUM · ITS_Daemon_Health carries 5 stale/legacy placeholder rows
- **Live:** 11 rows, of which **5 never update** — `safety_reports.intake_poll` (Last Heartbeat **2026-06-05**, daemon **DELETED 2026-07-03**, `Enabled=True`, misleading); `safety_reports.weekly_generate` (`NEVER_RAN`, Notes cite the decommissioned `WPR_Pending_Review`); `safety_reports.weekly_send` (`NEVER_RAN`, Notes cite "Resend" for customer send — wrong transport — + decommissioned sheet); `watchdog` (`NEVER_RAN` placeholder); `shared.picklist_sync` (`NEVER_RAN` placeholder).
- **Code:** only **6** daemons instantiate `HeartbeatReporter` (portal_poll, weekly_send_poll, compile_now_poll, progress_send_poll, publish_daemon, fieldops_sync). The 5 stale rows were hand-seeded in the daemon-health rollout (PRs #45–51) with a "retrofit post-cascade" intent never carried out.
- **Impact:** pollutes the canonical operator-visibility surface; the `intake_poll` row (`Enabled=True`, a month stale, deleted daemon) is actively misleading and could mask a real staleness signal.
- **Fix:** delete the `intake_poll`, `weekly_generate`, `weekly_send` rows (superseded / deleted daemons; name-guarded `delete_rows`). For `watchdog` + `shared.picklist_sync`: either delete (they don't self-report by design — watchdog is externally monitored via UptimeRobot) or finally wire the deferred `HeartbeatReporter` retrofit. Recommend delete + a note that watchdog/picklist-sync/weekly-generate/progress-generate/picklist-audit are intentionally non-self-reporting.

### M-3 · LOW · fieldops_sync heartbeat interval mismatch (300 vs 90)
- **Code:** `field_ops/fieldops_sync.py:104` `SYNC_INTERVAL_SECONDS = 300` (registered in the health row, live shows `Interval Seconds=300`).
- **launchd:** `scripts/launchd/install.sh:79` fieldops-sync `StartInterval` default **90s** (live row shows 3218 cycles today, consistent with ~90s, not ~300s).
- **Impact:** the health row advertises a 3.3× too-lax cadence; any staleness threshold derived from it is wrong. Cosmetic today (watchdog Check C not yet registered for the fieldops marker — see M-5), but incorrect.
- **Fix:** reconcile the two — set `SYNC_INTERVAL_SECONDS = 90` to match `install.sh` (recommended), or bump the plist to 300.

### M-4 · LOW · ITS_Errors.Workstream picklist options stale (inert)
- **Live:** `ITS_Errors.Workstream = [ai_employee, email_triage, global, po_materials, safety_reports, subcontracts]` — missing `progress_reports` and `field_ops`.
- **Code:** `picklist_validation.REGISTRY` gates `SHEET_ERRORS` and `_WORKSTREAM_VALUES_GLOBAL` includes `progress_reports`.
- **Impact:** **INERT today** — `error_log` writes no `Workstream` cell (verified 2026-07-03), so the gate never fires on this column. Latent only if `error_log` ever starts setting `Workstream`.
- **Fix:** low priority — add `progress_reports` (+`field_ops` if ever used) to the live `ITS_Errors.Workstream` options, or document the inert-by-omission contract next to the REGISTRY entry.

---

## SEMANTIC findings (design / expectation drift)

### S-1 · MEDIUM (systemic) · silent config-default resolution — forensic class #7 / issue #336
~40 `ITS_Config` reads fall back to a hardcoded default on a missing/blank row with **no loud WARN + resolved-source line** (deliberate fail-open, but violates the "observable config resolution" standard). The highest-value are the two cross-workstream footguns — `progress_reports.intake_enabled` (read under `safety_reports`) and `safety_reports.portal.worker_base_url` (must exist under **both** workstreams): both are **currently seeded correctly** (no active break), but a re-seed under the wrong workstream would silently disable intake / the progress rollup with no signal. **Fix:** the tracked `REQUIRED_CONFIG` startup-logging pass (issue #336) — log each resolved setting + source at startup, WARN-loud on a missing declared key. Reaffirm priority; not new.

### S-2 · LOW · daemon-health "one row per daemon" expectation is wrong
The handoff (and the intuitive operator model) expects "one ITS_Daemon_Health row per loaded daemon (all 11), recent Last Cycle At." Reality: **only 6 daemons self-provision**; the other 5 loaded daemons (weekly-generate, progress-generate, picklist-sync, picklist-audit, watchdog) **never write a row by design**. So the 11 rows present are 6 live + 5 legacy (M-2), and two loaded daemons (progress-generate, picklist-audit) have **no row at all**. **Fix:** correct the operator-visibility expectation in the daemon-health schema doc / ROADMAP; decide whether watchdog/picklist-sync *should* self-report (retrofit) or are intentionally externally monitored.

### S-3 · LOW · RESOLVED — `sheet_ids.py:55` comment is wrong (its#462 target confirmed)
The `sheet_ids.py:55` comment claims `FOLDER_ARCHIVE_CLOSED_PROJECTS` (1034553964947332, `Closed Projects`) is in `WORKSPACE_SAFETY_PORTAL`. **Live check refutes it:** the folder is the sole top-level folder of `WORKSPACE_ARCHIVE` (5528280611743620) — exactly as the handoff describes. So the **code comment is the bug** (a documentation drift), and its#462's target is unambiguous: cross-workspace move from the progress workspace → Archive/Closed Projects. **Fix:** correct the `sheet_ids.py:55` comment (folded into the its#462 PR).

---

## Reconciliation summary

| Check | Verdict | Findings |
|-------|---------|----------|
| B1 config completeness + workstream scoping | CLEAN (critical) | M-1 (capacity ceiling absent), S-1 (silent defaults) |
| B2 picklist REGISTRY three-way parity | CLEAN | M-4 (ITS_Errors options stale, inert) |
| B3 workspace/folder/sheet topology + DATE | CLEAN | S-3 (#462 folder workspace) |
| B4 Active-Jobs + job-tracker pivot | CLEAN | — |
| B5 daemon health surface | 6 live healthy | M-2 (5 stale rows), M-3 (interval), S-2 (expectation) |
| B6 capacity guards + §46 | CLEAN | M-1 (ceiling absent) |

**No correctness breaks. Task A (activate Hours Log) is unblocked** — the hours path (config keys, `hours_log.py` row-cap guard default 15000, Active-Jobs-Progress + Portal Job Key, capacity guard) is verified; the only caveat is M-1 (capacity runs on the default ceiling, harmless at current volume).

---

## Appendix — Task A: Hours Log live activation smoke (GREEN, 2026-07-04)

The mandatory live smoke for the P7 Slice 1 Hours Log (mocks pass but only live catches a real
SDK/gate rejection). Run **surgically** — called `fieldops_sync._mirror_hours_pass(base, bearer)`
directly (exactly what the daemon calls) rather than flipping the live `hours_enabled` flag, so
the production-mirror daemon stayed **dark** (verified: 0 `hours_enabled` config rows after) and
there was no lock contention. Input: the 4 real pending `time_entries` for JOB-000018.

- **Core mirror — GREEN.** `{mirrored:4, reviewed:0, errors:0}`; hours-pending → 0 (mark-mirrored committed the D1 watermark). Created `Portal create test 2 — Hours Log` (sheet 7906994588438404) in the progress workspace's per-job folder with 4 Active rows — **Personnel = display names** ("Tool bitch"/"test admin"/"test sub"/"Test mini sub", personnel.name, never usernames), Hours 9/5/8/5, Work Date populated, Status=Active, Entry UUIDs matching.
- **Idempotent re-mirror (crash-safe) — GREEN.** Re-run → `{mirrored:0}`, sheet still exactly 4 rows, all UUIDs unique — no duplicates (find-or-create by Entry UUID).
- **Row-cap WARN (§51 A5 watchdog) — GREEN.** Temporarily lowered `progress_reports.hours_log.row_cap_warn_threshold` to 3 → `check_row_cap` fired the WARN ("has 4 rows, at/over threshold 3 … NEVER delete rows") + wrote a Review-Queue period-split row (`Reason=policy-edge`, `Workstream=progress_reports`, "nearing the Smartsheet row cap (4/3)"). Cleaned up (signature-guarded).
- **Amend-supersede — NOT live-exercised** (no amend in the pending batch); unit-test-covered, operator-optional live sub-smoke.
- **Cleanup:** all smoke mutations removed (RQ smoke row, temp threshold config row, ITS_Errors WARN row); the `… — Hours Log` sheet + its 4 mirrored rows were **left** (the correct, consistent artifact: D1 `mirrored_at` set ↔ sheet exists). Daemon left dark.

**Verdict: the P7 Hours Log up-sync works end-to-end against live Smartsheet + the live Worker.**
Go-live = operator flips `field_ops.fieldops_sync.hours_enabled=true` (Workstream=field_ops) **after**
its#462 archive-on-closure lands (the §2.3 gate) — left to the operator per the deploy punch-list.
