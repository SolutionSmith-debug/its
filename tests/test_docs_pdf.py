"""Tests for the docs → branded-PDF pipeline (WS3 / D2-1).

Covers the three ``docs_pdf`` modules + the ``scripts/build_docs_pdfs.py`` CLI:

  * md_render: non-empty flowables + valid PDF bytes for a fixture; frontmatter + HTML
    comments stripped; a pipe table, blockquote callout, and code block all survive to the
    text layer; the brand footer stamps version + git-sha; every REAL enablement guide in
    the manifest renders to a valid multi-page PDF.
  * manifest: the committed manifest round-trips + is self-consistent (recorded sha256 ==
    current source bytes); loader rejects malformed manifests; the SHA-256 currency check
    BITES on a drifted / missing source.
  * build CLI: --check is clean on the committed tree, --doc renders one PDF to an out dir,
    --upload is the dark-gated D2-3 Box publish leg (mock-only), run_check returns non-zero
    on synthetic drift.

Deterministic: every render passes an explicit git_sha (no subprocess), and the
currency-bites tests construct Manifest objects in-memory (no reliance on git state).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pypdf
import pytest

from docs_pdf.manifest import (
    Manifest,
    ManifestEntry,
    ManifestError,
    check_currency,
    compute_sha256,
    load_manifest,
)
from docs_pdf.md_render import (
    render_markdown_to_flowables,
    render_markdown_to_pdf_bytes,
)

_ROOT = Path(__file__).resolve().parents[1]

# scripts/ is not a Python package; use the repo's sys.path-insert idiom (see
# tests/test_verify_cutover.py) so the module imports as the top-level `build_docs_pdfs`
# — a `from scripts import …` would make mypy see the file under two module names.
_SCRIPTS_DIR = _ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_docs_pdfs  # noqa: E402  — sys.path-driven import

_FIXTURE_MD = """---
title: Fixture Doc
type: operations
status: active
---
<!-- TODO(operator): this reminder comment must never print onto a page -->

# Heading One

A paragraph with **bold**, *italic*, `inline_code`, and an [external](https://example.com)
link plus an [internal](#heading-one) anchor.

## Heading Two

- first bullet
- second bullet with **emphasis**
  - a nested bullet

1. ordered one
2. ordered two

| Column A | Column B |
|----------|----------|
| cell-aaa | cell-bbb |
| cell-ccc | cell-ddd |

> This is a blockquote that becomes a gold callout box.

```python
def sentinel_code():
    return 42
```

---

Closing paragraph.
"""


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() for page in reader.pages)


def _norm(s: str) -> str:
    return " ".join(s.split())


# ── md_render: flowables + PDF bytes ─────────────────────────────────────────────────────
def test_flowables_non_empty() -> None:
    flow = render_markdown_to_flowables(_FIXTURE_MD)
    assert len(flow) > 5, "expected a flowable per block plus the constituents"


def test_empty_input_yields_no_flowables() -> None:
    assert render_markdown_to_flowables("") == []
    assert render_markdown_to_flowables("   \n  \n") == []


def test_renders_valid_pdf_bytes() -> None:
    pdf = render_markdown_to_pdf_bytes(_FIXTURE_MD, title="Fixture", version="v1", git_sha="abc1234")
    assert pdf[:5] == b"%PDF-", "not a PDF"
    assert len(pdf) > 2000, "implausibly small PDF"


def test_frontmatter_stripped() -> None:
    text = _pdf_text(render_markdown_to_pdf_bytes(
        _FIXTURE_MD, title="Fixture", version="v1", git_sha="abc1234"))
    assert "type: operations" not in text
    assert "status: active" not in text
    # The frontmatter title string should not leak into the body text layer.
    assert "Fixture Doc" not in text


def test_html_comments_stripped() -> None:
    text = _pdf_text(render_markdown_to_pdf_bytes(
        _FIXTURE_MD, title="Fixture", version="v1", git_sha="abc1234"))
    assert "TODO(operator)" not in text
    assert "must never print" not in text


def test_table_callout_code_all_present() -> None:
    text = _norm(_pdf_text(render_markdown_to_pdf_bytes(
        _FIXTURE_MD, title="Fixture", version="v1", git_sha="abc1234")))
    # pipe table cells
    assert "cell-aaa" in text and "cell-ddd" in text
    assert "Column A" in text and "Column B" in text
    # blockquote → callout
    assert "gold callout box" in text
    # code block
    assert "sentinel_code" in text


def test_headings_and_lists_render() -> None:
    text = _norm(_pdf_text(render_markdown_to_pdf_bytes(
        _FIXTURE_MD, title="Fixture", version="v1", git_sha="abc1234")))
    assert "Heading One" in text and "Heading Two" in text
    assert "first bullet" in text and "nested bullet" in text
    assert "ordered one" in text and "ordered two" in text


def test_footer_stamps_version_and_sha() -> None:
    text = _norm(_pdf_text(render_markdown_to_pdf_bytes(
        _FIXTURE_MD, title="My Manual", version="v3", git_sha="deadbee")))
    assert "EVERGREEN RENEWABLES" in text  # brand wordmark in the footer/masthead text layer
    assert "v3" in text                    # version subtitle + footer provenance
    assert "deadbee" in text               # short git sha in the footer provenance


# ── every real enablement guide renders ──────────────────────────────────────────────────
@pytest.mark.parametrize("entry", load_manifest().entries, ids=lambda e: e.key)
def test_every_manifest_doc_renders(entry: ManifestEntry) -> None:
    md_text = entry.source_path().read_text(encoding="utf-8")
    pdf = render_markdown_to_pdf_bytes(
        md_text, title=entry.title, version=entry.version, git_sha="0000000")
    assert pdf[:5] == b"%PDF-", f"{entry.key} did not render a PDF"
    text = _pdf_text(pdf)
    assert "EVERGREEN RENEWABLES" in text, f"{entry.key} missing brand wordmark"


# ── manifest loader ──────────────────────────────────────────────────────────────────────
def test_committed_manifest_round_trips() -> None:
    man = load_manifest()
    assert man.manifest_version == 1
    keys = {e.key for e in man.entries}
    # the seven D2-1 guides + the four D2-2 docs (owner's manual, safety-forms + admin-
    # dashboard guides, and the generated ITS_Config data dictionary) + the two delivery-
    # critical guides added 2026-07-13 (operator dashboard WS2, subcontracts generator) +
    # the eight Tier-1 system references (documentation-corpus, Tranche A, 2026-07-14/15)
    assert keys == {
        "fieldops_checklists", "manager_tier", "subcontractor_tier", "portal_job_creation",
        "progress_rollup_numbers", "crew_time_corrections", "purchase_orders",
        "its_owners_manual", "safety_reports_guide", "portal_admin_dashboard",
        "its_config_dictionary", "operator_dashboard", "subcontracts",
        "system_architecture", "daemon_reference", "data_model_reference",
        "integration_reference", "security_trust_model", "escalation_matrix",
        "glossary", "documentation_index",
    }
    # by_key / by_source lookups
    assert man.by_key("manager_tier") is not None
    assert man.by_source("docs/enablement/manager_tier.md") is man.by_key("manager_tier")
    assert man.by_source("purchase_orders.md") is man.by_key("purchase_orders")  # filename fallback
    assert man.by_source("docs/references/daemon_reference.md") is man.by_key("daemon_reference")
    assert man.by_key("does_not_exist") is None


def test_committed_manifest_is_self_consistent() -> None:
    """Every recorded sha256 must equal the current source bytes — a committed drift is a bug."""
    results = check_currency(load_manifest())
    stale = [r.entry.key for r in results if r.status != "ok"]
    assert not stale, f"manifest sha256 drift on committed docs: {stale}"


def test_manifest_carries_audience() -> None:
    """The optional `audience` field loads onto ManifestEntry (Tier-1 docs set it; older
    guides omit it and default to "")."""
    man = load_manifest()
    sysarch = man.by_key("system_architecture")
    glossary = man.by_key("glossary")
    manager = man.by_key("manager_tier")
    assert sysarch is not None and glossary is not None and manager is not None
    # Tier-1 references carry an audience tag.
    assert sysarch.audience == "operator"
    assert glossary.audience == "all"
    # A guide with no `audience:` key defaults to the empty string, never raises.
    assert manager.audience == ""


def test_manifest_audience_defaults_when_absent(tmp_path: Path) -> None:
    """A minimal entry with no `audience:` key loads with audience == "" (backward compat)."""
    p = tmp_path / "m.yaml"
    p.write_text(
        "manifest_version: 1\ndocs:\n"
        "  - key: x\n    title: X\n    version: v1\n    source: docs/enablement/manager_tier.md\n"
        f"    sha256: {'0' * 64}\n",
        encoding="utf-8",
    )
    entry = load_manifest(p).entries[0]
    assert entry.audience == ""


def test_loader_rejects_bad_version(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("manifest_version: 2\ndocs:\n  - key: x\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="unsupported"):
        load_manifest(p)


def test_loader_rejects_missing_docs(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("manifest_version: 1\ndocs: []\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="no 'docs' list"):
        load_manifest(p)


def test_loader_rejects_incomplete_entry(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(
        "manifest_version: 1\ndocs:\n  - key: x\n    title: T\n    version: v1\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="missing key"):
        load_manifest(p)


def test_loader_rejects_duplicate_key(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(
        "manifest_version: 1\ndocs:\n"
        "  - {key: x, title: T, version: v1, source: a.md, sha256: aa}\n"
        "  - {key: x, title: U, version: v1, source: b.md, sha256: bb}\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="duplicate key"):
        load_manifest(p)


def test_loader_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="missing"):
        load_manifest(tmp_path / "nope.yaml")


# ── SHA-256 currency check BITES on drift ────────────────────────────────────────────────
def test_currency_check_bites_on_changed_doc() -> None:
    """A wrong recorded sha256 against a real source file must register as 'stale'."""
    real_source = "docs/enablement/manager_tier.md"
    good_sha = compute_sha256(_ROOT / real_source)
    ok = Manifest(1, [ManifestEntry("manager_tier", "T", "v1", real_source, good_sha)])
    assert all(r.status == "ok" for r in check_currency(ok))

    drifted = Manifest(1, [ManifestEntry("manager_tier", "T", "v1", real_source, "0" * 64)])
    results = check_currency(drifted)
    assert [r.status for r in results] == ["stale"]
    assert results[0].current_sha256 == good_sha  # reports the real current hash

    gone = Manifest(1, [ManifestEntry("ghost", "T", "v1", "docs/enablement/ghost.md", "0" * 64)])
    assert [r.status for r in check_currency(gone)] == ["missing"]


def test_run_check_returns_nonzero_on_drift() -> None:
    drifted = Manifest(1, [ManifestEntry(
        "manager_tier", "T", "v1", "docs/enablement/manager_tier.md", "0" * 64)])
    assert build_docs_pdfs.run_check(drifted) == 1


# ── build CLI ─────────────────────────────────────────────────────────────────────────────
def test_cli_check_clean_on_committed_tree() -> None:
    assert build_docs_pdfs.main(["--check"]) == 0


def test_cli_upload_is_dark_when_disabled(
    mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    # CLI-level: `--upload` with no ITS_Config gate is dark — rc 0, a loud message, no Box call.
    mocker.patch.object(
        build_docs_pdfs.smartsheet_client, "get_setting",
        side_effect=build_docs_pdfs.smartsheet_client.SmartsheetNotFoundError("absent"),
    )
    upload = mocker.patch.object(build_docs_pdfs.box_client, "upload_bytes_or_new_version")
    assert build_docs_pdfs.main(["--upload"]) == 0
    assert "DARK" in capsys.readouterr().out
    upload.assert_not_called()


def test_cli_doc_renders_one_pdf(tmp_path: Path) -> None:
    rc = build_docs_pdfs.main(["--doc", "manager_tier", "--out", str(tmp_path)])
    assert rc == 0
    out = tmp_path / "manager_tier.pdf"
    assert out.is_file()
    assert out.read_bytes()[:5] == b"%PDF-"


def test_cli_doc_by_source_path(tmp_path: Path) -> None:
    rc = build_docs_pdfs.main(
        ["--doc", "docs/enablement/purchase_orders.md", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "purchase_orders.pdf").is_file()


def test_cli_unknown_doc_errors(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_docs_pdfs.main(["--doc", "no_such_guide", "--out", str(tmp_path)])


def test_cli_all_renders_every_manifest_doc(tmp_path: Path) -> None:
    rc = build_docs_pdfs.main(["--all", "--out", str(tmp_path)])
    assert rc == 0
    rendered = {p.stem for p in tmp_path.glob("*.pdf")}
    assert rendered == {e.key for e in load_manifest().entries}


def test_render_entry_missing_source_raises(tmp_path: Path) -> None:
    ghost = ManifestEntry("ghost", "T", "v1", "docs/enablement/ghost.md", "0" * 64)
    with pytest.raises(FileNotFoundError):
        build_docs_pdfs.render_entry(ghost, tmp_path, "abc1234")


# ---- D2-3: Box publish leg (--upload), dark-gated — MOCK-ONLY (no live Box) -----------


def _enabled_config(folder_id: str = "999001"):
    """A get_setting side-effect: enabled=true + the given folder id."""
    def _gs(key: str, *, workstream: str) -> str:
        return {
            build_docs_pdfs.CFG_UPLOAD_ENABLED: "true",
            build_docs_pdfs.CFG_UPLOAD_FOLDER_ID: folder_id,
        }[key]
    return _gs


def test_upload_is_dark_and_uploads_nothing_when_disabled(mocker) -> None:
    # No `docs_pdf.upload.enabled` row → dark. Nothing rendered, nothing uploaded, rc 0.
    mocker.patch.object(
        build_docs_pdfs.smartsheet_client, "get_setting",
        side_effect=build_docs_pdfs.smartsheet_client.SmartsheetNotFoundError("absent"),
    )
    upload = mocker.patch.object(build_docs_pdfs.box_client, "upload_bytes_or_new_version")
    render = mocker.patch.object(build_docs_pdfs, "render_entry")
    rc = build_docs_pdfs.run_upload(load_manifest(), Path("/nonexistent"), "abc1234")
    assert rc == 0
    upload.assert_not_called()
    render.assert_not_called()


def test_upload_refuses_when_enabled_but_folder_unset(mocker) -> None:
    def _gs(key: str, *, workstream: str) -> str:
        if key == build_docs_pdfs.CFG_UPLOAD_ENABLED:
            return "true"
        raise build_docs_pdfs.smartsheet_client.SmartsheetNotFoundError("no folder")
    mocker.patch.object(build_docs_pdfs.smartsheet_client, "get_setting", side_effect=_gs)
    upload = mocker.patch.object(build_docs_pdfs.box_client, "upload_bytes_or_new_version")
    rc = build_docs_pdfs.run_upload(load_manifest(), Path("/nonexistent"), "abc1234")
    assert rc == 2
    upload.assert_not_called()


def test_upload_publishes_each_doc_to_the_configured_folder(mocker, tmp_path: Path) -> None:
    mocker.patch.object(
        build_docs_pdfs.smartsheet_client, "get_setting", side_effect=_enabled_config()
    )

    def _fake_render(entry, out_dir, git_sha):  # noqa: ANN001 — mock seam
        p = tmp_path / f"{entry.key}.pdf"
        p.write_bytes(f"PDF:{entry.key}".encode())
        return p

    mocker.patch.object(build_docs_pdfs, "render_entry", side_effect=_fake_render)
    upload = mocker.patch.object(
        build_docs_pdfs.box_client, "upload_bytes_or_new_version",
        return_value={"id": "file-1"},
    )
    man = load_manifest()
    rc = build_docs_pdfs.run_upload(man, tmp_path, "abc1234")
    assert rc == 0
    assert upload.call_count == len(man.entries)
    for call in upload.call_args_list:
        folder_id, name, content = call.args
        assert folder_id == "999001"
        assert name.endswith(".pdf")
        assert content == f"PDF:{name[:-4]}".encode()  # the rendered bytes, unmodified


def test_upload_fences_a_per_doc_box_failure_and_continues(mocker, tmp_path: Path) -> None:
    mocker.patch.object(
        build_docs_pdfs.smartsheet_client, "get_setting", side_effect=_enabled_config()
    )
    def _fake_render(entry, out_dir, git_sha):  # noqa: ANN001 — mock seam
        p = tmp_path / f"{entry.key}.pdf"
        p.write_bytes(b"x")
        return p

    mocker.patch.object(build_docs_pdfs, "render_entry", side_effect=_fake_render)
    man = load_manifest()
    # First upload raises BoxError; the rest succeed — the fence counts it and keeps going.
    upload = mocker.patch.object(
        build_docs_pdfs.box_client, "upload_bytes_or_new_version",
        side_effect=[build_docs_pdfs.box_client.BoxError("boom")]
        + [{"id": "ok"}] * (len(man.entries) - 1),
    )
    rc = build_docs_pdfs.run_upload(man, tmp_path, "abc1234")
    assert rc == 1
    assert upload.call_count == len(man.entries)  # every doc still attempted
