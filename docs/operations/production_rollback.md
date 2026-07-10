---
type: operations
date: 2026-07-09
status: active
related_prs: []
workstream: null
tags: [cutover, rollback, aug7_delivery, fail_closed]
---

# Production Rollback (tenant cutover → mirror)

Per-workstream rollback paths for the Aug-3 tenant cutover
(`docs/operations/cutover_checklist.md`), plus the global brake. Design
posture: the mirror tenant stays a **warm rollback target through the Day-7
gate** — the mirror Worker stays deployed, mirror workspaces/sheets stay
intact, and the mirror secret set survives in a sealed offline backup. A
rollback is a **config repoint + key re-seed**, never a code revert.

Every rollback leg ends with a mechanical verify (§53 — a rollback claim is
as narrative as a cutover claim until verified). Re-run the relevant
`scripts/verify_cutover.py` checks with `--allow-sandbox` after any leg (the
gate's sandbox scan inverts: on a rolled-back system, mirror values are the
*correct* state).

## Purpose

If a production surface fails after cutover (recipient wiring wrong, tenant
auth broken, Worker regression), restore that workstream to the known-good
mirror state within minutes, without disturbing the workstreams that are
healthy — and without ever losing the mirror credentials that make the
rollback possible.

## Procedure

### R0 — sealed mirror-secret backup (BEFORE the cutover overwrites anything)

The cutover re-seeds Keychain entries in place (`security
add-generic-password -U …`). The OLD mirror values are unrecoverable once
overwritten — so **before CL-16 / the M365 flip / any `ITS_MS_*` or Box
re-seed**, capture the mirror set:

1. On the production host, for each secret about to be overwritten
   (`ITS_MS_TENANT_ID`, `ITS_MS_CLIENT_ID`, `ITS_MS_CLIENT_SECRET`,
   `ITS_BOX_CLIENT_ID`, `ITS_BOX_CLIENT_SECRET`, `ITS_BOX_REFRESH_TOKEN` —
   plus any portal bearer being rotated), read the current value:
   `security find-generic-password -a "$USER" -s <NAME> -w`
   (run at a terminal you control; output goes to the screen only).
2. Transcribe the set to the **offline backup medium** (printed sheet in a
   sealed envelope, or an encrypted disk image on a USB stick that never
   leaves the operator). Label it `ITS mirror secrets — sealed YYYY-MM-DD`.
3. **NEVER** write these values into the repo, a log, a Smartsheet cell, or
   any cloud note (§54 discipline).
4. **Box caveat:** `ITS_BOX_REFRESH_TOKEN` rotates on every exchange — the
   sealed copy is a snapshot that stays valid only while UNUSED (Box refresh
   tokens live ~60 days unexercised). A Box rollback later than that, or
   after the token was exercised anywhere, means a fresh
   `scripts/setup_box_oauth.py` run against the mirror account instead — plan
   for that path, it is the reliable one.

Verify: the sealed backup physically exists and is named in the cutover
session log (CL-31) — before the first overwrite, not after.

### R-global — the brake (any severity, instant)

```text
ITS_Config: Setting=system.state, Workstream=global → Value=PAUSED
```

Every `@require_active` daemon exits cleanly at its next cycle — capture
stops, compile stops, sends stop. The portal Worker keeps ACCEPTING
submissions into D1 (send-free by design); nothing drains until un-paused.
The kill switch is an operator-convenience brake, NOT a security control
(fail-open by design) — the send gate itself never depended on it.

Verify (mechanical):
- `python -c "from shared import smartsheet_client as s; print(s.get_setting('system.state', workstream='global'))"` → `PAUSED`.
- Next-cycle daemon logs show the PAUSED clean-exit line; ITS_Daemon_Health
  `Last Cycle Status` flips to SKIPPED/paused within one interval.

### R1 — Safety Portal / intake (Worker + portal_poll)

The mirror Worker at `https://safety.evergreenmirror.com` **stays deployed
until the Day-7 gate** precisely for this leg.

1. Repoint the daemons:
   `ITS_Config: safety_reports.portal.worker_base_url [safety_reports]`
   → `https://safety.evergreenmirror.com` (this one row feeds portal_poll,
   fieldops_sync, and the progress rollup read — one repoint, three
   consumers).
2. Re-enable the mirror portal users if they were disabled
   (`portal_admin enable-user …`).
3. If production D1 rows were the problem: production submissions already
   pulled to the Mac are filed; unpulled rows stay queued in production D1 —
   leave them (the Worker is send-free; nothing leaks).

Verify: test submission on the mirror portal → `portal_poll` pulls within
~60s → PDF filed to the mirror Box root; `python -m scripts.verify_cutover
--only daemon-health --allow-sandbox` → PASS.

### R2 — M365 / Graph (mail identities)

1. Re-seed the mirror `ITS_MS_TENANT_ID` / `ITS_MS_CLIENT_ID` /
   `ITS_MS_CLIENT_SECRET` from the sealed backup
   (scripted form: `security add-generic-password -U -a "$USER" -s <NAME> -w '<VALUE>'`
   — `-U` BEFORE `-w`; or the interactive bare `-w` prompt form).
2. Repoint the from-mailbox rows:
   `safety_reports.weekly_send.from_mailbox [safety_reports]` →
   `safety@evergreenmirror.com`;
   `progress_reports.progress_send.from_mailbox [progress_reports]` →
   the mirror progress mailbox (its#460); PO equivalent when live.

Verify: `python scripts/smoke_test_graph.py` exits 0 against the mirror
tenant; the from_mailbox cells read back the mirror values.

### R3 — Smartsheet approvers (F22 fail-closed lockout)

Symptom: every send HELD with `approval_unverified` forensic rows — the
production workspace share list doesn't match approver Smartsheet emails.

1. Do NOT widen shares in panic. Re-share the affected workspace (Safety /
   Progress / PO) with the mirror validation accounts as individual USER
   shares to restore a working approval set.
2. Fix the production approver accounts offline (exact-email match,
   individual USER shares, `renewables` spelling class), then re-run the
   CL-28 fail-closed smoke before switching back.

Verify: `list_workspace_share_emails(<workspace_id>)` returns the intended
set; one member-approved row DISPATCHES, one non-member row BLOCKS with the
forensic ITS_Errors row.

### R4 — Box

Preferred path (token-rotation-proof): fresh OAuth against the mirror
account — run `scripts/setup_box_oauth.py` on the production host signed in
as the mirror Box identity; then re-seed the folder-root / routing config
rows back to the mirror folder IDs.

Verify: `python -c "from shared import box_client; print(box_client.get_client().user().get().login)"`
→ the mirror identity; a dry `get_folder` on each configured root resolves;
next portal round trip files into the mirror ROOT→job→week path.

### R5 — send paths (safety weekly / progress / PO)

Reverting a send path = flipping its gate off, not surgery:

```text
safety_reports.weekly_send.polling_enabled  [safety_reports]  → false
progress_reports.progress_send.polling_enabled [progress_reports] → false
(PO gate when live)
```

Approved-but-unsent rows keep `Send Status=PENDING` and simply wait —
the two-process model means nothing is half-sent by a gate flip (the
write-ahead SENDING marker bounds the one in-flight row; a row stuck at
SENDING is the documented §43 stuck-send case, not a rollback matter).

Verify: gate cell reads `false`; next poll cycle logs the disabled clean
exit; no new `Sent At` stamps appear.

### Mirror decommission (the END of rollback capability)

Only after the Day-7 gate (CL-33) passes: disable mirror portal users,
optionally tear down the mirror Worker, retire the sealed backup (destroy
the printed sheet / wipe the stick — do not archive it). From this point a
rollback is a rebuild, not a repoint — which is why nothing above happens
before Day 7.

Verify: decommission recorded in a session log with the date; CL-31's
`curl -sI https://safety.evergreenmirror.com/` expectation flips to
non-200/NXDOMAIN thereafter.

## Validation

A rollback leg is complete when its mechanical verify passes AND a dated
session-log entry records: which leg, why, the verify output, and the
forward plan (fix + re-cutover date). Re-run
`python -m scripts.verify_cutover --allow-sandbox` after any multi-leg
rollback and paste the output.

## Owner

`@solutionsmith` (Developer-Operator). Every leg above touches secrets,
send-gate config, or tenant identity — **§44 high-capability-class across
the board**: the Successor-Operator's only rollback action is R-global
(`system.state=PAUSED`, a documented low-class toggle), then escalate.
