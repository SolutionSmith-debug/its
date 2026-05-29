"""Live-API integration tests for shared/approval_verification.py +
shared/smartsheet_client.get_cell_history (F22).

Why this file exists
--------------------
Op Stds v14 §30 mandates a paired integration test for every new
`shared/*` SDK wrapper that performs create / update / delete on typed
columns or rows. F22 added two surfaces that touch Smartsheet's typed
cell-history API:

  - `smartsheet_client.get_cell_history(sheet_id, row_id, column_title)`
    — wraps `Cells.get_cell_history`, deserializes into `CellHistoryEvent`
    dataclasses, and resolves the column by title (same cache semantics as
    `_resolve_cells`). The `modifiedBy` payload exposes only `{name, email}`;
    `actor_user_id` comes back `None` from the live API.

  - `approval_verification.verify_approval(sheet_id, row_id, approval_column,
    *, authorized_actors)` — a total function (never raises) that reads cell
    history and returns an `ApprovalVerdict`. Fail-CLOSED posture: any
    inability to verify returns `verified=False`. The match key is
    `actor_email`, case-insensitively.

Four preceding SDK-vs-Live bugs (PRs #47/#48/#49/#51) all had green
`SimpleNamespace`-mock unit tests AND failing live API calls. This file
exercises the full create → write → history-read → verdict cycle against
a real Smartsheet sandbox sheet so any future shape drift between the SDK
mock surface and the live API surfaces here.

How to run
----------
Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
`-m 'not integration'`). To run:

    .venv/bin/pytest -m integration tests/test_approval_verification_integration.py

Requires `ITS_SMARTSHEET_TOKEN` in macOS Keychain (the same Keychain entry
the production runtime uses). Without that credential, the module-level
`_token_available` fixture skips the entire module cleanly — no failures,
just a skip notice.

Sandbox resources created / torn down
--------------------------------------
One throwaway sheet is created in `FOLDER_SYSTEM_CONFIG` (the established
sandbox parent across all integration tests in this repo). The sheet name
carries a `_int_` prefix and a microsecond timestamp so it is visually
distinct from real config artifacts in the Smartsheet UI. The sheet is
deleted in a `finally` block — including on test failure — leaving no
orphan state.

What is proven end-to-end
--------------------------
1. `get_cell_history` returns at least one event after a real `update_rows`
   write, the latest event's value is truthy (checkbox checked = True), and
   `actor_email` is a non-empty string.
2. `verify_approval` with the real actor's email in `authorized_actors`
   yields `verified=True` / `reason=AUTHORIZED`.
3. `verify_approval` with the SAME live history but a non-authorized email
   in `authorized_actors` yields `verified=False` /
   `reason=UNAUTHORIZED_ACTOR` — the fail-CLOSED gate holds.

NOT run in CI
-------------
GitHub Actions doesn't have access to the operator's Keychain. Running
these in CI would require a sandbox token in repository secrets — a
deliberate decision the operator hasn't made.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import requests  # type: ignore[import-untyped]

from shared import keychain, sheet_ids, smartsheet_client
from shared.approval_verification import VerdictReason, verify_approval

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Module-level credential guard
# ---------------------------------------------------------------------------


class _SecretToken:
    """Wraps the real ITS_SMARTSHEET_TOKEN so its value can never leak into a
    pytest failure traceback.

    pytest renders a failing test's fixture/argument values via ``repr()``.
    A fixture that returned the raw token string therefore printed the live
    secret into the traceback when one of these tests failed — which forced a
    real token rotation this session. ``__repr__`` here redacts (and ``str()``
    / f-strings fall back to it), so the value only escapes via an explicit
    ``.reveal()`` call — the REST cleanup helper below is the sole caller.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw token. Call only where the real value is required
        (the ``Authorization: Bearer`` header in REST cleanup)."""
        return self._value

    def __repr__(self) -> str:
        return "<ITS_SMARTSHEET_TOKEN redacted>"


@pytest.fixture(scope="module")
def _token_available() -> _SecretToken:
    """Skip the whole module if ITS_SMARTSHEET_TOKEN isn't in Keychain.

    Returns the token wrapped in `_SecretToken` so cleanup helpers can call
    the REST API directly (via `.reveal()`) when no SDK-level delete wrapper
    exists, while the raw value can never render in a failure traceback.
    """
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return _SecretToken(token)


@pytest.fixture(scope="module", autouse=True)
def _reset_smartsheet_client() -> Iterator[None]:
    """Force a fresh real-token Smartsheet client for this module.

    `smartsheet_client._client` is a process-wide singleton built lazily from
    the keychain token. In an isolated `pytest -m integration` run the
    conftest keychain opt-out already guarantees it is built with the real
    token, so this fixture is a no-op there. But in a MIXED-process run (full
    suite / `pytest -m ''` / IDE "run all"), an earlier unit test runs with
    the autouse keychain stub active and can prime `_client` with the fake
    `"test-ITS_SMARTSHEET_TOKEN"` — which would then 401 here. Resetting on
    entry forces a rebuild from the (now real) keychain; resetting on exit
    keeps this module's real-token client from leaking into a unit test that
    runs afterward in the same process.
    """
    smartsheet_client._client = None
    yield
    smartsheet_client._client = None


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _delete_sheet_rest(sheet_id: int, token: _SecretToken) -> None:
    """Cleanup helper — direct REST DELETE (no SDK delete-sheet wrapper today).

    Mirrors the precedent in test_smartsheet_client_integration.py.
    Takes the redacting `_SecretToken` (not a raw str) so the value cannot
    render in a traceback frame; `.reveal()` is called only to build the
    Authorization header. Swallows failures silently: teardown cleanup should
    not mask the real test outcome.
    """
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token.reveal()}"},
    )


def _sandbox_name(label: str) -> str:
    """Build a timestamped sandbox sheet name within Smartsheet's 50-char limit.

    Layout: `_int_<label>_HHMMSS_µµµµµµ`.
    Matches the naming convention from test_smartsheet_client_integration.py
    (errorCode 1041 from the live API triggered that limit discovery).
    """
    ts = datetime.now(UTC).strftime("%H%M%S_%f")
    name = f"_int_{label}_{ts}"
    assert len(name) <= 50, (
        f"sandbox name {name!r} is {len(name)} chars; Smartsheet sheet "
        f"names must be <= 50 (errorCode 1041). Shorten label."
    )
    return name


def _wait_for_history(
    sheet_id: int,
    row_id: int,
    column: str,
    *,
    attempts: int = 8,
    delay_s: float = 0.5,
) -> list[smartsheet_client.CellHistoryEvent]:
    """Poll `get_cell_history` until at least one event is visible.

    Smartsheet's cell-history endpoint is eventually consistent: for up to a
    few seconds after `update_rows` returns, `get_cell_history` can still
    report zero events for the just-written cell. A test that reads history
    with no intervening settle therefore races — and an empty read is exactly
    `verify_approval`'s fail-CLOSED `NO_HISTORY` verdict, which masks the
    authorization path under test (observed deterministically once the
    keychain fix let these tests reach the live API). This bounded poll
    (default ~3.5 s ceiling) returns the events as soon as any appear; on
    exhaustion it returns the last (empty) result so the caller's own
    assertion still surfaces the failure with full context.
    """
    events = smartsheet_client.get_cell_history(sheet_id, row_id, column)
    for _ in range(attempts - 1):
        if events:
            break
        time.sleep(delay_s)
        events = smartsheet_client.get_cell_history(sheet_id, row_id, column)
    return events


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_get_cell_history_after_checkbox_write(_token_available: _SecretToken) -> None:
    """get_cell_history returns a real history event after update_rows writes
    a CHECKBOX cell.

    Flow:
      1. Create throwaway sheet with a TEXT_NUMBER primary column and an
         "Approved for Send" CHECKBOX column.
      2. Add one row.
      3. Set "Approved for Send" to True via update_rows.
      4. Read cell history — assert at least one event, the latest value is
         truthy, and actor_email is a non-empty string.

    This catches any SDK-vs-Live shape mismatch in the get_cell_history
    deserializer (CellHistoryEvent field mapping, `modified_by` attribute
    path, `include_all` pagination flag) before it reaches production.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("cell_hist_write"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Approved for Send", "type": "CHECKBOX"},
        ],
    )
    try:
        # Step 1: add a row so there is a row to update.
        row_ids = smartsheet_client.add_rows(
            sheet_id,
            [{"Name": "integration-test-row"}],
        )
        assert len(row_ids) == 1, "add_rows must return exactly one row ID"
        row_id = row_ids[0]

        # Step 2: write True into the checkbox — this is the approval event
        # that get_cell_history must surface.
        smartsheet_client.update_rows(
            sheet_id,
            [{"_row_id": row_id, "Approved for Send": True}],
        )

        # Step 3: read the cell history. Poll to absorb Smartsheet's
        # cell-history eventual-consistency window after the write above.
        events = _wait_for_history(sheet_id, row_id, "Approved for Send")

        # At least one event must exist after the write above.
        assert len(events) >= 1, (
            f"Expected at least 1 history event; got {len(events)}. "
            "Smartsheet may not have committed history for a freshly-created cell."
        )

        # The latest value must be truthy (True / 1 / non-empty — CHECKBOX
        # checked state). Pick the newest-timestamped event; fall back to
        # index 0 if no timestamps (matches _latest_event logic in the SUT).
        dated = [e for e in events if e.modified_at is not None]
        latest = max(dated, key=lambda e: e.modified_at) if dated else events[0]  # type: ignore[arg-type,return-value]

        assert latest.value, (
            f"Expected the latest history event to carry a truthy value "
            f"(checkbox=True), got {latest.value!r}"
        )

        # actor_email must be a non-empty string — this is the identity that
        # verify_approval will match against the authorized_actors set.
        assert isinstance(latest.actor_email, str) and latest.actor_email, (
            f"Expected actor_email to be a non-empty string; "
            f"got {latest.actor_email!r}. "
            "The cell-history modifiedBy payload may not include email."
        )

        # actor_user_id is expected to be None today (Smartsheet omits it
        # from the cell-history modifiedBy payload — see module docstring in
        # shared/smartsheet_client.py). Assert None rather than a value so
        # that if the API ever starts returning an ID the test surfaces the
        # change rather than silently ignoring it.
        assert latest.actor_user_id is None, (
            f"Expected actor_user_id=None (live API does not populate it in "
            f"cell-history modifiedBy); got {latest.actor_user_id!r}. "
            "If Smartsheet now returns a user ID here, update "
            "shared/smartsheet_client.py and shared/approval_verification.py "
            "to use it as the match key (more stable than email)."
        )
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


def test_verify_approval_authorized_actor(_token_available: _SecretToken) -> None:
    """verify_approval returns verified=True / AUTHORIZED when the real actor
    is in authorized_actors.

    This is the happy-path test for the External Send Gate boundary: a row
    genuinely approved by an authorized reviewer must produce a green verdict
    so the send can proceed.

    Flow:
      1. Create throwaway sheet + row.
      2. Write True to "Approved for Send" (operator's sandbox account).
      3. Read cell history to discover the actual actor_email.
      4. Call verify_approval with that email in authorized_actors.
      5. Assert verified=True, reason=AUTHORIZED.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("verify_auth_actor"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Approved for Send", "type": "CHECKBOX"},
        ],
    )
    try:
        row_ids = smartsheet_client.add_rows(
            sheet_id,
            [{"Name": "approval-test-row"}],
        )
        row_id = row_ids[0]

        smartsheet_client.update_rows(
            sheet_id,
            [{"_row_id": row_id, "Approved for Send": True}],
        )

        # Discover the real actor_email from live history — do not hardcode it
        # so the test works under any sandbox credentials without modification.
        # Poll to absorb the cell-history eventual-consistency window.
        events = _wait_for_history(sheet_id, row_id, "Approved for Send")
        assert events, "Precondition: must have at least one history event"
        dated = [e for e in events if e.modified_at is not None]
        latest = max(dated, key=lambda e: e.modified_at) if dated else events[0]  # type: ignore[arg-type,return-value]
        actor_email = (latest.actor_email or "").strip().lower()
        assert actor_email, (
            "Precondition: actor_email must be non-empty to test authorization"
        )

        # The authorized_actors set contains exactly the real actor.
        verdict = verify_approval(
            sheet_id,
            row_id,
            "Approved for Send",
            authorized_actors=frozenset({actor_email}),
        )

        assert verdict.verified is True, (
            f"Expected verified=True; got False with reason={verdict.reason!r}, "
            f"detail={verdict.detail!r}"
        )
        assert verdict.reason is VerdictReason.AUTHORIZED, (
            f"Expected reason=AUTHORIZED; got {verdict.reason!r}"
        )
        # actor on the verdict must match the email we authorized.
        assert verdict.actor == actor_email, (
            f"verdict.actor={verdict.actor!r} does not match the authorized "
            f"actor {actor_email!r}"
        )
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


def test_verify_approval_unauthorized_actor(_token_available: _SecretToken) -> None:
    """verify_approval returns verified=False / UNAUTHORIZED_ACTOR when the
    real actor is NOT in authorized_actors.

    This is the fail-CLOSED gate test: the same live history that produces
    AUTHORIZED above must produce UNAUTHORIZED_ACTOR when a different
    (non-authorized) email is passed as the allowlist. A send must be
    blocked.

    Flow:
      1. Create throwaway sheet + row.
      2. Write True to "Approved for Send" (operator's sandbox account).
      3. Call verify_approval with an email that is NOT the operator's.
      4. Assert verified=False, reason=UNAUTHORIZED_ACTOR.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("verify_unauth_actor"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Approved for Send", "type": "CHECKBOX"},
        ],
    )
    try:
        row_ids = smartsheet_client.add_rows(
            sheet_id,
            [{"Name": "unauth-test-row"}],
        )
        row_id = row_ids[0]

        smartsheet_client.update_rows(
            sheet_id,
            [{"_row_id": row_id, "Approved for Send": True}],
        )

        # Settle the cell-history eventual-consistency window BEFORE calling
        # verify_approval. Without this, the verdict races to NO_HISTORY (an
        # empty read) instead of reaching the UNAUTHORIZED_ACTOR path this
        # test targets — verify_approval reads history immediately, so unlike
        # the two tests above there is no intervening round-trip to absorb the
        # lag. This precondition both waits and asserts the event is visible.
        settle_events = _wait_for_history(sheet_id, row_id, "Approved for Send")
        assert settle_events, (
            "Precondition: approval history must be visible before the verdict; "
            "verify_approval fail-CLOSED-returns NO_HISTORY on an empty read, "
            "which would mask the UNAUTHORIZED_ACTOR path under test."
        )

        # authorized_actors contains only a known-wrong email, not the
        # operator's sandbox address. The real actor's email is absent.
        verdict = verify_approval(
            sheet_id,
            row_id,
            "Approved for Send",
            authorized_actors=frozenset({"nobody@evergreenmirror.com"}),
        )

        assert verdict.verified is False, (
            f"Expected verified=False (fail-CLOSED); got True. "
            f"detail={verdict.detail!r}"
        )
        assert verdict.reason is VerdictReason.UNAUTHORIZED_ACTOR, (
            f"Expected reason=UNAUTHORIZED_ACTOR; got {verdict.reason!r}. "
            f"detail={verdict.detail!r}"
        )
        # actor on the verdict is the REAL actor, even though the verdict
        # is negative — confirms the history was read and actor extracted.
        assert verdict.actor is not None, (
            "verdict.actor must be set (the real actor was identified; "
            "they're just not in the authorized set)"
        )
        assert verdict.actor != "", "verdict.actor must be a non-empty string"
    finally:
        _delete_sheet_rest(sheet_id, _token_available)
