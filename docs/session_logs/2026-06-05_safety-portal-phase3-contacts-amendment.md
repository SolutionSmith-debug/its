---
type: session_log
date: 2026-06-05
status: closed
related_prs: [162]
workstream: safety_portal
tags: [safety-portal, contacts, active-jobs, migration, email-model, smartsheet, phase3]
---

# Session log — Safety Portal Phase 3: contacts amendment (contact routing columns)

Follow-up to PR #160 (Phase 3 job model). Implemented amendment A/B/C from the
operator's 2026-06-05 brief: added six TEXT contact-routing columns to ITS_Active_Jobs
(`Safety Reports Contact Name`, `CC 1`–`CC 5`), extended `ActiveJob` with the new
fields, and built `_flatten_cc()` for ordered de-duped CC resolution. The column-type
choice (TEXT over CONTACT_LIST) was driven by a live sandbox verification finding:
Smartsheet's MULTI_CONTACT_LIST loses external email addresses on API read-back.
Landed as PR #162.

## Commits / PRs landed

- **PR #162 — feat(safety-portal): Phase 3 contacts amendment** — squash `9e1ff9c` on main.
  Files changed: new migration `scripts/migrations/add_active_jobs_contact_routing_columns.py`;
  modified `shared/active_jobs.py`, `tests/test_active_jobs.py`, `tests/test_intake.py`,
  `docs/runbooks/safety_portal_job_management.md`, `docs/tech_debt.md`. Details per module:

  - **`scripts/migrations/add_active_jobs_contact_routing_columns.py`** (NEW): Live additive
    migration. Adds 6 TEXT columns to sandbox ITS_Active_Jobs: `Safety Reports Contact Name`,
    `CC 1`–`CC 5`. Columns are added one at a time — a batch add collides with Smartsheet's
    trailing system columns and returns a 400. Idempotent (skips existing columns), `--dry-run`
    flag, verify-after read. Applied to sandbox this session.

  - **`shared/active_jobs.py`** (MODIFIED): `ActiveJob` dataclass gains two new fields:
    `safety_reports_contact_name: str` and `cc_emails: tuple[str, ...]`. New helper
    `_flatten_cc()` reads the five CC slot strings from the row, splits on commas, strips
    whitespace, lowercases for de-duplication, preserves original-case first-occurrence order,
    and skips malformed entries with a WARN-level log (soft-fail, no raise). Module remains
    read-only.

  - **`tests/test_active_jobs.py`** (MODIFIED): New coverage for CC parsing (single, multi,
    comma-split-within-slot), case-insensitive de-duplication, malformed-entry skipping, and
    empty-CC behavior.

  - **`tests/test_intake.py`** (MODIFIED): `ActiveJob` fixture helper updated for the new
    fields; existing end-to-end tests adjusted accordingly.

  - **`docs/runbooks/safety_portal_job_management.md`** (MODIFIED): New columns documented;
    full email model recorded — TO = Safety Reports Contact Email; CC = CC 1-5 non-empty slots
    flattened + de-duped; greeting = Safety Reports Contact Name; stakeholder columns are
    reference-only, not recipients.

  - **`docs/tech_debt.md`** (MODIFIED): Accepted-risk entry added: CC/TO recipient addresses
    are operator-entered and not allowlist-validated (ops-stds W1). Allowlist validation of
    outbound recipients is a future hardening item.

## CI runs / four-part verify

PR #162 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-05T14:45:19Z
- mergeCommit: 9e1ff9c05c679fdf87947445d1b921c4172e6fdf
- main CI on merge commit: SUCCESS (ci + Push on main both success)

Per-session local validation gate before merge:

- pytest: green (CC parse / de-dup / malformed-skip coverage + ActiveJob helper updates)
- mypy: 0 errors / 164 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

`ops-stds-enforcer` pre-merge: WARN, no blockers. §3/§14/§30/§42 clean. W1 tech-debt entry
applied (recipient allowlist gap). W3 comment-reference applied. W2: no action taken.

## Key finding — MULTI_CONTACT_LIST loses external emails on Smartsheet API read-back

The amendment brief originally specified CONTACT_LIST columns for the recipient fields.
Before committing to that type, the live sandbox ITS_Active_Jobs sheet was queried directly.

**Finding:** Smartsheet's `MULTI_CONTACT_LIST` column type loses external (non-org-member)
email addresses on API read-back. A cell holding `one@ext.com, two@ext.com` returns
`"One, Two"` (display names only) via both `cell.value` and `objectValue`. The raw email
strings are gone. A `SINGLE_CONTACT_LIST` works for one contact — `value` returns the
email even for externals; `objectValue` returns `{email, name}` — but holds only one
contact per slot.

Because safety-report recipients are arbitrary external client emails (not Smartsheet org
members), CONTACT_LIST of any kind is unreliable as the storage mechanism.

**Resolution:** TEXT columns store the email string verbatim. `_cell` string coercion is
correct and 100% reliable. No contact-aware extraction needed. This is why the migration
adds TEXT, not CONTACT_LIST.

**Also verified:** An in-place TEXT → CONTACT_LIST column-type flip via PUT is allowed
(returns 200). Not needed given the TEXT decision, but confirmed as a future migration
option if the data set shifts to org members.

## Decisions made during session

1. **TEXT over CONTACT_LIST for all six new columns.** Alternative considered: CONTACT_LIST
   (the type explicitly named in the brief). Rejected after live read-back verification:
   MULTI_CONTACT_LIST loses external email addresses on API read-back; SINGLE_CONTACT_LIST
   is limited to one contact per slot. Safety-report recipients are arbitrary externals.
   TEXT stores the email string verbatim and is 100% reliable for this population. Decision
   is documented in the migration script header and in the §43 runbook.

2. **One-at-a-time column add in the migration.** Alternative considered: batch column
   add in a single API call. Rejected: Smartsheet returns a 400 when a batch add collides
   with trailing system columns. The one-at-a-time pattern is the established safe approach
   (same pattern used in PR #160). Documented in the migration script.

3. **Stakeholder is reference-only, not a CC recipient.** Amendment C decision. Alternative
   considered: include Stakeholder Email as an automatic CC. Rejected: stakeholder is a
   company-internal reference contact, not a field-PM-level safety-report addressee.
   Including the stakeholder as an automatic CC would route report traffic outside the
   intended audience without explicit operator intent. Decision is reversible — if the
   operator later determines stakeholder should receive copies, a CC slot can hold that
   value. Documented in the runbook email model section.

4. **CC/TO allowlist gap accepted as tech-debt (W1).** The ops-stds-enforcer flagged that
   CC and TO recipient addresses are operator-entered strings with no allowlist validation
   before they reach `weekly_send`. Alternative considered: block on W1 now and add
   validation before merge. Rejected: the send path (Phase 5) does not yet exist; adding
   a validator before the consumer is built is premature. The gap is tracked in
   `docs/tech_debt.md` as an explicit accepted-risk entry to be closed in Phase 5 scope.

5. **Malformed CC entry: skip + WARN, not raise.** Alternative considered: treat a
   malformed CC slot value as a fatal error, routing the item to Review Queue.
   Rejected: a typo in one CC slot should not prevent delivery to the TO recipient.
   Soft-fail (skip + WARN) preserves the primary send path while making the malformation
   observable in the error log. An empty TO still refuses and flags per the existing model.

## What was NOT touched

- Invariant 1 (External Send Gate) mechanics unchanged. `shared/active_jobs.py` is read-only
  with no send or AI capability.
- `weekly_send.py` and `weekly_generate.py` not touched — CC/TO resolution from the new
  fields is Phase 5 scope.
- The Phase 5 HMAC-verified portal-marker branch in `intake.py` not touched.
- No launchd plists added or modified.
- No doctrine or blueprint files touched.
- `lint_doc_conventions.py` workstream set not updated (pre-existing gap, carried forward).

## Open items handed off

- **Phase 5 — `weekly_send` consumes `cc_emails`.** TO + CC resolution, full-list logging
  at send time. Separate session, brief Part B.

- **Amendment D — intake.py PDF rendering.** The portal sends structured data, not a PDF.
  `intake.py` should render the submission PDF in Python via the Phase 4 renderer. Phase 5
  scope.

- **Operator UI steps (carried from PR #160):**
  - Add the `Job ID` AUTO_NUMBER column in the Smartsheet UI on ITS_Active_Jobs (prefix
    `JOB-`, 4-digit fill, start 1). Steps in `docs/runbooks/safety_portal_job_management.md`.
  - Create the "New Job" Smartsheet form on ITS_Active_Jobs.

- **D1 dropdown sync (A.1.4):** Deferred to the Phase 2 deploy session (portal D1 does
  not yet exist). Carried from PR #160.

- **Fill 6 Address cells in ITS_Active_Jobs** — PM fills manually; carried from the
  2026-06-04 session.

- **Blueprint pushes and Phase 2 deploy:** Carried forward. Still pending CLOUDFLARE_API_TOKEN
  provisioning session.

## Cross-references

- Immediately prior safety_portal session log (Phase 3 job model):
  [`2026-06-05_safety-portal-phase3-job-model.md`](2026-06-05_safety-portal-phase3-job-model.md)
- Prior safety_portal session log (Phase 2 Cloudflare scaffold):
  [`2026-06-04_safety-portal-phase2-cloudflare-scaffold.md`](2026-06-04_safety-portal-phase2-cloudflare-scaffold.md)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1
- Op Stds v16 §30 (SDK-vs-Live; read-only path; live verification drove the TEXT decision)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD; runbook updated)
- `docs/runbooks/safety_portal_job_management.md` — §43 successor-remediation runbook (updated)
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — W1 recipient allowlist gap (new entry)
- `shared/active_jobs.py` — CONTACT_LIST finding is load-bearing context for this module
