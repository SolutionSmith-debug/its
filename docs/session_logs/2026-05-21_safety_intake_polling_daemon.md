# 2026-05-21 — Safety intake polling-daemon trigger

PR: [#59](https://github.com/SolutionSmith-debug/its/pull/59) — squash-merged at 2026-05-21T19:00:05Z. Merge commit `f1e724f7415c7d122af94c931369d077efb97439`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Replaces the Mail.app rule trigger for `safety_reports/intake.py` with a launchd-driven Python polling daemon (`safety_reports/intake_poll.py`) reading directly from Microsoft Graph. Pipeline logic in intake.py is unchanged — only the invocation surface. Trigger configuration (poll interval, mailbox, per-workstream kill switch) lives in ITS_Config so the operator can adjust from Smartsheet without touching Mac-side config.

## What shipped

- **`safety_reports/intake.py`** — refactored. `process_message(message_id) -> ProcessResult` is now the public per-message entrypoint; `main()` becomes a thin CLI wrapper for manual reruns (`python -m safety_reports.intake <message_id>`). `_fetch_message_via_graph` replaces .eml parsing; `mark_read` (called by poller) replaces the prior `.eml → .eml.processed` rename watermark. The .eml parsing utilities (`parse_email_file`, `_extract_body_text`, `_iter_attachments`, `mark_email_processed`) are deleted since they're no longer reachable.
- **`safety_reports/intake_poll.py`** — new. `poll_once()` runs one cycle per launchd interval. fcntl lock at `~/its/state/safety_intake.lock` (skip-if-held), per-workstream `polling_enabled` ITS_Config gate, 1000-entry FIFO seen-set at `~/its/state/safety_intake_processed.json`, heartbeat at `~/its/state/safety_intake_heartbeat.txt`. `mark_read` called only on non-error statuses (processed / review_queue / quarantined / skipped_swo_other); error messages stay unread for retry.
- **`scripts/launchd/org.solutionsmith.its.safety-intake.plist`** — new template. `__POLL_INTERVAL_SECONDS__` placeholder substituted at install time.
- **`scripts/install_safety_intake_daemon.sh`** + **`uninstall_safety_intake_daemon.sh`** — new. Installer reads `safety_reports.intake.poll_interval_seconds` from ITS_Config, substitutes both placeholders, plutil-lints, bootstraps via launchctl bootstrap.
- **`scripts/migrations/seed_safety_intake_polling_config.py`** — new. Idempotent seeder for 3 new ITS_Config rows (`poll_interval_seconds=60`, `mailbox=safety@evergreenmirror.com`, `polling_enabled=true`). Ran live 2026-05-21 immediately post-merge — 3 rows created.
- **Tests** — `test_intake.py` refactored (parse_email_file tests removed; 6 test_main_* → test_process_message_* with mocked Graph; +2 new tests for skipped_swo_other + graph-fetch-failure). `test_intake_poll.py` new (24 tests + gated integration test). `test_intake_capability_gating.py` extended (parametrized over both intake.py and intake_poll.py; added belt-and-suspenders `.send_mail` attribute check). `test_capability_gating.py` adjusted (intake.py loses `graph_client` forbidden substring while keeping `send_mail`; intake_poll.py added). `test_intake_integration.py` deleted (.eml-based pattern obsolete; XOR routing assertion moved to test_intake_poll.py's gated integration test).
- **Docs** — `safety_reports/README.md` (Mail.app section replaced with daemon install/uninstall/troubleshooting + 3 new config rows documented); `docs/tech_debt.md` (Mail.app silent-disable entry `[OPEN]` → `[PARTIALLY MITIGATED 2026-05-21]`); `scripts/migrations/README.md` (new migration entry).

## Test count delta

| Metric | Pre-PR (PR #58 close) | This PR |
|---|---|---|
| pytest pass | 722 | 754 (+32) |
| pytest skip | 1 | 1 |
| pytest deselected | 7 | 7 |
| mypy source files | 91 | 93 (+2: `intake_poll.py` + `seed_safety_intake_polling_config.py`) |
| mypy issues | 0 | 0 |
| ruff | clean | clean |

## Design decisions (PR review acks)

- **`graph_client` capability surface for intake.py + intake_poll.py.** Loosened the GATED_SCRIPTS forbidden substring for intake.py: dropped `graph_client` (the new pipeline legitimately needs `get_message` / `list_attachments` / `download_attachment` / `mark_read`), kept `send_mail` as the narrow gate. Added intake_poll.py with the same gate. The External Send Gate's scope is customer-facing transmission; `mark_read` is an inbox-cursor PATCH on the operating tenant's own mailbox, not external transmission.
- **`SmartsheetError` + `GraphError` → soft `status='error'` returns, not raises.** Pre-PR `intake.main` raised SmartsheetError so the .eml file stayed unrenamed for retry. Post-PR `process_message` catches both error families and returns `status='error'`; the poller leaves the message unread for retry on the next cycle. Same operator-visible retry semantic, expressed via return-value instead of raise — necessary because the for-loop in poll_once would otherwise halt on a single Smartsheet hiccup. Programming-error exceptions (RuntimeError, etc.) still propagate so the poller's `@its_error_log` catches them.
- **New `skipped_swo_other` status.** Distinguishes "row written, Box upload skipped because category is Safe Work Observation or Other" from `processed`. Both still call `mark_read`; the distinction is observability only (poll logs can grep one without scanning Notes columns).
- **Plist label `org.solutionsmith.its.safety-intake`** — repo precedent (CLAUDE.md: `org.solutionsmith.its.*` during build, `com.evergreenrenewables.its.*` post-handover) wins over the brief's speculative `com.its.safety_intake`.
- **Plist path `scripts/launchd/`** — already exists in repo with picklist-sync + watchdog plists and a shared `install.sh`; preferred over the brief's `infra/launchd/`.
- **No `shared/runner.py` extraction.** Op Stds v10 §14 preservation-over-refactor: defer abstraction until ≥2 real reuse cases. This PR is the first polling consumer; PR #60 (second polling workstream) is where extraction lives.

## Live-tenant cutover (post-merge)

- **ITS_Config seed (2026-05-21):** `python scripts/migrations/seed_safety_intake_polling_config.py` created 3 rows live:
  - `safety_reports.intake.poll_interval_seconds=60` (row id 5908736168099716)
  - `safety_reports.intake.mailbox=safety@evergreenmirror.com` (row id 7030591557926788)
  - `safety_reports.intake.polling_enabled=true` (row id 2922329443073924)
- **Daemon install (2026-05-21):** `./scripts/install_safety_intake_daemon.sh` reported `installed: org.solutionsmith.its.safety-intake` at interval 60s. `launchctl list | grep` returned `-	0	org.solutionsmith.its.safety-intake` (loaded, exit 0, not currently running between cycles). Plist written to `~/Library/LaunchAgents/org.solutionsmith.its.safety-intake.plist`.

## Operator-side actions remaining

- **Delete the prior Mail.app rule** on the production Mac to avoid dual-processing once the daemon ships. Until that's done, both the daemon AND the rule will see new mail — the seen-set guard prevents the daemon from double-processing, but the .eml rule still drops files into the hot-folder (orphan files). Manual cleanup, no code task.
- **Optional smoke**: send one synthetic safety report from `seths@evergreenmirror.com` to `safety@evergreenmirror.com` and verify a Daily Reports row + Box upload + mark_read within 60 s. See the runbook in `safety_reports/README.md`.

## What's NOT touched

- `shared/runner.py` abstraction — PR #60 territory (preservation-over-refactor).
- Watchdog Check F repurpose to read the heartbeat file — separate PR. Check F's mailbox-idle proxy continues to work unchanged.
- `weekly_generate.py` / `weekly_send.py` — R3 sessions 2 + 3.
- Email Triage Brief v5 update — doc cascade after this PR lands.
- ITS_Daemon_Health heartbeat row integration — PR #59.5 (heartbeat) covers this.

## Baseline state at session close

- Main at `f1e724f` (PR #59 merge commit).
- pytest 754 / 1 / 7. mypy 0/93. ruff clean.
- Safety intake polling daemon: installed, loaded, awaiting first cycle.
- R3 session 2 prerequisites: zero remaining. PR #59.5 (heartbeat) is the immediate-next critical path.
