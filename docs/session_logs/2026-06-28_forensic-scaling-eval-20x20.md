---
type: session_log
date: 2026-06-28
status: closed
related_prs: []
workstream: null
tags: [scaling, forensic-audit, tier-a, cost-model, documentation-program, ultraplan]
---

# Session — Forensic scaling evaluation to 20 jobs / 20+ daily users (read-only; no commits)

A read-only forensic evaluation of ITS for a planned ramp to 20+ active jobs and 20+ daily **photo-heavy** portal users this quarter, plus first-draft Tier-A implementation specs. **No code was changed** — this session produced diagnosis + logged plans only.

## Purpose

Answer four questions at 20×20 (photo-heavy): which edge cases do we regularly hit, where are the bottlenecks, how do we prepare/avoid them, and what does it cost — then log it durably and capture the session.

## Pre-flight findings (firsthand reads that reframed the audit)

- `portal_poll` already holds an fcntl overlap lock (`portal_poll.py:616`) — a slow intake cycle **skips** the next launchd fire (backlog), it does not corrupt. Throughput risk reframed from "concurrency corruption" to "latency."
- F22 `verify_approval` runs fresh per-row immediately before send and treats an un-check as a benign WARN → the multi-approver "silent unsolicited send" race is **LOW**, not CRITICAL.
- `weekly_generate` plist sets no `ExitTimeOut` → launchd does **not** kill the Friday compile at >1h (the original CRITICAL was overstated); real risk is wall-clock + memory.
- The Worker photo bounds are mirrored **four ways** (`worker/index.ts` + `photo_screen.py` + `PhotoField.tsx` + `publishValidation.ts`) as §34 defense-in-depth — A7 must keep them synced (this was the A7 doctrine flag).

## Method (no code; multi-agent, read-only)

- `improve`-skill audit discipline run as a **55-agent Claude Workflow** (10 scaling-dimension investigators → per-finding adversarial verifiers → real-pricing cost models → synthesis) + a multi-approver-concurrency agent + a **15-agent Tier-A spec-authoring Workflow** (author → adversarial review → assemble) + ~20 firsthand code reads for vetting.
- Operator ran the plan through **Ultraplan** (remote Claude-Code-web refinement): first cloud attempt failed (`error_during_execution`); retry produced + approved a plan. Approval returned locally; logging executed in this session.

## Artifacts produced

- `docs/reports/2026-06-28_forensic-scaling-eval-20x20.md` — full eval (Part I) + 7 self-contained Tier-A specs (Part II) + the A7 doctrine resolution (Part III). Mirror of the plan-mode plan file.
- `docs/tech_debt.md` — new entry "ITS scaling hardening — 20-job/20-user Tier-A roadmap [OPEN 2026-06-28]".
- Auto-memory: `project_scaling-eval-20x20`, `feedback_documentation-program`, `reference_ultraplan-flow`.

## Findings headline

98 findings (7 CRITICAL / 33 HIGH / 50 MEDIUM / 8 LOW; **39 silent**). Binding constraints: (1) Smartsheet week-sheet proliferation (~1,040/yr → tier upgrade + possible hard cap, **UNVERIFIED**); (2) single-host SPOF (no daemon auto-start after reboot, no SDK network timeout, Box token-refresh race); (3) photo-heavy filing throughput; (4) Smartsheet 5,000-row silent drop on ITS_Review_Queue/ITS_Errors; (5) Friday serial compile (wall-clock + memory). Cost ≈ **$610–$2,410/mo hard ≈ the Smartsheet tier decision** + ~$8 Cloudflare; Anthropic ~$0 (portal deterministic); labor distributed across Evergreen staff.

## Decisions made during session (with rejected alternatives)

- **Logged the eval in the plan file in place, then mirrored to `docs/reports/`** — rejected materializing per-finding `plans/NNN-*.md` (heavier; the operator wanted a single explorable artifact first).
- **Demoted the human-labor ceiling** as the #1 bottleneck — operator confirmed approvals are distributed across existing Evergreen staff; rejected the audit's stale "operator labor = #1" framing.
- **Elevated the documentation program to P1** (A8) — operator directive that every ITS function gets a PDF guide/manual/troubleshooting tree; it is the enabling precondition for the distributed-operator model, not a nicety.
- **Used Claude Workflow fan-out, not the local Pit Wall fleet** — operator's call ("use it yourself") for the reasoning-heavy audit of a large codebase.

## Verification

N/A — read-only session; no commits, no four-part verify. Tier-A specs are first-draft (`needs-revision`) — refine before execution.

## Open items handed off

- **A1 (read-only, do first):** verify the real Smartsheet per-workspace sheet-count cap — gates the $600-vs-$2,400 tier decision + the cutover timing. `smartsheet_client` needs a list/count-sheets method (none today).
- The A8 documentation program (capability manifest + `ITS_Config` data-dictionary PDF) when ready.
- These docs are written to the working tree **uncommitted** — commit on a branch / PR if git history is wanted.

## What was NOT touched

No source code, schemas, prompts, launchd plists, Smartsheet/Box/D1 live data, or secrets. No commits, no deploys. The live daemon tree (`~/its`) source is unchanged.

## Lessons captured to memory

- `project_scaling-eval-20x20` — the eval, binding constraints, cost, vetting corrections, where logged.
- `feedback_documentation-program` — the every-ITS-function PDF-guide directive, why it's load-bearing, and the doc-currency risk.
- `reference_ultraplan-flow` — the Ultraplan remote-refinement workflow + that local execution is also available.
