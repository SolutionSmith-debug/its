---
type: session_log
date: 2026-06-09
status: closed
related_prs: [247, 248, 252, 253, 254, 255]
workstream: safety_reports
tags: [weekly-send, double-send, audit-h2, audit-m3, audit-m8, append-only, portal-poll, resend-timeout, picklist-regression, incident-report, e2e-validation, forensic-audit, form-editor]
---

# Session log — weekly_send hardening (audit H2/M3/M8) + append-only compile + picklist regression + incident-report E2E (PRs #247–#248, #252–#255)

Afternoon/evening session. A 12-dimension adversarial forensic audit of the full system
(39 agents, skeptic-verified) surfaced two HIGH and several MEDIUM findings. The session
fixed every HIGH/MEDIUM that warranted a code change (H2, M3, M8), landed the fixes across
four PRs (#247, #248, #252, #253), and caught a same-session picklist regression introduced
by #247 (fixed in #253). Two additional chore PRs (#254, #255) published a brand-new
"Incident Report" form definition to the live portal via the Form Editor UI, then validated
the full publish + submission + intake + Box-filing pipeline end-to-end with Playwright.

## PRs landed

### PR #247 — fix(safety-reports): write-ahead SENDING marker closes weekly_send double-send window

Audit finding H2: `weekly_send` sent via Graph then stamped SENT; a failed post-send
Smartsheet write left the row in PENDING with the approval checkbox set. The 15-minute
`weekly_send_poll` poller re-dispatched every cycle because PENDING is not retry-gated
— the row had already been sent once.

Fix: write `Send Status = SENDING` before calling `send_mail` (abort without sending if
that write fails), then flip `SENDING → SENT` after a confirmed send. `SENDING` is
excluded from `weekly_send_poll.DISPATCH_STATUSES`, so a post-send-stamp failure leaves the
row in `SENDING` rather than back in `PENDING`. The fail-safe is "stuck SENDING" (one email
delivered, no re-send) rather than re-dispatch. Watchdog Check N added to emit a read-only
WARN on stuck-SENDING WSR rows. `wsr_review.STATUS_SENDING` constant added; migration
option included.

- pytest: 1654 passed / 44 deselected
- mypy: Success (198 files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #247 — four-part verify clean

---

### PR #248 — fix(safety-reports): append-only weekly compilations — never overwrite Box/WSR/week-sheet records

Operator decision from live usage: a recompile was overwriting the Box packet
(`upload_bytes_or_new_version` re-versioned one file), the WSR row (`upsert_row` updated in
place, clobbering an already-SENT row's record), and the week-sheet Rollup.

Fix: compile operations are now append-only throughout:

- **Box:** each compile produces a distinct timestamped file
  (`upload_bytes` with a `YYYYMMDD-HHMMSS-<corr6>` suffix) rather than re-versioning the
  same filename.
- **WSR:** `wsr_review.add_wsr_row` (always-append) replaces `upsert_row`.
- **Week sheet:** `week_sheet.append_rollup_row` replaces `upsert_rollup_row`'s in-place
  update; new helpers `list_rollup_rows`, `get_rollup_row(latest)`,
  `any_compile_now_requested`, and `clear_compile_now_on_rollups` added.

No-new-docs skip preserved. No column migration.

- pytest: 1660 passed / 44 deselected
- mypy: Success (198 files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #248 — four-part verify clean

---

### PR #252 — fix(safety-reports): portal filing fails LOUD on a stalled puller + resend alert-path timeout

Audit findings M3 + M8. `portal_poll` was writing the watchdog Check-C freshness marker
even on a non-polling cycle (e.g. when `polling_enabled` was false), masking outages —
specifically, the 2026-06-07 Cloudflare-1042 404 storm went undetected by watchdog because
the marker stayed fresh throughout.

Fixes:

- Marker only written on a real poll cycle (not a gated-off no-op).
- CRITICAL fired immediately on missing-creds and `PortalAuthError(401)` — these are
  credential faults that won't self-heal; a single occurrence justifies an operator page.
- A consecutive-transport-failure counter persisted via `state_io`
  (`FETCH_FAIL_STATE_PATH`, threshold 5 consecutive failures ≈ 5 minutes) escalates a
  sustained outage from ERROR to CRITICAL.
- `resend_client._request` gains `REQUEST_TIMEOUT=(10, 30)` (connect/read) and fail-fast
  translation of `requests.RequestException → ResendError` (no silent hang on the alert
  path).

- pytest: 1664 passed / 44 deselected
- mypy: Success (198 files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #252 — four-part verify clean

---

### PR #253 — fix(safety-reports): allow SENDING in the WSR Send Status picklist registry (unbreaks send after #247)

Regression introduced by #247. `shared/picklist_validation.py` REGISTRY reused
`_WPR_SEND_STATUS_VALUES` (`{PENDING, SENT, FAILED, HELD}`) for the WSR sheet. After #247
added the `SENDING` status, `validate_row` (which gates every `update_rows` call) rejected
`'SENDING'` with `PicklistViolationError` → `weekly_send_poll` went DEGRADED and approved
reports could not send. The failure mode was fail-closed (no erroneous email delivered).

Root cause: #247 verified against the live Smartsheet dropdown (which has
`validation: false`) but did not update the code-side validation registry. The unit tests
mocked `update_rows`, so `picklist_validation` never ran — the classic "mocks-pass-but-live-fails"
class (Op Stds v16 §30).

Fix: `_WSR_SEND_STATUS_VALUES = _WPR_SEND_STATUS_VALUES | {"SENDING"}`.

Recovery verified live: `weekly_send_poll` flipped DEGRADED → OK at 21:43 UTC and the
stuck row (`row_id=2024504932106116`) sent.

- pytest: 1665 passed / 44 deselected
- mypy: Success (198 files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #253 — four-part verify clean

---

### PR #254 — chore(safety-portal): publish create: added incident-report-v1 (req 16)

Form definition created via the live Form Editor UI during E2E validation. Sectioned
assessment: Location and Reported By required header fields; Description free-form;
Reporter Sign-Off signature table. Publish pipeline (daemon auto-merge + deploy + archive)
completed in approximately 4 minutes.

PR #254 — four-part verify clean

---

### PR #255 — chore(safety-portal): publish edit: incident-report -> v2 (req 17)

Edit/new-version operation on incident-report-v1 adding a "Witness Name" field. Rendered
live with the new field immediately after deploy.

PR #255 — four-part verify clean

---

## Forensic audit summary

A 12-dimension adversarial workflow (39 agents, skeptic-verified) + live-tenant inspection
of the Safety Portal (Smartsheet architecture, Cloudflare Worker/domain, codebase). Overall
verdict: both system invariants (External Send Gate, Adversarial Input Handling) INTACT;
well-engineered.

Findings actioned this session:

- **H2** — weekly_send double-send window. Fixed in PR #247.
- **M3** — portal_poll freshness marker written on non-polling cycles. Fixed in PR #252.
- **M8** — resend alert-path had no timeout; sustained transport failure not escalated to
  CRITICAL. Fixed in PR #252.

Finding accepted by operator:

- **H1** — publish daemon auto-merge + deploy with branch protection requiring only the
  `test` job (no required PR review, `enforce_admins=false`). Reviewed and **ACCEPTED AS
  BY-DESIGN** (C12=A: fully-automatic publish pipeline, high guard-rails). Not a defect.

Remaining findings deferred to `docs/tech_debt.md` (session-close-maintainer to record):
M1 (submit-overwrite), M2 (static-only capability gate), M4 (bad-HMAC queue wedge), M5
(publish-stamp no state-machine), M6 (publish-daemon no watchdog coverage), M7 (live-tree
destructive git), M9 (CLAUDE.md v16/v18 contradiction).

## Incident Report E2E validation

Full publish + submission + intake + Box-filing pipeline validated end-to-end via Playwright
(logged in as `test.admin` on the live portal):

1. Built a sane incident-report definition in the Form Editor UI (sectioned_assessment:
   Location + Reported By header fields; Description free-form; Reporter Sign-Off signature
   table).
2. Published → publish daemon picked up the branch, ran CI, auto-merged + deployed +
   archived in approximately 4 minutes (#254).
3. Form rendered live; Edit → v2 (+ Witness Name field) → published (#255); new field
   rendered live.
4. Submitted incident-report-v2 on job "Test number two" → `portal_poll` pulled → HMAC
   verified → `intake` rendered the brand-new definition to PDF → filed to Box mirror tree
   → week sheet updated (errors=0, incomplete=0).

The Playwright session surfaced one unresolved UI anomaly: switching from the "Submit a
form" view to the Forms/Accounts admin tab using synthesized click events delivered zero DOM
events to the verified, unobscured tab button on some attempts, while a direct
`element.click()` call succeeded reliably. No overlay, JS error, or re-mount was observed;
the `AdminTabs` `onClick → setTab` handler is correct. Could not be attributed to an
application defect with confidence — possible headless-input interaction or a subtle
un-isolated race. No real-user report exists; flag for follow-up if one surfaces.

## Decisions made during session

1. **Write-ahead SENDING status, not post-send SENT stamp (PR #247).**
   - Decision: mark `Send Status = SENDING` before `send_mail`; flip to `SENT` after
     confirmed delivery.
   - Alternative considered: retry-gate PENDING rows (don't re-dispatch if approval
     checkbox is already consumed, tracking approval state separately).
   - Rationale: write-ahead is the canonical pattern for exactly-once delivery (the
     "write-before-act" principle). A SENDING sentinel gives a human-readable
     fail-safe state (stuck SENDING = one delivery, no re-send), whereas retry-gating
     PENDING adds state logic on top of a fundamentally racy update ordering.

2. **SENDING excluded from DISPATCH_STATUSES, not wrapped in a per-row lock (PR #247).**
   - Decision: SENDING is a terminal-until-corrected status from the poller's perspective;
     `weekly_send_poll` never re-dispatches a SENDING row.
   - Alternative considered: a per-row advisory lock in Smartsheet or state_io to prevent
     concurrent dispatch.
   - Rationale: SENDING is already the desired terminal state after a half-completed send;
     introducing a lock adds complexity for a race that the SENDING marker eliminates
     architecturally. Watchdog Check N provides visibility into stuck rows.

3. **Append-only for all three compile output surfaces (PR #248).**
   - Decision: Box timestamped filenames + `add_wsr_row` + `append_rollup_row` across the
     board.
   - Alternative considered: upsert/overwrite (status quo) with a compile-idempotency guard
     (skip if already compiled and no new docs — which already existed).
   - Rationale: operator stated directly from live usage that overwriting is wrong. A
     compiled + SENT WSR row must not be mutated by a later recompile — that destroys the
     audit record. Append-only is the structurally correct model for a durable record.

4. **Immediate CRITICAL on PortalAuthError(401) / missing-creds, not after N retries
   (PR #252).**
   - Decision: fire CRITICAL on the first occurrence of these fault classes.
   - Alternative considered: apply the 5-failure consecutive counter to 401s as well.
   - Rationale: 401 / missing-creds are credential faults — they will not self-heal between
     polling cycles and every additional attempt is noise. A single occurrence justifies an
     operator page; the consecutive counter applies to transport faults (flaky network)
     where transient recovery is plausible.

5. **H1 accepted as by-design (no code change).**
   - Decision: operator accepted auto-merge + deploy without a required PR review as
     intentional under C12=A.
   - Alternative considered: require PR review on `publish/req-*` branches (restricts the
     fully-automatic publish pipeline, requires operator to approve each publish).
   - Rationale: C12=A was an explicit prior architectural decision
     (`decision_phase2-form-editor`): the Mac daemon is the sole privileged actuator for
     publish; the guard-rails are the CI gate, the Smartsheet-sourced branch name, and the
     locked actuator code — not a human PR reviewer. Adding a required review would
     contradict the accepted C12 resolution.

6. **Picklist REGISTRY updated for SENDING rather than skipping code-side validation
   (PR #253).**
   - Decision: extend `_WSR_SEND_STATUS_VALUES` to include `SENDING`.
   - Alternative considered: set `validation: true` on the WSR sheet's Send Status column
     in Smartsheet (eliminate the code-side registry entirely for WSR).
   - Rationale: the code-side registry is a defense-in-depth layer that catches schema
     drift before it reaches the live sheet. Removing it for WSR would widen the gap
     between code validation and the live picklist. The correct fix is to keep the registry
     accurate, not to remove it.

## Open items / next session

- **Playwright tab-click anomaly:** if a real user reports the admin Forms/Accounts tab
  requiring a second click, instrument the `AdminTabs` `onClick` handler with a console
  event log to distinguish a missed-event vs. a React re-render race.
- **Delete retired `intake_poll` row from `ITS_Daemon_Health`:** row
  `7461022174478212` — retained only while classifier gating a safe delete; operator to
  action manually.
- **Untracked screenshots in `~/its`:** `jha-filled-before-submit.jpeg`,
  `portal-tour/` directory — throwaway artifacts from prior Playwright sessions;
  operator to delete.
- **Deferred audit findings (tech-debt):** M1, M2, M4, M5, M6, M7, M9 — recorded in
  `docs/tech_debt.md` by session-close-maintainer; not actioned this session.
- **CSP enforce flip** (carried from `2026-06-08_admin-dashboard-audit-and-security-hardening.md`):
  still held pending a live signature-capture smoke + zero console-violation confirm.
- **Portal deploy** (carried from prior session): Worker redeployment pending for D3
  timestamp fix, A1 stable UUID, A3 prune cron.
- **Load the compile-now daemon** (carried): watchdog Check C WARNs on the
  `safety_compile_now_poll` marker until loaded.

## What was NOT touched

- **`~/its-blueprint`:** exec-repo-only session. No doctrine, mission, brief, or reference
  files touched.
- **Invariant 1 (External Send Gate):** `weekly_send.py` / `weekly_send_poll.py` changes
  are status-management only (no new send path, no new external transmission capability).
  `tests/test_capability_gating.py` confirms gating unaltered.
- **Invariant 2 (Adversarial Input Handling):** no external-content processing paths
  modified; audit confirmed both invariants INTACT.
- **`intake.py`:** unchanged. Portal E2E used the existing intake pipeline without
  modification.
- **`portal_poll.py` HMAC verify / filing path:** unchanged. PR #252 changes the watchdog
  marker and alert escalation logic only.
- **`weekly_generate.py`:** not touched this session.
- **Evergreen production tenant:** all changes are mirror-only.

## Cross-references

- Prior session log (publish pipeline bugfix chain + WSR datetime, PRs #236, #241–#242,
  #244–#245):
  [`2026-06-09_publish-pipeline-bugfix-chain-and-wsr-datetime.md`](2026-06-09_publish-pipeline-bugfix-chain-and-wsr-datetime.md)
- Prior session log (Phase-2 form manager + publish pipeline, PRs #203–#218):
  [`2026-06-09_safety-portal-phase2-form-manager-publish-pipeline.md`](2026-06-09_safety-portal-phase2-form-manager-publish-pipeline.md)
- `safety_reports/weekly_send.py` — write-ahead SENDING marker
- `safety_reports/weekly_send_poll.py` — DISPATCH_STATUSES update; watchdog Check N
- `safety_reports/wsr_review.py` — `STATUS_SENDING`; `add_wsr_row` (append-only)
- `safety_reports/week_sheet.py` — `append_rollup_row`; `list_rollup_rows`;
  `get_rollup_row`; `any_compile_now_requested`; `clear_compile_now_on_rollups`
- `safety_reports/weekly_generate.py` — consumes `append_rollup_row` + `add_wsr_row`
- `safety_reports/portal_poll.py` — freshness marker gating; CRITICAL escalation paths;
  consecutive-failure counter
- `shared/resend_client.py` — `REQUEST_TIMEOUT`; `RequestException → ResendError`
- `shared/picklist_validation.py` — `_WSR_SEND_STATUS_VALUES`
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — deferred audit findings M1, M2, M4, M5, M6, M7, M9
- `decision_phase2-form-editor` memory entry — C12=A resolution (H1 accepted)
- Op Stds v16 §1 (External Send Gate — no send-path capability changes)
- Op Stds v16 §30 (SDK-vs-live discipline; mocks-pass-but-live-fails class; PR #253 root cause)
