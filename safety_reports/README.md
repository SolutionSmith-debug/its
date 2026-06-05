# Safety Reports — Phase 1 Active Build Target

Reference docs: **Safety Reports Mission v5** and **Safety Reports Brief v6** in the
planning project.

> **RETIRED 2026-06-05 — `intake_poll.py` (the safety email-intake poller).** The prose
> below that describes `intake_poll` as a live launchd polling daemon reading the `safety@`
> mailbox is **historical**. The safety intake is superseded by the Safety Portal **PULL**
> model (`portal_poll.py`, PLANNED; `decision_phase5-portal-transport`): the Worker queues
> submissions in D1; `portal_poll` pulls + HMAC-verifies + hands them to `intake.py`. The
> shared Graph plumbing is **preserved** for Email Triage. `intake.py` (the engine) stays.
> `WPR_Pending_Review` is decommissioned-by-doc (still used by the live weekly daemons
> pending the WSR rewire). See `CLAUDE.md` for the current-state table.

## Decision state (as of 2026-05-21)

- **5 resolved (2026-05-13)**: three intake document types (Q1), Outlook inbox addresses (Q3),
  ambiguous-report review surface routes to Teala Paradise (Q7), weekly cadence + gated send
  architecture (Q9), and WPR canonical template resolved-in-principle (Q2 — drafting deferred
  until inspection of mirror templates).
- **4 deferred-then-resolved (2026-05-21)**: see `workstreams/safety-reports/mission.md` in
  the its-blueprint repo for the full resolution. Q4 (job lookup = folder constants in
  `shared/sheet_ids.py`), Q5 (tracking row schema lives on `Daily Reports — Week of <date>`
  sheets in Field Reports tree, 9 columns), Q6 (Box taxonomy = `1111A (Copy for new projects)`
  template), Q8 (recipients in ITS_Config keyed `safety_reports.recipients.<job>` with
  JSON-list values). R3 session 1 (intake.py wiring) is unblocked.

## Three scripts — External Send Gate two-process model

Per Foundation Mission v8 Invariant 1, generation and send live in separate scripts:

- **`intake.py`** — `process_message(message_id)` runs the 12-stage pipeline per inbound
  message at `safety@evergreenmirror.com` (sandbox) / `safety@evergreenrenewables.com`
  (production). Sender allowlist enforced first (defense-in-depth on top of the Entra app's
  Application Access Policy + the operator-curated allowlist row); non-allowlist mail goes
  to Quarantine. Classifies into one of the 5 Daily Reports picklist categories (Daily JHA,
  Tool Box Talk, Equipment Check Sheets, Safe Work Observation, Other), extracts structured
  fields via Anthropic tool-use JSON-mode, files to Box, writes Daily Reports tracking row.
  **No customer-facing send capability** — `shared.graph_client` READ methods only
  (`get_message`, `list_attachments`, `download_attachment`); `send_mail` is AST-forbidden
  via `tests/test_capability_gating.py` + `tests/test_intake_capability_gating.py`.
- **`intake_poll.py`** — launchd-driven polling daemon (PR #59). Lists unread messages from
  the Graph mailbox each cycle, calls `intake.process_message`, calls `graph_client.mark_read`
  on success statuses. Replaces the prior Mail.app rule trigger. Maintains a 1000-entry FIFO
  seen-set at `~/its/state/safety_intake_processed.json` (defense-in-depth idempotency) and
  a heartbeat at `~/its/state/safety_intake_heartbeat.txt`. **Same capability gating as
  intake.py** — added to GATED_SCRIPTS.
- **`weekly_generate.py`** — SHIPPED (R3 Session 2). launchd-scheduled Friday 2:00 PM ET via
  `org.solutionsmith.its.weekly-generate.plist` (`StartCalendarInterval`, Weekday=5, Hour=14).
  Reads the week's Daily Reports + Weekly Rollup rows for each project, drafts a Weekly Project
  Report (WPR) via Anthropic Sonnet 4.6 with the `generate_weekly_project_report` tool schema
  (`schemas/safety_weekly_generate.json`), writes drafts to `WPR_Pending_Review` with
  `Approved for Send=false`. **No send capability** — `graph_client`, `send_mail`, `resend`,
  `smtplib`, `email.mime` AST-forbidden via `tests/test_capability_gating.py`. ZERO_DATA_WEEK
  branch writes placeholder rows when a project had no reports that week (reviewer decides
  whether to send-as-such, hold, or follow up with field PM). Idempotent: existing approved
  rows are skipped; existing unapproved rows have their `Draft Body` / `Recipients` / `Notes`
  replaced but approval columns never touched. Empty reviewer chain → CRITICAL abort, no
  Anthropic spend. Watchdog Check C tracks freshness via `safety_weekly_generate.last_run`
  marker (8-day per-job window).
- **`weekly_send.py`** + **`weekly_send_poll.py`** — SHIPPED (R3 Session 3). Per-event
  handler + polling daemon (default 15-min `StartInterval`, configurable via ITS_Config row
  `safety_reports.weekly_send.poll_interval_seconds`). Trigger model is polling (not static
  Monday cron) because approval is dynamic — Teala can approve Friday afternoon, Saturday
  morning, or Monday morning, and send-latency matters for sponsor experience.
  `weekly_send.send_one_row(row_id)` reads one `WPR_Pending_Review` row, validates state +
  Recipients, sends via `graph_client.send_mail` (content_type=Text for v0.1.0; HTML deferred),
  computes Late Send (informational only — never gates sending), updates row to
  `Send Status=SENT`. **No Anthropic API capability** — `anthropic_client`, `anthropic`
  AST-forbidden via `tests/test_capability_gating.py::SEND_SCRIPTS`. Refusal contract:
  `[GENERATION_FAILED:` tag refuses even when Approved (belt-and-suspenders); empty Recipients
  skips silently (the `[NO_RECIPIENTS]` design hold from weekly_generate). Retry-state
  tag-encoded in Notes column because the live sheet lacks dedicated `Send Retry Count` and
  `Last Send Error` columns — graceful-degrade per Op Stds v11 §23.3.

A separate `wpr_notify.py` runs at Friday 6:00 PM, Saturday 12:00 PM, Sunday 12:00 PM, and
Monday 6:00 AM ET to nag approvers about unapproved WPRs.

## Three intake document types

Field PMs submit three categories of safety-related documents:

1. **Daily safety brief + Daily Job Site Safety Worksheet (JSS).** Most frequent — every active
   workday.
2. **Machine pre-inspections.** Skid steer, lifts, other equipment. Per-machine, per-shift.
3. **Weekly toolbox talks.** Fridays. Documents attendance and topic; one per crew per week.

The intake script first classifies the inbound document by type, then runs the type-specific
extraction prompt. Misclassification routes to `ITS_Review_Queue` for human resolution
(reviewer: Teala Paradise).

## Adversarial Input Handling

Every Anthropic API call processing inbound mail:

- Email content wrapped in `<untrusted_content source="email-body">…</untrusted_content>` via
  `shared.untrusted_content.wrap()`.
- System prompt includes `shared.untrusted_content.system_boilerplate()`.
- Sender allowlist enforced at Mail.app rule level; non-allowlist routes to Quarantine.
  `shared.quarantine.is_allowlisted()` is the helper for any post-rule checks.
- Extraction output runs through `shared.anomaly_logger.check()`; anomalies route to
  `ITS_Review_Queue` with `security_flag=True`.

## Weekly cadence (Q9 resolved)

- **Generate**: Friday 2:00 PM ET. Drafts written to `WPR_Pending_Review` unchecked.
- **Approval deadline**: Friday 6:00 PM ET. Approver checks `Approved for Send`.
- **Notification cadence**: Friday 6:00 PM, Saturday 12:00 PM, Sunday 12:00 PM, Monday 6:00 AM
  ET. Recipients: Jacob Stephens + Teala Paradise (configurable in
  `ITS_Config.notification_recipients`).
- **Send**: Monday 6:00 AM ET. Idempotent — rows with non-empty `Sent At` never re-sent.
- **Late approval**: row approved after Monday 6:00 AM sends next business day with
  `Late Send` flag set; owner notified.
- **Unapproved Monday morning**: row held indefinitely. Never auto-sent unreviewed.

## WPR_Pending_Review sheet columns [DECOMMISSIONED 2026-06-05 — superseded by WSR_human_review]

> Superseded by `WSR_human_review` for the portal pull flow. `weekly_generate`/`weekly_send`
> still read/write WPR (the columns below remain the live schema) until the Phase-5 rewire.

`Customer`, `Job`, `Week`, `Draft Body`, `Recipients`, `Approved for Send` (checkbox),
`Approved By` (contact), `Approved At`, `Sent At`, `Send Status`, `Late Send` (checkbox), `Notes`.

## Status

- **`intake.py`** — wired end-to-end as of 2026-05-21, Graph-trigger refactor 2026-05-22 (PR #59).
  Sender allowlist → quarantine, project resolution, Anthropic classify+extract with
  `<untrusted_content>` tagging + tool-use JSON-mode output, confidence gate → review queue,
  anomaly check (sentinel + model-self-report) → review queue, week-folder resolution, Daily
  Reports row write, Box upload, row update with Box URL. The success watermark is now
  `graph_client.mark_read` (called by `intake_poll`), replacing the prior
  `.eml → .eml.processed` rename. 12-stage pipeline; see the module docstring for the
  per-stage breakdown.
- **`intake_poll.py`** — launchd-driven polling daemon shipped 2026-05-22 (PR #59). One
  poll cycle per launchd interval; fetches unread messages from Graph, calls
  `intake.process_message` per message, marks each as read on non-error statuses. See the
  module docstring for the per-cycle behavior + push-vs-record separation notes.
- **`weekly_generate.py`** — R3 session 2 scope. Not yet started.
- **`weekly_send.py`** — R3 session 3 scope. Not yet started.

`weekly_summary.py` remains as the pre-cascade scaffold and is unused; it will be deleted
when `weekly_generate.py` + `weekly_send.py` land per the two-process model.

## intake configuration surface (ITS_Config workstream `safety_reports`)

| Setting | Default | What it does |
|---|---|---|
| `safety_reports.intake.allowed_senders` | `["seths@evergreenmirror.com"]` | JSON list of allowlisted sender addresses or `@domain.com` patterns. Non-matching senders route to `ITS_Quarantine` without any Anthropic call. |
| `safety_reports.intake.classification_model` | `claude-sonnet-4-6` | Anthropic model ID for the classify+extract tool-use call. |
| `safety_reports.intake.box_filing_enabled` | `true` | When `false`, intake writes the Daily Reports row but skips Box upload and tags Notes / Action Items with `[box_filing_disabled]`. |
| `safety_reports.intake.review_queue_on_low_confidence` | `true` | When `true` and `confidence < confidence_threshold`, the message routes to `ITS_Review_Queue` (Reason=low-confidence-extraction) instead of the Daily Reports row. |
| `safety_reports.intake.confidence_threshold` | `0.75` | Float threshold for the confidence gate. |
| `safety_reports.intake.mailbox` | `safety@evergreenmirror.com` | Microsoft Graph mailbox polled by `intake_poll`. Cutover to production address happens at the Phase 1.5 sandbox-to-production tenant swap. |
| `safety_reports.intake.poll_interval_seconds` | `60` | Integer seconds between poll cycles. Read at install time by `scripts/install_safety_intake_daemon.sh` and substituted into the launchd plist's `StartInterval`. Re-run the installer after changing this row. |
| `safety_reports.intake.polling_enabled` | `true` | Per-workstream kill switch for the polling daemon. `false` short-circuits each cycle. Distinct from the global `system.state` kill switch — use this when you want every OTHER ITS workstream to keep running. |

Classification + pipeline rows are seeded by `scripts/migrations/seed_safety_intake_config.py`.
Polling rows (last three) are seeded by `scripts/migrations/seed_safety_intake_polling_config.py`.
Both are idempotent re-run safe.

## Installing the intake polling daemon

Replaces the prior Mail.app rule trigger (`intake.py` was previously invoked per inbound .eml
by an Apple Mail rule; the cutover landed in PR #59). The new trigger is a launchd-driven
polling daemon reading directly from Microsoft Graph.

```sh
# One-time: seed the three ITS_Config rows (idempotent — safe to re-run).
python3 scripts/migrations/seed_safety_intake_polling_config.py

# Install the launchd agent. Reads poll_interval_seconds from ITS_Config
# at install time and substitutes into the plist. Re-run after changing
# the interval row.
scripts/install_safety_intake_daemon.sh
```

Verify with:

```sh
launchctl list | grep org.solutionsmith.its.safety-intake
ls -lh ~/its/state/safety_intake_heartbeat.txt   # bumped each cycle
tail -f ~/its/logs/launchd/safety_intake_poll.err.log
```

Uninstall:

```sh
scripts/uninstall_safety_intake_daemon.sh
```

The Mail.app rule (if installed from the pre-PR-#59 runbook) should be **deleted post-cutover**
to avoid dual-processing. The .eml hot-folder may also be retired — `intake.py` no longer
reads from it.

## How to smoke-test intake

Post-merge smoke against the sandbox (`evergreenmirror.com`):

1. **Anthropic key**: ensure `ITS_ANTHROPIC_KEY` is in macOS Keychain
   (`security add-generic-password -a "$USER" -s "ITS_ANTHROPIC_KEY" -w`).
2. **Graph credentials**: ensure `ITS_MS_TENANT_ID` / `ITS_MS_CLIENT_ID` /
   `ITS_MS_CLIENT_SECRET` are in Keychain and the app has Mail.ReadWrite on the safety
   mailbox via Application Access Policy.
3. **Send the smoke email**: from `seths@evergreenmirror.com` (or any allowlisted sender)
   to `safety@evergreenmirror.com`. Subject example: `Bradley 1 — Daily JHA — 2026-05-22`.
   Body should mention the project name + a paraphrased summary. Attach a representative PDF.
4. **Wait one poll cycle** (default 60 s).
5. **Observe**: tail `~/its/logs/launchd/safety_intake_poll.err.log` for an `intake SUCCESS`
   INFO line and a `poll cycle: fetched=1 processed=1 marked_read=1 errors=0` summary line.
6. **Verify Smartsheet**: open Bradley 1's current week Daily Reports sheet; a new row
   with your subject's title and a Box URL in Notes / Action Items should be present.
7. **Verify Box**: navigate to
   `ITS DATA / Bradley 1 / (Project # & Name) Field / A. Onsite Reporting & Tracking / A. Safety Plan & Reports / D. JSA's/`.
   The uploaded PDF should be named `<report_date>_<category>_<original-filename>`.
8. **Verify mark_read**: the test message should now be marked as read in the inbox
   (Outlook web client or Outlook desktop).

To exercise the review-queue branch, send a smoke email whose project is ambiguous
(`Bradley 1 vs Bradley 2 …`) and confirm a `PENDING` row appears in
`ITS_Review_Queue` with `Reason=ambiguous-classification`.

## Troubleshooting the polling daemon

| Symptom | Likely cause | Where to look |
|---|---|---|
| Daemon installed but no poll cycles in log | launchd job failed to load | `launchctl print gui/$(id -u)/org.solutionsmith.its.safety-intake` |
| `poll cycle: skipped_disabled` lines in log | `polling_enabled=false` in ITS_Config | ITS_Config row `safety_reports.intake.polling_enabled` |
| Heartbeat file stale (>2 poll intervals old) | Daemon stuck or not running | `~/its/state/safety_intake_heartbeat.txt`; restart with the install script |
| Lock file present but no daemon running | Crash during a previous cycle left a stale lock | Delete `~/its/state/safety_intake.lock` and restart |
| Same message processed twice | Seen-set state was lost or `mark_read` failed | `~/its/state/safety_intake_processed.json` + the stderr log; delete duplicate row in Daily Reports |
| Manual rerun needed for one message | Operator-initiated retry | `python -m safety_reports.intake <message_id>` (CLI wrapper around process_message) |
| `daemon_health_write_failed` entries in ITS_Errors | Heartbeat write to ITS_Daemon_Health failed (PR #59.5) | Check the heartbeat row at `ITS_Daemon_Health`; row may have been deleted or columns renamed. The cache auto-invalidates on 404 — see Operator visibility below |
| Daemon missing from ITS_Daemon_Health | Row not yet provisioned | A missing row now **self-provisions** on the next cycle (A1). If `daemon_health_write_failed` / `self-provision create failed` persists, or a `daemon_health_race_duplicate` WARN appears, follow [`docs/runbooks/daemon_health_self_provision.md`](../docs/runbooks/daemon_health_self_provision.md) |

## Operator visibility — ITS_Daemon_Health (PR #59.5)

Every poll cycle writes a heartbeat row to `ITS_Daemon_Health` (sheet `4529351700729732`,
under `ITS — System / 04 — Daemons`). The row keyed `safety_reports.intake_poll`
carries the canonical operator-facing status. **Self-provision (A1):** if a
daemon's row is absent, the daemon creates its own row (registration columns —
Daemon Name / Workstream / Enabled / Interval Seconds / Source ID) on the next
cycle and the per-cycle columns fill immediately after; no manual seed step is
needed. The create is failure-isolated (heartbeat never blocks the daemon) and
race-safe (a duplicate-on-race adopts the first row and WARNs
`daemon_health_race_duplicate`). See
[`docs/runbooks/daemon_health_self_provision.md`](../docs/runbooks/daemon_health_self_provision.md)
for the Tier-2 remediation. The status columns:

- **Last Heartbeat** — UTC ISO timestamp of the most recent successful poll cycle.
- **Last Cycle Status** — `OK` (errors=0) or `WARN` (errors>0). `ERROR` / `SKIPPED` are
  reserved for future failure modes the daemon doesn't currently reach inline.
- **Last Cycle Items Processed** — message count from the most recent cycle.
- **Total Cycles Today** — lifetime monotonic counter (despite the column title; see
  PR #59.5 ARCH-3 — semantics are lifetime, not daily-reset, to avoid a
  read-before-write round trip per cycle).
- **Last Error Summary** + **Last Error Correlation ID** — only set when status=WARN or
  ERROR. Grep ITS_Errors with the correlation ID for the full traceback.
- **Enabled** — operator-facing filter checkbox. **Read for report filtering only —
  not as a runtime gate.** The daemon's on/off switch is the `polling_enabled`
  row in ITS_Config (PR #59.5 ARCH-1: one canonical runtime gate, one operator
  filter flag, no overlap). Toggling `Enabled` in ITS_Daemon_Health does NOT halt
  the daemon; toggle `polling_enabled` in ITS_Config instead.

Where to look:

- **Heartbeat fresh?** Operator-view report (Smartsheet) filters
  `Last Heartbeat < now - 2*Interval Seconds` → those daemons are stale.
- **Cycle errors?** Filter `Last Cycle Status != OK`. The correlation ID
  jumps from the row to the matching `ITS_Errors` rows.
- **Disable the daemon?** Set `safety_reports.intake.polling_enabled=false` in
  `ITS_Config`. The next poll cycle short-circuits without touching Graph or the
  heartbeat sheet. The previous heartbeat row stays as-is until the daemon resumes.

Cache files (local, under `~/its/state/`):

- `heartbeat_row_ids.json` — `{daemon_name: {row_id, total_cycles}}`. Persists the
  ITS_Daemon_Health row ID across launchd-poll-once process restarts (otherwise each
  cycle would re-resolve via `find_row_by_primary`, adding a Smartsheet read per cycle).
  Also stores the lifetime `total_cycles` counter. The file auto-recovers from a
  404 (row deleted/re-seeded) by invalidating the entry; the next cycle re-resolves.

## Portal pull daemon (`portal_poll.py`) — Phase 5

The Safety Portal pull model (`decision_phase5-portal-transport`). `portal_poll`
drains the Worker's D1 queue: `GET /api/internal/pending` → per-row HMAC verify
(`shared/portal_hmac.py`) → `intake.process_portal_submission` → `POST
/api/internal/mark-filed` (the receipt). It is a generation-side daemon with **zero
external-send capability** (enrolled in `tests/test_capability_gating.py`); the
human-approved send is the separate `weekly_send` process.

> **NOT live-verified.** The end-to-end chain (portal → Worker → poll → intake →
> Box → Smartsheet → WSR → send) is validated in the deploy session, which also
> deploys the Worker, seeds the secrets below, and loads this daemon's launchd job.

### Configuration surface

| Key (ITS_Config workstream `safety_reports`) | Default | Meaning |
|---|---|---|
| `safety_reports.portal_poll.polling_enabled` | `true` | Runtime kill switch. `false` short-circuits each cycle (canonical on/off gate — NOT the ITS_Daemon_Health `Enabled` checkbox, which is report-filter metadata). |
| `safety_reports.portal_poll.poll_interval_seconds` | `60` | launchd cadence (read at install time). |
| `safety_reports.portal.worker_base_url` | — | The Worker origin, e.g. `https://…workers.dev`. **Fail-closed: if unset, the daemon does NOT poll.** |

Keychain entries (mirror the Worker's `PORTAL_INTERNAL_API_TOKEN` + `HMAC_PAYLOAD_SECRET`):

- `ITS_PORTAL_INTERNAL_TOKEN` — the bearer for `/api/internal/*`. **Fail-closed if absent.**
- `ITS_PORTAL_HMAC_SECRET` — the per-row HMAC verify secret. **Fail-closed if absent.**

State files (under `~/its/state/`): `portal_poll_heartbeat.txt`, `portal_poll.lock`,
`portal_poll_seen.json` (the idempotency seen-set — `{uuid: {status, box_link}}`).

### Mark-filed (drain) policy

- `processed` / `already_filed` → mark-filed with the Box link (queue drains).
- `review_queue` (unknown/inactive job, unknown form, malformed payload/date,
  unresolved Box) → **also drained**, because a re-pull can't fix it; the
  **Review Queue entry is the operator's action item** and holds the full payload
  for manual re-filing. _Trade-off: the portal shows "filed" though it went to review._
- `error` (transient Smartsheet/Box auth·rate·5xx) → **NOT drained** → auto-retries
  next cycle.
- **HMAC verify failure** → rejected: anomaly-logged + Review-Queue-flagged
  (`security_flag=True`, CRITICAL) + recorded `rejected` in the seen-set (one-shot,
  no re-spam) + **never filed, never drained** (the row stays in D1 for forensics).

### §43 Successor-Operator remediation runbook

Low-capability-class repairs the trained Successor-Operator may perform (no code,
no secrets/Keychain, no doctrine):

| Symptom | Likely cause | Low-class repair |
|---|---|---|
| `poll cycle: skipped_disabled` in the log | `polling_enabled=false` | Set `safety_reports.portal_poll.polling_enabled=true` in ITS_Config to resume; `false` to pause. |
| `portal_creds_missing` ERROR; daemon not polling | Worker base URL row missing, or the daemon is running before the deploy session seeded secrets | Confirm the `safety_reports.portal.worker_base_url` ITS_Config row is set. If the row exists and it still fails, the Keychain secrets are missing → **escalate to Seth** (secrets/auth = high-class). |
| `portal_pending_fetch_failed` ERROR each cycle | Worker unreachable / network blip | Transient — self-heals when the Worker is reachable. If it persists >1 h, **escalate to Seth**. |
| Submissions stuck in `ITS_Review_Queue` with reason `job_not_found` / `job_inactive` | The job isn't an Active row in `ITS_Active_Jobs` (config lag) | Add/activate the job in `ITS_Active_Jobs`, then **re-file the submission manually** from the Review-Queue payload (the auto-drain means it won't re-pull): `python -m safety_reports.intake` is email-only — re-filing a portal submission is a **developer task → escalate to Seth**. |
| `portal_hmac_failure` CRITICAL | A pulled row's signature didn't verify (tamper, or a secret mismatch between Worker and Mac) | **Escalate to Seth** — this is a security event (secrets/auth = high-class). Do NOT clear it. |
| `daemon_health_write_failed` / daemon missing from `ITS_Daemon_Health` | Heartbeat-row write failed | Same as the intake daemon — see [`docs/runbooks/daemon_health_self_provision.md`](../docs/runbooks/daemon_health_self_provision.md). |

**Escalate-to-Seth boundary (high-capability-class — always escalate):** anything
touching the **HMAC/bearer secrets or the Keychain** (`portal_hmac_failure`,
`portal_creds_missing` when the row exists), any **code change**, or re-filing a
drained submission. The four fixed high-class categories (External Send Gate,
secrets/auth, doctrine, code) always escalate regardless of documentation.
