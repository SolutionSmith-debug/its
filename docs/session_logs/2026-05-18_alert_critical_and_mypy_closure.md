# 2026-05-18 — `_alert_critical` Resend wiring + mypy tech-debt closure

Two-phase session per the brief. Phase A closed three of four mypy
tech-debt entries from PR #17's inventory. Phase B wired
`shared/error_log.py`'s previously-stub `_alert_critical` to Resend,
implementing the third leg of the Op Stds v8 §3 triple-fire path.

## Commits landed

### Phase A — three small fixes

| PR | SHA | Tech-debt entry closed |
|---|---|---|
| #18 | `3ecbe81` | `parse_job_v3.py:767 matched annotation` |
| #19 | `ae36a8a` | `smartsheet_migration/ss_api.py:79 api body arg` |
| #20 | `f5d0acd` | `smartsheet_migration/migrate_fl.py:176 warnings annotation` |

### Phase B — Resend wiring (this PR)

| Commit | Title |
|---|---|
| _(this PR)_ | feat(shared): wire _alert_critical to Resend for CRITICAL alerts |

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26070052920](https://github.com/SolutionSmith-debug/its/actions/runs/26070052920) | `3ecbe81` (PR #18) | green (28s) |
| [26070146344](https://github.com/SolutionSmith-debug/its/actions/runs/26070146344) | `ae36a8a` (PR #19) | green (25s) |
| [26070235764](https://github.com/SolutionSmith-debug/its/actions/runs/26070235764) | `f5d0acd` (PR #20) | green (27s) |

This PR's run lands when it's pushed; URL to be backfilled on a future
chore touch per the established convention.

## Decisions made during session

### Phase A — Path 1 vs Path 2 triage

Inspected each of the four PR #17 mypy entries before fixing.

- **`parse_job_v3.py:767 matched annotation`** → Path 1. The variable was
  `matched = {s: [] for s in _V3_SIGNATURES}`. Type inferable from
  `_V3_SIGNATURES.keys()` (`Schema` enum) and `.append(name)` callsite
  (`name: str`). One-line annotation. Preservation §14 honored (only the
  annotation line touched, function body unchanged).
- **`ss_api.py:79 api body arg`** → Path 1. Real under-specification —
  `api()`'s `body` was `dict | None` but `add_rows()` passed `list[dict]`.
  Widened to `dict | list | None`. Real-bug carve-out under §14.
- **`migrate_fl.py:176 warnings annotation`** → Path 1. Element type
  inferable from `.append()` callsites (string literals). One-line
  annotation `warnings: list[str] = []`.
- **Vendor-SDK import-untyped noise (4 errors)** → Path 2. The brief
  explicitly named this as a Path 2 candidate ("anything in the
  vendor-SDK noise silencing direction probably qualifies"). Fix would
  require `pyproject.toml` config block + possibly `types-requests`
  dependency addition — neither is a "1-3 line annotation narrow."
  Entry stays OPEN; remains an input to the future mypy-in-CI decision.

PRs were shipped separately per the brief's "One PR per fixed entry,
OR one bundled PR if and only if all fixes touch the same file" — the
three fixes touched three different files.

### Phase B — retry strategy

Mirrored `shared/graph_client.py`'s `_request()` shape exactly:
- `MAX_RETRIES = 3`
- Retry on 429 and 503 only
- Honor `Retry-After` header if parseable as seconds
- Exponential backoff fallback (`2 ** attempt`)
- Last attempt's response is what gets `_check_response()`'d

Difference from graph_client: Resend has no token TTL — auth is a
bearer API key, lazy-loaded once from Keychain via `get_client()`
(named to match the brief; really just an API-key string accessor).
No token refresh logic needed.

### Phase B — test mocking boundary

Tests mock at `shared.resend_client.requests.request` (the HTTP
boundary) and `shared.resend_client.keychain.get_secret` (the auth
boundary). Same boundaries as `test_graph_client.py`. Result: 16 new
tests in `tests/test_resend_client.py`, no live HTTP, no live Keychain
read.

For `tests/test_error_log.py`, added a second autouse fixture
(`send_alert_mock`) parallel to the existing `add_rows_mock`. Both mocks
fire on every test by default; tests that want to assert against them
take the fixture as a parameter. Without this, the existing decorator-
CRITICAL tests would have hit live Keychain trying to read
`ITS_RESEND_API_KEY`. The two failures that surfaced when I first ran
the suite are documented under "Lessons captured" below.

### Phase B — broader-than-`ResendError` catch in `_alert_critical`

The brief specified "catch `ResendError`" but also "must NOT raise...
anything." When the Keychain entry isn't seeded (current operator
state), `keychain.get_secret` raises `KeychainError`, not `ResendError`
— a narrow `except ResendError` would have let `KeychainError`
propagate up through the decorator, breaking the caller's script.

Resolved the tension by widening to `except Exception` with a comment
documenting the choice. The brief's "must NOT raise...anything" intent
trumps the narrower exception class spec. The marker line includes the
exception type (`f"[resend-alert-failed] {e!r}"`) so operators can see
which path failed.

### Phase B — Resend API key NOT in Keychain at smoke time

Preflight check confirmed `ITS_RESEND_API_KEY` is missing from
Keychain:

```
$ security find-generic-password -a $USER -s ITS_RESEND_API_KEY -w
security: SecKeychainSearchCopyNext: The specified item could not be
found in the keychain.
```

Per the brief: "wire the code, ship the tests, but don't attempt the
smoke test." Code + tests landed; smoke deferred. `scripts/smoke_test_resend.py`
exists and is ready for the operator to run after seeding the key.

Hand-off item: operator must add the Resend API key with:

```
security add-generic-password -a $USER -s ITS_RESEND_API_KEY -w <YOUR_KEY>
```

…and verify the sender domain (`DEFAULT_FROM` placeholder in
`shared/resend_client.py`) in their Resend dashboard. Until both are
done, the smoke test will surface `ResendAuthError` from Resend's API.

### Phase B — message-consistency fix in the decorator

While writing the `_alert_critical` integration tests, noticed the
decorator was passing `str(e)` to `_alert_critical` but `f"unhandled: {e}"`
to `log()`. That meant the Smartsheet `Message` cell and the Resend
alert subject/body would differ — the same CRITICAL event, two
different texts across the triple-fire channels.

Adjusted the decorator to compute `msg = f"unhandled: {e}"` once and
pass it to both `log()` and `_alert_critical`. Behavior change for
operators reading alerts: subject and body now match the Smartsheet
row text exactly. Out-of-scope-but-tiny.

## Open items handed off

- **Sentry hook in `error_log.py`** — second leg of the triple-fire.
  Same shape as Resend wiring but different SDK. Separate PR.
- **Alert-routing dedupe** — when Sentry + Smartsheet + Resend all fire
  on one CRITICAL event, the design for de-noising the operator's
  inbox is a separate question. Out of scope tonight.
- **Vendor-SDK import-untyped noise** — Phase A Path 2 deferral.
  Still OPEN in `docs/tech_debt.md`. Should land before any mypy-in-CI
  integration so signal-to-noise is acceptable.
- **Resend smoke test** — blocked on operator adding API key to Keychain
  AND verifying the sender domain in Resend dashboard. Re-run
  `scripts/smoke_test_resend.py` once both are done; expect three
  outputs (key loaded, direct send_alert succeeded, _alert_critical
  succeeded) and two test emails delivered to
  `seths@evergreenmirror.com`.
- **DEFAULT_FROM sender placeholder** — `alerts@its.solutionsmith.org`
  in `shared/resend_client.py`. Update to the operator's verified
  Resend sender domain when known. Optional follow-up: move to an
  `ITS_Config` row (`system.alert_from_address`) if multi-tenant
  addressing is ever needed.
- **mypy-in-CI decision** — unchanged from PR #17's framing. The 4
  remaining errors (all import-untyped baseline noise) would need
  silencing first.
- **4 older `box_migration` tech_debt entries** — V/S vendor-sub, ISO
  date prefix, person_tag over-match audit, smartsheet_migration
  import-time side effects. Unchanged; each gets its own focused PR.

## What was NOT touched

Verbatim from the brief's "What this does NOT include":

- Sentry hook in `error_log.py` — separate PR.
- Alert routing rules / dedupe — separate design.
- HTML emails / fancy formatting — plain text only.
- Customer-facing email infrastructure — Resend is for operator
  alerts only; customer email stays on `graph_client.send_mail`.
- The four older `box_migration` / `smartsheet_migration` tech-debt
  items (V/S vendor-sub, ISO date prefix, person_tag over-match,
  smartsheet_migration import-time side effects).

## Lessons captured

Two test-isolation patterns generalized from this session:

1. **New side-channel writes need new autouse mocks in caller test
   files.** PR #11's `error_log` Smartsheet wiring established the
   `add_rows_mock` autouse pattern in `tests/test_error_log.py`. When
   I added the Resend leg tonight, two existing decorator-CRITICAL
   tests immediately broke because they were now hitting live
   Keychain. The fix was a second autouse fixture mirroring the
   first. **General rule:** when a function adds a new side channel
   that uses external state (Keychain, network, Smartsheet, etc.),
   the caller's test file needs a parallel autouse mock — otherwise
   the new path leaks through into previously-isolated tests.

2. **Triple-fire failure isolation deserves a shared helper, eventually.**
   `_smartsheet_log` and `_alert_critical` now both implement the same
   pattern: recursion guard flag + try/finally + broad-exception catch
   + marker-line fallback via `_local_log`. If Sentry lands as the third
   leg, we'll have three copies of this pattern. Worth considering a
   `_protected_sidechannel(name, fn)` helper at that point. Not
   extracting tonight (preservation-over-refactor: defer until ≥4
   real cases, and we're at 2 → 3 with Sentry pending). Flagging here
   so the next person who touches the file sees the pattern.

## Sequencing context

Lands at the end of the 2026-05-18 work block. 12 PRs landed today
(#9 through #20 + this one). Tomorrow's natural next start is one of:
(a) Sentry hook to complete the triple-fire, (b) the `review_queue` +
`quarantine` paired PR per Phase 1 critical path, (c) the deferred
vendor-SDK silencing + mypy-in-CI decision.

Per the brief: stopping here. No rolling into the older box_migration
tech-debt items or any other queued work.
