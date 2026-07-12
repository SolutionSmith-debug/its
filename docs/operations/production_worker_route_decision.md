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

**Context.** `scripts/launchd/` ships **15** daemon plists (template excluded). VC-02
(`_check_launchd`) derives its expected label set by globbing those plists and requires
**exact set-equality** against `launchctl list` — any shipped-but-unloaded plist FAILs
the gate. Two daemons ship **dark**: `po-send` and `subcontract-poll` (their
`*.polling_enabled` ITS_Config rows are seeded `false`).

**Decision.** At cutover, **all 15 plists are LOADED**; the two dark daemons are
**loaded-but-runtime-gated** — launchd runs them on cadence, but each cycle exits early on
its `polling_enabled=false` gate, so nothing sends. Dark is enforced at the **ITS_Config
runtime gate**, not by leaving the plist unloaded.

**Why.**
- Keeps VC-02's shipped==loaded set-equality green without special-casing (no per-plist
  exclusion list to drift).
- `subcontract-poll` is already loaded-but-dark today; `po-send` matches that posture.
- Runtime-gating is the canonical dark-ship mechanism (the `polling_enabled` gate rows),
  and the External Send Gate stays intact — a loaded-but-gated send daemon transmits
  nothing until its gate is flipped `true`, which is a FIXED high-class decision (Seth).

**Consequence for the docs.** `launchctl list | grep -c solutionsmith` → **15** at cutover
(cutover_checklist CL-03, host_migration_runbook A6, aug7_delivery_runbook gate 3). First
enabling either send path (`po-send` / a future `subcontract-send`) remains an operator
External-Send-Gate decision, tracked separately (VC-03 deliberately does NOT enroll their
`polling_enabled` as `true`).

**Operator override.** If the intent is instead to leave `po-send` launchd-**unloaded**
at cutover, VC-02 needs an explicit exclusion (a code change, Seth) — flag before the
freeze; this doc's default is loaded-but-runtime-dark.

## Owner

`@solutionsmith` (Developer-Operator). Both decisions are Seth-owned (Cloudflare topology
+ External-Send-Gate posture); recorded here at operator direction 2026-07-12.
