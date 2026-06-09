"""Apply each publish op to the REAL catalog manifest and re-assert every invariant
tests/test_form_catalog.py enforces — the daemon's manifest-mutation core (slice 3b)
must never produce a manifest the CI consistency check would reject."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from safety_reports.publish_manifest import PublishApplyError, apply_publish

_ROOT = Path(__file__).resolve().parents[1]
LIVE_CATALOG = json.loads((_ROOT / "safety_portal" / "catalog.json").read_text())
SCHEMA = json.loads((_ROOT / "safety_portal" / "catalog.schema.json").read_text())

# The apply_publish BEHAVIOR tests run against this FROZEN in-memory fixture, NOT the live
# catalog.json. That decoupling is load-bearing: the tests hardcode identity/parent names as
# "brand-new" or "already-exists" examples (e.g. "incident-report", "jha-special"), so if they
# read the live catalog, a publish that adds a matching name (as req-8's incident-report-test
# did) would collide and red-CI the gate — the very self-defeating failure mode the Part-D
# fix set out to eliminate. The live catalog is validated separately (test_baseline_catalog_is_valid).
# A standalone (jha), and two variant parents (equipment-preinspection, toolbox-talk) — enough
# to exercise every op + rejection.
def _single(identity: str, parent: str, variant: str | None, order: int) -> dict:
    return {
        "identity": identity, "variant_label": variant, "status": "active",
        "current_version": 1, "current_form_code": f"{identity}-v1",
        "versions": [{"version": 1, "form_code": f"{identity}-v1"}], "display_order": order,
    }


FIXTURE: dict = {
    "manifest_version": 1,
    "parents": [
        {"parent_form_code": "jha", "name": "Job Hazard Analysis", "display_order": 1,
         "forms": [_single("jha", "jha", None, 1)]},
        {"parent_form_code": "equipment-preinspection", "name": "Equipment Pre-Inspection",
         "display_order": 2, "forms": [
             _single("equipment-skid-steer", "equipment-preinspection", "Skid Steer", 1),
             _single("equipment-telehandler", "equipment-preinspection", "Telehandler", 2),
         ]},
        {"parent_form_code": "toolbox-talk", "name": "Toolbox Talk", "display_order": 3,
         "forms": [
             _single("toolbox-talk-ppe", "toolbox-talk", "PPE", 1),
             _single("toolbox-talk-electrical", "toolbox-talk", "Electrical", 2),
         ]},
    ],
}


def _validate(m: dict) -> None:
    """Re-assert the key test_form_catalog.py invariants on a mutated manifest."""
    jsonschema.validate(m, SCHEMA)
    codes: list[str] = []
    ids: list[str] = []
    parents: list[str] = []
    for p in m["parents"]:
        parents.append(p["parent_form_code"])
        labels = [f["variant_label"] for f in p["forms"]]
        nulls = sum(1 for x in labels if x is None)
        if nulls:
            assert nulls == len(labels) == 1, f"{p['parent_form_code']}: variant-mixing"
        orders = [f["display_order"] for f in p["forms"]]
        assert len(orders) == len(set(orders)), f"{p['parent_form_code']}: dup display_order"
        for f in p["forms"]:
            ids.append(f["identity"])
            assert f["current_form_code"] == f"{f['identity']}-v{f['current_version']}"
            vcodes = {v["form_code"] for v in f["versions"]}
            assert f["current_form_code"] in vcodes
            for v in f["versions"]:
                codes.append(v["form_code"])
                assert v["form_code"] == f"{f['identity']}-v{v['version']}"
    assert len(codes) == len(set(codes)), "duplicate form_code"
    assert len(ids) == len(set(ids)), "duplicate identity"
    assert len(parents) == len(set(parents)), "duplicate parent_form_code"


def _def(form_code: str, parent: str, version: int, variant: str | None = None,
         archetype: str = "rows_signatures") -> dict:
    return {
        "form_code": form_code, "parent_form_code": parent, "form_name": "Test Form",
        "variant_label": variant, "version": version, "archetype": archetype,
        "source_pdf": "x.pdf", "sections": [{"type": "static_text", "text": "x"}],
    }


def test_baseline_catalog_is_valid() -> None:
    _validate(LIVE_CATALOG)


def test_create_new_parent_and_form() -> None:
    d = _def("incident-report-v1", "incident-report", 1)
    m, files, _ = apply_publish(
        FIXTURE, op="create", identity="incident-report",
        parent_form_code="incident-report", definition=d,
    )
    _validate(m)
    assert files == {"incident-report-v1": d}
    _, form = _find(m, "incident-report")
    assert form["status"] == "active" and form["current_form_code"] == "incident-report-v1"


def test_add_version_new_variant_coexists() -> None:
    before = len(_parent(FIXTURE, "toolbox-talk")["forms"])
    d = _def("toolbox-talk-ladders-v1", "toolbox-talk", 1, variant="Ladder Safety",
             archetype="content_signin")
    m, _, _ = apply_publish(
        FIXTURE, op="add_version", identity="toolbox-talk-ladders",
        parent_form_code="toolbox-talk", definition=d,
    )
    _validate(m)
    tb = _parent(m, "toolbox-talk")
    assert "toolbox-talk-ladders" in [f["identity"] for f in tb["forms"]]
    assert len(tb["forms"]) == before + 1  # adding a variant grows the parent by exactly one


def test_create_into_no_variant_parent_rejects_mixing() -> None:
    d = _def("jha-special-v1", "jha", 1, variant="Special")
    with pytest.raises(PublishApplyError, match="mix"):
        apply_publish(FIXTURE, op="create", identity="jha-special",
                      parent_form_code="jha", definition=d)


def test_create_duplicate_identity_rejected() -> None:
    d = _def("jha-v1", "jha", 1)
    with pytest.raises(PublishApplyError, match="already exists"):
        apply_publish(FIXTURE, op="create", identity="jha",
                      parent_form_code="jha", definition=d)


def test_create_new_parent_with_variant_label_rejected() -> None:
    """A brand-new form type (new parent) is its own only variant, so it MUST be null-
    variant. This is the authoritative guard that stops a junk publish — exactly the
    create-flow test artifact (variant_label "test" on a lone new parent) that slipped
    past the enqueue gate and only reddened CI downstream — at the daemon's manifest
    re-check. Mirrors test_form_catalog.test_single_form_parent_is_null_variant."""
    d = _def("incident-report-v1", "incident-report", 1, variant="test")
    with pytest.raises(PublishApplyError, match="variant_label null"):
        apply_publish(FIXTURE, op="create", identity="incident-report",
                      parent_form_code="incident-report", definition=d)


def test_edit_bumps_version_swaps_active_keeps_history() -> None:
    d = _def("jha-v2", "jha", 2)
    m, files, _ = apply_publish(FIXTURE, op="edit", identity="jha",
                                parent_form_code="jha", definition=d)
    _validate(m)
    assert files == {"jha-v2": d}
    _, form = _find(m, "jha")
    assert form["current_form_code"] == "jha-v2" and form["current_version"] == 2
    assert {v["form_code"] for v in form["versions"]} == {"jha-v1", "jha-v2"}


def test_edit_nonexistent_identity_rejected() -> None:
    with pytest.raises(PublishApplyError, match="not found"):
        apply_publish(FIXTURE, op="edit", identity="ghost", parent_form_code="ghost",
                      definition=_def("ghost-v2", "ghost", 2))


def test_edit_non_bumping_version_rejected() -> None:
    with pytest.raises(PublishApplyError, match="bump"):
        apply_publish(FIXTURE, op="edit", identity="jha", parent_form_code="jha",
                      definition=_def("jha-v1", "jha", 1))


def test_edit_changing_variant_label_rejected() -> None:
    d = _def("equipment-skid-steer-v2", "equipment-preinspection", 2, variant="Renamed")
    with pytest.raises(PublishApplyError, match="variant_label"):
        apply_publish(FIXTURE, op="edit", identity="equipment-skid-steer",
                      parent_form_code="equipment-preinspection", definition=d)


def test_delete_retires_identity() -> None:
    m, files, _ = apply_publish(FIXTURE, op="delete", identity="jha", parent_form_code="jha")
    _validate(m)
    assert files == {}
    _, form = _find(m, "jha")
    assert form["status"] == "retired"
    active = {f["current_form_code"] for p in m["parents"] for f in p["forms"] if f["status"] == "active"}
    assert "jha-v1" not in active


def test_rollback_re_promotes_a_prior_version() -> None:
    # First bump jha to v2, then roll back to v1.
    m2, _, _ = apply_publish(FIXTURE, op="edit", identity="jha", parent_form_code="jha",
                             definition=_def("jha-v2", "jha", 2))
    m3, files, _ = apply_publish(m2, op="rollback", identity="jha",
                                 parent_form_code="jha", target_form_code="jha-v1")
    _validate(m3)
    assert files == {}
    _, form = _find(m3, "jha")
    assert form["current_form_code"] == "jha-v1" and form["current_version"] == 1
    assert {v["form_code"] for v in form["versions"]} == {"jha-v1", "jha-v2"}  # history retained


def test_rollback_unknown_version_rejected() -> None:
    with pytest.raises(PublishApplyError, match="not a known version"):
        apply_publish(FIXTURE, op="rollback", identity="jha", parent_form_code="jha",
                      target_form_code="jha-v9")


def test_input_manifest_is_never_mutated() -> None:
    before = json.dumps(FIXTURE, sort_keys=True)
    apply_publish(FIXTURE, op="edit", identity="jha", parent_form_code="jha",
                  definition=_def("jha-v2", "jha", 2))
    apply_publish(FIXTURE, op="delete", identity="jha", parent_form_code="jha")
    assert json.dumps(FIXTURE, sort_keys=True) == before


def test_unknown_op_rejected() -> None:
    with pytest.raises(PublishApplyError, match="unknown op"):
        apply_publish(FIXTURE, op="nuke", identity="jha", parent_form_code="jha")


# ── helpers ───────────────────────────────────────────────────────────────────
def _parent(m: dict, code: str) -> dict:
    return next(p for p in m["parents"] if p["parent_form_code"] == code)


def _find(m: dict, identity: str):
    for p in m["parents"]:
        for f in p["forms"]:
            if f["identity"] == identity:
                return p, f
    return None, None
