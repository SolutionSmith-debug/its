---
type: operations
date: 2026-07-10
status: active
related_prs: []
workstream: safety_portal
tags: [enablement, a8, portal, admin, accounts, rbac, sessions, handover]
---

# Enablement — The Portal Admin Dashboard (accounts) · Op Stds §6/A8

**Audience:** the office **admin** who manages who can log into the ITS Portal. No code knowledge
assumed. This is the plain-language companion to the successor-operator runbook
[`docs/runbooks/safety_portal_admin_dashboard.md`](../runbooks/safety_portal_admin_dashboard.md).
It is also the surface Handover Plan v9, Step 8 hands to the office.

## What the dashboard is, and how to reach it

Portal accounts are managed from the **Accounts** page inside the portal itself. Sign in as an
**admin**, and on the portal home page you'll see an **Accounts** card ("Create, edit, and set
roles on portal accounts") — open it. If you don't see that card, your account isn't an admin.

Being an admin is what unlocks this page. The portal checks your role on **every** request, so a
change to your role (or an account being locked out) takes effect on your *next* click — there's
no need to sign out and back in.

## What you can do here

The Accounts page lists every account, and lets you do four things:

| Action | What it does |
|---|---|
| **Create an account** | Mint a new login — username, a starting password, and a role. |
| **Edit login** | Rename an account and/or set a **new password** for it. |
| **Change role** | Move an account between the three roles using the dropdown on its row. |
| **Delete** | Permanently remove an account. |

Each account row shows the username, its role, and — if it's been locked out — a **disabled**
badge. (That badge is *read-only* here; locking/unlocking is an operator action, see below.)

## The three roles

Every account is exactly one of three roles. Pick the smallest one that fits the person's job.

| Role | For | In short |
|---|---|---|
| **Subcontractor (field PM)** | field crews who submit reports | Submit forms, log time, do field actions. No management. |
| **Manager (crew lead)** | crew leads | Everything a subcontractor does, **plus** manage crew/personnel and assign people to jobs. No account or job creation. |
| **Admin (office)** | the office | Everything, including this Accounts page, the job/form builders, and creating jobs. |

Details of what each role can do are in the *Manager role* and *Subcontractor tier* guides. The
important rule here: **only an admin can open this page or change anyone's role.**

## Creating an account

1. In **Create an account**, type the **username** as `lastname.firstname` — lowercase, exactly
   one dot (e.g. `smith.jane`).
2. Type a **starting password** (at least 8 characters). Note the field shows the password on
   screen as you type it, so no one is standing behind you.
3. Pick the **role** (Subcontractor / Manager / Admin).
4. Click **Create account**, then give the person their username and password **directly** (in
   person or over a channel you trust) — the portal does not email credentials.

> **"Temporary password" is just a label.** The portal does **not** force the person to change it
> at first login and does **not** expire it. If you want a password rotated, you (or they, via
> you) do it with **Edit login**. Treat the starting password like any real password.

## Resetting a password

Use **Edit login** on the person's row, type a **new password**, and save (leave the username field
unchanged to keep their name). Two things happen:

- the new password takes effect immediately, and
- **the person is signed out everywhere** — any session they had open stops working, so a reset
  doubles as "kick them out and make them log back in."

Give them the new password out-of-band, the same way as a new account. There is no self-service
"forgot password" for field users — an admin resets it for them.

## Changing a role

Use the **role dropdown** on the account's row; the change takes effect on that person's next
click. Two guardrails protect you from locking the office out:

- **You cannot remove the last admin.** If you try to demote or delete the *only* remaining active
  admin, the portal refuses (so there's always at least one way back in).
- **Editing your own account signs you out.** Renaming yourself, changing your own password, or
  demoting/deleting yourself drops you to the login screen — expected, just log back in.

## Deleting an account

**Delete is permanent** — the portal asks you to confirm ("this cannot be undone"), and there's no
undo. If someone is only leaving temporarily, or you're not sure, **don't delete** — ask the
operator to *disable* the account instead (reversible, see below).

## What this dashboard deliberately can't do

A few account actions are intentionally **not** on this page — they're **operator actions** run by
Seth (or the trained operator) from the command line, because they're either destructive or the
"break-glass" recovery path. Ask the operator when you need:

| Need | Who does it |
|---|---|
| **Lock out / re-enable** an account (reversible disable) | operator — CLI `portal_admin disable-user` / `enable-user` |
| **Create the very first admin**, or recover if **all** admins are locked out | operator — CLI break-glass path |
| **Change what a role is allowed to do** | not editable — a role's capabilities are fixed in the system; changing them is a code/policy change (Seth) |

The card copy mentions "capabilities," but there is no screen (or command) to hand-edit them —
what each role can do is set once, in the code, per role.

## Sessions and timeouts

- **Admins time out after 30 minutes of inactivity** (a security measure for the account that can
  do everything). Active use keeps you signed in.
- **Field users (submitter / manager) stay signed in for up to 90 days.**
- **To end someone's sessions immediately**, reset their password (above) — that's the "sign them
  out now" action from this page. Disabling or deleting them also cuts their access on the next
  request.

## Golden rules

- **Smallest role that fits.** Don't hand out Admin for convenience — it can create/delete
  accounts and jobs and see everything.
- **Deliver credentials directly**, never by email, and treat the starting password as real.
- **Prefer disable over delete** when someone might come back (ask the operator).
- **Keep at least two admins** so a single locked-out or forgotten admin never strands the office.
- **When in doubt, ask Seth** — accounts, credentials, and roles are security-class; the operator
  and Seth own the parts of this that aren't on the page.

## Owner

`@solutionsmith`. Part of the §6 / A8 documentation program. This in-repo version is the source of
truth for its content; the polished distributable PDF is rendered from it. (The matching §43
successor runbook, `docs/runbooks/safety_portal_admin_dashboard.md`, is the operator's fix-it
companion — note it predates the unified home page and the Manager role.)
