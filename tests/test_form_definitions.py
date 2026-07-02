"""Validate every Safety Portal form definition against the meta-schema.

`safety_portal/forms/*.json` are the single source of truth both renderers (the
TS display runtime + the Python PDF renderer) consume, so they MUST conform to
`forms/meta-schema.json`. This test is the enforcement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from safety_reports.publish_manifest import PublishApplyError, check_required_content

_ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = _ROOT / "safety_portal" / "forms"
REF_DIR = _ROOT / "safety_portal" / "reference_forms"
META = json.loads((FORMS_DIR / "meta-schema.json").read_text())
DEF_PATHS = sorted(p for p in FORMS_DIR.glob("*.json") if p.name != "meta-schema.json")
REQUIRED_CONTENT = json.loads((_ROOT / "safety_portal" / "required-content.json").read_text())


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def test_meta_schema_is_itself_valid_jsonschema() -> None:
    jsonschema.Draft202012Validator.check_schema(META)


def test_there_are_definitions() -> None:
    assert DEF_PATHS, "no form definitions found"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_definition_conforms_to_meta_schema(path: Path) -> None:
    jsonschema.validate(_load(path), META)


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_definition_source_pdf_exists(path: Path) -> None:
    d = _load(path)
    assert (REF_DIR / d["source_pdf"]).exists(), f"{d['source_pdf']} not in reference_forms/"


def test_form_codes_are_unique() -> None:
    codes = [_load(p)["form_code"] for p in DEF_PATHS]
    assert len(codes) == len(set(codes)), "duplicate form_code"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_signature_tables_have_exactly_one_signature_column(path: Path) -> None:
    for s in _load(path)["sections"]:
        if s["type"] == "signature_table":
            sig = [c for c in s["columns"] if c["input"] == "signature"]
            assert len(sig) == 1, f"{path.stem}/{s['key']}: need exactly one signature column"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_checklist_groups_have_scale_and_items(path: Path) -> None:
    for s in _load(path)["sections"]:
        if s["type"] == "checklist":
            for g in s["groups"]:
                assert g["scale"], f"{path.stem}/{g['key']}: empty scale"
                assert g["items"], f"{path.stem}/{g['key']}: no items"


def test_seed_parent_types_present() -> None:
    """The five seed parent types must remain present. Asserted as a SUBSET, not an exact
    set — the publish pipeline adds new parent types, so an equality check would be
    self-defeating (red-CI every new-form-type publish)."""
    parents = {_load(p)["parent_form_code"] for p in DEF_PATHS}
    seed = {
        "jha", "equipment-preinspection", "toolbox-talk",
        "visitor-sign-in", "hsse-work-observation",
    }
    assert seed <= parents, f"seed parent type(s) missing: {sorted(seed - parents)}"


def test_jha_mandatory_footer_and_signature_present() -> None:
    d = _load(FORMS_DIR / "jha-v1.json")
    texts = [s["text"] for s in d["sections"] if s["type"] == "static_text"]
    assert any("REVIEW AND REVISE THE PLAN" in t for t in texts)
    assert any(s["type"] == "signature_table" for s in d["sections"])


def test_equipment_lockout_legal_text_present() -> None:
    for code in ("equipment-telehandler-v1", "equipment-skid-steer-v1"):
        d = _load(FORMS_DIR / f"{code}.json")
        texts = [s["text"] for s in d["sections"] if s["type"] == "static_text"]
        assert any("lock/tag-out" in t for t in texts), code


def test_equipment_telehandler_item_count() -> None:
    # The Telehandler tri-state checklist must keep all items (no silent drop).
    d = _load(FORMS_DIR / "equipment-telehandler-v1.json")
    checklist = next(s for s in d["sections"] if s["type"] == "checklist")
    total = sum(len(g["items"]) for g in checklist["groups"])
    assert total == 64, f"expected 64 telehandler items, got {total}"


def test_hsse_has_eleven_assessment_categories() -> None:
    d = _load(FORMS_DIR / "hsse-work-observation-v1.json")
    s1 = next(s for s in d["sections"] if s.get("key") == "section_1")
    assert len(s1["groups"][0]["items"]) == 11


def test_toolbox_variants_have_content_and_signin() -> None:
    # Lower-bound, not exact: the 5 seed toolbox-talk variants must remain, but the publish
    # pipeline adds variants (an add_version under the existing parent writes a 6th
    # toolbox-talk-*.json into the globbed forms/ dir) — an `== 5` here would red-CI that
    # publish, the self-defeating gate Part D set out to remove. Each variant (seed or new)
    # must still carry the content_blocks + signature renderer contract.
    tb = [p for p in DEF_PATHS if _load(p)["parent_form_code"] == "toolbox-talk"]
    assert len(tb) >= 5
    for p in tb:
        d = _load(p)
        assert any(s["type"] == "content_blocks" and s["blocks"] for s in d["sections"])
        assert any(s["type"] == "signature_table" for s in d["sections"])


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_live_definition_satisfies_required_content(path: Path) -> None:
    """Every shipped definition satisfies its required-content legal floor (Brief 1 PR-1) — the
    generalized form of the per-form footer/lockout/signature assertions above, driven by
    safety_portal/required-content.json. check_required_content raises on a violation; a clean
    return is the pass. This locks the floor against future shipped forms too."""
    d = _load(path)
    identity = re.sub(r"-v\d+$", "", d["form_code"])
    try:
        check_required_content(
            d, identity=identity, parent_form_code=d["parent_form_code"],
            required_content=REQUIRED_CONTENT,
        )
    except PublishApplyError as exc:
        raise AssertionError(f"{path.stem}: {exc}") from exc


# ── guidance + form_link sections (SOP daily form, slice D1) ────────────────────
_CATALOG = json.loads((_ROOT / "safety_portal" / "catalog.json").read_text())
_CATALOG_PARENTS = {p["parent_form_code"] for p in _CATALOG["parents"]}


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_form_link_parents_exist_in_catalog(path: Path) -> None:
    """Every form_link section's parent_form_code must resolve to a catalog form type —
    the repo-side (live-HEAD) twin of the worker enqueue gate's KNOWN_PARENT_FORM_CODES
    check (a JSON Schema can't cross-file check, so this test is the enforcement)."""
    for s in _load(path)["sections"]:
        if s["type"] == "form_link":
            assert s["parent_form_code"] in _CATALOG_PARENTS, (
                f"{path.stem}: form_link → {s['parent_form_code']!r} is not a catalog form type"
            )


def test_daily_report_v2_sop_structure() -> None:
    """daily-report-v2 (the SOP daily form) carries the spec's structure: the SOP part
    headings verbatim, the three deep links, and the duty-confirm sections."""
    d = _load(FORMS_DIR / "daily-report-v2.json")
    headings = [s["heading"] for s in d["sections"] if s["type"] == "guidance"]
    for expected in (
        "7:30 AM — Arrive On Site — You Set the Tone",
        "A. Morning Kickoff — 1. Sign Workers In",
        "2. PPE Verification",
        "3. Complete the Daily JHA (Job Hazard Analysis)",
        "4. Visitor Log",
        "6. Electrical Safety",
        "7. General OSHA Compliance",
        "C. Quality Control — Verifying the Work",
        "13. Material & Equipment Deliveries",
        "14. Safety Oversight",
        "END OF DAY — Before Leaving the Site",
        "F. General Expectations & Standards of Conduct",
    ):
        assert expected in headings, f"missing SOP guidance heading: {expected!r}"
    # The three deep links (spec rows 4, 5, 12).
    links = [s["parent_form_code"] for s in d["sections"] if s["type"] == "form_link"]
    assert links == ["jha", "visitor-sign-in", "incident-report"]
    # The named callouts are present with their styles.
    callouts = {
        (b["style"], b["text"].split(":")[0])
        for s in d["sections"] if s["type"] == "guidance"
        for b in s["blocks"] if b["type"] == "callout"
    }
    assert ("note", "NOTE") in callouts
    assert ("critical", "CRITICAL RULE") in callouts
    assert ("quality", "QUALITY RULE") in callouts
    assert ("note", "FINAL STATEMENT") in callouts


def test_daily_report_v2_dfr_field_coverage() -> None:
    """Nothing lost vs the v1 Daily Field Report (the spec's coverage checklist):
    job_name/report_date moved to the submission envelope (job / work_date header
    fields); every other DFR datum keeps a value key in v2."""
    d = _load(FORMS_DIR / "daily-report-v2.json")
    keys: set[str] = set()
    for s in d["sections"]:
        if s["type"] == "header":
            keys.update(f["key"] for f in s["fields"])
        elif s["type"] == "checklist":
            keys.add(s["key"])
            for g in s["groups"]:
                keys.update(it["key"] for it in g["items"])
        elif s["type"] in ("repeating_table", "signature_table", "freeform"):
            keys.add(s["key"])
    # Envelope-bound header fields (the fill page / Daily tab provide these).
    assert {"job", "work_date"} <= keys
    # DFR coverage (spec): weather, average_temp, prepared_by, crew_progress,
    # tomorrows_goals, equipment_on_site, deliveries_received, site_visitors, comments.
    assert {
        "weather", "average_temp", "prepared_by", "crew_progress", "tomorrows_goals",
        "equipment_on_site", "deliveries_received", "site_visitors", "comments",
    } <= keys
    # The SOP duty confirms + tables added by v2 (spec rows 1-14).
    assert {
        "arrived_walkthrough", "workers_signed_in", "manpower_total", "ppe_verified",
        "trenching_inspected", "electrical_safe", "osha_walk_done", "qc_spot_checks",
        "photos_taken", "photos_uploaded", "safety_observations", "incidents_none",
        "cm_checkin_am", "cm_checkin_pm", "eod_secure",
    } <= keys
    # crew_progress keeps the v1 column keys (the S5 rollup prefill targets them).
    crew = next(s for s in d["sections"] if s.get("key") == "crew_progress")
    assert [c["key"] for c in crew["columns"]] == [
        "crew_subcontractor", "manpower", "todays_progress",
    ]
