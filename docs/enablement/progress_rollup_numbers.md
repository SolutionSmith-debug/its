---
type: operations
date: 2026-06-30
status: active
related_prs: []
workstream: progress_reports
tags: [enablement, a8, p6, progress-reporting, rollup, field-ops]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest once that artifact
exists in this exec repo (see docs/tech_debt.md "§6a enablement-doc DoD owed"; same pending
registration note as docs/enablement/portal_job_creation.md). Do not fabricate a registration. -->

# Guide — The weekly progress rollup numbers (P6)

**Audience:** the Evergreen office PM and the stakeholders who read a weekly **progress** packet.
No code knowledge required. This explains the **rollup-numbers page** that now appears in every
compiled progress packet — what each number means and where it comes from.

## Where it is

Open a compiled weekly **progress** packet PDF. Page 1 is the cover; **page 2 is the rollup
numbers page** ("Weekly Progress Rollup"); the contents index and the individual daily reports
follow. It is one page inside the same one-attachment, one-approval packet — not a separate file.

## What each number means

| Number | What it tells you |
|---|---|
| **Labor hours** | Total crew hours logged against the job for the Saturday→Friday week. Corrected entries are collapsed to their latest value (an edited timesheet counts once, at its corrected number — never double-counted). |
| **Equipment on site** | The distinct pieces of equipment that reported a location on this job during the week (name + type). A machine that reported many times shows once. |
| **Open tasks** | How many assigned tasks are **not yet done** for this job right now. This is a current snapshot, not a "closed this week" count — the system deliberately does not claim a completion number it cannot prove. |
| **Materials** | A placeholder — the page prints **"Materials reporting is not yet included in this packet."** Materials tracking arrives in a later phase (M2); until then this line is intentionally a placeholder. |

There is **no "percent complete"** on this page. A single overall progress-percent is a guess, not
a measurement, so it was deliberately left off — the numbers above are things the crew actually
recorded.

## Where the numbers come from (important)

The rollup reads the **structured field-ops data** — the time, equipment, and task entries crews
log in the portal — **NOT** the free-text of the Daily Field Report. That means:

- If crews are logging time / equipment / tasks in the portal, the numbers populate automatically.
- If they are **not** yet using those surfaces for a job, the page reads **"No field-ops activity
  recorded for this week."** That is honest and expected, not an error — the daily-report
  narrative is still in the packet; only the aggregate numbers need the structured entries.
- **Costs are off** for now (no labor-cost or materials-value columns) until a later cost-tracking
  phase turns them on.

## One known limitation — late-synced equipment

**Equipment** is counted by **when its location reached the server**, while **labor hours** are
counted by **when the work actually happened** (the crew-reported start time). For a crew with a
live connection these agree. But if a crew logs equipment locations **offline** (e.g. a remote site
with no signal) and the device syncs a day or more later, that equipment can land in the **week it
synced** rather than the week it was on site — while the same crew's labor hours still land in the
correct week. So on rare occasions the two numbers can disagree about which week a crew's activity
belongs to. This is a deliberate tradeoff (server-receipt time can't be shifted by a wrong
field-set clock); flag it if an offline-heavy crew's equipment count ever looks off.

If a packet is missing its numbers page entirely, that is a wiring/health matter for the operator
— see the successor-remediation runbook
[`docs/runbooks/progress_weekly_generate.md`](../runbooks/progress_weekly_generate.md) (Fault D).
