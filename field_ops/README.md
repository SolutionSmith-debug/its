# field_ops — Field Operations workstream (Mac-side)

The Mac-side half of the **Field-Ops Expansion of the Evergreen Safety Portal**. The
portal SPA + Cloudflare Worker (under `safety_portal/`) are the editor + send-free D1
store for operational data (jobs, clients, crew, tasks, time, equipment, materials,
checklist instances); this package mirrors that data **up to Smartsheet** as the
operator-visible system of record.

## Data-residency model (plan decision)
**D1 primary + Smartsheet mirror.** The Worker writes operational data to D1 (it never
transmits externally — only `c.env.ASSETS.fetch`). `fieldops_sync.py` mirrors D1 → Smartsheet
and reconciles the **portal-job-create → `ITS_Active_Jobs` inversion** (provisional
`PJOB-<uuid8>` → canonical `JOB-####` write-back), so the existing safety weekly-send
pipeline keeps resolving recipients from `ITS_Active_Jobs`.

## Contents
- `fieldops_sync.py` — the mirror daemon. **P0: skeleton** (kill-switch + error-log gating,
  `field_ops.fieldops_sync.sync_enabled` ITS_Config gate, no business logic). Mirror +
  inversion + heartbeat land in **P2**.
- `data/material_catalog_draft.json` — **draft** material catalog (36 deduped types) auto-extracted
  from the 80 project datasheets by a Pit Wall agent (P3). **Pending operator review** before it
  seeds the `material_catalog` table.

## Invariants
- AI-free + customer-send-free (FM Invariant 1): no `anthropic*`, no `graph_client.send_mail`
  / `resend` / `smtplib` / `email.mime`. Smartsheet writes are system-of-record mirroring.
- Bearer privilege separation: `PORTAL_FIELDOPS_API_TOKEN` (distinct from the portal_poll +
  admin tokens) gates `/api/internal/fieldops/*` (endpoints land in P2).
- Capability model: migration `0013_add_roles_capabilities.sql` (submitter = field PM, admin
  = office), resolved fail-closed per request in `safety_portal/worker/auth.ts::resolveCapabilities`.

See the full program plan for the phased briefs (P0–P5) and the risk register.
