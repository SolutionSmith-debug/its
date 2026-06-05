"""Tests for safety_reports/wsr_review.py (WSR_human_review access).

Smartsheet calls mocked. Live coverage: tests/test_wsr_review_integration.py.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from safety_reports import wsr_review


@pytest.fixture
def ss(mocker) -> dict[str, MagicMock]:
    return {
        "get_rows": mocker.patch.object(wsr_review.smartsheet_client, "get_rows", return_value=[]),
        "add_rows": mocker.patch.object(wsr_review.smartsheet_client, "add_rows", return_value=[777]),
        "update_rows": mocker.patch.object(wsr_review.smartsheet_client, "update_rows"),
    }


# ---- email body template -------------------------------------------------


def test_email_body_template_uses_name_week_job_contact():
    body = wsr_review.email_body_template(
        contact_name="Dana PM", week_label="Sat 2026-05-30 → Fri 2026-06-05",
        job_name="Bradley 1", evergreen_contact="Teala",
    )
    assert "Good morning Dana PM" in body
    assert "Bradley 1" in body and "2026-05-30" in body and "Teala" in body


def test_email_body_template_blank_name_falls_back():
    body = wsr_review.email_body_template(
        contact_name="", week_label="w", job_name="j", evergreen_contact="c",
    )
    assert "Good morning team" in body


# ---- find_row ------------------------------------------------------------


def test_find_row_matches_job_id_and_week(ss):
    ss["get_rows"].return_value = [
        {"_row_id": 1, wsr_review.COL_JOB_ID: "JOB-1", wsr_review.COL_WEEK_OF: "2026-05-30"},
        {"_row_id": 2, wsr_review.COL_JOB_ID: "JOB-1", wsr_review.COL_WEEK_OF: date(2026, 6, 6)},
    ]
    row = wsr_review.find_row(123, "JOB-1", date(2026, 5, 30))
    assert row is not None and row["_row_id"] == 1


def test_find_row_no_match(ss):
    ss["get_rows"].return_value = [
        {"_row_id": 1, wsr_review.COL_JOB_ID: "JOB-2", wsr_review.COL_WEEK_OF: "2026-05-30"},
    ]
    assert wsr_review.find_row(123, "JOB-1", date(2026, 5, 30)) is None


# ---- upsert (create) -----------------------------------------------------


def test_upsert_create_seeds_body_and_pending(ss):
    row_id, created = wsr_review.upsert_row(
        123, job_project="Bradley 1", job_id="JOB-1", week_of=date(2026, 5, 30),
        compiled_pdf_link="https://app.box.com/file/9", recipient_to="to@x.com",
        cc_display="cc@x.com", email_body="BODY", notes="2 subs",
    )
    assert (row_id, created) == (777, True)
    payload = ss["add_rows"].call_args.args[1][0]
    assert payload[wsr_review.COL_EMAIL_BODY] == "BODY"
    assert payload[wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_PENDING
    assert payload[wsr_review.COL_COMPILED_PDF] == "https://app.box.com/file/9"
    assert payload[wsr_review.COL_JOB_ID] == "JOB-1"


# ---- upsert (update) — never touch body / approval / status --------------


def test_upsert_update_refreshes_pdf_only_not_body_or_approval(ss):
    ss["get_rows"].return_value = [
        {"_row_id": 55, wsr_review.COL_JOB_ID: "JOB-1", wsr_review.COL_WEEK_OF: "2026-05-30"},
    ]
    row_id, created = wsr_review.upsert_row(
        123, job_project="Bradley 1", job_id="JOB-1", week_of=date(2026, 5, 30),
        compiled_pdf_link="https://app.box.com/file/NEW", recipient_to="to@x.com",
        cc_display="cc@x.com", email_body="IGNORED ON UPDATE", notes="3 subs",
    )
    assert (row_id, created) == (55, False)
    ss["add_rows"].assert_not_called()
    upd = ss["update_rows"].call_args.args[1][0]
    assert upd["_row_id"] == 55
    assert upd[wsr_review.COL_COMPILED_PDF] == "https://app.box.com/file/NEW"
    # Exact-keys whitelist: an UPDATE touches ONLY the PDF link + recipient
    # display + Notes — never Email Body, approval, or send-status (F22 / human
    # source-of-truth). Asserting the full key set guards against future drift.
    assert set(upd.keys()) == {
        "_row_id", wsr_review.COL_COMPILED_PDF, wsr_review.COL_RECIPIENT_TO,
        wsr_review.COL_CC, wsr_review.COL_NOTES,
    }
