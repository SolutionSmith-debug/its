"""Tests for the P6 rollup-page hook in safety_reports/generate_core.py.

The shared compile engine is exercised end-to-end by tests/test_weekly_generate.py (safety
config) and tests/test_progress_weekly_generate.py (progress config). THESE tests pin the P6
optional rollup-page hook on `_build_weekly_packet` directly, with REAL PDFs (so the front-matter
path completes rather than fencing to a forms-only merge): the page is spliced after the cover /
before the index, the index pagination absorbs it, a provider failure is fenced (never breaks the
compile), and an unbound provider (safety) leaves the packet unchanged (§14 byte-identical).
"""
from __future__ import annotations

import dataclasses
import io
import re
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pypdf

from safety_reports import form_pdf, generate_core, weekly_generate
from shared import safety_week

WEEK = safety_week.week_bounds(date(2026, 6, 5))  # Sat 2026-05-30 → Fri 2026-06-05
CID = "abc123def456"
DT = datetime(2026, 6, 5, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
SAFETY = weekly_generate.SAFETY_GENERATE_CONFIG  # rollup_page_provider defaults to None


def _pdf(n_pages: int = 1) -> bytes:
    """A real, valid n-page PDF (no 'Page N' text, so it never collides with the index rows)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(n_pages):
        c.drawString(72, 720, "submission body")
        c.showPage()
    c.save()
    return buf.getvalue()


def _job() -> SimpleNamespace:
    return SimpleNamespace(project_name="Bradley 1", job_id="JOB-1")


def _metas(n: int) -> list[dict[str, str]]:
    return [{"date_display": f"Mon, Jun {i + 1}, 2026", "form_name": f"Form {i}"} for i in range(n)]


def _build(config, pdfs):  # type: ignore[no-untyped-def]
    return generate_core._build_weekly_packet(
        config, _job(), "Bradley 1", WEEK, pdfs, _metas(len(pdfs)), DT, CID
    )


def _text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() for page in reader.pages)


def _first_index_page(pdf_bytes: bytes) -> int:
    """The absolute page number the index cites for the FIRST form ('Page N' → N)."""
    hits = re.findall(r"Page (\d+)", _text(pdf_bytes))
    assert hits, "index cited no page numbers"
    return int(hits[0])


# ── the rollup page is spliced + the index absorbs it ─────────────────────────────
def test_rollup_page_adds_exactly_its_page_count():
    pdfs = [_pdf(1), _pdf(2)]
    rollup = _pdf(2)
    roll_cfg = dataclasses.replace(SAFETY, rollup_page_provider=lambda job, week: rollup)
    base = _build(SAFETY, pdfs)          # no provider → no rollup page
    withroll = _build(roll_cfg, pdfs)    # provider → rollup spliced
    assert form_pdf.page_count(withroll) == form_pdf.page_count(base) + form_pdf.page_count(rollup)


def test_index_start_pages_shift_by_the_rollup_page_count():
    # The index's cited page numbers must move DOWN by exactly the rollup's page count — proving
    # the page landed BEFORE the index and the pagination-convergence loop accounted for it.
    pdfs = [_pdf(1), _pdf(1)]
    rollup = _pdf(2)
    roll_cfg = dataclasses.replace(SAFETY, rollup_page_provider=lambda job, week: rollup)
    base_first = _first_index_page(_build(SAFETY, pdfs))
    roll_first = _first_index_page(_build(roll_cfg, pdfs))
    assert roll_first - base_first == form_pdf.page_count(rollup)


def test_rollup_page_lands_between_cover_and_index():
    # Layout: page 1 = cover, page 2 = rollup, page 3 = index, page 4 = first form. So the index
    # cites the first form at page 4 — proving the rollup occupies page 2, ahead of the index.
    pdfs = [_pdf(1)]
    rollup = _pdf(1)
    roll_cfg = dataclasses.replace(SAFETY, rollup_page_provider=lambda job, week: rollup)
    # The real rollup render carries its own distinctive title; assert it's present in the packet.
    titled = form_pdf.render_progress_rollup("Bradley 1", "Week", {"labor_hours": 5,
                                             "equipment": [], "open_tasks": 1})
    roll_cfg2 = dataclasses.replace(SAFETY, rollup_page_provider=lambda job, week: titled)
    assert "Weekly Progress Rollup" in _text(_build(roll_cfg2, pdfs))
    assert _first_index_page(_build(roll_cfg, pdfs)) == 4


# ── the rollup fence: a provider failure never breaks the compile ─────────────────
def test_rollup_provider_exception_is_fenced_and_packet_keeps_cover_and_index(mocker):
    log = mocker.patch.object(generate_core.error_log, "log")

    def boom(job, week):
        raise RuntimeError("worker down")

    cfg = dataclasses.replace(SAFETY, rollup_page_provider=boom)
    pdfs = [_pdf(1)]
    base = _build(SAFETY, pdfs)
    fenced = _build(cfg, pdfs)
    # rollup failed → NO rollup page → identical page count to the no-provider packet.
    assert form_pdf.page_count(fenced) == form_pdf.page_count(base)
    # …and a WARN was logged (never silent), and the front-matter fence did NOT fire.
    codes = [c.kwargs.get("error_code") for c in log.call_args_list]
    assert "weekly_generate.rollup_page_failed" in codes
    assert "weekly_generate.front_matter_failed" not in codes


def test_rollup_provider_returning_none_adds_no_page():
    cfg = dataclasses.replace(SAFETY, rollup_page_provider=lambda job, week: None)
    pdfs = [_pdf(1), _pdf(1)]
    assert form_pdf.page_count(_build(cfg, pdfs)) == form_pdf.page_count(_build(SAFETY, pdfs))


# ── §14: safety binds no provider → the hook is inert ─────────────────────────────
def test_safety_config_binds_no_rollup_provider():
    assert SAFETY.rollup_page_provider is None
