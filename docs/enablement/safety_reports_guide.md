---
type: operations
date: 2026-07-10
status: active
related_prs: []
workstream: safety_reports
tags: [enablement, a8, safety-reports, portal, weekly-packet, review, send-gate]
---

# Enablement — Safety Reports: submit → review → weekly packet · Op Stds §6/A8

**Audience:** the office reviewer who approves the weekly safety emails, and anyone who wants to
understand where a field safety report goes after "Submit." No code knowledge assumed. This is the
companion to the field-facing *Field-Ops daily report* guide (the submit side) and the
successor-operator runbooks in [`../runbooks/`](../runbooks/) (the fix-it side).

## The whole flow in one sentence

A field PM **submits** a safety report in the portal → ITS **files** it as a PDF in Box → every
Friday ITS **compiles** each job's week into one packet and stages it for you → you **review and
approve** → ITS **emails** the approved packet to the customer. Nothing is emailed until you
approve it.

## Step 1 — The field submits a report

A field PM opens the Safety Portal, picks the job, fills in the safety form, and attaches the
required job-site **photo**. When they hit **Submit**, the report is signed and queued in the
portal. That's the whole field-side job — they don't email anything, and they don't touch
Smartsheet or Box.

## Step 2 — ITS files it (automatically, within a minute)

The **portal intake** worker pulls the new submission over a secure channel and, for each one:

- **verifies it's genuine** (a cryptographic check that it really came from the portal),
- **screens the photo** — checks it's a real image, strips its hidden metadata, and (optionally)
  virus-scans it; a photo that fails is refused and flagged, never filed,
- **renders the report to a branded PDF**,
- **files that PDF in Box**, in the right job's week folder,
- and **acknowledges** the submission so it's never processed twice.

If anything looks off — a duplicate, a bad photo, a filing error — the item is flagged to the
**ITS_Review_Queue** instead of being filed silently. By the end of this step, a clean PDF of the
report is sitting in Box under its job and week.

## Step 3 — Friday: ITS compiles the week

Once a week (**Friday afternoon**), the **safety compile** worker runs. For each **active** job it
takes that job's **Saturday-through-Friday** week of filed reports, merges them into **one weekly
packet PDF**, files the packet in Box, and writes a **review row** into the
**`WSR_human_review`** Smartsheet sheet:

- the **Email Body** is pre-filled with a standard cover note (yours to edit),
- the **Send Status** is set to **PENDING**,
- and the compiled packet is attached.

No email exists yet. An empty week still gets a row (so you can see it ran); a week that's already
been compiled and has nothing new is skipped.

> **Need it sooner than Friday?** Check the **Compile Now** box on the job's week and the
> "compile now" worker will build the packet on the spot instead of waiting.

## Step 4 — You review and approve

This is the human-in-the-loop step and the only thing standing between a report and the customer's
inbox. Open the **`WSR_human_review`** sheet and, for each PENDING row:

1. **Read the packet** (the attached PDF) and the **Email Body**. Edit the body if you want — what
   you leave there is exactly what the customer receives.
2. **Approve it** by checking one of:
   - **Send Now** — send on the next cycle (within ~15 minutes), or
   - **Approve for Scheduled Send** — hold and send in the Monday-morning window.
3. ITS records **who** approved and **when**.

If a row shouldn't go out, just leave it unchecked — it stays PENDING and nothing happens.

## Step 5 — ITS sends the approved packet

The **safety send** worker (a *separate* program with no AI in it) picks up each approved row and:

- **looks up the recipients fresh** from the job's row in `ITS_Active_Jobs` — the email goes
  **TO** the job's Safety Reports contact and **CC** its listed CCs (the recipients are read at
  send time, not from the review row, so a contact change always takes effect),
- **attaches the compiled Box packet**,
- **sends** the email, and marks the row **SENT** with the timestamp.

If the recipient is missing or the packet can't be found, the row is **HELD** (not sent, flagged
for you to fix) rather than sent to the wrong place or sent blank. A temporary email hiccup is
**retried** a few times before it gives up and raises a critical alert.

## The safety net

- **Nothing sends without your check.** The compile worker literally cannot send email; the send
  worker literally cannot compile or think. Approval is the only bridge between them.
- **Recipients are resolved at send time**, so fixing a contact in the job record fixes every
  future send — you never chase stale addresses on old review rows.
- **A very large packet is handled or held, never silently dropped:** ITS switches to a
  large-attachment send path automatically, and an implausibly huge packet is HELD for you with a
  clear status.

## Where the records live

| Thing | Where |
|---|---|
| The individual report PDFs | **Box**, under each job's week folder |
| The compiled weekly packet | **Box**, in the job's `ITS`-prefixed week folder |
| The review + approval row | the **`WSR_human_review`** Smartsheet sheet |
| Who to send to (contacts + CCs) | the **`ITS_Active_Jobs`** Smartsheet sheet |
| What actually got sent | **Outlook / Microsoft 365** sent items |
| Anything flagged or uncertain | the **`ITS_Review_Queue`** sheet |

## If something looks stuck

- **A packet is PENDING and you didn't approve it** — that's correct; it's waiting for you.
- **You approved it but Send Status is HELD** — the recipient or the PDF is missing; fix the job's
  contact in `ITS_Active_Jobs` (or check the packet) and it will send on the next cycle. The
  runbook `docs/runbooks/safety_weekly_send.md` has the step-by-step.
- **A photo was refused** — look in `ITS_Review_Queue` for the flagged item; the runbook
  `docs/runbooks/safety_photo_path.md` covers it.
- **Anything you're unsure about** goes to Seth — sending, credentials, and policy are always his.

## Owner

`@solutionsmith`. Part of the §6 / A8 documentation program. This in-repo version is the source of
truth for its content; the polished distributable PDF is rendered from it.
