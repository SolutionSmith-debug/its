# ITS — Integrated Technical System (Execution Layer)

[![ci](https://github.com/SolutionSmith-debug/its/actions/workflows/ci.yml/badge.svg)](https://github.com/SolutionSmith-debug/its/actions/workflows/ci.yml)

This is the execution layer of ITS — a Claude-powered computer employee being built for
construction and renewables firms. Productized partnership with Evergreen Renewables as
Customer 0. Solution Smith owns all IP.

ITS runs as Claude Code scripts on a MacBook, triggered by Apple-native automation primitives
(launchd, Mail.app rules, Shortcuts).

The **planning layer** lives in a Claude.ai project ("ITS Foundation & Planning"). This repo
implements what is decided there.

## Quick orientation

- `shared/` — cross-cutting helpers every workstream uses (kill switch, error log, API
  clients, untrusted-content tagging, anomaly logging, sender quarantine).
- `schemas/` — JSON schemas for Anthropic tool-use / structured output calls.
- `prompts/` — prompt files, version-controlled in markdown.
- `scripts/` — top-level scheduled scripts (e.g., watchdog) and launchd plists.
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

Everything below is normative. See `CLAUDE.md` for the conversational version Claude Code
reads on every launch. See the Claude.ai planning project for the full canonical
specifications (Foundation Mission v5, Operational Standards v7).

- **Kill switch first.** Every script's entry point starts with `check_system_state()`.
- **Error log decorator.** Every script's main function is wrapped in `@its_error_log`.
- **Credentials from Keychain.** Never in env files, never in git. See `shared/keychain.py`.
- **External Send Gate (permanent).** No external transmission without explicit human
  approval. Two-process model: generation scripts have no send capability; send scripts have
  no AI step.
- **Adversarial Input Handling.** External content is untrusted data. Wrap inbound content
  with `shared.untrusted_content.wrap()`; validate every extraction with
  `shared.anomaly_logger.check()`.
- **Production-quality, defensively-built.** Appropriate for 10–50 person construction firm
  scale. Failures must be observable, recoverable, and never silent.

## Status

| Phase | State |
|-------|-------|
| 0 — Scaffold | ✓ committed; framework defaults landing |
| 1 — Safety Reports | sandbox build active; 5 of 9 owner decisions resolved; 4 deferred-now-resolvable, Box sandbox data uploaded 2026-05-14, Smartsheet sandbox provisioning in progress, M365 Graph mail wired 2026-05-17 |
| 1.5 — Hardware Handover (Florida → Customer) | scheduled after Phase 1 stable |
| 1.6 — Multi-Tenancy Framework | scheduled before Customer 2 |
| 2 — POs / Subcontracts | not started |
| 3 — Email Triage / AI Employee | not started |
| 4 — Renewables-specific surfaces | not started |
