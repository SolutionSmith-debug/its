"""Domain-transform tests for po_materials/config_apply.py — the config actuator's Stage-1
validate-and-write against live HEAD (§50 config editor, slice 2).

Every write is exercised against a TMP root (a copy of the seeded po_materials/config +
po_materials/terms), never the live tree. Covers: tax integer-bp validation (incl. the
float-reject money-path guard + bad state code + parity), purchaser required-fields + email
routing, and the terms add_version immutability contract (new file + sha256 + legal_review
pending + current_version untouched + duplicate-version reject)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from po_materials import config_apply
from po_materials.config_apply import ConfigApplyError

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A throwaway repo root seeded with copies of the live PO config + terms manifest."""
    (tmp_path / "po_materials" / "config").mkdir(parents=True)
    (tmp_path / "po_materials" / "terms").mkdir(parents=True)
    for name in ("purchaser.json", "tax.json"):
        (tmp_path / "po_materials" / "config" / name).write_bytes(
            (REPO_ROOT / "po_materials" / "config" / name).read_bytes()
        )
    (tmp_path / "po_materials" / "terms" / "manifest.json").write_bytes(
        (REPO_ROOT / "po_materials" / "terms" / "manifest.json").read_bytes()
    )
    return tmp_path


def _req(artifact: str, op: str, payload: dict, target_version: str | None = None) -> dict:
    return {
        "id": 1, "workstream": "po_materials", "artifact_key": artifact, "op": op,
        "target_version": target_version, "payload": json.dumps(payload), "status": "queued",
    }


def _read(root: Path, *parts: str) -> dict:
    return json.loads((root.joinpath("po_materials", *parts)).read_text())


# ── tax / edit ──────────────────────────────────────────────────────────────────


def test_tax_edit_writes_and_bumps_config_version(root: Path):
    note = config_apply.apply_config(
        _req("tax", "edit", {"rates_bp": {"IL": 950, "OR": 0}, "state_names": {"IL": "Illinois", "OR": "Oregon"}}),
        root,
    )
    tax = _read(root, "config", "tax.json")
    assert tax["rates_bp"] == {"IL": 950, "OR": 0}
    assert tax["config_version"] == 2  # bumped from the seeded 1
    assert "comment" in tax  # comment preserved
    assert "config_version 2" in note


def test_tax_edit_rejects_float_rate(root: Path):
    """No floats in the money path — a 9.0 basis point must refuse (integer-only)."""
    with pytest.raises(ConfigApplyError, match="INTEGER"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"IL": 9.0}, "state_names": {"IL": "Illinois"}}), root
        )


def test_tax_edit_rejects_bool_rate(root: Path):
    with pytest.raises(ConfigApplyError, match="INTEGER"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"IL": True}, "state_names": {"IL": "Illinois"}}), root
        )


def test_tax_edit_rejects_out_of_range_bp(root: Path):
    with pytest.raises(ConfigApplyError, match="out of range"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"IL": 10001}, "state_names": {"IL": "Illinois"}}), root
        )
    with pytest.raises(ConfigApplyError, match="out of range"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"IL": -1}, "state_names": {"IL": "Illinois"}}), root
        )


def test_tax_edit_rejects_bad_state_code(root: Path):
    with pytest.raises(ConfigApplyError, match="USPS state code"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"illinois": 900}, "state_names": {"illinois": "Illinois"}}),
            root,
        )


def test_tax_edit_rejects_state_names_parity_gap(root: Path):
    with pytest.raises(ConfigApplyError, match="must match rates_bp"):
        config_apply.apply_config(
            _req("tax", "edit", {"rates_bp": {"IL": 900, "OR": 0}, "state_names": {"IL": "Illinois"}}),
            root,
        )


def test_tax_edit_rejects_empty_rates(root: Path):
    with pytest.raises(ConfigApplyError, match="non-empty"):
        config_apply.apply_config(_req("tax", "edit", {"rates_bp": {}, "state_names": {}}), root)


# ── purchaser / edit ──────────────────────────────────────────────────────────────


def test_purchaser_edit_writes_and_bumps(root: Path):
    note = config_apply.apply_config(
        _req("purchaser", "edit", {
            "entity": "Evergreen Renewables LLC",
            "address_lines": ["1 Main St", "Irvine, CA 92618"],
            "phone": "888-303-6424",
            "invoice_routing": {"to": "ap@evergreen.com", "cc": ["a@evergreen.com", "b@evergreen.com"]},
        }),
        root,
    )
    pur = _read(root, "config", "purchaser.json")
    assert pur["config_version"] == 2
    assert pur["entity"] == "Evergreen Renewables LLC"
    assert pur["invoice_routing"]["cc"] == ["a@evergreen.com", "b@evergreen.com"]
    assert "comment" in pur
    assert "config_version 2" in note


def test_purchaser_edit_rejects_bad_to_email(root: Path):
    with pytest.raises(ConfigApplyError, match="valid email"):
        config_apply.apply_config(
            _req("purchaser", "edit", {
                "entity": "X", "address_lines": ["a"], "phone": "1",
                "invoice_routing": {"to": "not-an-email", "cc": []},
            }),
            root,
        )


def test_purchaser_edit_rejects_bad_cc_email(root: Path):
    with pytest.raises(ConfigApplyError, match="valid email"):
        config_apply.apply_config(
            _req("purchaser", "edit", {
                "entity": "X", "address_lines": ["a"], "phone": "1",
                "invoice_routing": {"to": "ok@x.com", "cc": ["fine@x.com", "broken"]},
            }),
            root,
        )


def test_purchaser_edit_requires_entity(root: Path):
    with pytest.raises(ConfigApplyError, match="entity"):
        config_apply.apply_config(
            _req("purchaser", "edit", {
                "entity": "", "address_lines": ["a"], "phone": "1",
                "invoice_routing": {"to": "ok@x.com", "cc": []},
            }),
            root,
        )


def test_purchaser_edit_requires_nonempty_address(root: Path):
    with pytest.raises(ConfigApplyError, match="address_lines"):
        config_apply.apply_config(
            _req("purchaser", "edit", {
                "entity": "X", "address_lines": [], "phone": "1",
                "invoice_routing": {"to": "ok@x.com", "cc": []},
            }),
            root,
        )


# ── terms / add_version ────────────────────────────────────────────────────────────


def test_terms_add_version_writes_new_file_and_manifest_entry(root: Path):
    note = config_apply.apply_config(
        _req("terms", "add_version",
             {"profile_id": "standard_17", "text": "New clause for {{purchaser_entity}} and {{seller_name}}."},
             target_version="standard_17_v2"),
        root,
    )
    new_file = root / "po_materials" / "terms" / "standard_17_v2.md"
    assert new_file.exists()
    manifest = _read(root, "terms", "manifest.json")
    entry = manifest["profiles"]["standard_17"]["versions"]["standard_17_v2"]
    assert entry["file"] == "standard_17_v2.md"
    assert entry["sha256"] == hashlib.sha256(new_file.read_bytes()).hexdigest()
    assert entry["tokens"] == ["purchaser_entity", "seller_name"]  # extracted, sorted
    assert entry["legal_review"] == "pending"
    # current_version is LEFT UNTOUCHED — the new version is inert until legal clears it.
    assert manifest["profiles"]["standard_17"]["current_version"] == "1"
    assert "legal_review pending" in note


def test_terms_add_version_never_mutates_existing_version(root: Path):
    before = (root / "po_materials" / "terms" / "manifest.json").read_text()
    config_apply.apply_config(
        _req("terms", "add_version", {"profile_id": "standard_17", "text": "x"},
             target_version="standard_17_v2"),
        root,
    )
    manifest = _read(root, "terms", "manifest.json")
    # v1 entry unchanged (immutable); only a new key was added.
    assert manifest["profiles"]["standard_17"]["versions"]["1"] == json.loads(before)[
        "profiles"]["standard_17"]["versions"]["1"]


def test_terms_add_version_rejects_duplicate_version(root: Path):
    with pytest.raises(ConfigApplyError, match="already exists"):
        config_apply.apply_config(
            _req("terms", "add_version", {"profile_id": "standard_17", "text": "x"},
                 target_version="1"),  # "1" already exists
            root,
        )


def test_terms_add_version_rejects_bad_target_version(root: Path):
    with pytest.raises(ConfigApplyError, match="target_version"):
        config_apply.apply_config(
            _req("terms", "add_version", {"profile_id": "standard_17", "text": "x"},
                 target_version="Bad Version!"),
            root,
        )


def test_terms_add_version_rejects_unknown_profile(root: Path):
    with pytest.raises(ConfigApplyError, match="unknown profile"):
        config_apply.apply_config(
            _req("terms", "add_version", {"profile_id": "field_21", "text": "x"},
                 target_version="field_21_v1"),
            root,
        )


def test_terms_add_version_rejects_attach_profile(root: Path):
    with pytest.raises(ConfigApplyError, match="library profile"):
        config_apply.apply_config(
            _req("terms", "add_version", {"profile_id": "negotiated_gtc", "text": "x"},
                 target_version="negotiated_gtc_v2"),
            root,
        )


def test_terms_add_version_rejects_empty_text(root: Path):
    with pytest.raises(ConfigApplyError, match="text must be non-empty"):
        config_apply.apply_config(
            _req("terms", "add_version", {"profile_id": "standard_17", "text": "   "},
                 target_version="standard_17_v2"),
            root,
        )


# ── dispatch / payload guards ───────────────────────────────────────────────────────


def test_unknown_artifact_rejected(root: Path):
    with pytest.raises(ConfigApplyError, match="unknown config artifact"):
        config_apply.apply_config(_req("gremlins", "edit", {"x": 1}), root)


def test_wrong_op_for_artifact_rejected(root: Path):
    with pytest.raises(ConfigApplyError, match="tax takes op 'edit'"):
        config_apply.apply_config(_req("tax", "add_version", {"rates_bp": {"IL": 900}}), root)
    with pytest.raises(ConfigApplyError, match="terms takes op 'add_version'"):
        config_apply.apply_config(_req("terms", "edit", {"profile_id": "standard_17"}), root)


def test_bad_payload_json_rejected(root: Path):
    req = {"id": 1, "workstream": "po_materials", "artifact_key": "tax", "op": "edit",
           "target_version": None, "payload": "{not json", "status": "queued"}
    with pytest.raises(ConfigApplyError, match="not valid JSON"):
        config_apply.apply_config(req, root)
