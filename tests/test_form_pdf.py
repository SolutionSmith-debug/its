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
    load_definition,
    merge_pdfs,
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
# ── weekly-packet merge (Sat→Fri) ─────────────────────────────────────────────
def _one_page(form: str, job: str) -> bytes:
    return render_submission_pdf(_load(form), {"job_name": job, "work_date": "2026-06-03", "values": {}})


def test_merge_concatenates_in_order() -> None:
    a = _one_page("jha-v1.json", "Bradley 1")
    b = _one_page("visitor-sign-in-v1.json", "Bradley 1")
    merged = merge_pdfs([a, b])
    reader = pypdf.PdfReader(io.BytesIO(merged))
    pa = len(pypdf.PdfReader(io.BytesIO(a)).pages)
    pb = len(pypdf.PdfReader(io.BytesIO(b)).pages)
    assert len(reader.pages) == pa + pb, "merged page count must equal the sum"
    text = _norm(" ".join(p.extract_text() for p in reader.pages))
    assert "JOB HAZARD ANALYSIS" in text and "VISITOR SIGN" in text.upper()


def test_merge_empty_raises() -> None:
    with pytest.raises(ValueError):
        merge_pdfs([])


def test_merge_single_pdf_roundtrips() -> None:
    a = _one_page("jha-v1.json", "Bradley 1")
    merged = merge_pdfs([a])
    assert merged[:5] == b"%PDF-"
    assert len(pypdf.PdfReader(io.BytesIO(merged)).pages) == len(pypdf.PdfReader(io.BytesIO(a)).pages)


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


# ── load_definition (Phase-5 Python-side form-definition loader) ───────────────
def test_load_definition_known_form_returns_dict() -> None:
    d = load_definition("jha-v1")
    assert d is not None
    assert d["form_code"] == "jha-v1"
    assert "sections" in d


def test_load_definition_matches_every_shipped_form() -> None:
    # Every form file (except the meta-schema) must load by its filename stem.
    for path in DEF_PATHS:
        d = load_definition(path.stem)
        assert d is not None, f"{path.stem} failed to load"
        assert d.get("form_code") == path.stem


def test_load_definition_unknown_form_returns_none() -> None:
    assert load_definition("does-not-exist-v9") is None


@pytest.mark.parametrize(
    "bad",
    [
        "",                       # empty
        "jha-v1.json",            # has a dot → blocked (and would double-suffix)
        "../jha-v1",              # path traversal
        "jha/../../etc/passwd",   # path traversal
        "JHA-V1",                 # uppercase not in the charset
        "jha_v1",                 # underscore not in the charset
        "foo bar",                # space not in the charset
    ],
)
def test_load_definition_rejects_unsafe_form_codes(bad: str) -> None:
    assert load_definition(bad) is None


def test_load_definition_malformed_json_returns_none(tmp_path, monkeypatch) -> None:
    # Point the loader at a temp dir holding a malformed file.
    import safety_reports.form_pdf as fp

    bad = tmp_path / "broken-v1.json"
    bad.write_text("{ not valid json ")
    monkeypatch.setattr(fp, "_FORMS_DIR", tmp_path)
    assert fp.load_definition("broken-v1") is None


def test_load_definition_non_object_json_returns_none(tmp_path, monkeypatch) -> None:
    """Valid JSON whose top level is not an object (e.g. an array) → None."""
    import safety_reports.form_pdf as fp

    (tmp_path / "arr-v1.json").write_text("[1, 2, 3]")
    monkeypatch.setattr(fp, "_FORMS_DIR", tmp_path)
    assert fp.load_definition("arr-v1") is None


# ── site photos (PR-2) ────────────────────────────────────────────────────────
def _tiny_jpeg() -> bytes:
    from PIL import Image as _PILImage

    buf = io.BytesIO()
    _PILImage.new("RGB", (48, 36), (12, 100, 60)).save(buf, format="JPEG")
    return buf.getvalue()


_PHOTO_DEF = {
    "form_name": "JHA",
    "sections": [
        {
            "type": "header",
            "fields": [
                {"key": "job", "input": "text", "label": "Job"},
                {"key": "site_photos", "input": "photo", "label": "Site Photos", "max_count": 4},
            ],
        }
    ],
}


def test_screened_photos_render_grid_with_caption() -> None:
    out = render_submission_pdf(
        _PHOTO_DEF,
        {
            "job_name": "Bradley 1",
            "work_date": "2026-06-12",
            "values": {},
            "screened_photos": [("front.jpg · 2026-06-12 09:30", _tiny_jpeg())],
        },
    )
    assert out[:5] == b"%PDF-"
    text = _norm(_pdf_text(out))
    assert "Site Photos" in text
    assert "front.jpg" in text


def test_header_photo_field_never_dumps_base64() -> None:
    """A header photo field whose value is raw base64 PhotoValue objects must NOT be
    rendered inline (the renderer skips input=='photo' header fields). Without
    screened_photos, no photo content appears and the base64 never leaks into the PDF."""
    import base64

    blob = base64.b64encode(_tiny_jpeg()).decode()
    out = render_submission_pdf(
        _PHOTO_DEF,
        {
            "job_name": "Bradley 1",
            "work_date": "2026-06-12",
            "values": {"site_photos": [{"data": blob, "name": "x.jpg", "taken_at": "", "gps": ""}]},
        },
    )
    text = _pdf_text(out)
    assert blob[:40] not in text          # the base64 payload never reaches the PDF
    assert "Site Photos" not in _norm(text)  # no grid without screened_photos


def test_unrenderable_screened_photo_is_skipped_not_fatal() -> None:
    """A corrupt JPEG in screened_photos is dropped (logged) — the document still renders."""
    out = render_submission_pdf(
        _PHOTO_DEF,
        {
            "job_name": "Bradley 1",
            "work_date": "2026-06-12",
            "values": {},
            "screened_photos": [("bad", b"\xff\xd8\xffnotanimage")],
        },
    )
    assert out[:5] == b"%PDF-"  # no crash; the bad photo is simply absent
