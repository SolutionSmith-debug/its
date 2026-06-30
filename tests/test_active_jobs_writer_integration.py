"""Live-API integration tests for shared/active_jobs_writer.py (P2.5 Slice 5).

Why this file exists:
    `active_jobs_writer.upsert_job` is the WRITE half of the job-tracker pivot's
    dual-sheet mirror (Op Stds §§50/51): it CREATEs and UPDATEs rows on a real
    Active-Jobs sheet, explodes a CC list into 5 typed slots, maps a `lifecycle`
    string onto an "Active" PICKLIST, and (Slice 6) WRITES the portal-assigned
    "Job ID" (== job_id) — the canonical id every downstream consumer joins on.
    The unit suite (tests/test_active_jobs_writer.py) mocks
    `shared.smartsheet_client` wholesale — by design it cannot catch a live
    body-shape rejection or a picklist the SDK silently coerces. Op Stds §30
    makes this live-API counterpart non-optional for any `shared/*` SDK wrapper
    with create/update/delete on typed columns.

How to run:
    Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
    -m 'not integration'). To run:

        pytest -m integration tests/test_active_jobs_writer_integration.py

    Requires ITS_SMARTSHEET_TOKEN in macOS Keychain (the same source
    the runtime SDK uses). Without that, the module-level
    `_token_available` fixture skips the whole module cleanly.

    Each test creates its own throwaway Active-Jobs-shaped sandbox sheet
    under FOLDER_SYSTEM_CONFIG and deletes it in a `finally` block — no
    orphan state, even on test failure.

Job ID is portal-owned TEXT (Slice 6 — read before editing):
    The portal now ASSIGNS the canonical JOB-###### and `upsert_job` WRITES it
    into the "Job ID" column (no Smartsheet AUTO_NUMBER, no read-back). The live
    ITS_Active_Jobs "Job ID" column is retyped AUTO_NUMBER → TEXT at the Slice-6
    cutover, so the scratch sheet's plain TEXT_NUMBER "Job ID" column now MATCHES
    the live column. The CREATE test asserts the written Job ID == job_id; the
    UPDATE test seeds a DRIFTED value and asserts the upsert self-heals it back
    to == job_id (the portal owns the column).

When to run:
    - Before merging any change to shared/active_jobs_writer.py.
    - Before merging any change to shared/smartsheet_client.py that touches
      add_rows / update_rows / get_row / get_rows / update_row_cells_by_id.
    - Periodically (operator judgment) to catch upstream SDK / picklist drift.

NOT run in CI: GitHub Actions doesn't have access to the operator's
Keychain. Running these in CI would require a sandbox token in
repository secrets, which is a deliberate decision the operator hasn't
made.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import requests  # type: ignore[import-untyped]

from shared import active_jobs_writer, keychain, picklist_validation, sheet_ids, smartsheet_client

# Each test creates its own scratch sheet then immediately reads/writes/deletes
# it, so all are exposed to Smartsheet's create→read/write eventual-consistency
# flapping (transient errorCode 1006 / HTTP 404 for several seconds after
# create; see docs/tech_debt.md "Smartsheet integration tests flake on
# create→read/write"). Mirrors tests/test_smartsheet_client_integration.py's
# approach: a rerun re-runs the whole test against a FRESH sheet, so a
# transient not-found clears; a real assertion failure still surfaces after
# the reruns are exhausted.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.flaky(reruns=3, reruns_delay=2),
]


class _SecretToken:
    """Wraps the real ITS_SMARTSHEET_TOKEN so its value can never leak into a
    pytest failure traceback.

    pytest renders a failing test's fixture/argument values via ``repr()``.
    A fixture that returned the raw token string therefore printed the live
    secret into the traceback when one of these tests failed. ``__repr__``
    here redacts (and ``str()`` / f-strings fall back to it), so the value
    only escapes via an explicit ``.reveal()`` call — the REST cleanup
    helper below is the sole caller.
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

    Returns the token wrapped in `_SecretToken` so the raw value cannot
    render in a failure traceback (see the class docstring).
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


def _delete_sheet_rest(sheet_id: int, token: _SecretToken) -> None:
    """Cleanup helper — direct REST DELETE (no SDK wrapper today).

    Takes the redacting `_SecretToken` (not a raw str) so the value cannot
    render in a traceback frame; `.reveal()` is called only to build the
    Authorization header.
    """
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token.reveal()}"},
    )


def _sandbox_name(label: str) -> str:
    """Build a sandbox sheet name <= 50 chars (Smartsheet's hard limit on
    sheet.name; errorCode 1041 — see tests/test_smartsheet_client_integration.py).
    """
    ts = datetime.now(UTC).strftime("%H%M%S_%f")
    name = f"_int_{label}_{ts}"
    assert len(name) <= 50, (
        f"sandbox name {name!r} is {len(name)} chars; Smartsheet sheet "
        f"names must be <= 50 (errorCode 1041). Shorten label."
    )
    return name


def _create_scratch_active_jobs_sheet(name: str) -> int:
    """Create a throwaway sheet shaped like an Active-Jobs sheet — exactly the
    columns `active_jobs_writer` reads or writes, plus "Notes" (an
    operator-owned column it must never clobber). Mirrors the live
    ITS_Active_Jobs / ITS_Active_Jobs_Progress column recipe in
    scripts/migrations/build_its_active_jobs_progress_sheet.py.

    "Job ID" is TEXT_NUMBER, matching the live column post-Slice-6 retype —
    see the module docstring's "Job ID is portal-owned TEXT" section.
    """
    return smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        name,
        [
            {"title": "Project Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Job ID", "type": "TEXT_NUMBER"},
            {"title": "Portal Job Key", "type": "TEXT_NUMBER"},
            {"title": "Address", "type": "TEXT_NUMBER"},
            {"title": "Stakeholder Name", "type": "TEXT_NUMBER"},
            {"title": "Stakeholder Email", "type": "TEXT_NUMBER"},
            {"title": "Stakeholder Phone", "type": "TEXT_NUMBER"},
            {"title": "Safety Reports Contact Email", "type": "TEXT_NUMBER"},
            {"title": "Safety Reports Contact Name", "type": "TEXT_NUMBER"},
            {"title": "CC 1", "type": "TEXT_NUMBER"},
            {"title": "CC 2", "type": "TEXT_NUMBER"},
            {"title": "CC 3", "type": "TEXT_NUMBER"},
            {"title": "CC 4", "type": "TEXT_NUMBER"},
            {"title": "CC 5", "type": "TEXT_NUMBER"},
            {"title": "Active", "type": "PICKLIST",
             "options": ["Active", "Inactive", "Archived"]},
            {"title": "Notes", "type": "TEXT_NUMBER"},
        ],
    )


def _scratch_write_config(sheet_id: int) -> active_jobs_writer.WriteConfig:
    """A `SAFETY_WRITE_CONFIG`-style binding pointed at a scratch sheet."""
    return active_jobs_writer.WriteConfig(
        sheet_id=sheet_id,
        contact_name_column="Safety Reports Contact Name",
        contact_email_column="Safety Reports Contact Email",
        src_contact_name_key="safety_contact_name",
        src_contact_email_key="safety_contact_email",
        src_cc_key="safety_cc",
        label="_scratch_active_jobs_writer_test",
    )


def _job_payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": f"int-ajw-{uuid.uuid4().hex[:8]}",
        "project_name": "Integration Test Job",
        "lifecycle": "active",
        "address": "1 Sandbox Way",
        "stakeholder_name": "Sam Stakeholder",
        "stakeholder_email": "sam@example.com",
        "stakeholder_phone": "5551234567",
        "safety_contact_name": "Pat Safety",
        "safety_contact_email": "pat@example.com",
        "safety_cc": ["a@x.com", "b@x.com"],
    }
    base.update(over)
    return base


# ---- upsert_job: CREATE branch -------------------------------------------


def test_upsert_job_create_branch_live_round_trip(_token_available: _SecretToken) -> None:
    """CREATE: a new job lands on the live sheet with every portal-owned
    column populated, a 6-element CC list explodes losslessly (CC 1-4 one
    each, CC 5 comma-joined overflow), "Active" is set from `lifecycle`, and
    (Slice 6) the portal-owned "Job ID" is WRITTEN == job_id, matching the
    returned `canonical_job_id` — the SDK-vs-Live write→read consistency point
    this scaffold exists to catch.
    """
    sheet_id = _create_scratch_active_jobs_sheet(_sandbox_name("ajw_create"))
    try:
        config = _scratch_write_config(sheet_id)
        job = _job_payload(
            safety_cc=["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com", "f@x.com"]
        )

        row_id, canonical_job_id = active_jobs_writer.upsert_job(config, job)

        rows = smartsheet_client.get_rows(sheet_id, filters={"Portal Job Key": job["job_id"]})
        assert len(rows) == 1, "exactly one row created"
        row = rows[0]
        assert row["_row_id"] == row_id
        assert row["Project Name"] == job["project_name"]
        assert row["Address"] == job["address"]
        assert row["Stakeholder Name"] == job["stakeholder_name"]
        assert row["Stakeholder Email"] == job["stakeholder_email"]
        assert row["Stakeholder Phone"] == job["stakeholder_phone"]
        assert row["Safety Reports Contact Name"] == job["safety_contact_name"]
        assert row["Safety Reports Contact Email"] == job["safety_contact_email"]
        assert row["Active"] == "Active"
        assert row["Portal Job Key"] == job["job_id"]
        assert row["CC 1"] == "a@x.com"
        assert row["CC 2"] == "b@x.com"
        assert row["CC 3"] == "c@x.com"
        assert row["CC 4"] == "d@x.com"
        assert row["CC 5"] == "e@x.com, f@x.com"  # >5 overflow, comma-joined, lossless

        # Slice 6: the portal owns the number — "Job ID" is WRITTEN == job_id (no AUTO_NUMBER,
        # no read-back), and the returned canonical_job_id == job_id. An independent live read
        # of the row sees the same value (SDK-vs-Live write→read consistency).
        assert row["Job ID"] == job["job_id"]
        assert canonical_job_id == job["job_id"]
        live_row = smartsheet_client.get_row(sheet_id, row_id)
        assert live_row.get("Job ID") == job["job_id"]
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# ---- upsert_job: UPDATE branch (non-clobber) ------------------------------


def test_upsert_job_update_branch_non_clobber_live_round_trip(
    _token_available: _SecretToken,
) -> None:
    """UPDATE: re-upserting the SAME `job_id` finds the existing row by
    "Portal Job Key" (no second row), overwrites only the portal-owned
    columns with the new contacts + lifecycle, leaves an operator-written
    "Notes" cell untouched, and (Slice 6) self-heals a DRIFTED "Job ID" back
    to == job_id — the portal owns the column; canonical_job_id == job_id.
    """
    sheet_id = _create_scratch_active_jobs_sheet(_sandbox_name("ajw_update"))
    try:
        config = _scratch_write_config(sheet_id)
        job = _job_payload()

        row_id, _create_canonical = active_jobs_writer.upsert_job(config, job)

        # Operator-owned cell, written directly (upsert_job never touches "Notes") —
        # must survive the UPDATE branch untouched (the non-clobber invariant, §51).
        smartsheet_client.update_rows(sheet_id, [{"_row_id": row_id, "Notes": "operator note"}])

        # Slice 6 self-heal: drift the portal-owned "Job ID" to a wrong value; the next upsert
        # must overwrite it back to == job_id (the portal owns the column).
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        job_id_col_id = next(c["id"] for c in cols if c["title"] == "Job ID")
        smartsheet_client.update_row_cells_by_id(sheet_id, row_id, {job_id_col_id: "JOB-DRIFT"})

        updated_job = dict(job)
        updated_job["stakeholder_name"] = "New Stakeholder"
        updated_job["stakeholder_email"] = "new@example.com"
        updated_job["safety_contact_name"] = "New Safety Contact"
        updated_job["safety_contact_email"] = "newsafety@example.com"
        updated_job["lifecycle"] = "archived"

        row_id2, canonical_job_id = active_jobs_writer.upsert_job(config, updated_job)
        assert row_id2 == row_id, "found-by-Portal-Job-Key — no second row created"

        rows = smartsheet_client.get_rows(sheet_id, filters={"Portal Job Key": job["job_id"]})
        assert len(rows) == 1, "still exactly one row on the sheet"
        row = rows[0]
        assert row["Stakeholder Name"] == "New Stakeholder"
        assert row["Stakeholder Email"] == "new@example.com"
        assert row["Safety Reports Contact Name"] == "New Safety Contact"
        assert row["Safety Reports Contact Email"] == "newsafety@example.com"
        assert row["Active"] == "Archived"
        assert row["Notes"] == "operator note"  # untouched — non-clobber invariant
        assert row["Job ID"] == job["job_id"]   # self-heal: drifted Job ID overwritten to job_id

        assert canonical_job_id == job["job_id"]
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# ---- upsert_job: unmapped lifecycle -> typed picklist rejection ----------


def test_upsert_job_unmapped_lifecycle_raises_picklist_violation_live(
    _token_available: _SecretToken, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `lifecycle` value with no mapping (e.g. "bogus") passes through
    verbatim into the "Active" cell; once the scratch sheet is registered in
    `picklist_validation.REGISTRY` (mirroring how the real ITS_Active_Jobs
    sheet is registered — see shared/picklist_validation.py), the write must
    raise the typed `PicklistViolationError` BEFORE any row is created on the
    live sheet — never a silent default / silent wrong write.
    """
    sheet_id = _create_scratch_active_jobs_sheet(_sandbox_name("ajw_picklist"))
    try:
        monkeypatch.setitem(
            picklist_validation.REGISTRY,
            sheet_id,
            {"Active": frozenset({"Active", "Inactive", "Archived"})},
        )
        config = _scratch_write_config(sheet_id)
        job = _job_payload(lifecycle="bogus")  # unmapped -> passes through verbatim

        with pytest.raises(picklist_validation.PicklistViolationError):
            active_jobs_writer.upsert_job(config, job)

        # Typed error surfaced BEFORE the API call — confirm live: no row exists.
        rows = smartsheet_client.get_rows(sheet_id)
        assert rows == []
    finally:
        _delete_sheet_rest(sheet_id, _token_available)
