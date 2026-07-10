"""Tests for scripts/generate_config_dictionary.py (WS3 / D2-2).

Covers the config-dictionary generator + its integration with the docs pipeline:

  * DETERMINISM — the generator is a pure function of the repo tree: rendering the same
    collected keys twice, and re-collecting + re-rendering, produce byte-identical output
    (the idempotency the doc-currency manifest relies on). No timestamp / git-SHA leaks in.
  * COMPLETENESS — discovery finds the daemon ``REQUIRED_CONFIG`` declarations, every key
    resolves to a purpose (no gaps) and a consistent default/kind (no declarer conflicts),
    and every merged key has the required shape.
  * MANIFEST — ``docs_pdf.manifest`` accepts the four new D2-2 entries (shape-check passes)
    and the generated dictionary source is registered + on disk.
  * RENDER — the committed dictionary markdown renders to a valid branded multi-page PDF,
    and the JSON twin has the schema WS2 consumes.

Importing the generator imports every daemon module (to read its ``REQUIRED_CONFIG``); that
is the same network-free import the rest of the suite already does. The heavy collection runs
once via a module-scoped fixture.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pypdf
import pytest

from docs_pdf.manifest import load_manifest
from docs_pdf.md_render import render_markdown_to_pdf_bytes

_ROOT = Path(__file__).resolve().parents[1]

# scripts/ is not a package; use the repo's sys.path-insert idiom (mirrors test_docs_pdf).
_SCRIPTS_DIR = _ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate_config_dictionary as gen  # noqa: E402 — sys.path-driven import

_NEW_D22_KEYS = (
    "its_owners_manual",
    "safety_reports_guide",
    "portal_admin_dashboard",
    "its_config_dictionary",
)
_VALID_KINDS = {"str", "bool", "int", "float"}


@pytest.fixture(scope="module")
def collected() -> tuple[list[gen.MergedKey], list[str], list[str]]:
    """Collect once (imports every daemon to read its REQUIRED_CONFIG)."""
    return gen.collect_all()


# ── determinism / idempotency ─────────────────────────────────────────────────────────────
def test_markdown_is_deterministic(collected) -> None:
    keys, _conflicts, _gaps = collected
    assert gen.render_markdown(keys) == gen.render_markdown(keys)


def test_json_is_deterministic(collected) -> None:
    keys, _conflicts, _gaps = collected
    assert gen.render_json(keys) == gen.render_json(keys)


def test_recollecting_produces_identical_bytes() -> None:
    """A full re-collect + re-render must be byte-identical — the 'run twice → same bytes'
    idempotency (no timestamp / ordering nondeterminism)."""
    keys_a, _, _ = gen.collect_all()
    keys_b, _, _ = gen.collect_all()
    assert gen.render_markdown(keys_a) == gen.render_markdown(keys_b)
    assert gen.render_json(keys_a) == gen.render_json(keys_b)


# ── completeness ──────────────────────────────────────────────────────────────────────────
def test_no_purpose_gaps(collected) -> None:
    _keys, _conflicts, gaps = collected
    assert gaps == [], f"keys without a purpose: {gaps}"


def test_no_declarer_conflicts(collected) -> None:
    _keys, conflicts, _gaps = collected
    assert conflicts == [], f"declarers disagree on default/kind: {conflicts}"


def test_discovers_the_core_daemons() -> None:
    names = {name for name, _path in gen.discover_daemon_modules()}
    # a representative floor across every workstream + the two non-package scripts
    expected = {
        "safety_reports.portal_poll", "safety_reports.weekly_send",
        "progress_reports.progress_send", "field_ops.fieldops_sync",
        "po_materials.po_send", "watchdog", "run_picklist_sync",
    }
    assert expected <= names, f"missing daemons: {expected - names}"
    assert len(names) >= 15, f"expected the full daemon roster, got {len(names)}"


def test_every_merged_key_has_valid_shape(collected) -> None:
    keys, _conflicts, _gaps = collected
    assert keys, "expected at least one config key"
    for k in keys:
        assert k.setting and k.workstream, f"blank identity: {k}"
        assert k.kind in _VALID_KINDS, f"bad kind {k.kind!r} for {k.setting}"
        assert k.purpose and k.purpose != "—", f"missing purpose for {k.setting}"
        assert k.read_by, f"no declarer recorded for {k.setting}"
        assert k.origin in {"daemon", "shared-infra"}


def test_shared_infra_keys_present(collected) -> None:
    keys, _conflicts, _gaps = collected
    settings = {k.setting for k in keys}
    # the shared-helper keys REQUIRED_CONFIG deliberately does not re-declare per-daemon
    for s in ("system.state", "alerting.dedupe_window_minutes", "circuit_breaker.enabled",
              "smartsheet.sheet_count_ceiling"):
        assert s in settings, f"shared-infra key {s} not surfaced"


# ── manifest integration (loader accepts the new entries) ──────────────────────────────────
def test_manifest_accepts_the_four_new_entries() -> None:
    man = load_manifest()  # shape-check passes (raises ManifestError otherwise)
    for key in _NEW_D22_KEYS:
        entry = man.by_key(key)
        assert entry is not None, f"manifest missing {key}"
        assert entry.source_path().is_file(), f"{key} source not on disk: {entry.source}"


def test_config_dictionary_registered_from_references() -> None:
    man = load_manifest()
    entry = man.by_key("its_config_dictionary")
    assert entry is not None
    assert entry.source == "docs/references/its_config_dictionary.md"


# ── render + JSON shape ────────────────────────────────────────────────────────────────────
def test_committed_dictionary_renders_to_branded_pdf() -> None:
    entry = load_manifest().by_key("its_config_dictionary")
    assert entry is not None
    md = entry.source_path().read_text(encoding="utf-8")
    pdf = render_markdown_to_pdf_bytes(md, title=entry.title, version=entry.version, git_sha="0000000")
    assert pdf[:5] == b"%PDF-"
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) > 1, "the dictionary should be multi-page"
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "EVERGREEN RENEWABLES" in text
    assert "ITS_Config" in text


def test_committed_json_has_expected_schema() -> None:
    payload = json.loads(gen.JSON_OUT.read_text(encoding="utf-8"))
    assert payload["schema_version"] == gen.JSON_SCHEMA_VERSION
    assert isinstance(payload["keys"], list) and payload["keys"]
    required = {"setting", "workstream", "kind", "default", "purpose", "read_by", "origin"}
    for entry in payload["keys"]:
        assert required <= set(entry), f"json entry missing fields: {required - set(entry)}"
        assert entry["kind"] in _VALID_KINDS
        assert isinstance(entry["read_by"], list) and entry["read_by"]
