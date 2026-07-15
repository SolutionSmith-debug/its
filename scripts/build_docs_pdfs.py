"""Render the §6a enablement manifest to branded PDF manuals (WS3 / D2-1).

Reads ``docs/enablement/manifest.yaml`` (via ``docs_pdf.manifest``) and renders each
listed guide to a branded PDF (``docs_pdf.md_render``) under a GITIGNORED build directory
(default ``docs/_build_pdf/``). The canonical distributable copy is the Box upload (D2-3,
``--upload``, dark-gated below) — the repo never commits the rendered binaries.

Each manual's footer carries ``title · version · git-SHA · page N of M``; the git SHA is
``git rev-parse --short HEAD`` at build time (degrades to ``unknown`` off a git tree), so a
printed manual is traceable to the exact source revision it was rendered from.

CLI
---

    python -m scripts.build_docs_pdfs --all              # render every manifest doc
    python -m scripts.build_docs_pdfs --doc fieldops_checklists   # one doc (key OR source path)
    python -m scripts.build_docs_pdfs --check            # doc-currency: flag drifted sources
    python -m scripts.build_docs_pdfs --upload           # D2-3 Box publish (DARK unless enabled)
    python -m scripts.build_docs_pdfs --all --out /tmp/x # override the output directory

``--check`` recomputes each source's SHA-256 and compares to the manifest's recorded value,
mirroring ``regen_doc_indexes --check``: it exits non-zero when a guide changed without its
manifest sha being re-recorded (a re-render + re-seed is owed). Intended to be wired
warn-only in CI (wrap with ``|| echo "::warning::…"`` like the regen step) — the non-zero
is a signal, not a hard gate, during the docs-program build-out.

``--upload`` (D2-3) renders every manifest doc then uploads it to the Box folder named by
``docs_pdf.upload.box_folder_id`` (version-on-conflict, so a re-publish updates rather than
duplicates) — but only when ``docs_pdf.upload.enabled`` is true in ITS_Config. Both keys
default to absent → DARK (HOUSE_REFLEXES §5); first activation is the operator's. Still not
built here (D2-2): the ITS Owner's Manual + generated ITS_Config data dictionary.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from docs_pdf import manifest as _manifest
from docs_pdf.manifest import Manifest, ManifestEntry, ManifestError
from docs_pdf.md_render import render_markdown_to_pdf_bytes
from shared import box_client, smartsheet_client

REPO_ROOT = _manifest.REPO_ROOT
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "_build_pdf"

# D2-3 Box publish leg — SHIPS DARK. `--upload` renders every manifest doc then uploads it
# (version-on-conflict, so a re-publish updates the Box file rather than duplicating) to the
# folder named by `docs_pdf.upload.box_folder_id`, but ONLY when `docs_pdf.upload.enabled` is
# true in ITS_Config. Both DEFAULT to absent → dark (HOUSE_REFLEXES §5). First activation is
# the operator's: seed the folder id, then flip enabled=true. NOT read by any daemon (CLI
# only), so these keys are intentionally outside the #336 REQUIRED_CONFIG / config-dictionary
# surface.
CFG_UPLOAD_WORKSTREAM = "docs_pdf"
CFG_UPLOAD_ENABLED = "docs_pdf.upload.enabled"
CFG_UPLOAD_FOLDER_ID = "docs_pdf.upload.box_folder_id"


def resolve_git_sha() -> str:
    """``git rev-parse --short HEAD``, or ``"unknown"`` off a git tree / on any error."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _display_path(path: Path) -> str:
    """Repo-relative path for logging, or the absolute path when outside the repo."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def render_entry(entry: ManifestEntry, out_dir: Path, git_sha: str) -> Path:
    """Render one manifest entry to ``<out_dir>/<key>.pdf`` and return the output path.

    Raises FileNotFoundError if the source markdown is missing (never renders a blank
    manual for an absent guide — the caller surfaces it)."""
    src = entry.source_path()
    if not src.is_file():
        raise FileNotFoundError(f"source markdown missing for {entry.key!r}: {src}")
    md_text = src.read_text(encoding="utf-8")
    pdf = render_markdown_to_pdf_bytes(
        md_text, title=entry.title, version=entry.version, git_sha=git_sha
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{entry.key}.pdf"
    out_path.write_bytes(pdf)
    return out_path


def build_entries(entries: list[ManifestEntry], out_dir: Path, git_sha: str) -> int:
    """Render a list of entries; return a process exit code (0 = all rendered)."""
    failures = 0
    for entry in entries:
        try:
            out_path = render_entry(entry, out_dir, git_sha)
            print(f"rendered {entry.key:24s} → {_display_path(out_path)} "
                  f"({out_path.stat().st_size:,} bytes)")
        except (FileNotFoundError, OSError, ValueError) as exc:
            failures += 1
            print(f"FAILED   {entry.key:24s} — {exc}", file=sys.stderr)
    if failures:
        print(f"\n{failures} doc(s) failed to render.", file=sys.stderr)
    return 1 if failures else 0


def _upload_enabled() -> bool:
    """True only if `docs_pdf.upload.enabled` is an affirmative ITS_Config value.

    Absent/malformed/unreachable → False (dark). A publish leg must never fail OPEN to an
    upload; the gate is the operator's deliberate flip.
    """
    try:
        raw = smartsheet_client.get_setting(CFG_UPLOAD_ENABLED, workstream=CFG_UPLOAD_WORKSTREAM)
    except smartsheet_client.SmartsheetError:
        return False
    return isinstance(raw, str) and raw.strip().lower() in ("true", "1", "yes", "on")


def _upload_folder_id() -> str | None:
    """The configured Box folder id for the publish, or None if unset."""
    try:
        raw = smartsheet_client.get_setting(CFG_UPLOAD_FOLDER_ID, workstream=CFG_UPLOAD_WORKSTREAM)
    except smartsheet_client.SmartsheetError:
        return None
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def run_upload(man: Manifest, out_dir: Path, git_sha: str) -> int:
    """D2-3 publish leg: render every manifest doc then upload it to Box (dark-gated).

    Returns a process exit code: 0 when dark (nothing to do, loud message) or all uploaded;
    2 when enabled-but-misconfigured (no folder id); 1 on any per-doc upload failure.
    """
    if not _upload_enabled():
        print(
            f"Box upload is DARK — {CFG_UPLOAD_ENABLED} (workstream {CFG_UPLOAD_WORKSTREAM}) is not "
            "true. Nothing uploaded. To publish: seed "
            f"{CFG_UPLOAD_FOLDER_ID}=<box folder id> and flip {CFG_UPLOAD_ENABLED}=true in ITS_Config."
        )
        return 0
    folder_id = _upload_folder_id()
    if not folder_id:
        print(
            f"{CFG_UPLOAD_ENABLED} is true but {CFG_UPLOAD_FOLDER_ID} is unset — cannot publish "
            "without a target Box folder.",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for entry in _publish_order(man):
        try:
            out_path = render_entry(entry, out_dir, git_sha)
            result = box_client.upload_bytes_or_new_version(
                folder_id, out_path.name, out_path.read_bytes()
            )
            print(f"uploaded {entry.key:24s} → Box file {result.get('id', '?')} ({out_path.name})")
        except (FileNotFoundError, OSError, ValueError, box_client.BoxError) as exc:
            failures += 1
            print(f"FAILED   {entry.key:24s} — {exc}", file=sys.stderr)
    if failures:
        print(f"\n{failures} doc(s) failed to upload.", file=sys.stderr)
    return 1 if failures else 0


def _publish_order(man: Manifest) -> list[ManifestEntry]:
    """Manifest entries in publish order: the corpus INDEX (documentation_index) first, so the
    operator/reader lands on the map, then the rest in manifest order."""
    index = man.by_key("documentation_index")
    rest = [e for e in man.entries if e.key != "documentation_index"]
    return ([index] if index is not None else []) + rest


def run_upload_dry(man: Manifest) -> int:
    """`--upload --dry-run`: print the exact publish plan and make NO Box call.

    Shows the resolved gate state + target Box folder (fail-soft — an unreachable/unset config
    reads as '<unset>'), then the ordered file list with each source's sha8 and audience.
    """
    enabled = _upload_enabled()
    folder = _upload_folder_id() or "<unset>"
    order = _publish_order(man)
    print("Box publish PLAN (dry-run — no upload performed)")
    print(f"  gate {CFG_UPLOAD_ENABLED} (ws {CFG_UPLOAD_WORKSTREAM}): "
          f"{'LIVE' if enabled else 'DARK'}")
    print(f"  target Box folder ({CFG_UPLOAD_FOLDER_ID}): {folder}")
    print(f"  {len(order)} file(s), published in this order (INDEX first):")
    print(f"  {'PDF filename':32s} {'sha8':10s} audience")
    for e in order:
        try:
            sha8 = _manifest.compute_sha256(e.source_path())[:8]
        except OSError:
            sha8 = "MISSING"
        tag = " (INDEX)" if e.key == "documentation_index" else ""
        print(f"  {e.key + '.pdf':32s} {sha8:10s} {e.audience or '—'}{tag}")
    if not enabled:
        print("\n  gate is DARK — a real `--upload` would upload nothing. Seed "
              f"{CFG_UPLOAD_FOLDER_ID} + flip {CFG_UPLOAD_ENABLED}=true to publish.")
    return 0


def run_check(man: Manifest) -> int:
    """Doc-currency check: flag any source that drifted from its recorded SHA-256.

    Returns 1 if any entry is stale/missing (mirrors regen_doc_indexes --check), else 0.
    """
    results = _manifest.check_currency(man)
    drifted = [r for r in results if r.status != "ok"]
    for r in drifted:
        if r.status == "missing":
            print(f"MISSING  {r.entry.key:24s} {r.entry.source} (source file not found)")
        else:
            print(f"STALE    {r.entry.key:24s} {r.entry.source}")
            print(f"           recorded {r.entry.sha256}")
            print(f"           current  {r.current_sha256}")
    if drifted:
        print(f"\n{len(drifted)} enablement doc(s) drifted from the manifest baseline.")
        print("Re-render (`--all`) and re-record the sha256 in docs/enablement/manifest.yaml.")
        return 1
    print(f"(all {len(results)} enablement docs current)")
    return 0


def _select_one(man: Manifest, doc: str) -> ManifestEntry:
    """Resolve a ``--doc`` argument (a manifest key OR a source path) to one entry."""
    entry = man.by_key(doc) or man.by_source(doc)
    if entry is None:
        keys = ", ".join(e.key for e in man.entries)
        raise SystemExit(f"--doc {doc!r} matched no manifest key or source (known keys: {keys})")
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.build_docs_pdfs",
        description="Render the §6a enablement manifest to branded PDF manuals. "
                    "See docs_pdf/ + docs/enablement/manifest.yaml.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="render every manifest doc")
    mode.add_argument("--doc", metavar="KEY|PATH", help="render one doc (manifest key or source path)")
    mode.add_argument("--check", action="store_true",
                      help="doc-currency: flag sources drifted from their recorded sha256")
    mode.add_argument("--upload", action="store_true",
                      help="Box publish leg (D2-3) — render + upload every doc; DARK unless "
                           "docs_pdf.upload.enabled=true in ITS_Config")
    parser.add_argument("--out", metavar="DIR", default=None,
                        help=f"output directory (default: {DEFAULT_OUT_DIR.relative_to(REPO_ROOT)})")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --upload: print the exact publish plan (folder, files, shas) "
                             "and make NO Box call")
    args = parser.parse_args(argv)

    try:
        man = _manifest.load_manifest()
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return run_check(man)

    out_dir = Path(args.out).resolve() if args.out else DEFAULT_OUT_DIR
    git_sha = resolve_git_sha()

    if args.upload:
        if args.dry_run:
            return run_upload_dry(man)
        return run_upload(man, out_dir, git_sha)

    if args.doc:
        entry = _select_one(man, args.doc)
        return build_entries([entry], out_dir, git_sha)

    # --all
    return build_entries(list(man.entries), out_dir, git_sha)


if __name__ == "__main__":
    sys.exit(main())
