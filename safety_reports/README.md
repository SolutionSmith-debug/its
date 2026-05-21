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
  rule level; non-allowlist mail goes to Quarantine. Classifies into one of the 5 Daily
  Reports picklist categories (Daily JHA, Tool Box Talk, Equipment Check Sheets, Safe Work
  Observation, Other), extracts structured fields via Anthropic tool-use JSON-mode, files
  to Box, writes Daily Reports tracking row. **No send capability** — AST-enforced via
  `tests/test_capability_gating.py` + `tests/test_intake_capability_gating.py`.
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

- **`intake.py`** — wired end-to-end as of 2026-05-21. Sender allowlist → quarantine,
  project resolution, Anthropic classify+extract with `<untrusted_content>` tagging + tool-use
  JSON-mode output, confidence gate → review queue, anomaly check (sentinel + model-self-
  report) → review queue, week-folder resolution, Daily Reports row write, Box upload, row
  update with Box URL, .eml → .eml.processed watermark. 12-stage pipeline; see the
  module docstring for the per-stage breakdown.
- **`weekly_generate.py`** — R3 session 2 scope. Not yet started.
- **`weekly_send.py`** — R3 session 3 scope. Not yet started.

`weekly_summary.py` remains as the pre-cascade scaffold and is unused; it will be deleted
when `weekly_generate.py` + `weekly_send.py` land per the two-process model.

## intake.py configuration surface (ITS_Config workstream `safety_reports`)

| Setting | Default | What it does |
|---|---|---|
| `safety_reports.intake.allowed_senders` | `["seths@evergreenmirror.com"]` | JSON list of allowlisted sender addresses or `@domain.com` patterns. Non-matching senders route to `ITS_Quarantine` without any Anthropic call. |
| `safety_reports.intake.classification_model` | `claude-sonnet-4-6` | Anthropic model ID for the classify+extract tool-use call. |
| `safety_reports.intake.box_filing_enabled` | `true` | When `false`, intake.py writes the Daily Reports row but skips Box upload and tags Notes / Action Items with `[box_filing_disabled]`. |
| `safety_reports.intake.review_queue_on_low_confidence` | `true` | When `true` and `confidence < confidence_threshold`, the message routes to `ITS_Review_Queue` (Reason=low-confidence-extraction) instead of the Daily Reports row. |
| `safety_reports.intake.confidence_threshold` | `0.75` | Float threshold for the confidence gate. |

Seeded by `scripts/migrations/seed_safety_intake_config.py`. Idempotent re-run safe.

## How to smoke-test intake.py

The post-merge smoke is operator-driven against the sandbox (`evergreenmirror.com`):

1. **Mail.app rule**: add a rule to Apple Mail that watches incoming messages to
   `safety@evergreenmirror.com` (or wherever the sandbox intake address routes) and
   runs `python ~/its/safety_reports/intake.py <path-to-eml>` with the dropped message.
   The .eml file should land in a hot-folder the rule watches.
2. **Anthropic key**: ensure `ITS_ANTHROPIC_KEY` is in macOS Keychain
   (`security add-generic-password -a "$USER" -s "ITS_ANTHROPIC_KEY" -w`).
   intake.py was the first production consumer of `shared.anthropic_client`; the key
   may not be seeded if the previous sessions didn't need it.
3. **Send the smoke email**: from `seths@evergreenmirror.com` to the configured intake
   address. Subject example: `Bradley 1 — Daily JHA — 2026-05-21`. Body should mention
   the project name + a paraphrased summary. Attach a representative PDF.
4. **Observe**: tail `~/Library/Logs/its/safety_reports.log` (or wherever `error_log`
   writes locally) for the single INFO line:
   `intake SUCCESS sender='seths@evergreenmirror.com' project='Bradley 1' category='Daily JHA' entry=<row_id> box_urls=N box_errors=M`.
5. **Verify Smartsheet**: open Bradley 1's current week Daily Reports sheet; a new row
   with your subject's title and a Box URL in Notes / Action Items should be present.
6. **Verify Box**: navigate to
   `ITS DATA / Bradley 1 / (Project # & Name) Field / A. Onsite Reporting & Tracking / A. Safety Plan & Reports / D. JSA's/`.
   The uploaded PDF should be named `<report_date>_<category>_<original-filename>`.

To exercise the review-queue branch, send a smoke email whose project is ambiguous
(`Bradley 1 vs Bradley 2 …`) and confirm a `PENDING` row appears in
`ITS_Review_Queue` with `Reason=ambiguous-classification`.
