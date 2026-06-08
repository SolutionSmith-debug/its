---
type: operations
date: 2026-06-08
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, safety-portal, admin, auth, tier-2, tier-3, phase-1]
---

# Runbook — Safety Portal admin dashboard (account management + lockout recovery) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry. The §42 code-reader rationale lives in
`safety_portal/worker/index.ts` (the `/api/admin/*` routes + `requireRole` + the
last-admin guard) and `safety_portal/migrations/0007_add_user_role_and_audit_log.sql`.
Companion: [safety_portal_job_management.md](safety_portal_job_management.md).

## What this is

The two admins (CEO + head PM) sign into the portal and see a tabbed dashboard:
**Submit a form** and **Accounts**. On the Accounts tab they create / edit-login /
change-role / delete any account, including each other's. Field PMs (role
`submitter`) never see the tabs and are blocked server-side from every admin route.

This is **normal product use by the admins**, not operator remediation. The role is
re-checked from the database on every request, so a role change takes effect
immediately — no re-login needed (except when you edit your **own** login, which
signs you out so you log back in with the new credentials).

## Symptom (what the Successor-Operator actually observes)

The observable starting conditions that bring you to this runbook:

- **"I log in but there are no Accounts / Submit tabs."** — that account's role is
  `submitter`, not `admin` (the tabs render only for admins). Expected for field PMs;
  a problem only if it's one of the two people who are supposed to be admins.
- **"An admin can't log in at all"** — wrong password, or the account was disabled.
- **"Nobody can get into the Accounts tab / we have no admins"** — admin lockout (both
  admins demoted, disabled, or passwords lost). This is the escalation case below.
- There is **no alert email or Smartsheet row** for any of these — the portal admin
  surface is browser-only and send-free, so the symptom arrives as a person telling
  you, not as a daemon alert.

## Tier-2 (Successor-Operator) — what you CAN do

Almost nothing here is a Successor-Operator task: managing accounts is the admins'
own job in the browser, and the recovery path touches a secret (below). The
low-class, no-secrets things you can do:

- **Confirm the symptom** from what a person observes: "I log in but don't see the
  Accounts tab" (their role is `submitter`, not `admin`), or "I can't log in at all"
  (wrong password, or the account is disabled).
- **Check the audit trail is being written** is a code/DB task — not Tier-2.
- **Reassure + gather facts**, then escalate the cases below. Do **not** run
  `portal_admin` — it reads the Keychain admin bearer, which is a high-capability
  (secrets/auth) operation reserved for the Developer-Operator.

## Escalate to Seth (Tier 3) when

These are **high-capability (secrets/auth) — fixed-category escalation**, regardless
of documentation:

- **Admin lockout** — no one can reach the Accounts tab (both admins demoted,
  disabled, or passwords lost). Recovery is the **break-glass** bearer CLI, which
  needs the Keychain `ITS_PORTAL_ADMIN_TOKEN`:
  - restore an admin: `python -m safety_reports.portal_admin set-role <lastname.firstname> admin`
  - re-enable a disabled account: `… enable-user <lastname.firstname>`
  - reset a forgotten password: `… reset-password <lastname.firstname>`
  These bearer routes have **no** last-admin guard on purpose — they are the path
  *out* of a zero-admin state. (The in-app UI **does** guard: it refuses to demote or
  delete the last enabled admin with a `last_admin` error, which is why a UI-only
  lockout is hard to reach in the first place.)
- **Provisioning a new admin** — `portal_admin add-user <name> --role admin`
  (Keychain bearer; secrets/auth).
- **The portal deploy, a D1 migration, a Worker secret, the send path, or any code.**

## Notes for the escalation target (Seth)

- The last-admin guard counts only **enabled** admins (`role='admin' AND disabled=0`).
  A disabled admin does not satisfy it — so demoting/deleting the only *enabled* admin
  is blocked even if disabled admins exist in the table.
- Account changes (create / edit / role-change / delete) and submit-as events are
  written to the D1 `audit_log` table (`actor_username`, `action`, `target_username`,
  `detail`). Read out-of-band:
  `npx wrangler d1 execute its-safety-portal-db --remote --command "SELECT * FROM audit_log ORDER BY id DESC LIMIT 50"`.
- **Submit-as ("filled out as").** When an admin fills a form on a field PM's behalf,
  the submission records BOTH parties: `submissions.actor_username` = the admin who
  hit submit (the true actor, never dropped) and `submissions.submitted_as` = the
  attributed account (migration 0008). A real submit-as also writes an `audit_log`
  row `action='submit_as'` (`actor_username`=admin, `target_username`=attributed,
  `detail`={submission_uuid, job_id}). A normal self-submit sets both columns to the
  same user and writes NO submit_as audit row. The server is the gate — a submitter
  who forges `submitted_as` is rejected 403; an unknown/disabled attributed account is
  422. This is **high-capability (impersonation) — Tier-3**: anything touching the
  submit-as gate, the attribution columns, or the audit trail escalates to Seth (the
  Successor-Operator does not modify it). The two attribution columns are NOT part of
  the canonical HMAC and NOT returned by `/api/internal/pending`, so the downstream
  pipeline (portal_poll → intake → Box → WSR) is unchanged.
