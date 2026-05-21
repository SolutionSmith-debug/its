# 2026-05-21 — R3 session 1: intake.py wiring

PR: [#57](https://github.com/SolutionSmith-debug/its/pull/57) — squash-merged at 2026-05-21T16:52:10Z. Merge commit `c4c4bc9501c321421ead2837b6d74ecb8d2013f1`. Three-assertion verify clean.

Closes R3 session 1: `safety_reports/intake.py` is wired end-to-end against the live Smartsheet + Box sandbox surfaces that PRs #54, #55, and #56 prepared. After this PR the operator can set up the Mail.app hot-folder rule and dummy safety emails flow through to Bradley 1's current-week Daily Reports + the corresponding Box subfolder.

## Pipeline stages implemented

All 12 stages from the brief:

1. Parse the `.eml` file (stdlib `email.message_from_bytes`).
2. Sender allowlist gate (defense-in-depth via `shared.quarantine.is_allowlisted`; non-allowlisted → `ITS_Quarantine`, no Anthropic call).
3. Attachment + body extraction (handles multipart; falls back from text/plain to stripped HTML).
4. Project resolution (subject-prefix substring match against the 6 Forefront project names; falls through to body's first 500 chars; ambiguous → review queue).
5. Anthropic classify+extract via tool-use JSON-mode (`extract_safety_report_fields` tool; `<untrusted_content>` wrapping; `system_boilerplate()` prepended).
6. Confidence gate (`confidence < safety_reports.intake.confidence_threshold` → review queue with Reason=low-confidence-extraction).
7. Anomaly check (union of `anomaly_logger.check()` sentinel hits + the model's self-reported `anomaly_flags`; high-severity flags → review queue with `security_flag=True`, `Severity=CRITICAL`, `Reason=security-trigger`).
8. Week-folder resolution (`ensure_current_week_folder(project, week_start=extraction.report_date)` — backfill emails land in the right week).
9. Daily Reports row write (next sequential `Entry #`, full 9-column mapping).
10. Box upload (per-category subfolder mapping; categories with no fixed path skip Box and tag Notes with the reason).
11. Row update with Box URL summary in Notes / Action Items (non-fatal failure mode — row stays without the link, WARN logged).
12. Rename `.eml` → `.eml.processed` (the success watermark) + INFO success log line.

## Test count delta

| Metric | Pre-PR (PR #56 close) | This PR |
|---|---|---|
| pytest pass | 684 | 722 (+38) |
| pytest skip | 2 | 1 (the previously-empty `GATED_SCRIPTS` parametrize-skip became a real pass when intake.py was enrolled) |
| pytest deselected | 6 | 7 (+1 new integration test, gated `@pytest.mark.integration`) |
| mypy source files | 87 | 91 (+4: intake.py +grew, new migration, 3 new test files) |
| ruff | clean | clean |

## Subtleties found mid-implementation

- **Subfolder mapping for `Safe Work Observation` + `Other`**: the brief gave 3 confirmed paths but punted the other two to "consult Q6 resolution doc." The doc isn't local; I made the call: these two categories file `subpath=None` and the upload helper returns no-Box-mapping which routes the row's Notes field to a `[box_filing_skipped: <category>]` tag. Operator can manually file via the review queue's audit trail. Cleaner than guessing a wrong subfolder; tradeoff is operator touch on two of five categories until the doc is read into a follow-on PR.
- **`ensure_current_week_folder` for backfill**: pipeline computes `week_start = extraction.report_date` rather than today. An email arriving Wednesday for a report dated last Friday correctly lands in the previous week's sheet. PR #54's week_folder helper supports this directly via its `week_start: date | None = None` parameter.
- **Two CI iterations to land**: first push failed because `test_main_smartsheet_write_failure_leaves_file_for_retry` triggered the `@its_error_log` decorator's CRITICAL alert path on Linux, which calls `log() → _smartsheet_log → smartsheet_client → keychain.get_secret` — and Linux CI has no macOS `security` CLI so the read failed with `KeychainError`. First fix patched `_alert_critical` but missed the prior `log(CRITICAL)` call in the decorator's except branch. Second fix patches both `shared.error_log.log` and `shared.error_log._alert_critical`. Other main() tests already patched `error_log.log` so they passed; only the SmartsheetError-raising test was exposed.
- **Anthropic key not in Keychain yet**: intake.py is the first production consumer of `shared.anthropic_client`. The Keychain entry `ITS_ANTHROPIC_KEY` was never seeded. The integration test gracefully skips on missing key (module-level fixture pattern). Operator seeds via `security add-generic-password -a "$USER" -s "ITS_ANTHROPIC_KEY" -w` then runs `pytest -m integration tests/test_intake_integration.py`.
- **Defense-in-depth allowlist (not Mail.app-only)**: Brief framed the allowlist as primarily a Mail.app rule with intake.py's in-code check as backup. Brief and existing `shared/quarantine.py` agree on this. Implemented the in-code check unconditionally — if a future operator routes mail by a different mechanism (e.g., direct Graph API poll instead of Mail.app), the allowlist gate remains.

## Integration-test outcome

Skipped at this PR's local run (no `ITS_ANTHROPIC_KEY` in Keychain). The test is committed and ready; operator seeds the key and runs `pytest -m integration tests/test_intake_integration.py` against the sandbox as the post-merge verification. Test design: synthetic .eml → live Anthropic classify+extract → live Smartsheet add_rows into Bradley 1 / Week of (current Monday) → live Box upload → cleanup of the row + Box file in `finally`.

## Operator smoke-test (post-merge)

Full instructions in the PR description and in `safety_reports/README.md`. Summary:

1. Seed `ITS_ANTHROPIC_KEY` in macOS Keychain.
2. Run the integration test to confirm the live wiring works against sandbox.
3. Set up the Mail.app rule + hot-folder.
4. Send a dummy daily report from `seths@evergreenmirror.com` to the sandbox safety inbox.
5. Verify the row in Smartsheet, the file in Box, the INFO success log line.
6. Exercise the review-queue branch by sending an ambiguous-project email.

## What's NOT touched

- `weekly_generate.py` and `weekly_send.py` — R3 sessions 2 and 3.
- The `Box Link` dedicated column in the Daily Reports schema — `docs/tech_debt.md` PR #54 entry remains OPEN. This PR continues the embed-in-Notes pattern from that entry's recommendation. A future schema-edit PR closes the gap.
- The five Safety Reports Mission v5 + Brief v6 + Q4-Q8 Resolution docs in the planning project — referenced but not modified (planning project, not repo work).

## Baseline state at session close

- Main at `c4c4bc9` (PR #57 merge commit).
- pytest 722 / 1 / 7. mypy 0/91. ruff clean.
- R3 session 1 prerequisites: zero remaining.
- R3 session 2 (`weekly_generate.py`): no prerequisites identified; can start next session.
