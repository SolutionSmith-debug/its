"""Domain-transform tests for po_materials/config_apply.py — the config actuator's Stage-1
validate-and-write against live HEAD (§50 config editor, slice 2).

Every write is exercised against a TMP root seeded with KNOWN FIXED fixtures (NOT copies of the
live po_materials/config + terms), never the live tree. Covers: tax integer-bp validation (incl.
the float-reject money-path guard + bad state code + parity), purchaser required-fields + email
routing, and the terms add_version immutability contract (new file + sha256 + legal_review
pending + current_version untouched + duplicate-version reject).

GUARD (HOUSE REFLEXES §5 — the config-editor merge-blocker class): the fixtures below are FIXED
and every version assertion is RELATIVE to the seed (``new == SEED_CONFIG_VERSION + 1``). Do NOT
re-seed by COPYING the live config files, and do NOT assert an absolute ``config_version`` /
``current_version``. The §50 config editor auto-merges purchaser/tax/terms edits on green CI, so a
test coupled to the live file's CURRENT content red-lights the moment the operator edits it and
strands the edit PR (exactly how PR #511 got stuck). Assert shape / relative diffs only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from po_materials import config_apply
from po_materials.config_apply import ConfigApplyError

# A NON-1 sentinel: the transforms bump config_version relative to whatever the file holds, so a
# fixed non-1 seed proves the assertions are version-agnostic (the copies-live fixture this replaced
# only passed while the live files happened to sit at version 1 — the root of PR #511's stuck edit).
SEED_CONFIG_VERSION = 5

_SEED_PURCHASER = {
    "config_version": SEED_CONFIG_VERSION,
    "comment": "test fixture — deliberately NOT the live purchaser identity",
    "entity": "Seed Fixture Co.",
    "address_lines": ["1 Fixture Way", "Testville, CA 90000"],
    "phone": "000-000-0000",
    "invoice_routing": {"to": "seed@fixture.test", "cc": ["seed-cc@fixture.test"]},
}
_SEED_TAX = {
    "config_version": SEED_CONFIG_VERSION,
    "comment": "test fixture — deliberately NOT the live tax table",
    "rates_bp": {"CA": 725, "NV": 0},
    "state_names": {"CA": "California", "NV": "Nevada"},
}
# Fixed terms manifest: one library profile (standard_17, one immutable version) + one attach
# profile (negotiated_gtc). NO field_21 (the unknown-profile test relies on its absence) and NO
# standard_17_v2 (the add_version tests target it, so it must be absent from the seed — copying the
# live manifest once it gains standard_17_v2 would raise a spurious 'already exists').
_SEED_MANIFEST = {
    "manifest_version": 1,
    "comment": "test fixture — deliberately NOT the live terms manifest",
    "profiles": {
        "standard_17": {
            "kind": "library",
            "label": "Seed library profile",
            "current_version": "1",
            "versions": {
                "1": {
                    "file": "standard_17_v1.md",
                    "sha256": "0" * 64,
                    "tokens": ["purchaser_entity", "seller_name"],
                    "legal_review": "pending",
                },
            },
        },
        "negotiated_gtc": {
            "kind": "attach",
            "label": "Seed attach profile",
            "render_line": "SUBJECT TO THE NEGOTIATED GTC.",
        },
    },
}


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A throwaway repo root seeded with KNOWN FIXED config + terms fixtures (never copies of the
    live files — see the module GUARD)."""
    (tmp_path / "po_materials" / "config").mkdir(parents=True)
    (tmp_path / "po_materials" / "terms").mkdir(parents=True)
    _write_json(tmp_path / "po_materials" / "config" / "purchaser.json", _SEED_PURCHASER)
    _write_json(tmp_path / "po_materials" / "config" / "tax.json", _SEED_TAX)
    _write_json(tmp_path / "po_materials" / "terms" / "manifest.json", _SEED_MANIFEST)
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
    assert tax["config_version"] == SEED_CONFIG_VERSION + 1  # RELATIVE bump, not an absolute 2
    assert "comment" in tax  # comment preserved
    assert f"config_version {SEED_CONFIG_VERSION + 1}" in note


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
    assert pur["config_version"] == SEED_CONFIG_VERSION + 1  # RELATIVE bump, not an absolute 2
    assert pur["entity"] == "Evergreen Renewables LLC"  # the test's OWN payload, round-tripped
    assert pur["invoice_routing"]["cc"] == ["a@evergreen.com", "b@evergreen.com"]
    assert "comment" in pur
    assert f"config_version {SEED_CONFIG_VERSION + 1}" in note


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


# ── terms / set_current (the legal-activation make-current op) ─────────────────────────


def test_terms_set_current_clears_and_repoints(root: Path):
    # mint a new version (pending), then make it current — clears its legal_review + bumps the pointer.
    config_apply.apply_config(
        _req("terms", "add_version", {"profile_id": "standard_17", "text": "New clause for {{purchaser_entity}}."},
             target_version="standard_17_v2"),
        root,
    )
    note = config_apply.apply_config(
        _req("terms", "set_current", {"profile_id": "standard_17"}, target_version="standard_17_v2"),
        root,
    )
    prof = _read(root, "terms", "manifest.json")["profiles"]["standard_17"]
    assert prof["current_version"] == "standard_17_v2"
    assert prof["versions"]["standard_17_v2"]["legal_review"] == "cleared"
    # The OLD version is untouched (immutable) — only the target's legal_review + current_version move.
    assert prof["versions"]["1"]["legal_review"] == "pending"
    assert prof["versions"]["standard_17_v2"]["sha256"]  # immutable fields preserved
    assert "legal_review cleared" in note


def test_terms_set_current_rejects_unknown_version(root: Path):
    with pytest.raises(ConfigApplyError, match="does not exist"):
        config_apply.apply_config(
            _req("terms", "set_current", {"profile_id": "standard_17"}, target_version="nope"), root
        )


def test_terms_set_current_rejects_unknown_profile(root: Path):
    with pytest.raises(ConfigApplyError, match="unknown profile"):
        config_apply.apply_config(
            _req("terms", "set_current", {"profile_id": "field_21"}, target_version="1"), root
        )


def test_terms_set_current_rejects_attach_profile(root: Path):
    with pytest.raises(ConfigApplyError, match="not a library profile"):
        config_apply.apply_config(
            _req("terms", "set_current", {"profile_id": "negotiated_gtc"}, target_version="1"), root
        )


def test_terms_set_current_requires_target_version(root: Path):
    with pytest.raises(ConfigApplyError, match="target_version is required"):
        config_apply.apply_config(_req("terms", "set_current", {"profile_id": "standard_17"}), root)


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


# ── terms / create_profile ──────────────────────────────────────────────────────────


def test_terms_create_library_profile_writes_manifest_file_and_fences_until_set_current(root: Path, monkeypatch):
    note = config_apply.apply_config(
        _req("terms", "create_profile", {
            "profile_id": "vendor_acme", "kind": "library", "label": "ACME vendor terms",
            "description": "ACME's inline regime.", "version_id": "v1",
            "text": "1. ACME clause with {{purchaser_entity}} and {{seller_name}}.",
        }),
        root,
    )
    manifest = _read(root, "terms", "manifest.json")
    prof = manifest["profiles"]["vendor_acme"]
    assert prof["kind"] == "library"
    assert prof["label"] == "ACME vendor terms"
    assert prof["description"] == "ACME's inline regime."
    # current_version points at the new version, but it is PENDING → Layer-A fences it.
    assert prof["current_version"] == "v1"
    v = prof["versions"]["v1"]
    assert v["legal_review"] == "pending"
    assert v["file"] == "vendor_acme_v1.md"                 # namespaced by profile id
    assert sorted(v["tokens"]) == ["purchaser_entity", "seller_name"]  # auto-extracted, STRICT
    # The immutable version file exists on disk with a matching sha256.
    file_bytes = (root / "po_materials" / "terms" / "vendor_acme_v1.md").read_bytes()
    assert hashlib.sha256(file_bytes).hexdigest() == v["sha256"]
    assert "NEW library profile" in note
    # The render-side Layer-A gate must FENCE the pending version (cannot render until set_current).
    from po_materials import terms
    monkeypatch.setattr(terms, "TERMS_DIR", root / "po_materials" / "terms")  # loader → tmp manifest
    with pytest.raises(terms.TermsError, match="NOT cleared"):
        terms.load_terms_text("vendor_acme")


def test_terms_create_attach_profile_writes_render_line_no_version(root: Path):
    note = config_apply.apply_config(
        _req("terms", "create_profile", {
            "profile_id": "vendor_gtc", "kind": "attach", "label": "Vendor GTC",
            "render_line": "SUBJECT TO THE VENDOR GTC.",
        }),
        root,
    )
    prof = _read(root, "terms", "manifest.json")["profiles"]["vendor_gtc"]
    assert prof == {"kind": "attach", "label": "Vendor GTC", "render_line": "SUBJECT TO THE VENDOR GTC."}
    assert "NEW attach profile" in note


def test_terms_create_profile_rejects_duplicate_id(root: Path):
    with pytest.raises(ConfigApplyError, match="already exists"):
        config_apply.apply_config(
            _req("terms", "create_profile", {
                "profile_id": "standard_17", "kind": "library", "label": "dup",
                "version_id": "v9", "text": "x",
            }),
            root,
        )


def test_terms_create_profile_rejects_bad_id_kind_and_empty_fields(root: Path):
    with pytest.raises(ConfigApplyError, match="profile_id"):
        config_apply.apply_config(
            _req("terms", "create_profile", {"profile_id": "Bad-Id!", "kind": "library",
                                             "label": "x", "version_id": "v1", "text": "y"}), root)
    with pytest.raises(ConfigApplyError, match="kind must be"):
        config_apply.apply_config(
            _req("terms", "create_profile", {"profile_id": "ok_id", "kind": "weird",
                                             "label": "x", "version_id": "v1", "text": "y"}), root)
    with pytest.raises(ConfigApplyError, match="label is required"):
        config_apply.apply_config(
            _req("terms", "create_profile", {"profile_id": "ok_id", "kind": "library",
                                             "label": "  ", "version_id": "v1", "text": "y"}), root)
    with pytest.raises(ConfigApplyError, match="text must be non-empty"):
        config_apply.apply_config(
            _req("terms", "create_profile", {"profile_id": "ok_id", "kind": "library",
                                             "label": "L", "version_id": "v1", "text": "   "}), root)


def test_terms_create_profile_rejects_reserved_id(root: Path):
    """A reserved profile id (deferred transcription) is not creatable via the generic form."""
    manifest = _read(root, "terms", "manifest.json")
    manifest["reserved_profile_ids"] = {"field_21": "reserved"}
    _write_json(root / "po_materials" / "terms" / "manifest.json", manifest)
    with pytest.raises(ConfigApplyError, match="RESERVED"):
        config_apply.apply_config(
            _req("terms", "create_profile", {"profile_id": "field_21", "kind": "library",
                                             "label": "x", "version_id": "v1", "text": "y"}), root)
