"""Loader for the §6a enablement manifest (``docs/enablement/manifest.yaml``).

The manifest is THE single source of truth for the delivery-critical PDF build set: which
in-repo enablement guides get rendered to branded manuals, each with a display title, a
version string, its source path, and a recorded SHA-256 of the source bytes. Adding a doc
to the build set = adding an entry here; ``scripts/build_docs_pdfs.py`` renders exactly the
listed docs.

The recorded SHA-256 is the doc-currency teeth: ``build_docs_pdfs.py --check`` recomputes
each source's hash and flags any that drifted from its recorded value (a guide edited
without re-rendering / re-recording), mirroring ``regen_doc_indexes --check``.

Pure loader — reads YAML + hashes files, no network, no state writes. Mirrors the
``po_materials/terms.py`` loader style (shape-checked, typed error, frozen dataclasses).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "docs" / "enablement" / "manifest.yaml"


class ManifestError(Exception):
    """Raised on any manifest integrity / shape error."""


@dataclass(frozen=True)
class ManifestEntry:
    """One doc in the build set."""
    key: str          # stable slug (matches the source filename stem + the output PDF stem)
    title: str        # display title for the PDF masthead + footer
    version: str      # manual version string (e.g. "v1")
    source: str       # repo-root-relative path to the markdown source
    sha256: str       # recorded SHA-256 of the source file bytes (doc-currency baseline)

    def source_path(self) -> Path:
        """Absolute path to the source markdown file."""
        return REPO_ROOT / self.source


@dataclass(frozen=True)
class Manifest:
    manifest_version: int
    entries: list[ManifestEntry]

    def by_key(self, key: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.key == key), None)

    def by_source(self, source: str) -> ManifestEntry | None:
        """Match by repo-root-relative source path (exact, or by filename fallback)."""
        norm = source.replace("\\", "/")
        exact = next((e for e in self.entries if e.source == norm), None)
        if exact is not None:
            return exact
        stem = Path(norm).name
        return next((e for e in self.entries if Path(e.source).name == stem), None)


def compute_sha256(path: Path) -> str:
    """SHA-256 hex digest of a file's RAW bytes (the currency baseline)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    """Load + shape-check the enablement manifest.

    Raises ManifestError on a missing file, invalid YAML, unsupported version, a missing
    ``docs`` list, or a malformed entry (missing required key). Never returns a partial.
    """
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ManifestError(f"enablement manifest missing: {path}") from e
    except yaml.YAMLError as e:
        raise ManifestError(f"enablement manifest is not valid YAML: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError(f"enablement manifest top level must be a mapping: {path}")
    if raw.get("manifest_version") != 1:
        raise ManifestError(
            f"unsupported enablement manifest_version {raw.get('manifest_version')!r} "
            "(this loader speaks version 1)"
        )
    docs = raw.get("docs")
    if not isinstance(docs, list) or not docs:
        raise ManifestError("enablement manifest has no 'docs' list")

    entries: list[ManifestEntry] = []
    required = ("key", "title", "version", "source", "sha256")
    seen_keys: set[str] = set()
    for i, d in enumerate(docs):
        if not isinstance(d, dict):
            raise ManifestError(f"enablement manifest docs[{i}] is not a mapping")
        missing = [k for k in required if not str(d.get(k, "")).strip()]
        if missing:
            raise ManifestError(f"enablement manifest docs[{i}] missing key(s): {missing}")
        key = str(d["key"])
        if key in seen_keys:
            raise ManifestError(f"enablement manifest has a duplicate key: {key!r}")
        seen_keys.add(key)
        entries.append(ManifestEntry(
            key=key, title=str(d["title"]), version=str(d["version"]),
            source=str(d["source"]).replace("\\", "/"), sha256=str(d["sha256"]).lower(),
        ))
    return Manifest(manifest_version=1, entries=entries)


@dataclass(frozen=True)
class CurrencyResult:
    """One doc's currency status against its recorded SHA-256."""
    entry: ManifestEntry
    status: str          # "ok" | "stale" | "missing"
    current_sha256: str  # "" when the source file is missing


def check_currency(manifest: Manifest) -> list[CurrencyResult]:
    """Recompute every source's SHA-256 and compare to the recorded value.

    Returns one CurrencyResult per entry. ``stale`` = the source changed since it was
    recorded (needs a re-render + a manifest sha re-seed); ``missing`` = the source file is
    gone. The caller decides how loud to be (build_docs_pdfs --check exits non-zero on any
    non-ok result, warn-only-friendly like regen_doc_indexes --check).
    """
    results: list[CurrencyResult] = []
    for entry in manifest.entries:
        path = entry.source_path()
        if not path.is_file():
            results.append(CurrencyResult(entry, "missing", ""))
            continue
        cur = compute_sha256(path)
        status = "ok" if cur == entry.sha256 else "stale"
        results.append(CurrencyResult(entry, status, cur))
    return results
