"""Render the §6a enablement manifest to branded PDF manuals (WS3 / D2-1).

Reads ``docs/enablement/manifest.yaml`` (via ``docs_pdf.manifest``) and renders each
listed guide to a branded PDF (``docs_pdf.md_render``) under a GITIGNORED build directory
(default ``docs/_build_pdf/``). The canonical distributable copy is the Box upload (D2-3,
not built here) — the repo never commits the rendered binaries.

Each manual's footer carries ``title · version · git-SHA · page N of M``; the git SHA is
``git rev-parse --short HEAD`` at build time (degrades to ``unknown`` off a git tree), so a
printed manual is traceable to the exact source revision it was rendered from.

CLI
---

    python -m scripts.build_docs_pdfs --all              # render every manifest doc
    python -m scripts.build_docs_pdfs --doc fieldops_checklists   # one doc (key OR source path)
    python -m scripts.build_docs_pdfs --check            # doc-currency: flag drifted sources
    python -m scripts.build_docs_pdfs --upload           # D2-3 stub (prints, does nothing)
    python -m scripts.build_docs_pdfs --all --out /tmp/x # override the output directory

``--check`` recomputes each source's SHA-256 and compares to the manifest's recorded value,
mirroring ``regen_doc_indexes --check``: it exits non-zero when a guide changed without its
manifest sha being re-recorded (a re-render + re-seed is owed). Intended to be wired
warn-only in CI (wrap with ``|| echo "::warning::…"`` like the regen step) — the non-zero
is a signal, not a hard gate, during the docs-program build-out.

Not built here (deliberately, per the D2 slice split): the Box publish leg (``--upload`` is
a stub) is D2-3; the ITS Owner's Manual + generated ITS_Config data dictionary are D2-2.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from docs_pdf import manifest as _manifest
from docs_pdf.manifest import Manifest, ManifestEntry, ManifestError
from docs_pdf.md_render import render_markdown_to_pdf_bytes

REPO_ROOT = _manifest.REPO_ROOT
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "_build_pdf"


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
                      help="Box publish leg — stub only (D2-3, not built)")
    parser.add_argument("--out", metavar="DIR", default=None,
                        help=f"output directory (default: {DEFAULT_OUT_DIR.relative_to(REPO_ROOT)})")
    args = parser.parse_args(argv)

    if args.upload:
        print("Box upload is D2-3, not built. This slice (D2-1) renders manuals locally to "
              f"{DEFAULT_OUT_DIR.relative_to(REPO_ROOT)}/ only.")
        return 0

    try:
        man = _manifest.load_manifest()
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return run_check(man)

    out_dir = Path(args.out).resolve() if args.out else DEFAULT_OUT_DIR
    git_sha = resolve_git_sha()

    if args.doc:
        entry = _select_one(man, args.doc)
        return build_entries([entry], out_dir, git_sha)

    # --all
    return build_entries(list(man.entries), out_dir, git_sha)


if __name__ == "__main__":
    sys.exit(main())
