# Safety Reports — Phase 1 Active Build Target

Reference docs: **Safety Reports Mission v5** and **Safety Reports Brief v6** in the
planning project.

## Decision state (as of 2026-05-21)

- **5 resolved (2026-05-13)**: three intake document types (Q1), Outlook inbox addresses (Q3),
  ambiguous-report review surface routes to Teala Paradise (Q7), weekly cadence + gated send
  architecture (Q9), and WPR canonical template resolved-in-principle (Q2 — drafting deferred
  until inspection of mirror templates).
- **4 deferred-then-resolved (2026-05-21)**: see `ITS_Q4-Q8_Resolution_2026-05-21.docx` in the
  planning project for the full resolution. Q4 (job lookup = folder constants in
  `shared/sheet_ids.py`), Q5 (tracking row schema lives on `Daily Reports — Week of <date>`
  sheets in Field Reports tree, 9 columns), Q6 (Box taxonomy = `1111A (Copy for new projects)`
  template), Q8 (recipients in ITS_Config keyed `safety_reports.recipients.<job>` with
  JSON-list values). R3 session 1 (intake.py wiring) is unblocked.

## Three scripts — External Send Gate two-process model

Per Foundation Mission v6 Invariant 1, generation and send live in separate scripts:

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
- **`weekly_generate.py`** — launchd-scheduled Friday 2:00 PM ET. Reads the week's tracking
  rows, drafts a Weekly Project Report (WPR) per active job, writes drafts to
  `WPR_Pending_Review` Smartsheet with `Approved for Send` unchecked. **No send capability.**
- **`weekly_send.py`** — launchd-scheduled Monday 6:00 AM ET. Reads `WPR_Pending_Review` rows
  where `Approved for Send` is checked and `Sent At` is empty. Sends customer email via Graph
  API. Updates `Sent At` + `Send Status`. Files sent copy in Box. **No Anthropic API capability** —
  only reads already-approved structured data.

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

## WPR_Pending_Review sheet columns

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
