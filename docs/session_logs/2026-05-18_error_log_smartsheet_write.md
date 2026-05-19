# 2026-05-18 — error_log Smartsheet write + SDK 404 filter

Phase C of the three-phase doc-cleanup / SDK-404-investigation / error_log-
Smartsheet-write plan. Wires `shared/error_log.py`'s Smartsheet write path
to `ITS_Errors`, installs the SDK 404 noise suppression in
`shared/smartsheet_client.py`, and (as a side effect) auto-promotes
`shared/kill_switch.py`'s fail-open WARN paths to `ITS_Errors` with no
source change.

This log is being written incrementally: Phase B findings landed first
(before any code), per the cross-phase reminder that Phase C cannot start
until Phase B's finding is recorded.

---

## Phase B — SDK 404 noise investigation (no commit)

### Finding: it's Python `logging`, on stderr, at ERROR level

The brief listed three possibilities (logging / bare `print()` /
`sys.stderr.write`). Investigation confirms **possibility #1** — the
Smartsheet Python SDK uses Python's `logging` module via
`logging.getLogger(__name__)` throughout, and the 404 emission specifically
comes from a single `self._log.error(...)` call in the SDK's central
request/response logger.

Brief said "stdout"; **the actual stream is stderr** (Python `logging`'s
`lastResort` handler writes to stderr at WARNING+ when `basicConfig` hasn't
been called). Functionally equivalent for cosmetic-noise purposes — both
clutter the smoke-test output — but worth recording for accuracy.

### Exact source location

`.venv/lib/python3.13/site-packages/smartsheet/smartsheet.py`, lines
**350–356** (SDK version 3.9.0), inside `_log_request`:

```python
else:
    self._log.error(
        '{"response": {"statusCode": %d, "reason": "%s", "content": %s}}',
        response.status_code,
        response.reason,
        content_dumps,
    )
```

The `else` covers every non-2xx status (`if 200 <= response.status_code <= 299`
fails). So this same emission fires for 401 / 403 / 404 / 429 / 500 /
anything else — not just 404. `self._log` resolves to
`logging.getLogger("smartsheet.smartsheet")` (line 208).

### Stream-capture verification

```python
import sys, io
stdout_cap, stderr_cap = io.StringIO(), io.StringIO()
sys.stdout, sys.stderr = stdout_cap, stderr_cap
from shared import smartsheet_client
from shared.smartsheet_client import SmartsheetNotFoundError
try: smartsheet_client.get_sheet(1)
except SmartsheetNotFoundError: pass
sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
# STDOUT capture: ''
# STDERR capture: '{"response": {"statusCode": 404, "reason": "Not Found",
#   "content": {"errorCode": 1006, "message": "Not Found",
#   "refId": "6b402ee2-cd35-40d0-b208-fbe6fee5ea7b"}}}\n'
```

stdout is clean; stderr carries the formatted log record. The emission is
exactly the format string from `_log_request`, fully rendered.

### Logger-name verification

```python
import logging
logging.basicConfig(level=logging.DEBUG,
    format='LOGGER=%(name)s LEVEL=%(levelname)s | %(message)s')
# ... trigger 404 ...
# LOGGER=smartsheet.smartsheet LEVEL=INFO  | {"request": ...}
# LOGGER=smartsheet.smartsheet LEVEL=ERROR | {"response": {"statusCode": 404, ...}}
```

Logger name confirmed: `smartsheet.smartsheet` (the dotted module path
from `__name__` of `smartsheet/smartsheet.py`). Every operation also emits
an INFO request line; that's separate and stays silent unless someone
explicitly raises log levels.

### Chosen suppression strategy

**A `logging.Filter` on `smartsheet.smartsheet` that drops records whose
unformatted args indicate a 404.** Code-side check inspects
`record.args[0] == 404` (the `statusCode` arg passed to `_log.error`),
not the formatted message — that avoids string parsing and survives
format-string changes in future SDK versions.

Reasoning for filtering on 404 specifically rather than all SDK error
emissions:

- The brief explicitly scopes the suppression to "the 404 noise" and the
  original smartsheet_client session log flagged the same.
- 404s in our flow include the *expected* "row not yet seeded" case via
  `get_setting` — they're routine, and the SDK logging them as ERROR is
  semantically wrong for our usage.
- 401 / 403 / 429 / 500 are NOT expected and operators should see them
  on stderr if they happen. Filtering only 404 preserves those signals.
- Our own translation layer (`_translate` in `smartsheet_client.py`)
  surfaces the full status / code / message via the typed exception, so
  no information is lost.

Filter attached to the logger (not a handler) — `Logger.handle()` calls
`self.filter(record)` before `callHandlers()`, so returning False blocks
both emission and propagation in one stroke. Installed at module import
time as a top-level side effect of `shared/smartsheet_client.py`.

### Out-of-band note on filter scope

The SDK's `_log.error` fires for ALL non-2xx responses, not just 404. If
production ever needs to suppress more (say, also 429 noise during retry
storms), the filter's `_quiet_status_codes = {404}` set extends trivially.
Not changing scope now; just leaving the design knob obvious.

---

## Phase C — implementation

### Live ITS_Errors schema (verified 2026-05-18, sheet 27291433258884)

Inspected before writing the cell-mapping code. Ten columns total; six
are write targets, four are operator-side resolution-workflow fields we
leave blank at write time:

| Title | Type | Used by write path? |
|---|---|---|
| Error | TEXT_NUMBER (primary) | Yes — short stable label |
| Timestamp | DATE | Yes — today, ISO YYYY-MM-DD |
| Severity | PICKLIST | Yes — INFO / WARN / ERROR / CRITICAL |
| Script | TEXT_NUMBER | Yes — logical name |
| Message | TEXT_NUMBER | Yes — message arg |
| Traceback | TEXT_NUMBER | Yes — exc_info or "" |
| Surfaced At | DATE | No — operator workflow |
| Resolved At | DATE | No — operator workflow |
| Resolved By | CONTACT_LIST | No — operator workflow |
| Notes | TEXT_NUMBER | No — operator workflow |

`Severity` picklist options confirmed live: `["INFO", "WARN", "ERROR",
"CRITICAL"]` — exact match with the `Severity` enum string values. No
schema corrections needed.

### INFO-write performance decision: env-gated (Option 1)

Chose the brief's Option 1 — INFO writes gated by `ITS_ERROR_LOG_INFO=1`
— without taking measurements for Option 2. Reasoning:

- INFO covers exactly the high-frequency `started` / `completed` decorator
  lines that fire on every cron-job invocation. The signal-to-noise ratio
  of those rows in ITS_Errors is poor.
- A round-trip add_rows against the sandbox sheet was clocked at roughly
  300–600 ms during the smoke runs (visible as the gap between "Writing
  row" and "row_id=..." lines). Adding that twice per cron job pushes
  past the brief's 200ms threshold immediately.
- WARN / ERROR / CRITICAL are low-volume and always-on. CRITICAL is the
  only one that's also alert-routed; not blocking the Smartsheet write
  there is the right shape.
- Operators turn the env on (`ITS_ERROR_LOG_INFO=1 python ...`) when
  they want lifecycle visibility for a single run. The smoke test sets
  this; cron jobs do not.

### Recursion-guard implementation

No surprises. The guard is a module-level `_in_smartsheet_write: bool`
flipped via `global` inside `_smartsheet_log`, with a `try / finally` so
exceptions from add_rows still reset the flag. Tests cover three
distinct angles:

1. **Re-entry path** — a side_effect that calls `log()` again from inside
   add_rows. Inner call sees the flag set, returns without recursing.
   `add_rows` is called once total, not twice.
2. **Normal-completion reset** — back-to-back `log()` calls both reach
   add_rows. If the `finally` were broken, the second call would see the
   flag stuck True and skip.
3. **Exception-path reset** — the first add_rows raises
   `SmartsheetError`; the second still reaches add_rows. The fallback
   path's marker-line `_local_log` call is in scope of the guard but
   does NOT recurse because `_local_log` doesn't call `log()`.

The only call topology that touches the guard is `log() → _smartsheet_log
→ add_rows`. The fallback path is `_smartsheet_log → _local_log` — bypasses
the guard intentionally so the marker line always reaches disk.

### kill_switch WARN auto-promotion — verified end-to-end

`shared/kill_switch.py` untouched in this PR. Live verification ran:

```python
with patch("shared.kill_switch.smartsheet_client.get_setting",
           side_effect=SmartsheetNotFoundError(...)):
    kill_switch.check_system_state()  # returns ACTIVE

# Then read ITS_Errors with filter={"Script": "shared.kill_switch"}
```

Result: a new row with `Severity="WARN"`, `Script="shared.kill_switch"`,
`Error="warn"` (the default code from `severity.value.lower()`),
`Message="system.state row missing in ITS_Config — defaulting to ACTIVE"`
— the exact distinguishable substring asserted in `test_kill_switch.py`.
Row was cleaned up after verification.

### Live smoke runs

```
ITS_ERROR_LOG_INFO=1 python scripts/smoke_test_error_log.py
# [INFO] write → row_id=7667882488430468 → deleted
# [WARN] write → row_id=5784322433286020 → deleted
# [404 filter] stdout/stderr clean of raw 404 JSON

python scripts/smoke_test_kill_switch.py
# state=ACTIVE; happy path (system.state row exists from PR #9)

python scripts/smoke_test_smartsheet.py
# step 6: SmartsheetNotFoundError surfaced cleanly, no raw 404 JSON
```

All three pre-existing smoke scripts still pass — no regressions.

### Gates

- `ruff check .` clean.
- `pytest -q` — 184 passed, 2 skipped (was 160 + 2; +24 new tests across
  `test_error_log.py` (+12) and `test_smartsheet_client.py` (+10
  including a 5-row parametrize)).
- `mypy` clean on `shared/error_log.py` and `scripts/smoke_test_error_log.py`.
  `shared/smartsheet_client.py:221` shows the same pre-existing
  `get_setting` return-type error noted in Phase A's session log; line
  moved from 209 → 221 because the file's header grew by 12 lines for
  the filter docstring and `SDK_LOGGER_NAME` constant. Same error, not
  introduced here.

### CI runs

| Run | Commit | Result |
|---|---|---|
| [26062128291](https://github.com/SolutionSmith-debug/its/actions/runs/26062128291) | `343b84b` | green (32s) |

### Items deferred

- **`_alert_critical` Resend wiring.** Stub remains; CRITICAL events land
  in ITS_Errors but do not push out-of-band. Slated for the
  alert-routing PR (Op Stds v8 §3 triple-fire path).
- **Pre-existing `get_setting` return-type error** on
  `shared/smartsheet_client.py:221`. Pre-dates this work; not in scope.
  Cheap to fix in the next pass — either widen the annotation to `str |
  None` or raise on missing/non-string Value (the latter doubles as a
  data-integrity check).
- **Filter scope.** `_Suppress404JSON._QUIET_STATUS_CODES = {404}`. The
  set is parameterized for trivial extension if production ever needs
  to silence 429 retry storms; not extending speculatively.

### What was NOT touched

- `shared/kill_switch.py` — zero source changes. The auto-promotion to
  ITS_Errors is a pure side effect of the `log()` extension.
- `shared/sheet_ids.py` — `SHEET_ERRORS` was already defined.
- `shared/smartsheet_client.py` core helpers — only the docstring,
  `SDK_LOGGER_NAME` constant, and `_Suppress404JSON` filter at the
  bottom changed. `_translate`, `add_rows`, `get_rows`, `get_setting`,
  etc. — untouched.
- `tests/test_kill_switch.py` and `tests/test_helpers.py` — kill_switch
  tests still pass because they mock the boundary at
  `smartsheet_client.get_setting`, and the autouse fixture in
  `test_error_log.py` keeps error_log isolated. No cross-test pollution.

### Lessons captured to memory

None. Phase B's verify-before-fix discipline is already saved
(`feedback_verify_ci_diagnosis_before_fix`); the two corrections it
produced (stream is stderr not stdout; SDK logs all non-2xx not just
404) live in this log as the worked output of that discipline.
