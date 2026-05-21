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

- **`intake.py`** — fires per inbound email to `safety@evergreenmirror.com` (sandbox) /
  `safety@evergreenrenewables.com` (production). Sender allowlist enforced at the Mail.app
  rule level; non-allowlist mail goes to Quarantine. Classifies one of three intake document
  types, extracts structured fields, looks up the job, files in Box, writes tracking row.
  **No send capability.**
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

## What's blocked

- `intake.py` — needs Q4/Q5/Q6/Q8 from mirror inspection.
- `weekly_generate.py` — needs Q4/Q5/Q6/Q8 + WPR canonical template (Q2 deferred drafting).
- `weekly_send.py` — needs Q4/Q5/Q6/Q8 + `WPR_Pending_Review` sheet provisioned.

Current `intake.py` and `weekly_summary.py` are pre-cascade scaffolds. They get refactored to
the three-script two-process model after sandbox mirror inspection.
