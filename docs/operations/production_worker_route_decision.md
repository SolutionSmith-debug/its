---
type: operations
date: 2026-07-12
status: active
related_prs: []
workstream: null
tags: [cutover, aug7_delivery, cloudflare, worker, rollback, external_send_gate]
---

# Production Worker route + daemon-load posture decision (cutover)

## Purpose

Two cutover decisions the mechanical gate (`scripts/verify_cutover.py`) and the
rollback doc (`docs/operations/production_rollback.md`, **R1**) depend on, recorded
here so the cutover reads deterministically. Operator decisions of 2026-07-12.

## Decision 1 — production runs a SECOND Cloudflare Worker + D1 (NOT a route swap)

**Context.** Today one Worker (`its-safety-portal`, `safety_portal/wrangler.jsonc`)
serves the mirror at `safety.evergreenmirror.com` with `custom_domain: true`. The
`custom_domain: true` gotcha (error 1042) disables the `*.workers.dev` URL on deploy —
already handled by repointing daemon base-URLs to the custom domain.

**Decision (operator, 2026-07-12).** Production gets its **own** Worker + D1 (a second
env/Worker), leaving the mirror Worker and its D1 untouched. **Not** a route swap on the
single Worker.

**Why.**
- **Rollback R1 stays as written** — `production_rollback.md` R1 assumes the mirror
  Worker is an intact fallback surface; a route swap would collapse mirror and prod onto
  one Worker and force an R1 amendment. A second Worker keeps the sealed rollback valid.
- **Clean mirror/prod isolation** — dress rehearsals (`--allow-sandbox`) keep running on
  the mirror Worker while production is live; no shared D1, no cross-contamination.
- **Per-customer-fork-friendly** — each customer fork already gets its own Worker + D1
  (blueprint model), so load never aggregates; the second-Worker split is the same shape.

**Cutover checks.** `cutover_checklist.md` **CL-39**: the production `wrangler` env/route
points at the production custom domain; the mirror Worker (`safety.evergreenmirror.com`)
still resolves + serves. The three `safety_reports.portal.worker_base_url` ITS_Config
copies (+ the subcontract daemon, which reuses the `safety_reports` copy) must be the
production domain, sandbox-scanned by VC-03 (a mirror residue fails the gate).

**Rejected alternative — route swap on the one Worker.** Simpler infra (one Worker) but
requires amending R1 to account for the shared Worker and loses the intact-mirror
fallback. Declined.

## Decision 2 — daemon LOAD posture at cutover (so VC-02 exits 0)

**Context.** `scripts/launchd/` ships **15** daemon plists (template excluded). Two daemons
ship **dark** (their `*.polling_enabled` ITS_Config rows are seeded `false`): `po-send` (a
SEND daemon) and `subcontract-poll` (a generation daemon).

**Decision (operator, 2026-07-12 — the send-gate-strict alternative).** At cutover, **14
daemons are LOADED; `po-send` stays launchd-UNLOADED.** A dark external-SEND path is not even
running — send-gate defense-in-depth. `subcontract-poll` (and every other generation daemon —
portal-poll, po-poll, fieldops-sync, …) **loads but is runtime-gated dark**: generation/filing
daemons transmit nothing, so loaded-but-gated is fine for them; only the SEND daemon is held
unloaded.

**Code.** VC-02 encodes this with `DARK_UNLOADED_LABELS = {org.solutionsmith.its.po-send}`:
`_expected_labels()` returns shipped-minus-dark-unloaded (14), and the check **FAILS if a
dark-unloaded send daemon IS loaded** — a send daemon live at cutover is a distinct, named
send-gate violation, not a plain orphan. First-enabling PO send = remove `po-send` from
`DARK_UNLOADED_LABELS` + load its plist + enroll its `polling_enabled` — a FIXED high-class
External-Send-Gate decision (Seth). A future `subcontract-send` joins `DARK_UNLOADED_LABELS`.

**Why the alternative over loaded-but-dark.** It matches the daemon's actual dev-box state
(`po-send` was already the one unloaded plist), and a send daemon that isn't loaded *cannot*
transmit even if its runtime gate were flipped by accident — strictly stronger than relying
on the `polling_enabled` gate alone.

**Consequence for the docs.** `launchctl list | grep -c solutionsmith` → **14** at cutover
(cutover_checklist CL-03, host_migration_runbook A6, aug7_delivery_runbook gate 3); the load
loop skips `po-send`. VC-03 still does NOT enroll any send `polling_enabled` as `true`.

## Owner

`@solutionsmith` (Developer-Operator). Both decisions are Seth-owned (Cloudflare topology
+ External-Send-Gate posture); recorded here at operator direction 2026-07-12.
