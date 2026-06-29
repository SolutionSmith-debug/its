# ITS — Repository Overview

**Repository:** `https://github.com/SolutionSmith-debug/its`  
**Last sync:** 2026-06-12 (PR #281)  
**Primary purpose:** Execution layer for Evergreen Renewables' Integrated Technical System (ITS). Implements safety-reporting automation, Smartsheet Box migration tooling, and the Safety Portal (Cloudflare Workers + React SPA).

---

## Architecture Summary

### Stack
| Component | Tech | Scope |
|-----------|------|-------|
| **Backend** | Python 3.12+ | `shared/` (clients, utilities), `safety_reports/` (intake, weekly_generate, weekly_send, publish_daemon) |
| **Frontend** | React 19 + TypeScript | `safety_portal/` — Cloudflare-hosted SPA (Form Fill, Form Request, Admin Dashboard) |
| **Workers** | Hono (Cloudflare Workers) | `safety_portal/worker/` — auth, submit queue, publish requests, prune job |
| **Database** | Cloudflare D1 (SQLite) | User sessions, submissions, publish requests, PDF cache |
| **Integrations** | Smartsheet SDK v3.9.x, Box SDK v3.x, MS Graph API, Anthropic LLM API | Smartsheet (workstreams), Box (attachment archival), Microsoft Graph (email/notifications) |

### Key Workstreams (6 canonical)
1. `safety_reports` — Intake polling daemon, weekly report generation (Sat→Fri packets), trusted contacts routing, WSR review queue
2. `safety_portal` — Field PM portal: form filling, photo upload (§34 screening + PDF embed), form request/browse/download, admin dashboard
3. `box` — 1111A/1111B Box folder migrations, parse_job_v3 for job extraction, reconcile_box_listings
4. `ci` — pytest (48 test modules, integration-skip default), mypy zero-baseline, ruff linting, CI merge discipline
5. `security` — Picklist hardening (Smartsheet column drift detection), header forgery mitigation, attachment screening (re-encode + ClamAV optional)
6. `infrastructure` — Kill switch (§1 pause), watchdog daemon health, heartbeat, alert dedupe

### Deployment Model
- **Python backend:** `its` package installed in venv; scheduled via macOS `launchd` (scripts in `/Users/sethsmith/its/logs/launchd/`) or containerized equivalent
- **Frontend + Workers:** Cloudflare Pages for SPA static assets + Wrangler-deployed D1-bound Workers
- **Secrets:** macOS Keychain (`shared/keychain.py` wrapper) — no `.env` files in repo

---

## Recent Changes (Last 20 Commits)

| Commit | Type | Workstream | Description |
|--------|------|------------|-------------|
| `44370e1` | chore | safety_portal | Publish create: photo-test-v1 form request (#281) |
| `ff00308` | feat | safety_portal | Form Request month-year + form-type filter (#280) |
| `aaa161f` | fix | safety_portal | Editor validation accepts photo input — derive INPUTS from FIELD_INPUTS (#279) |
| `eddea47` | docs | all | Session close 2026-06-12 — PR-5/PR-3 session log + tech-debt + README drift fix (#278) |
| `e3543c9` | feat | safety-reports | Graph upload-session for large weekly packets + ADR/§43/tech-debt (#275) |
| `13ef2bc` | feat | safety_portal | Form Request — in-portal filed-form browse + requester-bound PDF download (#276) |
| `814aec6` | feat | safety_portal | Part A: request-driven canonical PDF download (receipt + cache) (#274) |
| `01e9d13` | feat | safety_portal | Add Crane & Rigging Critical Lift Plan form (PR-4 Part B) (#273) |
| `5a979e2` | feat | safety_portal | Photo upload PR-2 — Mac-side §34 screening + PDF embed + Box originals (#272) |
| `fadd53f` | feat | safety_portal | Photo upload PR-1 — photo input type + Worker bounds gate + SPA capture (D1-inline) (#271) |

---

## Testing Discipline

```bash
# Run unit tests (integration skipped by default — requires live credentials in Keychain)
pytest -q

# Run integration tests against Smartsheet sandbox (requires ITS_SMARTSHEET_TOKEN in Keychain)
pytest -m integration

# Type checking (zero baseline enforced)
mypy .

# Lint (ruff)
ruff check .
```

**Test coverage:** 48 test modules under `tests/`, default-skip integration. CI gate on merge enforces `pytest` + `mypy` + `ruff` + main-branch CI on the merge SHA (see `docs/operations/pr_merge_discipline.md`).

---

## Key Files & Directories

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Python deps, tooling config (ruff, pytest, mypy) |
| `safety_portal/package.json` | Node deps + Wrangler/Vite scripts for SPA + Workers |
| `shared/` | Client wrappers (Box, Smartsheet, Graph, Anthropic), shared utilities (state_io, scheduling, kill_switch) |
| `safety_reports/` | Workstream entrypoints: intake_poll.py, weekly_generate.py, weekly_send.py, publish_daemon.py |
| `safety_portal/src/` | React components (FormRenderer, FormEditor, AdminTabs), pages (FormFillPage, FormRequestPage) |
| `docs/session_logs/` | Dated narrative records of CC sessions (one per merge commit typically) |
| `docs/audits/` | Structured findings against closed scopes (picklist hardening, doctrine drift) |
| `logs/migrations/` | Box 1111B migration reports + folder ID mappings |

---

## Doctrine Version (Critical Invariant)

**Current doctrine:** Operational Standards v19 / Foundation Mission v11 / Handover Plan v9 (last verified against blueprint `0e85a1a`, 2026-06-07).

Citations in code/docs should read: `"Op Stds v19"`, `"FM v11"`, `"Handover Plan v9"`.

Drift signal regexes are in `docs/doctrine_manifest.yaml`; the `doc-reconciliation-auditor` agent runs a recurring check.

---

## Quick Start (Local Development)

```bash
# Python backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"  # from pyproject.toml root

# Safety Portal (SPA + Workers)
cd safety_portal
npm ci
npm run dev          # Vite dev server with Cloudflare Workers hot-reload
```

**Secrets setup:** Add credentials to macOS Keychain via `shared/keychain.py` helper scripts (no `.env` files).

---

## Known Technical Debt

See `docs/tech_debt.md` for the full accumulator. Notable items:

- **Smartsheet SDK pinning:** `<3.10.0` due to a breaking change in >3.9.0 that removed `smartsheet.exceptions` (revised 2026-06-08)
- **Box SDK migration:** Legacy 3.x vs Gen API 10.x — deferring migration pending migration decision (Gen API uses `box_sdk_gen`, different surface)
- **Doctrine cross-repo drift:** No automated checker against blueprint; relies on manual session-close checks + agent audits

---

## Contact / Ownership

- **Repository owner:** `@solutionsmith`
- **Doctrine sync:** All agents in `.claude/agents/` are reconciled to Op Stds v19 (see `docs/doctrine_manifest.yaml`)
