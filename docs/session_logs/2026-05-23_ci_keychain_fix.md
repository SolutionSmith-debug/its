# 2026-05-23 — CI main-branch keychain fix + four-part verification discipline

Restores `main`'s CI to green after 6 consecutive red runs (#229 / #232 / #236 / #239 / #246 / #251) since PR #68 (R3 Session 3 weekly_send_poll, 2026-05-23T02:02:33Z). Codifies a four-part PR-landed verification protocol so the same blind spot can't recur.

Branch: `fix/ci-keychain-mock` off `main` at `06337bd` (PR #73 close).

## Purpose

Two intertwined problems:

1. **Linux-CI keychain failure** in `tests/test_weekly_send_poll.py` (3 tests). On macOS dev machines the failure was masked because the operator's real keychain returned a real token; on Linux runners `security` CLI is absent and `keychain.get_secret` raised `KeychainError`. Five session logs since PR #68 framed this as "pre-existing test_weekly_send_poll.py Linux-Keychain CI failures, not introduced by this PR" — that framing was wrong: PR #68 introduced both the test AND the mock-namespace gap.

2. **PR verification blind spot**: the existing three-assertion verify (`state=MERGED` / `mergedAt` non-null / `mergeCommit.oid` present) catches GitHub-side ghost merges (PR #34 case) but misses post-merge `push: main` failures because it only inspects `pull_request`-attached checks. PR #68's red main was visible from the GitHub UI but never caught by our discipline.

## Pre-flight findings

- Baseline test count: **1004** (matches PR #73 session log).
- HEAD `main = 06337bd` (PR #73 picklist hardening); no PRs landed between brief and execution.
- Failure cascade (`gh run list --branch main --workflow ci.yml --status failure`): Runs #229 (PR #68 sha `5959d25`), #232 (PR #69 sha `2d44d2f`), #236 (PR #70 sha `6a89024`), #239 (PR #71 sha `7e479ae`), #246 (PR #72 sha `4b239fc`), #251 (PR #73 sha `06337bd`) — all FAILURE on `push: main`.
- Last green main run: #226 (sha `347a9c0`, PR #67 — docs only).
- Operator's `feat/post-1111b-cutover` work is in-flight in the working tree (modifications to CLAUDE.md, README.md, intake.py, weekly_generate.py, sheet_ids.py, parse_job_v3.py + untracked reclone migration files) — must remain untouched.

## Phase 1: Local reproduction

```bash
mkdir -p /tmp/no_security_bin
PATH="/tmp/no_security_bin" .venv/bin/python -m pytest \
    tests/test_weekly_send_poll.py::test_poll_once_skipped_when_polling_disabled --tb=long
```

Reproduced the CI failure exactly:

```
FAILED tests/test_weekly_send_poll.py::test_poll_once_skipped_when_polling_disabled
shared.keychain.KeychainError: macOS `security` CLI not found. This module is macOS-only.
```

Captured stdout showed the actual call chain (slightly more nuanced than the brief's stated chain):

1. Test's `_patch_all` fixture sets `smartsheet_client.get_setting` side_effect to return `"false"` (intent: polling_enabled returns False).
2. `poll_once()` decorated `@its_error_log @require_active`. `require_active` calls `kill_switch.check_system_state`.
3. `check_system_state` calls `smartsheet_client.get_setting("system.state", workstream="global")` — same mocked binding (module attribute, shared) → returns `"false"`.
4. `SystemState("false")` raises `ValueError` (not in enum). Kill switch's third fail-open branch runs: `log(Severity.WARN, ...)`.
5. `log()` → `_smartsheet_log()` → `smartsheet_client.add_rows(SHEET_ERRORS, ...)` → `_get_client()` → `keychain.get_secret("ITS_SMARTSHEET_TOKEN")`.
6. On Linux: `security` CLI missing → `KeychainError`. `_smartsheet_log`'s `try/except SmartsheetError` does NOT catch `KeychainError` (different exception hierarchy) → propagates up.
7. `KeychainError` escapes through `check_system_state` → through `require_active` wrapper → into `@its_error_log` wrapper's catch-all.
8. `its_error_log` fires CRITICAL → `_alert_critical` → Resend leg → `keychain.get_secret("ITS_RESEND_API_KEY")` → `KeychainError` → caught and logged as `[resend-alert-failed]`. Sentry leg same.
9. `its_error_log` re-raises → pytest sees test FAILED.

## Phase 2: Fix

`tests/conftest.py` (new) ships with two autouse fixtures:

- `_mock_keychain` — `monkeypatch.setattr("shared.keychain.get_secret", ...)` returns `f"test-{service}"`. Covers all 7 credentialed surfaces transitively (smartsheet / graph / box / resend / sentry / anthropic / alert_dedupe) because all of them resolve to `shared.keychain.get_secret`. Opt-out for `test_keychain.py` + `test_helpers.py` (those exercise the real entry point).

- `_mock_kill_switch_state` — `monkeypatch.setattr("shared.kill_switch.check_system_state", lambda: SystemState.ACTIVE)`. Patches the function directly rather than its underlying `smartsheet_client.get_setting` — the latter would mutate the `smartsheet_client` module's `get_setting` attribute (since `kill_switch.smartsheet_client` IS the same module) and break `tests/test_smartsheet_client.py::test_get_setting_*`. Opt-out for `test_kill_switch.py`.

Per-test `mocker.patch` calls win over the autouse fixture (pytest applies test-local mocks after autouse), so existing kill-switch-aware tests still work without modification.

Verified after the fix:

- `PATH="/tmp/no_security_bin" pytest tests/test_weekly_send_poll.py::test_poll_once_*` — 3 previously-failing tests PASS.
- `PATH="/tmp/no_security_bin" pytest` (full suite, Linux-equivalent) — **1002 passed, 2 skipped** (the 2 skips are `test_helpers.py` + `test_keychain.py` which explicitly skip when `security` is absent — pre-existing behavior, not a regression).
- `pytest` (full suite, normal PATH) — **1004 passed, 16 deselected** (matches PR #73 baseline).

Audit of other latent gaps via `grep -rn "smartsheet_client.get_setting\|keychain.get_secret" shared/ safety_reports/ --include="*.py"`: 26 call sites across 9 modules. All keychain calls covered by the source-attribute mock. All `get_setting` calls not on the `kill_switch` path are caller-mocked by their per-test fixtures already (no test reports a KeychainError-via-get_setting from another module).

## Phase 3: Four-part PR-landed verification

`docs/operations/pr_merge_discipline.md` (new) codifies the canonical protocol:

1. **PR-state triplet** (original three-assertion verify — PR #34 ghost prevention).
2. **Capture merge commit SHA**.
3. **Wait for the `push: main` workflow run on that SHA** (separate from PR-attached `pull_request` checks).
4. **Verify all main-branch runs conclude as success**.

"Functionally not landed" framing: a PR that passes step 1 but fails step 4 is not landed for discipline purposes, regardless of whether the failure is introduced or inherited. The "inherit and propagate" path (which the 6 PRs took) is retired.

Retroactive check on PR #73 (proof the discipline catches the existing state):

```
PR #73 merge SHA: 06337bd2b78f066f09d83737f5b240e8516ad4d3
[{"conclusion":"failure","databaseId":26335962586,"name":"ci","status":"completed"}]
```

Step 4 returns FAILURE on the existing red state. Captured in the operations doc as the proof reference.

`CLAUDE.md` extended with a reference to `docs/operations/pr_merge_discipline.md` and the session-log line convention update (four parts now, including `main-branch CI on merge commit: SUCCESS`).

## Retroactive correction of the "pre-existing" framing

Five prior session logs (PR #68, #69, #70, #71, #72, #73) framed the failure as "pre-existing test_weekly_send_poll.py Linux-Keychain CI failures, not introduced by this PR." That framing was diagnostically correct (the symptom IS keychain) but operationally wrong: PR #68 introduced the test AND the mock gap, and subsequent PRs inherited the red main without fixing it. The "pre-existing" label was a soft dodge.

No history rewrite — the prior session logs stand as written. This session log is the canonical correction reference; future readers of the prior logs should consult this entry for the actual provenance.

## Verification gates

- `pytest -q` (normal PATH): **1004 passed**, 16 deselected.
- `pytest` (Linux-equivalent `PATH=/tmp/no_security_bin`): **1002 passed, 2 skipped** (pre-existing skipif guards on macOS-only tests).
- `mypy tests/conftest.py`: 0 errors.
- `ruff check`: clean (one auto-fix to remove unused `typing.Any` import + sort the import block).
- `tests/test_capability_gating.py`: still passes (no code-side changes that would affect gating).
- Phase 1 reproduction confirmed PRE-fix on a `security`-less PATH; same step run AFTER the fix shows the test passing.
- Phase 3 retroactive check on PR #73 returns FAILURE (proof the discipline catches the existing red state).

## Done when (for this PR)

- pytest: 1004 passed / 0 skipped / 16 deselected (normal PATH). 1002 + 2 skipped on Linux-equivalent.
- mypy: 0 errors / 122 source files.
- ruff: clean.
- main-branch CI on merge commit: SUCCESS — this is the test of the test. If this PR's merge commit's `push: main` run is green, the conftest fix works AND the four-part discipline is canonical-by-construction going forward.

## Out of scope (per brief, restated)

- Refactor `shared/smartsheet_client.py` to lazy-load the token at first network call. Logged as `Structural fix: lazy keychain loading + DI-injected kill_switch` tech-debt entry.
- Refactor `shared/kill_switch.py` to accept dependency injection. Same entry.
- Modifications to PRs #68-#73 that landed with red main. Fix-forward only; no history rewrite.
- Workflow trigger changes. Hiding the gap is not a fix.
- Operator's `feat/post-1111b-cutover` in-flight work — resumes on a green main after this PR lands.
- Phase 1.4 #3 attachment screening — same.

## Notes / gotchas surfaced this session

- **Brief's "kill_switch.smartsheet_client" patch target was technically incorrect** — patching `shared.kill_switch.smartsheet_client.get_setting` mutates the `smartsheet_client` module attribute (it's the same module object), which breaks `tests/test_smartsheet_client.py`'s `get_setting` tests. Switched the second fixture to patch `check_system_state` directly. Outcome is functionally equivalent (kill_switch is no-op) but the implementation route avoids the module-attribute mutation. Captured in the conftest docstring.
- **Two opt-out lists**: `test_kill_switch.py` (kill_switch fixture) and `test_keychain.py` + `test_helpers.py` (keychain fixture). All three test files exercise the patched surface directly. The opt-out is by file name via `request.node.path.name`.
- **2 skips on Linux-equivalent PATH** are pre-existing `@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("security") is None, ...)` guards on `test_keychain.py::test_*` and `test_helpers.py::test_keychain_*` — NOT regressions from this PR.
- **Operator's parallel work in tree**: CLAUDE.md, README.md, box_migration/parse_job_v3.py, safety_reports/intake.py, safety_reports/weekly_generate.py, shared/sheet_ids.py modifications + 4 untracked files. None affected by this PR; staged only my files explicitly.
- The first CI run for this PR is the proof: if `push: main` on this PR's merge commit returns SUCCESS, the discipline is correct-by-construction going forward.
