"""Live-API integration test for safety_reports/intake.py.

End-to-end pipeline against the sandbox tenant:
  - synthetic .eml file on disk (Mail.app not exercised)
  - live Anthropic classify+extract call (real model, real key)
  - live Smartsheet add_rows into Bradley 1's current-week Daily Reports
    OR live add to ITS_Review_Queue (whichever path the pipeline takes
    based on the model's confidence + anomaly self-report)
  - live Box upload into the per-category subfolder under Bradley 1
    (happy path only)
  - live cleanup of any rows + files this test created, in finally

Default `pytest -q` SKIPS this file via the pyproject `addopts = -m 'not
integration'`. Operator runs with:

    pytest -m integration tests/test_intake_integration.py

Requires `ITS_SMARTSHEET_TOKEN`, `ITS_ANTHROPIC_KEY`, and the Box OAuth
keychain entries (handled by `shared.box_client`) all present in macOS
Keychain. Without any of those, the module-level fixtures `_token_*`
skip the whole module.

Cost note: this test makes one live Anthropic call per run (Sonnet 4.6,
~2k tokens). Run sparingly. Not part of CI.

Why the assertion accepts either path
-------------------------------------

intake.py's contract is: route an inbound email somewhere sensible. The
"somewhere" varies based on the model's classification confidence and
anomaly-flag self-report against the configured threshold + sentinel
list. A synthetic test email can land at either:

  - Daily Reports row (happy path, `confidence >= threshold`, no
    high-severity anomalies)
  - ITS_Review_Queue row (any gate fires: low confidence, anomaly,
    structured-output edge, project unresolved)

Both outcomes prove the pipeline is wired end-to-end. The test asserts
XOR: exactly one of the two paths produced a row. Neither would mean
the pipeline silently dropped the message; both would mean a duplicated
write bug. The print line surfaces which path fired so the operator can
spot a routing-pattern drift across runs without needing to dig into
the row data.
"""
from __future__ import annotations

import re
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
WORKSTREAM = "safety_reports"


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


def _find_daily_reports_row(sheet_id: int, marker: str) -> dict | None:
    """Return the Daily Reports row whose Safety Topic / Report Title
    matches `marker`. The marker is embedded in the synthetic email body
    so the model carries it verbatim into the title field."""
    rows = smartsheet_client.get_rows(
        sheet_id, filters={"Safety Topic / Report Title": marker}
    )
    return rows[0] if rows else None


def _find_review_queue_row(source_file: str) -> dict | None:
    """Return the ITS_Review_Queue row whose Source File matches the
    .eml path we wrote. intake.py passes `email_path` as `source_file`
    on every review-queue write, so the row carries the exact .eml path
    (unique per pytest tmp_path) — clean primary key for cleanup."""
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_REVIEW_QUEUE,
        filters={"Source File": source_file, "Workstream": WORKSTREAM},
    )
    return rows[0] if rows else None


def _find_quarantine_row(sender: str, subject: str) -> dict | None:
    """Return the ITS_Quarantine row matching sender + subject.

    No current code path in this test triggers the quarantine branch
    (the sandbox sender is allowlisted in setup), but the finally cleans
    up defensively for symmetry — if a future test exercises the
    quarantine path, this helper is already in place."""
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_QUARANTINE,
        filters={"Sender": sender, "Subject": subject},
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Synthetic email → live pipeline → exactly one of Daily Reports OR Review Queue → cleanup."""
    # Build the .eml file. Use a marker the model will pass through
    # verbatim into safety_topic_or_report_title so we can find a Daily
    # Reports row by it; the .eml path serves as the primary key for
    # ITS_Review_Queue cleanup via the Source File column.
    ts = datetime.now(UTC).strftime("%H%M%S")
    marker = f"_int_intake_integration_{ts}"
    subject = f"Bradley 1 — Daily JHA — {marker}"
    today_iso = date.today().isoformat()
    body = (
        f"Bradley 1 site, Daily JHA on {today_iso}. "
        f"Bradleys Solar Services crew on Block A. "
        f"Standard module replacement work, no incidents. "
        f"Report title: {marker}"
    )
    eml_bytes = _build_eml_bytes(
        SANDBOX_SENDER,
        subject,
        body,
        b"%PDF-1.4\n%integration test placeholder\n%%EOF\n",
    )
    eml_path = tmp_path / "integration.eml"
    eml_path.write_bytes(eml_bytes)
    eml_path_str = str(eml_path)

    # Add the sandbox sender to the live allowlist row; restore in finally.
    allowlist_row_id = _add_sandbox_sender_to_allowlist(_token_available)
    if allowlist_row_id is None:
        pytest.skip(
            "safety_reports.intake.allowed_senders config row missing; "
            "run scripts/migrations/seed_safety_intake_config.py first."
        )

    created_sheet_id: int | None = None
    created_box_file_ids: list[str] = []
    try:
        intake.main(eml_path_str)

        # The pipeline writes into the project's current-week sheet — recompute
        # the week-folder scaffold to find the sheet ID.
        from safety_reports.week_folder import ensure_current_week_folder
        scaffold = ensure_current_week_folder(SANDBOX_PROJECT)
        created_sheet_id = scaffold.daily_reports_sheet_id

        daily_row = _find_daily_reports_row(created_sheet_id, marker)
        review_row = _find_review_queue_row(eml_path_str)

        # XOR: exactly one of the two paths produced a row. Both would be
        # a duplicated-write bug; neither would mean the pipeline silently
        # dropped the message.
        present_count = (daily_row is not None) + (review_row is not None)
        assert present_count == 1, (
            f"intake.py routing-contract violated: expected exactly ONE of "
            f"Daily Reports row or Review Queue row; got "
            f"daily_row={daily_row!r}, review_row={review_row!r}."
        )

        if daily_row is not None:
            # Happy path: confidence >= threshold, no high-severity anomalies.
            print(
                f"[intake-test] Path: Daily Reports row "
                f"(row_id={daily_row['_row_id']}) — confidence >= threshold, "
                f"no high-severity anomalies"
            )
            notes = daily_row.get("Notes / Action Items") or ""
            assert "Box: " in notes or "[box_filing" in notes, (
                f"Notes / Action Items lacks Box-link prefix: {notes!r}"
            )
            # Capture box file IDs from the URLs so finally can delete them.
            for match in re.finditer(r"app\.box\.com/file/(\d+)", notes):
                created_box_file_ids.append(match.group(1))
        else:
            # review_row is not None.
            reason = review_row.get("Reason") if review_row else None
            print(
                f"[intake-test] Path: ITS_Review_Queue row "
                f"(row_id={review_row['_row_id'] if review_row else '?'}) "
                f"— gate routed (Reason={reason!r})"
            )
    finally:
        _restore_allowlist(allowlist_row_id)
        # Defensive cleanup: search every possible target sheet for rows
        # this test could have created, delete what we find. Runs whether
        # the test passed, failed, or main() raised partway through.
        if created_sheet_id is not None:
            daily_row_cleanup = _find_daily_reports_row(created_sheet_id, marker)
            if daily_row_cleanup is not None:
                _delete_row(
                    created_sheet_id,
                    int(daily_row_cleanup["_row_id"]),
                    _token_available,
                )
        review_row_cleanup = _find_review_queue_row(eml_path_str)
        if review_row_cleanup is not None:
            _delete_row(
                sheet_ids.SHEET_REVIEW_QUEUE,
                int(review_row_cleanup["_row_id"]),
                _token_available,
            )
        quarantine_row_cleanup = _find_quarantine_row(SANDBOX_SENDER, subject)
        if quarantine_row_cleanup is not None:
            _delete_row(
                sheet_ids.SHEET_QUARANTINE,
                int(quarantine_row_cleanup["_row_id"]),
                _token_available,
            )
        for file_id in created_box_file_ids:
            _delete_box_file(file_id)
        # The intake.main rename-on-success made the .eml.processed file;
        # tmp_path cleans up automatically.
