# 2026-05-21 — Smartsheet error-translation refactor

PR: [#55](https://github.com/SolutionSmith-debug/its/pull/55) — squash-merged at 2026-05-21T15:09:43Z. Merge commit `e3945c3b173904775326d0809510c4095b23355f`. Three-assertion verify clean.

Pure refactor on top of PR #54. Closes the §14 threshold trigger flagged in `2026-05-21_r3_foundation_pr.md` decision #3 ("Phase 3 scope grew by 2 helpers — error-translation block now appears 4× in the module … deferred extraction to a follow-on PR focused on it").

## §14 threshold trigger

`shared/smartsheet_client.py` had 1 REST helper at end-of-PR-#50 (`find_sheet_by_name_in_folder`). PR #54 added 3 more (`find_folder_by_name_in_folder` + `create_folder_in_folder` + `create_sheet_in_folder_from_template`), each carrying the same 14-line dispatch from `requests.HTTPError` to the typed `SmartsheetError` hierarchy. Op Stds v10 §14 (preservation-over-refactor) defers abstraction until ≥4 real reuse cases — count reached at PR #54 merge. Extract before R3 session 1 lands the 5th caller.

## What landed

Single private helper at `shared/smartsheet_client.py` (just above the first REST helper):

```python
def _translate_smartsheet_error(response: requests.Response, *, context: str) -> None:
```

- No-op on 2xx (returns `None`).
- On non-2xx, dispatches status code onto the same typed hierarchy as `_translate(exc)` for SDK errors (401→Auth, 403→Permission, 404→NotFound, 429→RateLimit, else→base).
- Context string is prepended to error messages so operator-facing logs identify the failing operation without a stack trace.

Each of the 4 REST helpers collapsed its 14-line try/except to ~3 lines: a one-line `requests.RequestException` catch (genuine connection-level failures), a `context = f"..."` f-string naming the operation, and a single `_translate_smartsheet_error(response, context=context)` call. Net `shared/smartsheet_client.py`: ~58 lines removed, ~54 added (helper + its docstring + context lines), small negative delta as expected.

## Subtleties found mid-extraction

- **Drove the helper off `response.raise_for_status()` rather than `response.ok` + `response.status_code` inspection.** Initial design used the inspection path (simpler, more idiomatic). But the existing PR #54 test fixtures (`_rest_get_folder_response`, `_rest_get_folder_response_with_folders`, `_rest_post_response`) build `MagicMock` responses that wire up `raise_for_status.side_effect = HTTPError(...)` to simulate 4xx but do NOT set `.ok` — on a MagicMock, `.ok` auto-returns a truthy mock object regardless of `status_code`. The inspection-path helper would have miscategorized the existing 4xx test cases as 2xx pass-throughs. Two fixes possible: (1) update three fixtures to set `.ok = (status < 400)`, or (2) drive the helper off `raise_for_status()`. Picked (2) — zero fixture changes, helper is no more code, and the implementation matches what callers would have written by hand. Documented inline in the helper docstring so the next maintainer doesn't "simplify" back to the inspection path.

- **Context-prefix is an operator-facing message-format change, not pure refactor.** Previously 4xx/5xx messages were `f"HTTP {status}: {body_text}"` (no operation context). New messages prepend a context f-string like `"copying sheet 99 into folder 42 as 'X': HTTP 500: ..."`. Pre-checked: zero REST-helper unit tests assert on message content (only on exception type via `pytest.raises(SmartsheetError)`), so safe. The change is strictly additive — old content still embedded. Operator logs get more triage-friendly.

- **`requests.HTTPError` is a subclass of `requests.RequestException`.** Caller pattern now has `except requests.RequestException as e:` AROUND the request itself (catches connection-level errors before any response exists) and `_translate_smartsheet_error(...)` AFTER the request returns (handles the response-with-error case). Cleanly separates "request never completed" from "request returned an error response" — both still surface as `SmartsheetError` for callers, but with distinguishable messages.

## Test count delta

- 3 new contract-lock unit tests on the helper directly:
  - `test_translate_smartsheet_error_passes_through_2xx`
  - `test_translate_smartsheet_error_raises_with_context_on_4xx` — asserts context prefix + status code + body excerpt all present in message.
  - `test_translate_smartsheet_error_raises_on_5xx` — confirms untyped 5xx falls through to base `SmartsheetError`, not one of the typed subclasses.
- 60 existing `shared/smartsheet_client.py` unit tests pass unchanged.
- Integration tests (6) not re-run as part of this PR; refactor is purely internal-to-module and integration tests exercise the post-refactor code paths through the same public APIs.

Baseline:

| Metric | PR #54 close | PR #55 close |
|---|---|---|
| pytest pass | 680 | 683 |
| pytest skip | 2 | 2 |
| pytest deselected | 6 | 6 |
| mypy | 0 / 85 | 0 / 85 |
| ruff | clean | clean |

## What was NOT touched

- `shared/graph_client.py` and `shared/box_client.py`: both have their own error-translation patterns from MSAL / boxsdk. Cross-module extraction belongs at the Op Stds level, not in this scope. No tech_debt.md entry created — Op Stds §14 already covers when to revisit.
- The SDK-using helpers in the same `shared/smartsheet_client.py` module (everything that uses `_translate(exc)` against `smartsheet.exceptions.ApiError`). Different boundary (SDK exceptions vs HTTP responses); Op Stds v10 §30 keeps SDK-vs-Live discipline as a separate decision surface.
- `docs/tech_debt.md`. PR #54 added two entries (week-folder race, Daily Reports Box Link gap); no entry was created for the error-translation duplication itself, so nothing to mark `[CLOSED]` here. The session log + the §14 cross-reference in PR #55's description are the audit trail.

## Lessons captured to memory

None this session. The §14 threshold is already in `feedback_preservation_over_refactor`. The auto-mode-blocks-direct-push constraint is already in `project_session_logs_convention` (added at PR #54 close). No new patterns surfaced that aren't already memorialized.
