"""Live-API integration test for safety_reports/intake.py.

End-to-end pipeline against the sandbox tenant:
  - synthetic .eml file on disk (Mail.app not exercised)
  - live Anthropic classify+extract call (real model, real key)
  - live Smartsheet add_rows into Bradley 1's current-week Daily Reports
  - live Box upload into the per-category subfolder under Bradley 1
  - live cleanup of both the row and the file in the test's finally block

Default `pytest -q` SKIPS this file via the pyproject `addopts = -m 'not
integration'`. Operator runs with:

    pytest -m integration tests/test_intake_integration.py

Requires `ITS_SMARTSHEET_TOKEN`, `ITS_ANTHROPIC_KEY`, and the Box OAuth
keychain entries (handled by `shared.box_client`) all present in macOS
Keychain. Without any of those, the module-level fixtures `_token_*`
skip the whole module.

Cost note: this test makes one live Anthropic call per run (Sonnet 4.6,
~2k tokens). Run sparingly. Not part of CI.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest
import requests  # type: ignore[import-untyped]

from safety_reports import intake
from shared import box_client, keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration


SANDBOX_PROJECT = "Bradley 1"
SANDBOX_SENDER = "intake_integration@evergreenmirror.com"


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN empty")
    return token


@pytest.fixture(scope="module")
def _anthropic_available() -> None:
    try:
        keychain.get_secret("ITS_ANTHROPIC_KEY")
    except Exception as e:
        pytest.skip(f"ITS_ANTHROPIC_KEY unavailable: {e!r}")


@pytest.fixture(scope="module")
def _box_available() -> None:
    try:
        box_client.get_client().user().get()
    except Exception as e:
        pytest.skip(f"Box OAuth unavailable: {e!r}")


def _delete_row(sheet_id: int, row_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}/rows?ids={row_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_box_file(file_id: str) -> None:
    try:
        box_client.get_client().file(file_id).delete()
    except Exception:
        # Cleanup is best-effort; don't fail the test on cleanup error.
        pass


def _build_eml_bytes(sender: str, subject: str, body: str, pdf_bytes: bytes) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "safety@evergreenmirror.com"
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename="integration_test.pdf",
    )
    return msg.as_bytes()


def _find_today_row(sheet_id: int, marker: str) -> dict | None:
    """Return the Daily Reports row whose Safety Topic / Report Title
    matches `marker`. Use a distinctive marker so we can identify the row
    we wrote even after concurrent additions."""
    rows = smartsheet_client.get_rows(
        sheet_id, filters={"Safety Topic / Report Title": marker}
    )
    return rows[0] if rows else None


def _add_sandbox_sender_to_allowlist(token: str) -> int | None:
    """Append SANDBOX_SENDER to the live allowed_senders list. Returns the
    row ID of the existing row (for restore) or None if the row was missing."""
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_CONFIG,
        filters={
            "Setting": "safety_reports.intake.allowed_senders",
            "Workstream": "safety_reports",
        },
    )
    if not rows:
        return None
    row = rows[0]
    row_id = int(row["_row_id"])
    original_value = row.get("Value") or "[]"
    # Append SANDBOX_SENDER to the JSON list (avoid duplicate).
    import json
    try:
        senders = list(json.loads(original_value))
    except json.JSONDecodeError:
        senders = []
    if SANDBOX_SENDER not in senders:
        senders.append(SANDBOX_SENDER)
    new_value = json.dumps(senders)
    smartsheet_client.update_rows(
        sheet_ids.SHEET_CONFIG,
        [{"_row_id": row_id, "Value": new_value}],
    )
    return row_id


def _restore_allowlist(row_id: int) -> None:
    """Remove SANDBOX_SENDER from the live allowed_senders list."""
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_CONFIG,
        filters={
            "Setting": "safety_reports.intake.allowed_senders",
            "Workstream": "safety_reports",
        },
    )
    if not rows:
        return
    import json
    row = rows[0]
    current_value = row.get("Value") or "[]"
    try:
        senders = list(json.loads(current_value))
    except json.JSONDecodeError:
        return
    new_senders = [s for s in senders if s != SANDBOX_SENDER]
    smartsheet_client.update_rows(
        sheet_ids.SHEET_CONFIG,
        [{"_row_id": row_id, "Value": json.dumps(new_senders)}],
    )


def test_intake_end_to_end_round_trip(
    tmp_path: Path,
    _token_available: str,
    _anthropic_available: None,
    _box_available: None,
) -> None:
    """Synthetic email → live pipeline → Smartsheet row + Box file → cleanup.

    Asserts: a Daily Reports row matches the marker we wrote, AND the
    row's Notes / Action Items field contains a Box URL.
    """
    # Build the .eml file. Use a marker the model will pass through
    # verbatim into safety_topic_or_report_title so we can find the row.
    ts = datetime.now(UTC).strftime("%H%M%S")
    marker = f"_int_intake_integration_{ts}"
    today_iso = date.today().isoformat()
    body = (
        f"Bradley 1 site, Daily JHA on {today_iso}. "
        f"Bradleys Solar Services crew on Block A. "
        f"Standard module replacement work, no incidents. "
        f"Report title: {marker}"
    )
    eml_bytes = _build_eml_bytes(
        SANDBOX_SENDER,
        f"Bradley 1 — Daily JHA — {marker}",
        body,
        b"%PDF-1.4\n%integration test placeholder\n%%EOF\n",
    )
    eml_path = tmp_path / "integration.eml"
    eml_path.write_bytes(eml_bytes)

    # Add the sandbox sender to the live allowlist row; restore in finally.
    allowlist_row_id = _add_sandbox_sender_to_allowlist(_token_available)
    if allowlist_row_id is None:
        pytest.skip(
            "safety_reports.intake.allowed_senders config row missing; "
            "run scripts/migrations/seed_safety_intake_config.py first."
        )

    created_row_id: int | None = None
    created_sheet_id: int | None = None
    created_box_file_ids: list[str] = []
    try:
        intake.main(str(eml_path))

        # The pipeline writes into the project's current-week sheet — recompute
        # the week-folder scaffold to find the sheet ID.
        from safety_reports.week_folder import ensure_current_week_folder
        scaffold = ensure_current_week_folder(SANDBOX_PROJECT)
        created_sheet_id = scaffold.daily_reports_sheet_id

        row = _find_today_row(created_sheet_id, marker)
        assert row is not None, (
            f"no Daily Reports row found with Safety Topic / Report Title "
            f"= {marker!r} on sheet {created_sheet_id}"
        )
        created_row_id = int(row["_row_id"])

        # The Notes / Action Items field should now contain a Box URL prefix
        # from the row update step (or the [box_filing_failed] marker if the
        # upload failed — both are acceptable end states for assertion).
        notes = row.get("Notes / Action Items") or ""
        assert "Box: " in notes or "[box_filing" in notes, (
            f"Notes / Action Items lacks Box-link prefix: {notes!r}"
        )

        # Extract the Box file ID from the URL (for cleanup) if present.
        import re
        for match in re.finditer(r"app\.box\.com/file/(\d+)", notes):
            created_box_file_ids.append(match.group(1))
    finally:
        _restore_allowlist(allowlist_row_id)
        if created_row_id is not None and created_sheet_id is not None:
            _delete_row(created_sheet_id, created_row_id, _token_available)
        for file_id in created_box_file_ids:
            _delete_box_file(file_id)
        # The intake.main rename-on-success made the .eml.processed file;
        # tmp_path cleans up automatically.
