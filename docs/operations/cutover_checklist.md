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

> **Why this doc exists.** F22 (approval-attestation verification) introduced
> the first **fail-CLOSED** identity that is domain-specific: the
> `safety_reports.authorized_approvers` allowlist. Because it is fail-closed
> and matched on **email** (Smartsheet's cell-history `modifiedBy` exposes no
> stable user ID — see `shared/approval_verification.py`), getting the swap
> wrong at delivery does not error loudly — it **silently blocks every
> safety-report send** (every approval comes from an email not on the list).
> This checklist makes the swap mechanical, not from-memory. It will grow as
> later workstreams add cutover-sensitive config.

## Procedure

### Cutover item 1 — F22 approver list (owned by the F02/F22 PR)

The `safety_reports.authorized_approvers` ITS_Config row currently holds the
three **validation-phase** sandbox approvers. At delivery, edit that row:

**REMOVE** the three validation accounts:

- `daniels@evergreenmirror.com`
- `seths@evergreenmirror.com`
- `benf@evergreenmirror.com`

**REPLACE** with the seven real production approvers (comma-separated, no
spaces — the value is parsed by `approval_verification.parse_authorized_actors`):

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
> The original contact sheet carried a typo `renwables` for Ezra — the
> table above is the corrected spelling. A typo here fail-closes that
> approver silently.
>
> ⚠️ **Confirm each is a real Smartsheet user in the PRODUCTION workspace
> before delivery.** The match is against the email recorded in Smartsheet
> cell history; an approver who isn't a real Smartsheet user, or whose
> Smartsheet email differs, will be blocked fail-closed.

The resulting `Value` cell:

```
jacobs@evergreenrenewables.com,ezraj@evergreenrenewables.com,jechiahs@evergreenrenewables.com,benf@evergreenrenewables.com,tiffanym@evergreenrenewables.com,tealap@evergreenrenewables.com,samr@evergreenrenewables.com
```

(If a later design switches matching to Smartsheet user IDs — only possible
if the cell-history API begins exposing them — the values become the
production users' IDs, and the swap is still required because the validation
users are different accounts.)

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

After editing the `safety_reports.authorized_approvers` row (item 1):

1. **Fail-closed smoke (per `prompts/scaffold/manual-smoke.md`).** On the
   production WPR sheet, create a row approved by one of the seven production
   approvers → confirm `weekly_send_poll` dispatches it. Create one approved
   by an account NOT on the list (or a hand-edited approval cell) → confirm
   the send is **blocked** and a forensic `approval_unverified` event is
   recorded in ITS_Errors. This proves the gate end-to-end against
   production identities.
2. **No sandbox residue.** Re-run the `grep -rn "evergreenmirror"` sweep
   above and confirm the only remaining hits are intentional (e.g. this
   checklist, historical session logs) — zero in live config/runtime paths.
3. **Each approver resolves.** Spot-check that each of the seven emails is a
   real, active Smartsheet user in the production workspace.

## Owner

`@solutionsmith`. This checklist is appended to as future workstreams add
cutover-sensitive config; each addition names the PR that owns the swap.
