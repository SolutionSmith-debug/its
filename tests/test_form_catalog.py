"""Validate the Safety Portal form catalog manifest against its schema AND
against the form definition files it indexes.

`safety_portal/catalog.json` is the git-committed active-set / current-version /
parent-variant / display-order overlay introduced in Phase-2 (the admin form
editor). Form *definitions* stay one-JSON-per-form under `safety_portal/forms/`
(the rendering contract validated by `test_form_definitions.py`). This test is
the CI safety net asserting the manifest faithfully + consistently mirrors those
files.

WHY this matters now (not just hygiene): the Phase-2 publish pipeline is
fully-automatic with NO human merge gate (design brief C12), so the AUTOMATED
guard rails carry the safety. These invariants are part of that net — a bad
manifest (dangling current pointer, duplicate code, parent/variant skew,
variant-mixing that the renderer would silently drop) must STOP at CI, never
reach a live deploy.

Scope note (slice 1a): nothing READS the manifest yet (1b flips registry.ts +
load_definition). This file validates the *contract* the editor will write and
the renderers will later read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

_ROOT = Path(__file__).resolve().parents[1]
PORTAL = _ROOT / "safety_portal"
FORMS_DIR = PORTAL / "forms"
MANIFEST_PATH = PORTAL / "catalog.json"
SCHEMA_PATH = PORTAL / "catalog.schema.json"

MANIFEST: dict = json.loads(MANIFEST_PATH.read_text())
SCHEMA: dict = json.loads(SCHEMA_PATH.read_text())

# Universal form_code convention: <identity>-v<N>. Also the path-traversal guard
# charset that form_pdf.load_definition enforces.
_VN_RE = re.compile(r"^(?P<identity>[a-z0-9-]+)-v(?P<version>[0-9]+)$")

# Non-definition JSON files that live under forms/ and must NOT be treated as
# form definitions (mirrors registry.ts + test_form_definitions.py exclusions).
_NON_DEFINITION_FILES = {"meta-schema.json"}


def _form_files() -> set[str]:
    """form_code stems of every definition file actually on disk."""
    return {
        p.stem for p in FORMS_DIR.glob("*.json") if p.name not in _NON_DEFINITION_FILES
    }


def _load_form(code: str) -> dict:
    return json.loads((FORMS_DIR / f"{code}.json").read_text())


def _parents() -> list[dict]:
    return MANIFEST["parents"]


def _forms() -> list[tuple[dict, dict]]:
    """(parent, form) for every form entry."""
    return [(parent, form) for parent in _parents() for form in parent["forms"]]


def _versions() -> list[tuple[dict, dict, dict]]:
    """(parent, form, version_entry) for every version row."""
    return [
        (parent, form, v)
        for parent, form in _forms()
        for v in form["versions"]
    ]


# ── schema-level ────────────────────────────────────────────────────────────────


def test_schema_is_valid_jsonschema() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_manifest_conforms_to_schema() -> None:
    jsonschema.validate(MANIFEST, SCHEMA)


# ── uniqueness ──────────────────────────────────────────────────────────────────


def test_form_codes_globally_unique() -> None:
    codes = [v["form_code"] for _, _, v in _versions()]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    assert not dupes, f"duplicate form_code(s): {dupes}"


def test_identities_globally_unique() -> None:
    ids = [form["identity"] for _, form in _forms()]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate identity(ies): {dupes}"


def test_parent_form_codes_unique() -> None:
    pcs = [p["parent_form_code"] for p in _parents()]
    dupes = sorted({c for c in pcs if pcs.count(c) > 1})
    assert not dupes, f"duplicate parent_form_code(s): {dupes}"


# ── files <-> manifest parity ─────────────────────────────────────────────────────


def test_every_version_form_code_resolves_to_a_file() -> None:
    missing = [
        v["form_code"]
        for _, _, v in _versions()
        if not (FORMS_DIR / f"{v['form_code']}.json").is_file()
    ]
    assert not missing, f"manifest version(s) with no definition file: {missing}"


def test_manifest_indexes_every_form_file_and_vice_versa() -> None:
    """Bidirectional: the set of all manifest version form_codes == the set of
    definition files on disk. Catches both an orphan file (a form added to
    forms/ but never registered) and a dangling manifest entry."""
    manifest_codes = {v["form_code"] for _, _, v in _versions()}
    on_disk = _form_files()
    only_manifest = sorted(manifest_codes - on_disk)
    only_disk = sorted(on_disk - manifest_codes)
    assert not only_manifest, f"manifest references non-existent file(s): {only_manifest}"
    assert not only_disk, f"definition file(s) absent from the manifest: {only_disk}"


# ── naming + version convention ───────────────────────────────────────────────────


def test_version_form_codes_match_vn_convention() -> None:
    for _, form, v in _versions():
        m = _VN_RE.fullmatch(v["form_code"])
        assert m, f"{v['form_code']} is not <identity>-v<N>"
        assert m.group("identity") == form["identity"], (
            f"{v['form_code']} identity stem != manifest identity {form['identity']!r}"
        )
        assert int(m.group("version")) == v["version"], (
            f"{v['form_code']} version suffix != manifest version {v['version']}"
        )


def test_current_pointer_is_consistent() -> None:
    for _, form in _forms():
        version_codes = {v["form_code"] for v in form["versions"]}
        assert form["current_form_code"] in version_codes, (
            f"{form['identity']}: current_form_code {form['current_form_code']!r} "
            f"not in versions {sorted(version_codes)}"
        )
        expected = f"{form['identity']}-v{form['current_version']}"
        assert form["current_form_code"] == expected, (
            f"{form['identity']}: current_form_code {form['current_form_code']!r} "
            f"!= identity-vcurrent_version ({expected!r})"
        )
        cur = next(v for v in form["versions"] if v["form_code"] == form["current_form_code"])
        assert cur["version"] == form["current_version"], (
            f"{form['identity']}: current_version {form['current_version']} "
            f"!= the version row it points at ({cur['version']})"
        )


def test_version_numbers_unique_within_identity() -> None:
    for _, form in _forms():
        nums = [v["version"] for v in form["versions"]]
        assert len(nums) == len(set(nums)), f"{form['identity']}: duplicate version number"


# ── manifest mirrors the definition files ─────────────────────────────────────────


def test_manifest_mirrors_definition_fields() -> None:
    """Every manifest version row must agree with its definition file on the
    identity-bearing fields. Catches a manifest edited out of sync with a form."""
    for parent, form, v in _versions():
        d = _load_form(v["form_code"])
        assert d["form_code"] == v["form_code"], v["form_code"]
        assert d["parent_form_code"] == parent["parent_form_code"], (
            f"{v['form_code']}: file parent {d['parent_form_code']!r} "
            f"!= manifest parent {parent['parent_form_code']!r}"
        )
        assert d.get("variant_label") == form["variant_label"], (
            f"{v['form_code']}: file variant_label {d.get('variant_label')!r} "
            f"!= manifest variant_label {form['variant_label']!r}"
        )
        assert d["version"] == v["version"], (
            f"{v['form_code']}: file version {d['version']} != manifest version {v['version']}"
        )


# ── parent / variant structure (renderer-compatibility) ───────────────────────────


def test_parent_has_no_variant_mixing() -> None:
    """registry.ts formCatalog() branches binary on whether ANY member has a
    variant_label: a parent is either a single no-variant form OR all-variant.
    A mix would make the renderer silently drop the null-variant form, so the
    manifest must never encode one."""
    for parent in _parents():
        labels = [f["variant_label"] for f in parent["forms"]]
        null_count = sum(1 for lbl in labels if lbl is None)
        if null_count:
            assert null_count == len(labels) == 1, (
                f"{parent['parent_form_code']}: mixes a null-variant form with "
                f"variant forms (or has >1 null-variant) — not renderable"
            )


def test_variant_labels_unique_within_parent() -> None:
    for parent in _parents():
        labels = [f["variant_label"] for f in parent["forms"] if f["variant_label"] is not None]
        assert len(labels) == len(set(labels)), (
            f"{parent['parent_form_code']}: duplicate variant_label"
        )


# ── display order ─────────────────────────────────────────────────────────────────


def test_parent_display_order_unique() -> None:
    orders = [p["display_order"] for p in _parents()]
    assert len(orders) == len(set(orders)), "duplicate parent display_order"


def test_variant_display_order_unique_within_parent() -> None:
    for parent in _parents():
        orders = [f["display_order"] for f in parent["forms"]]
        assert len(orders) == len(set(orders)), (
            f"{parent['parent_form_code']}: duplicate variant display_order"
        )


# ── durable renderer-compatibility guards (survive every later slice) ─────────────


def test_single_form_parent_is_null_variant() -> None:
    """registry.ts formCatalog() branches binary on variant_label. A parent with
    EXACTLY ONE form must be the no-variant kind (variant_label null); a lone form
    WITH a variant_label takes the variant branch and renders a degenerate
    one-option 3rd picklist the PM must click through. (Complements
    test_parent_has_no_variant_mixing, which only guards the null side.)"""
    for parent in _parents():
        if len(parent["forms"]) == 1:
            assert parent["forms"][0]["variant_label"] is None, (
                f"{parent['parent_form_code']}: a lone form must have variant_label "
                f"null (got {parent['forms'][0]['variant_label']!r})"
            )


def test_variant_labels_non_empty() -> None:
    """A non-null variant_label must be a real label — never '' / whitespace,
    which renders a blank 3rd-picklist row (registry.ts treats '' as present)."""
    for _, form in _forms():
        lbl = form["variant_label"]
        if lbl is not None:
            assert lbl.strip(), f"{form['identity']}: empty/whitespace variant_label"


# ── 1a snapshot: RETIRED (the Phase-2 publish pipeline is now live) ────────────────
# `test_slice1a_snapshot_reproduces_current_renderer_behavior` proved the manifest was a
# PERFECT no-op vs registry.formCatalog() at slice 1a (same active set / dropdown order /
# labels). Its own docstring scheduled it for deletion "at slice 4/5/6 — the first admin-
# authored order/name, version-bump, or retire": the admin form editor + auto-publish
# pipeline IS that event. parent.name / display_order are stored precisely so admins can
# DIVERGE from the derived values (a newly-published form type appends at the end, NOT
# name-sorted), so the snapshot's parity asserts are intentionally obsolete and would
# red-CI every new-form publish. The DURABLE invariants it bundled live on in the separate
# tests above: active-set↔files parity in `test_manifest_indexes_every_form_file_and_vice_versa`,
# variant grouping in `test_parent_has_no_variant_mixing`, and the lone-form-null renderer
# guard in `test_single_form_parent_is_null_variant`.
