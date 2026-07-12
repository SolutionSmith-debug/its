"""Exhibit A (Scope of Work) skeleton + per-trade Article II loader (SC-S3b) — pure functions, no side
effects. A faithful sibling of subcontracts/terms.py against the subcontracts/exhibit dir.

Exhibit A (ADR-0003) is git-versioned prose: ``exhibit/manifest.json`` pins the FIXED skeleton
(``skeleton.md`` — Article I General + a ``{{article_ii}}`` marker + Articles III/IV/V/VI) and a set of
per-trade Article II "The Work" bodies (``art2/<key>.md``), each sha256-verified on every load. The
skeleton and every trade template are immutable + hash-pinned — a wording change is a NEW file + manifest
entry, never an edit; a pinned draft renders identically forever.

A subcontract's trade (the ITS_Subcontractors "Trade" vocabulary) maps through ``trade_map`` to a
template key; the three electrical trades (AC/MV/DC) share the single ``electrical`` scope because the
corpus does not distinguish them at template level. ``Specialty`` has no corpus scope — its template is
an operator-authored placeholder.

Substitution tokens (``{{token}}``) are STRICT: rendering with a missing/blank token raises (a
subcontract must never go out with an unfilled contract blank). ``{{article_ii}}`` is one of the
skeleton's required tokens — the renderer fills it with the trade's Article II body (via
``load_trade_art2``), so by substitution time it must be present like every other token. No network, no
Smartsheet, no state writes — safe to import anywhere.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

EXHIBIT_DIR = Path(__file__).resolve().parent / "exhibit"

_TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")


class ExhibitError(Exception):
    """Raised on any manifest/skeleton/trade-template integrity or usage error."""


def load_manifest() -> dict[str, Any]:
    """Load + shape-check exhibit/manifest.json."""
    path = EXHIBIT_DIR / "manifest.json"
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ExhibitError(f"exhibit manifest missing: {path}") from e
    except json.JSONDecodeError as e:
        raise ExhibitError(f"exhibit manifest is not valid JSON: {path}: {e}") from e
    if manifest.get("manifest_version") != 1:
        raise ExhibitError(
            f"unsupported exhibit manifest_version {manifest.get('manifest_version')!r} "
            "(this loader speaks version 1)"
        )
    skeleton = manifest.get("skeleton")
    if not isinstance(skeleton, dict) or not skeleton.get("file") or not skeleton.get("sha256"):
        raise ExhibitError("exhibit manifest has no valid skeleton entry")
    templates = manifest.get("trade_templates")
    if not isinstance(templates, dict) or not templates:
        raise ExhibitError("exhibit manifest has no trade_templates")
    trade_map = manifest.get("trade_map")
    if not isinstance(trade_map, dict) or not trade_map:
        raise ExhibitError("exhibit manifest has no trade_map")
    return manifest


def _verify_and_read(rel_file: str, sha256: str, what: str) -> str:
    """Read `EXHIBIT_DIR/rel_file`, sha256-verify the RAW bytes against `sha256`, return decoded text."""
    path = EXHIBIT_DIR / rel_file
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        raise ExhibitError(f"exhibit {what} file missing: {path}") from e
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sha256:
        raise ExhibitError(
            f"exhibit {what} file HASH MISMATCH ({path.name}): manifest pins {sha256} but file is "
            f"{digest} — skeleton/trade files are immutable; a wording change must be a NEW file + "
            "manifest entry"
        )
    return raw.decode("utf-8")


def load_skeleton() -> str:
    """The verbatim Exhibit A skeleton text — sha256-verified against manifest.skeleton.sha256 on EVERY
    load. Carries the ``{{article_ii}}`` marker the renderer fills with the trade's Article II body."""
    skeleton = load_manifest()["skeleton"]
    return _verify_and_read(str(skeleton["file"]), str(skeleton["sha256"]), "skeleton")


def template_key_for_trade(trade: str) -> str:
    """Map a subcontract Trade (ITS_Subcontractors vocabulary) to its Article II template key, or
    ExhibitError for an unknown trade."""
    trade_map: dict[str, str] = load_manifest()["trade_map"]
    if trade not in trade_map:
        raise ExhibitError(
            f"unknown subcontract trade {trade!r} (known: {sorted(trade_map)})"
        )
    return trade_map[trade]


def load_trade_art2(trade: str) -> str:
    """The verbatim Article II 'The Work' body for `trade` — resolved through trade_map to a template key
    and sha256-verified against manifest.trade_templates[key].sha256 on EVERY load."""
    manifest = load_manifest()
    key = template_key_for_trade(trade)
    templates: dict[str, Any] = manifest["trade_templates"]
    entry = templates.get(key)
    if not isinstance(entry, dict) or not entry.get("file") or not entry.get("sha256"):
        raise ExhibitError(
            f"exhibit trade_map points trade {trade!r} at key {key!r} with no valid trade_templates "
            f"entry (known keys: {sorted(templates)})"
        )
    return _verify_and_read(str(entry["file"]), str(entry["sha256"]), f"trade template {key!r}")


def required_tokens() -> list[str]:
    """The substitution tokens the skeleton declares (includes ``article_ii``, filled by the renderer)."""
    tokens: list[str] = list(load_manifest()["skeleton"].get("tokens") or [])
    return tokens


def substitute_tokens(text: str, values: Mapping[str, str]) -> str:
    """Fill every ``{{token}}`` in `text` from `values` — STRICT on missing/blank tokens (a subcontract
    must never render with an unfilled contract blank). ``{{article_ii}}`` is not special-cased: the
    renderer supplies the trade's Article II body as the ``article_ii`` value before this runs, so it is
    required-present like any other token. Extra keys ignored."""
    missing = {
        m.group(1)
        for m in _TOKEN_RE.finditer(text)
        if not str(values.get(m.group(1), "")).strip()
    }
    if missing:
        raise ExhibitError(
            f"exhibit text has unfilled token(s): {sorted(missing)} — refusing to render with a blank "
            "where contract language belongs"
        )
    return _TOKEN_RE.sub(lambda m: str(values[m.group(1)]), text)
