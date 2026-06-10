---
type: operations
date: 2026-05-29
status: active
related_prs: []
workstream: null
tags: [cutover, delivery, fail_closed, security]
---

# ITS Cutover Checklist

Mechanical checklist for the **validation → production** cutover, when the
build flips off the `evergreenmirror.com` sandbox domain and onto the real
`evergreenrenewables.com` (California) production identities. This is a
**tracked delivery deliverable** — the cutover is not done until every item
below is walked and verified.

> **Why this doc exists.** F22 (approval-attestation verification) is the first
> **fail-CLOSED** identity that is domain-specific: the authorized-approver set is
> the **ITS — Safety Portal workspace's member list** (sharing the workspace IS
> granting approval authority; read live by `weekly_send_poll` via
> `smartsheet_client.list_workspace_share_emails`). Because it is fail-closed and
> matched on **email** (Smartsheet's cell-history `modifiedBy` exposes no stable
> user ID — see `shared/approval_verification.py`), getting the production
> workspace sharing wrong at delivery does not error loudly — it **silently blocks
> every safety-report send** (every approval comes from a non-member email). This
> checklist makes the workspace re-share mechanical, not from-memory. It will grow
> as later workstreams add cutover-sensitive config.

## Procedure

### Cutover item 1 — F22 approver authority = workspace membership (owned by the F02/F22 PR)

The authorized-approver set is the **ITS — Safety Portal workspace member list** —
there is **no** `authorized_approvers` ITS_Config row any more (retired 2026-06-06).
During validation the (sandbox) workspace is shared with the three validation
accounts. At delivery, on the **production** ITS — Safety Portal workspace:

**UNSHARE** the three validation accounts:

- `daniels@evergreenmirror.com`
- `seths@evergreenmirror.com`
- `benf@evergreenmirror.com`

**SHARE** the production workspace with the seven real production approvers (any
access level — membership is the gate; the email must match the approver's
Smartsheet account exactly):

| Email | Person | Role |
|-------|--------|------|
| `jacobs@evergreenrenewables.com`   | Jacob Stephens     | CEO |
| `ezraj@evergreenrenewables.com`    | Ezra Jones         | CFO |
| `jechiahs@evergreenrenewables.com` | Jechiah Stephens   | Head of Engineering |
| `benf@evergreenrenewables.com`     | Ben Finkhousen     | Senior PM |
| `tiffanym@evergreenrenewables.com` | Tiffany Montastirsky | Head of Permitting |
| `tealap@evergreenrenewables.com`   | Teala Paradise     | Procurement & Subcontracting |
| `samr@evergreenrenewables.com`     | Sam Rigney         | Head of Field Operations |

> ⚠️ **Spelling.** The domain is `evergreenrenewables.com` (re-**new**-ables).
> The original contact sheet carried a typo `renwables` for Ezra — the table
> above is the corrected spelling. Sharing a non-matching email fail-closes that
> approver silently.
>
> ⚠️ **Confirm each is a real Smartsheet user before sharing.** The match is
> against the email Smartsheet records in cell history; an approver who isn't a
> real Smartsheet user, or whose Smartsheet email differs, will be blocked
> fail-closed even when shared.
>
> ⚠️ **GROUP shares don't count (today).** `list_workspace_share_emails` reads
> individual (USER) shares only — a workspace shared *only* to a Smartsheet group
> yields an empty authorized set → all sends blocked. Share with individuals, or
> track group-membership expansion as a follow-up.

### Cutover item 2 — broader domain flip (enumerate only; NOT this PR's scope)

These surfaces are cutover-sensitive but are **not** changed by the F02/F22
PR. Audit each at delivery:

1. **Box auth identity** — currently `seths@evergreenmirror.com` (Keychain
   `ITS_BOX_*`); flips to the real Evergreen Box identity. See
   `shared/box_client.py` (refresh-token rotation invariant) and
   `scripts/setup_box_oauth.py`.
2. **Intake mailbox(es)** — any `evergreenmirror.com` safety-intake mailbox
   addresses flip to production. See `safety_reports/weekly_send.py`
   `DEFAULT_FROM_MAILBOX` (`safety@evergreenmirror.com`) and the
   `safety_reports.weekly_send.from_mailbox` ITS_Config row.
3. **Resend sender** — `shared/resend_client.py` `DEFAULT_FROM`
   (`onboarding@resend.dev` sandbox sender) → operator's verified Resend
   domain.
4. **Any other `evergreenmirror.com` reference** — sweep both code and
   ITS_Config:

   ```bash
   grep -rn "evergreenmirror" --include="*.py" .
   # plus an ITS_Config sweep for the domain in any Value cell.
   ```

5. **Daniel's role transition** — Daniel (`daniels@`) is the operator's
   Evergreen contact and the **future maintainer** of this system; at/after
   cutover he transitions from validation-approver to system steward. Note
   for handover planning, not a code change.

## Validation — confirm the swap took

After re-sharing the production workspace (item 1):

1. **Fail-closed smoke (per `prompts/scaffold/manual-smoke.md`).** On the
   production WSR sheet, create a row approved by one of the seven production
   approvers (a workspace member) → confirm `weekly_send_poll` dispatches it.
   Create one approved by an account NOT shared on the workspace → confirm
   the send is **blocked** and a forensic `approval_unverified` event is
   recorded in ITS_Errors. This proves the gate end-to-end against
   production identities.
2. **No sandbox residue.** Re-run the `grep -rn "evergreenmirror"` sweep
   above and confirm the only remaining hits are intentional (e.g. this
   checklist, historical session logs) — zero in live config/runtime paths.
3. **Each approver resolves.** Spot-check that each of the seven emails is a
   real, active Smartsheet user in the production workspace.

### Cutover item 2 — Safety Portal production D1 hygiene (owned by the Portal PR)

The sandbox D1 (`its-safety-portal-db`) carries **seed/test rows** that must NOT cross into production:

- The **seed-data migrations** (sample jobs + the `test.pm` / sample `users`) are sandbox-only. On the
  **production** D1, apply ONLY the schema migrations — **skip the seed migrations**, or immediately
  `DELETE` the seeded `users` (especially `test.pm`) and any sample `jobs` / `submissions` rows. A
  stray ENABLED `test.pm` is a live credential on the production portal (and login is now gated on
  `disabled` — PR-4 — so a disabled stub is safe, but deletion is cleaner).
- Verify post-cutover: `SELECT username, disabled FROM users` lists ONLY real field-PM accounts;
  `SELECT COUNT(*) FROM submissions` is 0 (or only real submissions).

### Cutover item 3 — Worker production guards (owned by the Portal PR)

- **Branch protection** re-verified on the production deploy path: `main` requires `test` + `portal` +
  `secrets` (the publish daemon merges on all-green — see Gate-0, set 2026-06-10).
- **Cloudflare rate-limiting / WAF** on the production custom domain (the sandbox runs open) — at a
  minimum a `/api/login` rate-limit, since that endpoint is unauthenticated and runs bcrypt cost-10.
- The production Worker runs on the **Paid plan** (a cost-10 bcrypt compare can exceed the FREE plan's
  10 ms CPU cap → Error 1102 — see `safety_portal/README.md`).

## Daemon (re)install — interval substitution (§43 note)

A cutover that (re)installs the launchd daemons uses
`scripts/launchd/install.sh load <plist> [interval]`. For the **interval**
daemons (`org.solutionsmith.its.weekly-send`, `…portal-poll`, `…compile-now-poll`) the
installer substitutes `__POLL_INTERVAL_SECONDS__` in `<integer>StartInterval</integer>`
from (priority): the optional `[interval]` arg → the daemon's ITS_Config
poll-interval row (`safety_reports.weekly_send` / `safety_reports.portal_poll` /
`safety_reports.compile_now_poll` `.poll_interval_seconds`) → a per-daemon default
(900 / 60 / 90). If the token /
Smartsheet isn't ready yet at cutover time the read falls back to the default (a
`note:` line on stderr — harmless). After each load, confirm with
`install.sh status` and `plutil -lint` the installed copy under
`~/Library/LaunchAgents/`.

**Successor-Operator boundary (Op Stds §43/§44):** running the installer + reading
a config row is a **low-capability-class** action. But if `install.sh load` fails
`plutil -lint` with a surviving `__…__` placeholder, **escalate to Seth** — a
plist or installer change is a **code change** (high-capability-class). (This
exact gap — install.sh not substituting `__POLL_INTERVAL_SECONDS__` — was the
2026-06-02 fix; see `docs/tech_debt.md`.)

## Owner

`@solutionsmith`. This checklist is appended to as future workstreams add
cutover-sensitive config; each addition names the PR that owns the swap.
