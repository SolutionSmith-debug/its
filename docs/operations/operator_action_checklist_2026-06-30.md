---
type: operations
date: 2026-06-30
status: active
related_prs: [363, 365, 366]
workstream: null
tags: [tech-debt, operator-checklist, cleanup, security]
---

# Operator Action Checklist — Tech-Debt Cleanup (2026-06-30)

The **operator-only** residue from the 2026-06-30 tech-debt cleanup pass — items that
cannot be done in code (Smartsheet/Box/Cloudflare UI or SDK deletes, Keychain, owner
decisions, live-deploy confirmations). The code-side wins of that pass already landed:
the docs currency sweep (#363), the Smartsheet integration-test flake fix (#365), and a
stale-entry close (#366). Package A (allowlist-drift validation, #364) was **closed
unmerged** as premature — see [Not on this list](#not-on-this-list).

> **Verify-live-first rule (lesson #1).** Several items below name a Smartsheet sheet/
> folder/row or a Cloudflare/Box resource that may already be gone (404) or already done.
> Each entry that deletes or mutates live state says **VERIFY FIRST**. MCP cannot delete
> Smartsheet sheets/folders or Box folders — use the Python SDK clients directly, and
> **name-guard every bulk delete with a hard-coded allowlist** (`reference_mcp-cannot-delete-sheets-folders`).
> All trace back to entries in `docs/tech_debt.md` (referenced by title, not line — lines drift).

## Suggested sequencing

1. **Security, pre-Customer-1** (§A) — these are real exposure, do first.
2. **Smartsheet housekeeping** (§B) — low-risk tidy; verify-live-first.
3. **Box / config provisioning** (§C) and **Cloudflare / deploy confirms** (§D).
4. **M365 / Anthropic / misc** (§E) and the **docs backfill** (§F) as time permits.

---

## §A — Security (pre-Customer-1)

- [ ] **Cloudflare WAF rate-limit on `/api/login` + `/api/*`.** No rate limiting exists on
  the portal auth/API surface today — a real brute-force / CPU-amplification gap. Add a
  Cloudflare rate-limit rule (dashboard or Terraform) at cutover. *(tech_debt: "Safety Portal —
  no rate limiting on `/api/login` or `/api/*`")*
- [ ] **Picklist Restrict-to-dropdown hardening (pre-Customer-1).** Run the Restrict-to-dropdown
  UI toggles + the audit-doc checklist, then `audit_picklist_drift.py --update-audit-doc`. UI-only
  constraints Smartsheet doesn't expose via API. *(tech_debt: "Smartsheet UI-only constraints"
  + "Picklist-hardening pre-Customer-1")*
- [ ] **ITS_Trusted_Contacts cutover** *(do this when Email Triage work actually begins — see
  [Not on this list](#not-on-this-list))*: run the seed migrations, add the 3 picklist values in
  the UI, delete the legacy `ITS_Config` allowlist row, then a live smoke. The allowlist is
  currently dormant (`SHEET_TRUSTED_CONTACTS = 0`), so there is **no urgency** until an
  email-facing consumer exists. *(tech_debt: "Fallback path removal after ITS_Config cutover")*

## §B — Smartsheet housekeeping (VERIFY FIRST — some may be 404/done)

- [ ] **Delete the orphan per-job folder** "I don't know project name Montgomery" (empty, from the
  JOB-000013 50-char-cap incident) in the `ITS — Safety Portal` workspace. Harmless but stray.
  *(tech_debt: "Orphan per-job Smartsheet folder from the JOB-000013 50-char-cap incident")*
- [ ] **Verify + delete the 5 duplicate `ITS_Errors` sheets** in System / 02 — Logs. Confirm each id
  is live first (several may already be 404), name-guard the delete script. *(tech_debt: "5-duplicate
  ITS_Errors sheets in System/02-Logs")*
- [ ] **Delete the stale retired `intake_poll` row** in `ITS_Daemon_Health` (the daemon is retired;
  its health row lingers). *(tech_debt: "ITS_Daemon_Health sheet observability drift")*
- [ ] **Fill the `ITS_Active_Jobs` Address column** for the seeded rows (office-PM data entry).
  *(tech_debt: "ITS_Active_Jobs Address cells blank — office PM fill required")*
- [ ] **Confirm the `ITS_Active_Jobs` AUTO_NUMBER `Job ID` column exists**, then close the entry.
  *(tech_debt: "ITS_Active_Jobs AUTO_NUMBER `Job ID` column — manual operator UI step pending")*
  — **[SUPERSEDED same-day by P2.5 Slice 6: `Job ID` is now plain TEXT — the portal assigns
  `JOB-######` (`job_counter`, migration 0022) and the mirror writes it; retyped per that
  cutover, confirmed 2026-07-23.]**
- [ ] **Build the "New Job" Smartsheet form** on `ITS_Active_Jobs` (~15 min, UI-only). *(tech_debt:
  "New Job Smartsheet form on ITS_Active_Jobs")* — **NOTE:** confirm this is still wanted; the
  **job-tracker pivot** (Stage-2 plan) moves authoritative job creation into the **portal Job
  Tracker**, which may supersede a Smartsheet entry form. Coordinate with the Phase-2 session before building.
- [ ] *(Optional, low)* **Reorder `ITS_Active_Jobs` columns** for readability — cosmetic, not
  load-bearing. *(tech_debt: "ITS_Active_Jobs column order cosmetically scrambled")*

## §C — Box / config provisioning

- [ ] **Seed `system.box_smoke_folder_id` in `ITS_Config`** + create the corresponding Box folder
  (only needed for the opt-in `--write-test` Box smoke). *(tech_debt: "Seed `system.box_smoke_folder_id`")*
- [ ] **Phase-1.5: provision the dedicated ITS Box user account** + re-run `scripts/setup_box_oauth.py`
  (run headless / use `security -w VALUE` to avoid the TTY trap). *(tech_debt: "Phase 1.5 — provision
  dedicated ITS Box user account, re-auth")*
- [ ] **Owner decision: confirm the `canonical_job_path()` write format** before the first consumer
  locks it in. *(tech_debt: "Confirm `canonical_job_path()` format with owner")*

## §D — Cloudflare / deploy confirmations (then docs-close)

> These three docs-closes were **deliberately not flipped** in the #363 sweep because they depend on
> live Cloudflare/D1 deploy state I can't grep. Confirm against the live deploy, then mark each CLOSED
> in `docs/tech_debt.md`.

- [ ] **Confirm the live Worker deploy + provisioning** (HMAC secret, internal token, D1, wrangler IDs)
  — the portal was end-to-end mirror-validated 2026-06-08, so this is almost certainly done; confirm +
  close. *(tech_debt: "Safety Portal — deploy + provisioning deferred"; "Safety Portal Phase 5 — deploy
  prerequisites")*
- [ ] **Confirm the Pages-vs-Workers Static Assets topology** is settled (live `wrangler.jsonc` uses
  Workers Static Assets + `ASSETS.fetch`) → close. *(tech_debt: "Safety Portal — Pages-vs-Workers Static
  Assets topology TBD")*
- [ ] **Confirm remote D1 migration 0012 + the Worker deploy carry the PR-5 `pdf_requests` table**, then
  close. `git -C ~/its pull origin main` to latest BEFORE any `wrangler d1 migrations list/apply`
  (stale-checkout lockout class). *(tech_debt: "PR-5 Worker + migration 0012 NOT yet deployed to live mirror")*
- [ ] **Run the `weekly_send` upload-session live-Graph integration smoke** pre-Customer-1 (live token +
  a throwaway >2.5 MB PDF round-trip) to confirm the inline-vs-upload-session boundary against the real
  tenant. *(tech_debt: "weekly_send upload-session — live-Graph integration smoke")*

## §E — M365 / Anthropic / misc

- [ ] **Calendar a 2026-08-15 check** for the PowerShell macOS Gatekeeper deprecation (2026-09-01); ready
  the Azure Cloud Shell runbook variant. *(tech_debt: "PowerShell macOS Gatekeeper deprecation 2026-09-01")*
- [ ] **Archive the stale Anthropic Service Account** `svac_…SR7vDMJ` on the next Console visit (the live
  key is already removed). *(tech_debt: "Stale Anthropic Service Account `svac_…SR7vDMJ` for archival")*
- [ ] **Finish the safety email-intake retirement**: unload the launchd job
  (`scripts/uninstall_safety_intake_daemon.sh`) + delete the `intake_poll` tombstone after confirming no
  orphan plist on the production Mac. *(tech_debt: "Safety email-intake retire — operator-manual")*

## §F — Docs backfill

- [ ] **Backfill the exec session log** for the 2026-06-17→18 arc (#292 / #294 / #295 + the D1 clean-slate)
  — invoke `session-log-writer` with that context. *(tech_debt: "Exec session log gap — 2026-06-17 to
  2026-06-18 arc still missing")*

---

## Not on this list

- **Package A — allowlist-drift Layer-1 validation (PR #364, closed unmerged).** Premature: it hardened
  the dormant `ITS_Trusted_Contacts` read path (sheet not wired, `intake_poll` retired, Email Triage not
  built). The drafted+reviewed work is preserved on branch `feat/allowlist-drift-layer1` — land it when
  Email Triage exists and the allowlist is populated. See memory `feedback_dont-harden-dormant-subsystems`.
- **The 37 Phase-2-owned items** (heartbeat extraction, watchdog hardening, Worker auth/capability-gate,
  the permission-model / Manager-tier plumbing, field-ops SoR integration, …) — owned by the in-flight
  Phase-2 build; do **not** touch independently. They are tracked in `docs/tech_debt.md` against their
  owning slice.
