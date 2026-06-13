---
type: operations
date: 2026-06-12
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, safety-portal, photo, weekly-send, tier-2]
---

# Runbook — Safety photo path (photo rejected / clamd down / oversized packet HELD) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and reads Smartsheet rows + alert emails,
but does **not** read code. Claude loads the relevant block to drive a Tier-2
repair; the operator sees the Smartsheet / ITS_Errors / ITS_Review_Queue evidence
and approves. The §42 code-reader rationale lives in the docstrings of
`safety_reports/photo_screen.py`, `safety_reports/intake.py`
(`_screen_portal_photos` / `_portal_photo_refusal`), and
`safety_reports/weekly_send.py` (the Stage-4b / Stage-6 transport switch).

## Purpose

The Safety Portal accepts **site photos** on a submission. Photos are screened
on the Mac (§34, four-layer), embedded in the per-submission PDF, filed to Box,
and merged into the Sat→Fri **weekly packet** that `weekly_send` emails. This
runbook covers the three Tier-2-reachable failure modes that photos introduce:

1. **A photo was rejected** — a submission routed to review (never filed/sent).
2. **clamd is down** — the optional ClamAV layer is enabled but unreachable, so
   photos route to review.
3. **An oversized weekly packet is HELD** — a photo-bearing packet too large to
   email.

Each block below follows the §43 four-part shape (Symptom → checks → Claude/UI
action → escalate-to-Seth).

---

## Symptom 1 — a photo was rejected (submission routed to review)

### Symptom

- **ITS_Review_Queue** has a **security-flagged** row whose Reason is
  `security_trigger`, summary mentioning a **photo** ("MALICIOUS photo rejected …"
  or "suspicious photo routed to review …"), naming a `submission_uuid` and the
  submitting portal **account**.
- **ITS_Errors** carries a matching row: `Error = portal_photo_malicious`
  (Severity CRITICAL — also paged) or `Error = portal_photo_suspicious`
  (Severity WARN). The submission was **NOT** filed and **NOT** sent — refusal is
  whole-submission and permanent (a re-pull re-screens to the same verdict).

### What the Successor-Operator checks

1. **Severity.** `portal_photo_malicious` (CRITICAL, paged) means the photo
   tripped the §34 trust boundary AND the page instructs **disabling the
   submitting portal account** pending review — treat as a security event.
   `portal_photo_suspicious` (WARN) is a softer route-to-review (e.g. a
   structurally-odd or undecodable image), no account-disable instruction.
2. **The detail tag** in the Review-Queue row / ITS_Errors message
   (`<layer>:<detail>`, e.g. `L2:reencode_failed`, `over_submission_cap:N`,
   `undecodable_base64`) tells you *why* it was refused.
3. **Is this a real crew submission or an attack?** Check the named account and
   `submission_uuid` against who was on that job that day. A genuine crew photo
   that screened "suspicious" is usually a corrupt/odd upload, not an attack.

### The Claude prompt or UI action

- **Suspicious, genuine crew photo** (corrupt upload): ask the crew to **re-take
  and re-submit** the photo through the portal. The original submission stays in
  review as a record; no code action. Low-class.
- **Malicious page**: in the Safety Portal **admin dashboard**, **disable the
  named portal account** pending review (the page names it). This is the documented
  admin-dashboard account action — see
  [`safety_portal_admin_dashboard.md`](safety_portal_admin_dashboard.md). Disabling
  an account is operator UI, not code. Then leave the Review-Queue row for Seth to
  inspect the payload.

  > "Claude, ITS_Review_Queue has a security-flagged `portal_photo_malicious` row
  > for submission `<uuid>` from account `<actor>`. Walk me through disabling that
  > portal account in the admin dashboard and confirm the submission was never
  > filed or sent."

### Escalate-to-Seth condition

Escalate to the Developer-Operator (Seth, Tier 3) when **any** of:

- A **`portal_photo_malicious`** verdict on a photo you cannot attribute to a
  corrupt-but-genuine crew upload — a real malicious upload is a **security**
  event (high-class), even though disabling the account is low-class.
- The refusals **repeat** from the same account or across many submissions
  (possible probing).
- The detail tag is **novel** / not one of the documented screening details.

Both-rule (Op Stds §44): "ask the crew to re-submit" and "disable a portal account
the page named" are low-class / documented (Tier 2). "Decide whether a malicious
upload is a real attack, inspect the payload, change screening thresholds" is a
**security / code** decision = high-class → Tier 3.

---

## Symptom 2 — clamd is down (ClamAV enabled but unreachable)

### Symptom

- ClamAV screening is **enabled** (ITS_Config `safety_reports.photo_screen.clamav_enabled`
  = `true`) **and** photo submissions are routing to review with a detail tag of
  `L3:clamav_error` / `L3:pyclamd_unavailable` (the scanner was required but could
  not run), i.e. `portal_photo_suspicious` rows whose detail points at **L3**.
- This only happens when the config gate is ON. Default is **OFF** (the mirror has
  no clamd; the production Mac installs it). With the gate OFF, photos never reach
  L3 and this symptom cannot occur.

### What the Successor-Operator checks

1. **ITS_Config** — confirm `safety_reports.photo_screen.clamav_enabled` is
   actually `true`. If it is OFF, the detail tag cannot be an L3 error — re-read
   the row; it is a different (L1/L2) refusal (see Symptom 1).
2. **Is clamd running on the Mac?** ClamAV's `clamd` daemon must be up and its
   unix socket reachable. If the operator's host shows clamd stopped, that is the
   cause — photos correctly route to review rather than passing unscanned (the §34
   "scanner required but unavailable → do NOT pass blindly" rule).

### The Claude prompt or UI action

Two low-class repairs, in order of preference:

- **Restart clamd** (preferred): bring the ClamAV daemon back up on the Mac, then
  ask the crew to re-submit the affected photo(s). Re-screening with clamd healthy
  passes the L3 layer. Restarting a local service + re-submitting is low-class.
- **Disable the config gate** (stopgap, if clamd can't be restored quickly): set
  ITS_Config `safety_reports.photo_screen.clamav_enabled` = `false`. Photos then
  screen on L1+L2 only (magic-number + Pillow re-encode sanitizer, which is the
  load-bearing layer) — a deliberate, documented reduction, not a bypass. Toggling
  a documented ITS_Config value is the canonical Tier-2 action.

  > "Claude, photo submissions are routing to review with an `L3:clamav_error`
  > detail and ITS_Config `clamav_enabled` is true. Help me confirm clamd's state
  > on this Mac, restart it if it's down, and otherwise toggle the config gate off
  > as a stopgap."

### Escalate-to-Seth condition

Escalate to Seth when **any** of:

- clamd **will not restart** / the install looks broken (a software-install /
  host-config problem, not a toggle).
- You are unsure whether disabling the gate is acceptable for the current threat
  posture — leaving the AV layer off is a **doctrine/security** judgment
  (high-class) if it's more than a brief stopgap.
- The L3 errors persist **after** clamd is confirmed healthy (novel).

Both-rule: "restart a local daemon" and "toggle a documented config value" are
low-class / documented (Tier 2). "Fix a broken ClamAV install" or "decide to run
without AV indefinitely" is high-class → Tier 3.

---

## Symptom 3 — an oversized weekly packet is HELD (too large to email)

### Symptom

- A **WSR_human_review** row is stuck at **Send Status = HELD** and its **Notes**
  carry a `[HELD: compiled packet is <N> bytes, over Graph's <M>-byte
  upload-session ceiling — cannot email; reduce photo count / split the packet]`
  tag. `send_one_row` returned `held_oversized_packet`.
- **ITS_Errors** has a `weekly_send.held` WARN for that row. **No email was sent**
  (HELD is a refusal; the poller excludes HELD from re-dispatch).
- Context: most packets send **inline**; a packet over **2.5 MB** automatically
  switches to the Graph **upload-session** path (chunked large-attachment) and
  still sends. HELD only happens when a packet exceeds Graph's **hard ~150 MB**
  attachment ceiling — which, with the 8-photo/400 KB budget, should be rare and
  signals an anomalously large packet.

### What the Successor-Operator checks

1. **The packet size** in the HELD note (`<N> bytes`). Confirm it really is over
   the ~150 MB ceiling (`<M>`), i.e. this is a true oversized-HELD and not a
   different HELD reason (no-recipient / missing-PDF have different notes).
2. **The compiled Box packet** for that job/week — open the Compiled-PDF link on
   the WSR row. Is it plausibly that large (many/huge photos), or does the size
   look wrong (a corrupt/duplicated compile)?
3. **Did the upload-session path itself break?** If the packet is **well under**
   ~150 MB yet still HELD-oversized, the size constants may have drifted — that is
   NOT a normal oversized packet.

### The Claude prompt or UI action

- **Genuinely too-large packet**: this needs the **packet reduced**, which is a
  compile-side action — fewer/smaller photos for that week, or splitting the
  week's submissions. The Successor-Operator's low-class step is to **flag the
  job/week to Seth** with the size and the Box link; do **not** hand-edit the
  packet or force a send.

  > "Claude, WSR row `<id>` for `<project>` week `<date>` is HELD-oversized at
  > `<N>` bytes (over Graph's ~150 MB ceiling). Show me the compiled Box packet
  > and summarize what's in it so I can hand it to Seth."

- Do **NOT** mark the row SENT, change Send Status, or attempt a manual send — the
  packet cannot be transmitted as-is and forcing it touches the External Send Gate.

### Escalate-to-Seth condition

Escalate to Seth (Tier 3) when **any** of:

- The packet is genuinely over the ceiling — **reducing/splitting** a weekly packet
  is a compile + content decision Seth owns.
- The packet is **under** ~150 MB but still HELD-oversized (size-constant drift) —
  that is a **code** problem (the threshold / ceiling logic), high-class.
- The upload-session send path is failing for **normal-size** photo packets (FAILED
  with retry, not HELD) — a Graph **send-path** failure touches the External Send
  Gate = high-class.

Both-rule: "read the HELD note, open the Box packet, hand the facts to Seth" is
low-class / documented (Tier 2). Anything touching the **send** itself (forcing a
send, editing Send Status, fixing the transport) is the **External Send Gate** =
fixed high-class category → Tier 3.

## Symptom 4 — upload-session send FAILED+retry (a sendable-size packet, NOT HELD)

Distinct from Symptom 3 (oversized → HELD *before* send): here the packet IS a sendable
size, but the Graph upload-session **send itself** is failing.

### What the Successor-Operator sees

- The WSR row at **Send Status = FAILED** (not HELD), Notes carrying
  `[LAST_SEND_ERROR: …Graph upload…]`, and the retry counter advancing.
- **ITS_Errors** carries `weekly_send.graph_error` rows for that send.

### Action

**None at Tier 2 — escalate to Seth (Tier 3).** A failing send transport is the
**External Send Gate** (fixed high-capability-class): do NOT force a send, edit Send
Status, or touch the transport. Hand Seth the WSR row + the `weekly_send.graph_error`
detail. (A single transient Graph blip self-heals on the next retry — only a
**persistent** FAILED+retry across cycles is the escalation.)

## Owner

`@solutionsmith`. New photo-path failure modes that become Tier-2-reachable should
be added here as additional Symptom → checks → action → escalate blocks, per Op
Stds §43.
