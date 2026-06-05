"""Render-parity tests for safety_reports/form_pdf.py.

Renders each landed form definition to PDF, extracts the text back out (pypdf), and
asserts the source labels / mandatory legal text / N/A-vs-blank render faithfully.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pypdf
import pytest

from safety_reports.form_pdf import (
    _parse_ml_path,
    incomplete_checklist_items,
    render_submission_pdf,
)

_ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = _ROOT / "safety_portal" / "forms"
DEF_PATHS = sorted(p for p in FORMS_DIR.glob("*.json") if p.name != "meta-schema.json")


def _load(name: str) -> dict:
    return json.loads((FORMS_DIR / name).read_text())


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() for page in reader.pages)


def _norm(s: str) -> str:
    return " ".join(s.split())


# ── every form renders to a real PDF ──────────────────────────────────────────
@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_every_form_renders_to_pdf(path: Path) -> None:
    definition = json.loads(path.read_text())
    out = render_submission_pdf(
        definition, {"job_name": "Bradley 1", "work_date": "2026-06-03", "values": {}}
    )
    assert out[:5] == b"%PDF-", "not a PDF"
    assert len(out) > 1200, "implausibly small PDF"


# ── header + envelope + a field label ─────────────────────────────────────────
def test_jha_header_and_mandatory_footer() -> None:
    out = render_submission_pdf(
        _load("jha-v1.json"),
        {
            "job_name": "Bradley 1",
            "work_date": "2026-06-03",
            "values": {
                "work_location": "Array B",
                "crew_members": "A. Smith, B. Jones",
                "hazard_analysis": [{"task": "Lift panels", "hazards": "Strain", "mitigation": "2-person"}],
            },
        },
    )
    text = _norm(_pdf_text(out))
    assert "EVERGREEN RENEWABLES" in text
    assert "Bradley 1" in text and "2026-06-03" in text
    assert "Array B" in text and "Lift panels" in text
    # mandatory footer baked into the definition (non-editable)
    assert "REVIEW AND REVISE THE PLAN" in text
    assert "Worker Acknowledgement" in text


# ── equipment: lock/tag-out legal text + N/A distinct from blank ───────────────
def test_skid_steer_lockout_and_na_vs_blank() -> None:
    definition = _load("equipment-skid-steer-v1.json")
    submission = {
        "job_name": "Huntley",
        "work_date": "2026-06-03",
        "values": {
            "operator": "Daniel Field",
            "inspection": {
                "bs_bucket": {"response": "OK"},
                "bs_tracks": {"response": "N/A"},   # deliberately not applicable
                # bs_belts left BLANK (not inspected)
                "as_fuel": {"response": "1/2"},
            },
        },
    }
    text = _norm(_pdf_text(render_submission_pdf(definition, submission)))
    assert "ALWAYS lock/tag-out unsafe equipment" in text
    assert "N/A" in text  # the N/A answer rendered
    assert "Daniel Field" in text

    # N/A is a COMPLETE answer; blank is incomplete. Only the blank is flagged.
    blanks = incomplete_checklist_items(definition, submission)
    blank_keys = {item_key for _sec, item_key, _label in blanks}
    assert "bs_belts" in blank_keys, "a blank item must be flagged incomplete"
    assert "bs_tracks" not in blank_keys, "N/A must NOT be flagged as blank/incomplete"
    assert "bs_bucket" not in blank_keys


def test_skid_steer_is_tristate() -> None:
    definition = _load("equipment-skid-steer-v1.json")
    checklist = next(s for s in definition["sections"] if s["type"] == "checklist")
    for g in checklist["groups"]:
        assert g["scale"] == ["OK", "NOT OK", "N/A"], "Skid Steer must be tri-state"


# ── toolbox content + sign-in render ──────────────────────────────────────────
def test_toolbox_content_and_signin_render() -> None:
    out = render_submission_pdf(
        _load("toolbox-talk-back-sprains-v1.json"),
        {"job_name": "Rockford", "work_date": "2026-06-03",
         "values": {"sign_in": [{"instructor_worker_name": "D. Field", "signature": "M 1 1 L 50 40"}]}},
    )
    text = _norm(_pdf_text(out))
    assert "5-MINUTE SAFETY TALK" in text
    assert "BACK SPRAINS AND STRAINS" in text
    assert "D. Field" in text


# ── signature path parsing ────────────────────────────────────────────────────
def test_parse_ml_path_splits_strokes() -> None:
    strokes = _parse_ml_path("M 1 2 L 3 4 L 5 6 M 7 8 L 9 10")
    assert len(strokes) == 2
    assert strokes[0] == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert strokes[1] == [(7.0, 8.0), (9.0, 10.0)]


def test_parse_ml_path_tolerates_garbage() -> None:
    assert _parse_ml_path("") == []
    assert _parse_ml_path("M 1") == []  # truncated → no complete point


def test_signature_renders_without_error() -> None:
    # A JHA acknowledgement row carrying a real signature path must render.
    out = render_submission_pdf(
        _load("jha-v1.json"),
        {"job_name": "Bradley 1", "work_date": "2026-06-03",
         "values": {"worker_acknowledgement": [
             {"worker_name": "A. Smith", "company": "Evergreen", "signature": "M 10 20 L 30 40 L 60 25"}]}},
    )
    assert out[:5] == b"%PDF-"


# ── HSS&E sectioned assessment renders all sections ───────────────────────────
def test_hsse_sections_render() -> None:
    out = render_submission_pdf(
        _load("hsse-work-observation-v1.json"),
        {"job_name": "Bradley 1", "work_date": "2026-06-03",
         "values": {"observer": "J. Lee", "risk_rating": "Medium",
                    "section_1": {"s1_1": {"response": "Acceptable", "comment": "ok"}}}},
    )
    text = _norm(_pdf_text(out))
    assert "SECTION 2: JOB PLAN EVALUATION" in text
    assert "J. Lee" in text
