# ITS — Integrated Technical System (Execution Layer)

[![ci](https://github.com/SolutionSmith-debug/its/actions/workflows/ci.yml/badge.svg)](https://github.com/SolutionSmith-debug/its/actions/workflows/ci.yml)

This is the execution layer of ITS — a Claude-powered computer employee being built for
construction and renewables firms. ITS is a **white-glove custom-development practice**:
each customer gets a fully-customized build forked from the ITS blueprint and maintained in
their own private repository. Evergreen Renewables is **Customer 0** — the first deployment
and design partner. This repo is Evergreen-specific; future customer forks live in their
own repos.

ITS runs as Claude Code scripts on a MacBook, triggered by launchd-driven polling daemons
(canonical pattern per Op Stds v16 §31). Shortcuts for manual operator-triggered jobs.
Mail.app rules deprecated.

The **planning layer** lives in a Claude.ai project ("ITS Foundation & Planning"). This repo
implements what is decided there.

## Quick orientation

- `shared/` — cross-cutting helpers every workstream uses — kill switch, error log +
  triple-fire CRITICAL alert path (Smartsheet + Resend + Sentry), API clients (anthropic, Box,
  Graph, Smartsheet, Sentry, Resend), Keychain wrapper, untrusted-content tagging, anomaly
  logging, sender quarantine, review queue, alert-routing dedupe, cross-sheet picklist sync,
  scheduling (holidays + reviewer chain + PTO), bootstrap sheet IDs, default constants.
- `schemas/` — JSON schemas for Anthropic tool-use / structured output calls.
- `prompts/` — prompt files, version-controlled in markdown.
- `scripts/` — top-level scheduled scripts (watchdog daily; picklist sync hourly) and
  launchd plists. See `docs/references/picklist_sync.md` for the picklist sync runbook.
- `safety_reports/` — Phase 1 active workstream.
- `logs/` — local backup of error log (also written to Smartsheet ITS_Errors).
- `tests/` — pytest suite (run with `pytest`; integration tests gated behind
  `pytest -m integration`, require live Smartsheet sandbox credentials).

## First-time setup

```bash
# Python venv (project uses Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Sanity check
pytest -q
```

## Re-installing after dependency changes

When pulling a PR that adds or updates dependencies in `pyproject.toml`,
re-run the install step before running scripts. A `ModuleNotFoundError:
No module named '<package>'` after a `git pull` typically means a new
dependency was added and the install needs re-running.

```bash
# venv path (matches the First-time setup default above)
source .venv/bin/activate && pip install -e ".[dev]"

# system Python path
pip3 install -e ".[dev]" --break-system-packages
```

PR #39 (boxsdk addition, 2026-05-20) was the canonical case that surfaced this.

## Operational conventions

Everything below is normative. See `CLAUDE.md` for the conversational version Claude Code
reads on every launch. See the Claude.ai planning project for the full canonical
specifications (Foundation Mission v11, Operational Standards v16).

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

## Documentation

The `docs/` subtree organizes everything that isn't code:

- [`docs/session_logs/`](docs/session_logs/) — durable narrative records of cc sessions.
- [`docs/operations/`](docs/operations/) — runbook procedures (PR merge discipline, doc conventions).
- [`docs/audits/`](docs/audits/) — structured findings against a closed scope.
- [`docs/reports/`](docs/reports/) — one-shot quantitative or qualitative snapshots.
- [`docs/references/`](docs/references/) — evergreen explanatory docs.
- [`docs/tech_debt.md`](docs/tech_debt.md) — accumulator of deferred items.

Filename, frontmatter, and section conventions across every doc type are defined in
[`docs/operations/doc_conventions.md`](docs/operations/doc_conventions.md). Existing docs are
grandfathered (lazy retrofit policy); new docs MUST conform. CI runs `scripts/lint_doc_conventions.py`
and `scripts/regen_doc_indexes.py --check` warn-only during the retrofit window.

## Status

| Phase | State |
|-------|-------|
| 0 — Scaffold | ✓ shipped; 23-PR push 2026-05-18/19 wired the remaining shared/* modules (review_queue, quarantine, resend, sentry, error_log Smartsheet write, kill_switch refactor). Tests 137→781 (current; +644 from baseline). Triple-fire CRITICAL operational; mypy=0 enforced in CI; ruff clean. Resend-leg dedupe shipped 2026-05-21 (`shared/alert_dedupe.py` + watchdog Check G summary sweep + MAINTENANCE defer per PR #52). Polling-daemon doctrine codified Op Stds v16 §31 (watchdog + picklist_sync + safety-intake all launchd-driven Python pollers). |
| 1 — Safety Reports + parallel workstreams | sandbox build active; 5 of 9 owner decisions resolved; Box sandbox uploaded 2026-05-14; Smartsheet system + human-review workspaces fully provisioned 2026-05-17; M365 Graph mail wired 2026-05-17. Cross-sheet picklist sync foundation shipped 2026-05-21 (`shared/picklist_sync.py` + `Picklist_Sync_Config` sheet + hourly cron; activates at form-and-clone cascade time). R3 cycle complete end-to-end (PRs #57/#59/#60 intake + #63 weekly_generate + #65 retry fix + #68 weekly_send). Box 1111B canonical blueprint materialized PR #70, projects re-cloned post-cutover (zero-padded numeric naming convention; legacy 1111A clones archived under `ITS DATA / 99. Legacy 1111A Clones`). Phase 1.4 security cluster: ITS_Trusted_Contacts shipped PR #72; picklist-hardening + attachment screening remaining per V&R v9. |
| 1.5 — Combined Cutover + Hardware Handover (Florida → California) | scheduled after Phase 1 stable. 30-day clean-sandbox-operation gate per V&R v9. |
| 1.6 — Blueprint Generalization | pre-Customer-2 pass. Extracts Customer-0-specific assumptions from shared/* so a Customer 2 fork-and-customize cycle is mechanical. (Renamed from "Multi-Tenancy Framework" per the white-glove business-model commitment.) |
| 2 — POs / Subcontracts | not started |
| 3 — Email Triage / AI Employee | not started |
| 4 — Renewables-specific surfaces | not started |
