# ITS — Integrated Technical System (Execution Layer)

This is the execution layer of ITS — a Claude-powered computer employee for Evergreen Renewables.
It runs as Claude Code scripts on a MacBook, triggered by Apple-native automation primitives
(launchd, Mail.app rules, Shortcuts).

The **planning layer** lives in a Claude.ai project ("ITS Foundation & Planning"). This repo
implements what is decided there.

## Quick orientation

- `shared/` — cross-cutting helpers every workstream uses (kill switch, error log, API clients).
- `schemas/` — JSON schemas for Anthropic tool-use / structured output calls.
- `prompts/` — prompt files, version-controlled in markdown.
- `scripts/` — top-level scheduled scripts (e.g., watchdog).
- `safety_reports/` — Phase 1 active workstream.
- `logs/` — local backup of error log (also written to Smartsheet ITS_Errors).
- `tests/` — pytest suite (run with `pytest`).

## First-time setup

```bash
# Python venv (project uses Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Sanity check
pytest -q
```

## Operational conventions

Everything below is normative. See `CLAUDE.md` for the conversational version that
Claude Code sees on every launch. See the Claude.ai planning project for the full
canonical specifications (Foundation Mission, Operational Standards, etc.).

- **Kill switch first.** Every script's entry point starts with `check_system_state()`.
- **Error log decorator.** Every script's main function is wrapped in `@its_error_log`.
- **Credentials from Keychain.** Never in env files, never in git. See `shared/keychain.py`.
- **Human review before customer-facing output.** Drafts go to ITS_Review_Queue.
- **Best-effort reliability.** This is friend-favor scale; no 24/7 SLA.

## Status

| Phase | State |
|-------|-------|
| 0 — Scaffold | ✓ committed (this commit) |
| 1 — Safety Reports | ⏳ blocked on 9 owner decisions |
| 1.5 — Hardware Handover | scheduled after Phase 1 stable |
| 2 — POs / Subcontracts | not started |
| 3 — Email Triage / AI Employee | not started |
| 4 — Renewables-specific | not started |
| 4-deferred-indefinitely — Takeoffs | not started |
