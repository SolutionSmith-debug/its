# Safety Reports — Phase 1 Active Build Target

This workstream is **blocked on 9 owner decisions** documented in the planning project's
`ITS_Safety_Reports_Mission_v3.docx`. Code below is structural skeleton only; do not run
against production data until blockers resolve.

## Two scripts

- `intake.py` — fires per inbound email to the dedicated safety mailbox. Reads the email,
  extracts structured fields, looks up the job in Smartsheet, files in Box, writes tracking row.
- `weekly_summary.py` — launchd-scheduled. Reads the past week's tracking rows, drafts a
  Weekly Project Report (WPR) per active job, queues for human review before customer send.

## Workflow shape (mission v3)

```
intake.py:
  Mail.app rule → hot-folder → script
    → read email
    → Anthropic extract (job number, date, hazards, crew size, flags) + confidence
    → if high confidence: upload to Box, tracking row in Smartsheet
    → if low confidence: ITS_Review_Queue with email + extracted fields

weekly_summary.py:
  launchd (day/time TBD) → script
    → read week's tracking rows grouped by job
    → for each job: read Master WPR template from Box
    → Anthropic populates WPR draft from week's data
    → queue draft for review (SLA end-of-day Friday)
    → on approval (separate script): email customer, file sent copy in Box
```
