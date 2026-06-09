"""Tests for safety_reports/wsr_review.py (WSR_human_review access).

Smartsheet calls mocked. Live coverage: tests/test_wsr_review_integration.py.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
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


# ---- add_wsr_row (append-only — every compilation is a NEW PENDING row) ----


def test_add_wsr_row_seeds_body_and_pending(ss):
    row_id = wsr_review.add_wsr_row(
        123, job_project="Bradley 1", job_id="JOB-1", week_of=date(2026, 5, 30),
        compiled_pdf_link="https://app.box.com/file/9", recipient_to="to@x.com",
        cc_display="cc@x.com", email_body="BODY", notes="2 subs",
    )
    assert row_id == 777
    payload = ss["add_rows"].call_args.args[1][0]
    assert payload[wsr_review.COL_EMAIL_BODY] == "BODY"
    assert payload[wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_PENDING
    assert payload[wsr_review.COL_COMPILED_PDF] == "https://app.box.com/file/9"
    assert payload[wsr_review.COL_JOB_ID] == "JOB-1"


def test_add_wsr_row_always_appends_never_updates(ss):
    # APPEND-ONLY (operator decision 2026-06-09): even when a prior (job, week) row exists,
    # add_wsr_row ADDS a new PENDING row — it must NEVER update_rows, so an already-SENT row's
    # Compiled-PDF link + send history is never clobbered (the exact bug this fixes).
    ss["get_rows"].return_value = [
        {"_row_id": 55, wsr_review.COL_JOB_ID: "JOB-1", wsr_review.COL_WEEK_OF: "2026-05-30"},
    ]
    wsr_review.add_wsr_row(
        123, job_project="Bradley 1", job_id="JOB-1", week_of=date(2026, 5, 30),
        compiled_pdf_link="https://app.box.com/file/NEW", recipient_to="to@x.com",
        cc_display="cc@x.com", email_body="BODY2", notes="3 subs",
    )
    ss["add_rows"].assert_called_once()
    ss["update_rows"].assert_not_called()


# ---- to_wsr_datetime (ABSTRACT_DATETIME cell formatting) ------------------
# ABSTRACT_DATETIME is tz-naive and REJECTS an offset/'Z' (live-verified errorCode 5536),
# so the value must be naive Pacific wall-clock `YYYY-MM-DDTHH:MM:SS`.

_NAIVE_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"  # no offset, no 'Z', no microseconds


def test_to_wsr_datetime_utc_string_to_naive_pacific():
    # The F22 verdict.modified_at shape (UTC + offset). 16:39:18Z -> 09:39:18 PDT (June, UTC-7).
    out = wsr_review.to_wsr_datetime("2026-06-09T16:39:18+00:00")
    assert out == "2026-06-09T09:39:18"
    assert re.match(_NAIVE_RE, out), "must be naive — ABSTRACT_DATETIME rejects an offset"


def test_to_wsr_datetime_aware_datetime_to_naive_pacific():
    assert wsr_review.to_wsr_datetime(datetime(2026, 6, 9, 16, 39, 18, tzinfo=UTC)) == "2026-06-09T09:39:18"


def test_to_wsr_datetime_strips_microseconds():
    assert wsr_review.to_wsr_datetime("2026-06-09T16:39:18.654321+00:00") == "2026-06-09T09:39:18"


def test_to_wsr_datetime_handles_z_suffix():
    assert wsr_review.to_wsr_datetime("2026-06-09T16:39:18Z") == "2026-06-09T09:39:18"


def test_to_wsr_datetime_none_is_now_naive_pacific():
    out = wsr_review.to_wsr_datetime(None)
    assert re.match(_NAIVE_RE, out)
    assert "+" not in out and "Z" not in out  # naive — no offset survives
