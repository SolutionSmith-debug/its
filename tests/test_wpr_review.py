"""Tests for progress_reports/wpr_review.py — the thin progress review module.

Run with: pytest -q tests/test_wpr_review.py
"""
from __future__ import annotations

from datetime import date

from progress_reports import wpr_review
from safety_reports import wsr_review

# The send-side _ReviewModule surface the progress SendConfig/DaemonConfig (P5) bind.
_REVIEW_MODULE_SURFACE = (
    "SHEET_ID",
    "COL_JOB_PROJECT", "COL_JOB_ID", "COL_WEEK_OF", "COL_COMPILED_PDF",
    "COL_EMAIL_BODY", "COL_SEND_STATUS", "COL_SENT_AT", "COL_NOTES", "COL_WORKSTREAM",
    "COL_APPROVE_SCHEDULED", "COL_SEND_NOW", "COL_APPROVED_BY", "COL_APPROVED_AT",
    "STATUS_PENDING", "STATUS_SENT", "STATUS_FAILED", "STATUS_HELD", "STATUS_SENDING",
)


def test_wpr_review_exposes_review_module_surface():
    """wpr_review must structurally satisfy safety_reports.weekly_send._ReviewModule so the
    progress SendConfig can bind `review=cast(_ReviewModule, wpr_review)` (P5)."""
    for attr in _REVIEW_MODULE_SURFACE:
        assert hasattr(wpr_review, attr), f"wpr_review missing {attr}"
    assert callable(wpr_review.to_wsr_datetime)


def test_wpr_schema_is_byte_identical_to_wsr():
    """WPR and WSR are mirror sheets — the re-exported COL_*/STATUS_* titles MUST match wsr
    exactly, or the shared send machinery would read the wrong column on the progress sheet."""
    for attr in _REVIEW_MODULE_SURFACE:
        if attr == "SHEET_ID":
            continue  # the one thing that differs by design
        assert getattr(wpr_review, attr) == getattr(wsr_review, attr), attr
    assert wpr_review.STATUS_SENDING == "SENDING"


def test_wpr_review_binds_progress_tag_and_sheet():
    assert wpr_review.WORKSTREAM_TAG == "progress"
    # SHEET_ID resolves to the (currently-placeholder) progress sheet constant, NOT the WSR one.
    from shared import sheet_ids
    assert wpr_review.SHEET_ID == sheet_ids.SHEET_WPR_HUMAN_REVIEW
    assert wpr_review.SHEET_ID != wsr_review.SHEET_ID or sheet_ids.SHEET_WPR_HUMAN_REVIEW == 0


def test_add_wpr_row_delegates_with_progress_tag(monkeypatch):
    """add_wpr_row delegates to the canonical writer, hard-binding SHEET_ID + the 'progress'
    Workstream tag (the contamination guard the send path later verifies). Proves the tag is
    actually applied — not just defined."""
    captured: dict[str, object] = {}

    def fake_add_wsr_row(sheet_id: int, **kwargs: object) -> int:
        captured["sheet_id"] = sheet_id
        captured.update(kwargs)
        return 4242

    # wpr_review calls `wsr_review.add_wsr_row(...)` on the same module object.
    monkeypatch.setattr(wsr_review, "add_wsr_row", fake_add_wsr_row)

    row_id = wpr_review.add_wpr_row(
        job_project="Bradley 1",
        job_id="JOB-0001",
        week_of=date(2026, 6, 27),
        compiled_pdf_link="https://app.box.com/file/123",
        recipient_to="pm@example.com",
        cc_display="a@example.com",
        email_body="Good morning — see attached.",
        notes="",
    )

    assert row_id == 4242
    assert captured["sheet_id"] == wpr_review.SHEET_ID
    assert captured["workstream"] == "progress"
    assert captured["job_id"] == "JOB-0001"
    assert captured["job_project"] == "Bradley 1"
