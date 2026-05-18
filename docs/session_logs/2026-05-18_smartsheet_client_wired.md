# 2026-05-18 — smartsheet_client wired against sandbox

Replaces the stub at `shared/smartsheet_client.py` with an SDK-backed wrapper
that mirrors `shared/graph_client.py`'s shape (lazy singleton from Keychain,
typed exception hierarchy, thin operation helpers). PR scoped to the client,
its tests, and the smoke script — consumer refactors (`kill_switch`,
`error_log`, `review_queue`, `quarantine`, scheduling fetchers) intentionally
left for follow-on PRs.

## Commits landed

| SHA | Title | Purpose |
|---|---|---|
| _(this PR)_ | feat(shared): wire smartsheet_client over the SDK | New module + tests + smoke test + this log |

## CI runs

| Run | Commit | Result |
|---|---|---|
| _(pending after push)_ | | |

Local: `ruff check` clean on the three new/edited files. Full `pytest -q`
green (137 passed, 2 skipped — the same 2 skips as prior sessions).

## Live smoke test

`scripts/smoke_test_smartsheet.py` ran end-to-end against the sandbox:

1. SDK client init from Keychain — OK
2. Read ITS_Config (0 rows — see "Open items" below) — OK
3. Append INFO row to ITS_Errors — OK, new row_id returned
4. Update the row (Resolved At + Notes) — OK
5. Delete the row (no droppings) — OK
6. Bogus sheet ID translated to `SmartsheetNotFoundError` — OK

## Decisions made during session

- **SDK over `requests`-direct.** `graph_client.py` rolls its own HTTP path
  because MSAL + Application Access Policy quirks force it. Smartsheet's
  API doesn't have equivalent ugliness, and `smartsheet-python-sdk` is
  already a declared dependency. Wrapping the SDK saves rolling retry,
  rate-limit backoff, and typed response models. Structural pattern still
  mirrors `graph_client.py` (lazy singleton, typed exceptions, thin
  helpers) — only the HTTP path differs.
- **Title-keyed ergonomics with a per-sheet column-ID cache.** Callers
  pass `{"Setting": "kill_switch_state", "Value": "ACTIVE"}`; the wrapper
  resolves titles → column IDs against a module-level cache. Cache misses
  trigger one refetch before raising `KeyError`. Renames break the cache
  intentionally — silently writing into the wrong column on a typo or
  rename is the worst possible outcome; fast-failing is the cheap recovery.
  Documented in the module docstring and covered by
  `test_renamed_column_still_keyerrors_after_refetch`.
- **`get_setting()` requires `workstream` as a keyword.** No default.
  `ITS_Config` is keyed on `(Setting, Workstream)` — silently defaulting to
  `"global"` hides config misses for workstreams that should have an
  override row. Enforced by signature; covered by
  `test_get_setting_requires_workstream_kwarg`.
- **Stale-duplicate cleanup was already done.** Pre-implementation step
  verified that sheet IDs `4195780532326276`, `470411799121796`,
  `2704945844277124`, `4505679602601860` all return 404, and a Smartsheet
  search confirmed only the canonical `ITS_Errors` (`27291433258884`)
  remains. The 2026-05-17 evening workspace restructure must have caught
  them. No deletions needed.
- **Used the sheet schemas read live from Smartsheet, not Handover v5.**
  Confirmed before implementing — `ITS_Time_Off` uses `Reason` (not
  `Type`), `ITS_Errors` has no `Workstream` column, `ITS_Config`'s
  Workstream picklist values are `global` / `safety_reports` /
  `po_materials` / `subcontracts` / `email_triage` / `ai_employee`.
  None of these end up baked into the client — but the smoke test
  exercises them.
- **Test fixture deliberately doesn't mock `smartsheet.Smartsheet`
  globally.** `_install_client(mocker)` swaps `get_client()` itself so each
  test controls the surface SDK shape without re-mocking the SDK module.
  Mirrors the pattern in `test_graph_client.py` where `_mock_msal` returns
  a configured MagicMock rather than reaching into the MSAL module.

## Open items handed off

- **ITS_Config is empty.** Smoke test step 2 returned 0 rows. The
  config-writing path (manually seeding `kill_switch_state`,
  `anomaly_threshold`, reviewer-chain overrides) is a separate planning
  item — `kill_switch.py` will continue to default to `ACTIVE` until rows
  exist. **Suggested Master Checklist wording:** _"Seed ITS_Config with
  the global kill_switch_state row (ACTIVE) and the per-workstream
  reviewer-chain overrides before kill_switch.py reads from
  smartsheet_client."_
- **SDK 404 logs to stdout before our translation runs.** The Smartsheet
  SDK prints the raw 404 response JSON in addition to raising — visible in
  smoke test step 6 output. Not a correctness issue but noisy for
  launchd-captured logs. Worth a `logging` filter or SDK-level silencing
  pass when we wire `error_log` Smartsheet writes.
- **No coverage for SDK rate-limit retry budget exhaustion.** The SDK
  retries 429s internally up to `max_retry_time=30s`. We translate the
  final `ApiError(429)` to `SmartsheetRateLimitError` but never fed a
  realistic burst through the live API to verify the SDK's behavior
  matches its docs. Picked up in error_log's Smartsheet wiring when high
  write rates are plausible.

## What was NOT touched

- `shared/kill_switch.py` — still returns `ACTIVE` from the stub. Refactor
  to read `Setting=kill_switch_state` via `smartsheet_client.get_setting`
  is a separate PR.
- `shared/error_log.py` — still writes to a local file only; Smartsheet
  write path lands in its own PR.
- `shared/review_queue.py`, `shared/quarantine.py` — stubs unchanged.
- `shared/scheduling.py` — `_empty_fetcher` and `_no_override` defaults
  still in place. The real fetchers wired to `smartsheet_client.get_rows`
  are a follow-up.
- `shared/sheet_ids.py` — no edits. Already had every ID this module
  needs from the 2026-05-17 evening session.

## Lessons captured to memory

None this session. Decisions taken here are PR-local and live in this log
rather than persistent memory. The "preservation over refactor" rule was
already saved 2026-05-17 and didn't get bent — the scoping discipline
(separate PRs for consumers) came directly from PR feedback this session.
