---
type: operations
date: 2026-07-23
status: active
related_prs: []
workstream: null
tags: [cutover, phase_1, identity, f22, security, cloudflare, mailbox]
---

# Phase-1 Cutover Decisions (identity / mailbox / Cloudflare)

Decision record for five operator decisions ratified 2026-07-23 (Seth, chat
session) governing the Phase-1 production cutover. This doc exists so a future
session can reconstruct the *why* — especially the deliberately-not-shipped
F22 self-exclusion filter — without the originating chat. Companions:
`docs/operations/cutover_checklist.md` (the mechanical gate, CL-40–CL-42 added
by the same PR as this doc) and `docs/tech_debt.md` (the deferred
self-exclusion design, entry "F22 token-identity self-exclusion filter").

## Purpose

Turn five chat-ratified decisions into a durable, file-referenced record:
the Smartsheet token identity for Phase 1, the Cloudflare account posture,
the single-production-mailbox model, the sandbox disposition, and the staged
`system.operator_email` repoint.

## The five decisions

### D1 — Smartsheet identity: Daniel Stephens' personal PAT (for now)

**Decision (§44 high-class, operator-ratified; changeable later).** ITS runs
Phase 1 on Daniel Stephens' personal Smartsheet PAT. Daniel's account runs the
production stand-up and OWNS all ITS workspaces.

**The F22 self-exclusion filter is DELIBERATELY NOT shipped now.** Shipping it
would block Daniel's own approvals, because the workspace owner's email is
automatically part of the F22 approver set:

- The F22 authorized-approver set is the workspace's share-email list —
  `shared/smartsheet_client.py::list_workspace_share_emails` (raw REST
  `GET /workspaces/{id}/shares?includeAll=true`, ~:2023–2070) applies **no
  accessLevel filter** (~:2066): every share record carrying an email counts,
  and Smartsheet's `/shares` response **includes the workspace owner** —
  proven empirically in the `logs/migrations/prewipe_20260723T030026Z`
  workspace dumps (2026-07-23 sandbox wipe).
- Approval identity is matched on **email only**: Smartsheet cell-history
  `modifiedBy` exposes `{name, email}` with no stable user id
  (`shared/approval_verification.py:35-38`).
- The consuming seam is
  `safety_reports/send_poll_core.py::_load_authorized_approvers` (:191–:220),
  fail-closed, returning that share-email frozenset verbatim.

Consequence: with one email (Daniel's) holding both the token identity and an
approver role, a self-exclusion filter cannot be enabled without emptying his
approval authority — so one email cannot hold both roles *with the hole
closed*. The filter waits for a dedicated token identity (see migration
trigger below).

**Accepted residual.** An API write made via the token can mint an approval
(flip the checkbox → cell-history `modifiedBy` = Daniel) indistinguishable
from Daniel's human approval. This is the SAME posture the mirror validation
period ran under Seth's token — accepted, not new.

**Migration path / trigger.** A dedicated `its@` Smartsheet seat + the
self-exclusion filter (subtract the token identity's own email from the
approver set). Design fully scoped in `docs/tech_debt.md` → "F22
token-identity self-exclusion filter — DEFERRED until the dedicated its@
Smartsheet token".

### D2 — Cloudflare: transfer the existing account, migrate nothing

The existing dedicated Cloudflare account transfers to
`its@evergreenrenewables.com` ownership/billing (Super-Admin invite + payment
method + Workers Paid). **No Worker / D1 / zone migration.** The portal keeps
`safety.evergreenmirror.com` through Phase 1 (the `phase1-hybrid`
`verify_cutover` profile covers the deliberately-mirror rows). Checklist:
CL-41.

### D3 — Single production mailbox: its@evergreenrenewables.com

ALL send lanes (safety, progress, procurement/PO, RFQ, subcontracts) send from
the single production mailbox `its@evergreenrenewables.com`; the Exchange
Application Access Policy is scoped to it. Later identity splits use **SHARED
mailboxes** (free, no license) — **NEVER a personal mailbox**: the Application
Access Policy grants the app full mail read/write over the entire mailbox it
covers, which is unacceptable blast radius on a human's mailbox. Checklist:
CL-07 (reworded), CL-40.

### D4 — Sandbox left EMPTY after the dev wipe

The `evergreenmirror.com` sandbox tenants stay empty after the development
wipe; rebuild on demand via the rehearsal-proven stand-up tooling
(`scripts/migrations/standup.py`, `docs/runbooks/tenant_standup.md`).

### D5 — system.operator_email → its@; escalation stays Seth through Day-7

`system.operator_email` repoints to `its@evergreenrenewables.com` as a staged
repoint value (`production_repoint.py` sweep). Escalation ROUTING (Resend /
Sentry / UptimeRobot destinations) stays Seth through the Day-7 gate — CL-32
is unchanged by this decision.

## Execution plan pointer (parallel tracks A–F)

The decisions above execute via the parallel-track Phase-1 plan (chat-ratified
alongside them):

- **Track A** — Seth provisioning (M365 `its@` mailbox + AAP, Cloudflare
  Super-Admin invite/billing/Workers Paid, Smartsheet/Box identities).
- **Track B** — CC PRs B1–B6 (this doc lands as PR-B5).
- **Track C** — dump + wipe of the sandbox tenants (dump-before-delete;
  sandbox then stays empty per D4).
- **Track D** — production stand-up: `standup.py` with `--skip-shares`
  (CL-11 shares are Seth-attended via `seed_production_shares.py`, not
  stand-up-automated) and `--skip-restore-sheet ITS_Active_Jobs` ×2 (both
  Active-Jobs sheets start empty in production — no mirror-job restore).
- **Track E** — D1 cleanup (production hygiene per CL-19).
- **Track F** — verify + go-live (`python -m scripts.verify_cutover`
  `--profile phase1-hybrid`, then the CL-30 gate).

## Validation

- The checklist rows carrying these decisions are CL-07 (reworded), CL-40,
  CL-41, CL-42 in `docs/operations/cutover_checklist.md`.
- The F22 mechanism summary above is reconstructable from code alone:
  `shared/smartsheet_client.py::list_workspace_share_emails`,
  `shared/approval_verification.py` (email-only identity note),
  `safety_reports/send_poll_core.py::_load_authorized_approvers`.
- The deferred self-exclusion design lives in `docs/tech_debt.md`; its
  trigger is the `its@` Smartsheet identity migration (D1).

## Owner

`@solutionsmith` (Seth — all five are §44 operator decisions; D1's token
posture and any future gate flips remain FIXED high-class actions).
