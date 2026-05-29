---
type: session_log
date: 2026-05-29
status: closed
workstream: ci
related_prs: [123]
tags: [integration-tests, keychain-stub, token-leak, eventual-consistency, conftest, autouse-fixture, sdk-vs-live]
---

# 2026-05-29 — Integration tests silently broken by autouse keychain stub; token-leak redaction

PR: [#123](https://github.com/SolutionSmith-debug/its/pull/123) — squash-merged 2026-05-29T17:48:30Z, merge commit `2e00612039babb1785a92eb2412698e6f7deb0d5`. `pr-landed-verifier` output: **PR #123 — four-part verify clean / state: MERGED / mergedAt: 2026-05-29T17:48:30Z / mergeCommit: 2e00612039babb1785a92eb2412698e6f7deb0d5 / main CI on merge commit: SUCCESS (run 26653020849, workflow: ci) + SUCCESS (run 26653019495, workflow: CodeQL)**.

## Purpose

Fix `@pytest.mark.integration` tests that had been silently broken since PR #74 (the CI-fix follow-up to PR #68), and redact a real Smartsheet token that leaked into a pytest failure traceback during this session (requiring a token rotation).

## Commits landed

- **`2e00612`** — `fix(tests): integration keychain stub opt-out + token-leak redaction` (PR #123). Durable marker-based opt-out in `conftest.py`; `_SecretToken` redaction wrapper in both integration files; `_reset_smartsheet_client` module-scoped autouse; `_wait_for_history` eventual-consistency poll. Test-infra only — no production code changed.

## CI / verification

```
- pytest: 1141 passed / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

(Unit suite run with `-m 'not integration'`, matching CI-equivalent gate. Integration tests gated to operator runs with a live keychain.)

## Pre-flight findings

`brief-validator` confirmed all code-shape claims in the brief with one material correction: the autouse keychain stub was added in **PR #74** (the follow-up CI fix), not PR #68 (which caused the CI breakage #74 fixed). The attribution matters because it pinpointed when the silent breakage started and ruled out any regression in the intake pipeline itself.

The brief also named 2 integration files needing the opt-out. Direct inspection found **~10 `@pytest.mark.integration` files** (plus one mixed file, `test_intake_poll.py`, with per-test `@integration` decorators at lines 730/1132 alongside unit tests that must keep the stub). This count difference is the decisive reason the marker-based opt-out was chosen over the filename-list option.

## Root cause

`tests/conftest.py`'s autouse, function-scoped `_mock_keychain` fixture (PR #74) stubbed `shared.keychain.get_secret` for every test under `tests/`, opting out only `{test_keychain.py, test_helpers.py}` by filename. The ~10 integration files were never opted out. Confusing failure signature: the module-scoped `_token_available` fixture ran at module setup BEFORE the function-scoped stub and captured the real token; each test body's `get_client()` then saw the stub and sent `test-ITS_SMARTSHEET_TOKEN` to the live API → `SmartsheetAuthError: HTTP 401 (code 1002)`. The real token was valid; the stub was masking it.

## Decisions made during session

1. **Marker-based opt-out over filename-list opt-out.** The brief offered both as options. The filename-list approach (extend the existing `{"test_keychain.py", "test_helpers.py"}` set) would have broken `test_intake_poll.py`: that file mixes integration tests (lines 730, 1132) with unit tests that need the stub. A filename match disables the stub for the entire file. The marker check resolves at per-test granularity, correctly applying the stub only to unmarked (unit) tests. The two existing filename entries are retained for the handful of tests that need a finer carveout that pre-dates the marker scheme.

2. **`_SecretToken` wrapper rather than a bare string in `_token_available`.** A real Smartsheet token rendered verbatim in a pytest failure traceback during this session, forcing an immediate token rotation. The fix wraps the token in a `_SecretToken` class whose `__repr__` returns `"<redacted>"`. The only caller that needs the raw value (the `_delete_*_rest` helper's `Authorization` header) calls `.reveal()` explicitly. This makes the redaction structural rather than relying on pytest output configuration.

3. **`_reset_smartsheet_client` module-scoped autouse fixture added to both integration files.** Without it, a mixed-process run (unit tests first, integration tests second in the same pytest invocation) could leave `smartsheet_client._client` holding a stub-token client, causing the integration tests to fail for a different reason than the stub fix addresses. Nulling the private `_client` attribute at module scope ensures a clean client construction at first live call.

4. **`_wait_for_history` poll added for one deterministic race.** `test_verify_approval_unauthorized_actor` hit a NO_HISTORY failure on every run — Smartsheet cell-history eventual consistency meant the history row was not yet visible at assertion time. A bounded poll (not an unconditional sleep) fixed the race deterministically. Alternative considered: unconditional `time.sleep`. Rejected: a poll with a timeout fails fast on genuine absence and documents *why* the wait exists.

5. **Smartsheet create→read/write eventual consistency flaking deferred.** Once the keychain fix let integration tests reach the live API, they were found to flake intermittently (~40–60% of runs): `create_sheet_in_folder` succeeded, but a subsequent `get_sheet` or `add_rows` returned 404, because a successful read doesn't guarantee the next op's replica is caught up. This is pre-existing behavior (tests authored in PRs #47/#48/#49/#51/F22) that the keychain fix merely unmasked. A partial settle helper (`_settle_sheet` / `_wait_until_listed`) was prototyped and deliberately reverted: a single settle read can't guarantee the NEXT operation's replica is current — the race is in the replication graph, not the caller's timing. Deferred to a dedicated follow-up (see Open items). Operator chose "Focused PR + follow-up" scope.

6. **Brief attribution corrected before any code was written.** `brief-validator` flagged the #68→#74 attribution error. Verified independently against the PR list. No code was written until the true origin of the stub was confirmed.

## Open items handed off

- **[OPEN tech-debt] Smartsheet integration-test eventual-consistency flaking.** Tests in PRs #47/#48/#49/#51/F22 flake ~40–60% on create→read/write replica lag. Two approaches documented in `docs/tech_debt.md`: (a) `pytest-rerunfailures` with a narrow `@pytest.mark.flaky` decorator; (b) a retry-on-not-found wrapper scoped strictly to integration-test helpers. The retry must NOT move into the SUT — a 404 must surface in production for the `intake_poll` heartbeat-cache invalidation path. Operator decision on approach before the follow-up PR.
- **[OPEN tech-debt] No startup token write-capability validation.** An invalid or read-only Keychain token fails silently at the first write operation rather than loudly at boot. Surfaced as a new debt entry this session.
- **[OPEN tech-debt] Single-token blast radius.** One rotated `ITS_SMARTSHEET_TOKEN` gates all integration tests and all production scripts. Surfaced as a new debt entry this session.
- **Operator action — token rotation complete.** The raw token that appeared in the traceback was rotated during this session. The `_SecretToken` wrapper prevents future recurrence but the rotated token should be verified still valid against the sandbox.

## What was NOT touched

- **No production Python code.** All changes are in `tests/conftest.py` and the two integration test files. No SUT module was modified.
- **The existing `{"test_keychain.py", "test_helpers.py"}` filename carveouts** in `_mock_keychain` — retained, because they guard tests that predate the marker scheme and have distinct reasons for their opt-out.
- **The `_mock_keychain` autouse scope (function)** — unchanged. Marker check was inserted at the top of the fixture body; the scope, name, and structure are preserved per §14.
- **`weekly_send_poll.py` or any send-path code** — F22 (PR #118) is already merged; this PR is test-infra only and does not revisit that work.
- **Integration test assertions or test logic** — only the infrastructure (opt-out, redaction, client reset, one poll) was touched; the assertions themselves are unchanged.

## Subagents used

- **`brief-validator`** — validated all code-shape claims; flagged the #68→#74 attribution error (confirmed PASS after correction; all other claims verified against live HEAD).
- **`ops-stds-enforcer`** — CLEAN: 0 violations across §3/Inv1, §3/Inv2, §3.1, §14, §23, §30, §41, §42. Confirmed unit stub preserved (no Send Gate violation introduced by the opt-out).
- **`pr-landed-verifier`** — four-part verify clean (quoted verbatim above).

## Tech-debt entries added

Four entries written to `docs/tech_debt.md` this session:

1. **[RESOLVED]** Integration tests silently broken by autouse keychain stub — incident record: root cause, breakage window (since PR #74), fix approach (marker-based opt-out).
2. **[OPEN]** No startup token write-capability validation — a read-only or invalid token fails silently at the first write, not loudly at boot.
3. **[OPEN]** Single-token blast radius — one `ITS_SMARTSHEET_TOKEN` gates all integration tests and production.
4. **[OPEN]** Smartsheet integration tests flake on create→read/write eventual consistency — pre-existing, unmasked by the keychain fix; deferred follow-up (two candidate approaches above; retry must NOT enter SUT).

## Cross-references

- PR #74 — CI-fix follow-up to PR #68; the commit that introduced the autouse stub (root cause of the breakage fixed here).
- PR #68 — the intake-pipeline PR whose CI breakage #74 was fixing; not the stub author, per `brief-validator`.
- `docs/tech_debt.md` — four entries added/updated this session.
- `tests/conftest.py`, `tests/test_approval_verification_integration.py`, `tests/test_smartsheet_client_integration.py` — the three files changed.
