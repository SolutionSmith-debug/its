# CLAUDE.md — Project Context for Claude Code

You are working inside the execution layer of **ITS — Integrated Technical System**, a
Claude-powered computer employee for Evergreen Renewables (12-person construction/renewables
firm). The planning layer of this project lives in a separate Claude.ai project; this repo
implements what is decided there.

This is a **friend-favor build** — the system is being built for the owner of Evergreen, who
is a close friend of the maintainer. That raises the bar for honest, intellectually rigorous
work, not lowers it.

## Architectural model

Two layers, deliberately separated:

1. **Planning & Foundation** (lives in Claude.ai project, not in this repo). Mission files,
   architectural decisions, owner-facing artifacts, prompt designs, schemas.
2. **Execution** (this repo). Claude Code scripts on a MacBook, triggered by launchd, Mail.app
   rules, and Shortcuts. Reads/writes Smartsheet (structured data), Box (documents), Outlook
   (communication) via APIs. Calls Anthropic API for reasoning steps.

Smartsheet, Box, Outlook are systems of record — unchanged by ITS.

## Operational conventions — load-bearing

Every workstream script MUST follow these. Deviations need to be raised in the planning project
first, not invented locally.

- **Kill switch first.** Call `shared.kill_switch.check_system_state()` (or use
  `@require_active`) at script entry. If state is PAUSED or MAINTENANCE, exit cleanly. The
  switch lives in a Smartsheet `ITS_Config` sheet; anyone with sheet access can flip it.
- **Error log decorator.** Wrap every script's main function in `@its_error_log(script_name=...)`.
  Catches unhandled exceptions, writes to `ITS_Errors` sheet, surfaces CRITICAL via email + SMS.
- **Confidence scoring on extractions.** Anthropic API calls that extract structured data
  produce a confidence score. Below threshold → routes to `ITS_Review_Queue`, not silent success.
- **Human review before customer-facing output.** Drafts go to `ITS_Review_Queue` with an SLA;
  reviewer approves before send. Especially for Safety Reports (first 30–60 days), RFQs (every
  time, no exceptions), Subcontracts (every time).
- **Credentials from macOS Keychain.** Never env files, never committed. Use `shared.keychain.get_secret(name)`.
- **Schemas in `schemas/`. Prompts in `prompts/`.** Both version-controlled. JSON schemas have
  a `version` field; scripts reject responses on schema mismatch.

## What's stubbed vs. real (as of scaffold commit)

| Module | State | Notes |
|--------|-------|-------|
| `shared/keychain.py` | Working | macOS-only; uses `security` CLI. |
| `shared/error_log.py` | Local-file only | Smartsheet `ITS_Errors` write is TODO — gated on Smartsheet creds. |
| `shared/kill_switch.py` | Stub | Returns ACTIVE if config sheet unreachable (fail-open). Wire after Smartsheet creds + sheet ID land. |
| `shared/anthropic_client.py` | Working | Reads `ITS_ANTHROPIC_KEY` from Keychain. Lazy-loads. |
| `shared/smartsheet_client.py` | Stub | Awaits credentials and sheet IDs. |
| `shared/box_client.py` | Stub | Awaits credentials. |
| `shared/graph_client.py` | Stub | Awaits credentials. |
| `shared/review_queue.py` | Stub | Awaits Smartsheet + sheet schema. |
| `scripts/watchdog.py` | Stub | Awaits Smartsheet + alert path (Graph creds for email). |
| `safety_reports/*` | Stub | Blocked on 9 owner decisions — see planning project. |

## Adding a new workstream

1. Draft a mission file in the planning Claude.ai project. Resolve open questions with owner.
2. Draft an engineering brief in the planning project.
3. Create `<workstream>/` directory here. Mirror the `safety_reports/` shape.
4. Schemas go in `schemas/`. Prompts go in `prompts/`. Reuse `shared/` helpers.
5. launchd plist or Mail.app rule lives outside this repo (system-level config); document
   the trigger config in the workstream's brief.

## Model selection

Default for reasoning calls: `claude-sonnet-4-6`. Use `claude-haiku-4-5-20251001` for
high-volume classification (Email Triage). Use `claude-opus-4-7` only where reasoning depth
genuinely justifies the cost (rare in this project).

Revisit model selection quarterly — Anthropic ships new models on a roughly six-month cadence.

## What NOT to do

- Don't add cloud-server execution. The architecture is explicitly local-first on MacBook.
- Don't add observability platforms (Helicone, Langfuse, Sentry). Anthropic console + local
  logs + watchdog is the right level for this scale.
- Don't add a vector store before Phase 4. Premature.
- Don't expose SSH or any service to the public internet. Tailscale-only.
- Don't auto-approve at low confidence. Always route ambiguity to human review.
- Don't reproduce copyrighted material from any Box document or web fetch.

## Useful references in this repo

- `shared/` — start here when implementing a new workstream.
- `tests/test_helpers.py` — the test pattern.
- `scripts/watchdog.py` — the daily watchdog skeleton.
- `safety_reports/intake.py` — the canonical workstream-intake shape.

If something here contradicts the planning project's canonical docs (Foundation Mission v3,
Operational Standards v4, etc.), the planning project wins. Flag the inconsistency.
