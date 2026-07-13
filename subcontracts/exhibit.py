"""Exhibit A (Scope of Work) skeleton + per-trade Article II loader (SC-S3b) — pure functions, no side
effects. A faithful sibling of subcontracts/terms.py against the subcontracts/exhibit dir.

Exhibit A (ADR-0003) is git-versioned prose: ``exhibit/manifest.json`` pins the FIXED skeleton
(``skeleton.md`` — Article I General + a ``{{article_ii}}`` marker + Articles III/IV/V/VI) and a set of
per-trade Article II "The Work" template KEYS. The skeleton is a single immutable hash-pinned file; each
trade-template key is VERSIONED (``current_version`` + a ``versions`` map, each version a sha256-pinned
immutable ``art2/*.md`` file carrying a ``legal_review`` flag) — the §50 config editor mints new versions
``pending`` and the operator make-currents one to clear+activate it, exactly like the subcontract terms
library. A wording change is a NEW version file, never an edit; a pinned draft renders identically forever.
The render loads a key's CURRENT version through the Layer-A legal gate (``_trade_version_entry`` — a
non-cleared version fences).

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


def _trade_version_entry(key: str, version: str | None = None) -> tuple[str, dict[str, Any]]:
    """Resolve (version, entry) for a trade-template KEY; default = the key's ``current_version``.
    Enforces the Layer-A legal-review gate — a NON-``cleared`` version RAISES (a subcontract must never
    render un-attested Article II scope language). Mirrors ``subcontracts.terms._version_entry``, keyed
    by ``template_key`` on ``trade_templates`` (the versioned schema: each key carries a
    ``current_version`` + a ``versions`` map, each version ``{file, sha256, legal_review}``)."""
    templates: dict[str, Any] = load_manifest()["trade_templates"]
    if key not in templates:
        raise ExhibitError(f"unknown exhibit trade-template key {key!r} (known: {sorted(templates)})")
    tmpl = templates[key]
    if not isinstance(tmpl, dict):
        raise ExhibitError(f"exhibit trade template {key!r} is malformed (not an object)")
    versions = tmpl.get("versions")
    resolved = version if version is not None else str(tmpl.get("current_version"))
    if not isinstance(versions, dict) or resolved not in versions:
        raise ExhibitError(
            f"exhibit trade template {key!r} has no version {resolved!r} "
            f"(known: {sorted(versions) if isinstance(versions, dict) else '?'})"
        )
    entry = versions[resolved]
    if not isinstance(entry, dict) or not entry.get("file") or not entry.get("sha256"):
        raise ExhibitError(
            f"exhibit trade template {key!r} v{resolved} is malformed (missing file/sha256)"
        )
    # Layer-A legal gate: the render emits Article II scope only from a CLEARED version. A minted-but-
    # un-cleared version (add_version leaves it 'pending') fences here — the single choke point shared
    # by load_trade_art2 + load_trade_art2_by_key, whether reached by an explicit pin or current_version.
    if str(entry.get("legal_review")) != "cleared":
        raise ExhibitError(
            f"exhibit trade template {key!r} v{resolved} is legal_review={entry.get('legal_review')!r} — "
            "NOT cleared for live use; a subcontract must not render un-attested Article II scope. Clear "
            "it via the config editor (make it current) or keep current_version on the last cleared version."
        )
    return resolved, entry


def load_trade_art2(trade: str, version: str | None = None) -> str:
    """The verbatim Article II 'The Work' body for `trade` — resolved through trade_map to a template
    key, then to that key's CURRENT (legal-review-cleared) version (or an explicit `version`), and
    sha256-verified on EVERY load. The Layer-A gate lives in ``_trade_version_entry``."""
    key = template_key_for_trade(trade)
    resolved, entry = _trade_version_entry(key, version)
    return _verify_and_read(str(entry["file"]), str(entry["sha256"]), f"trade template {key!r} v{resolved}")


def load_trade_art2_by_key(key: str, version: str | None = None) -> str:
    """Config-editor read: the Article II body for a template KEY directly (not via a Trade), at its
    current or an explicit version — sha256-verified + legal-gated. Powers the 'edit from live' pre-fill."""
    resolved, entry = _trade_version_entry(key, version)
    return _verify_and_read(str(entry["file"]), str(entry["sha256"]), f"trade template {key!r} v{resolved}")


def list_trade_templates() -> list[dict[str, Any]]:
    """Config-editor picker source: every trade-template key with its ``current_version``, its versions
    (+ each version's ``legal_review``), and the Trades that map to it. Metadata only — no file reads."""
    manifest = load_manifest()
    templates: dict[str, Any] = manifest["trade_templates"]
    trade_map: dict[str, str] = manifest["trade_map"]
    trades_for: dict[str, list[str]] = {}
    for trade, mapped_key in trade_map.items():
        trades_for.setdefault(mapped_key, []).append(trade)
    out: list[dict[str, Any]] = []
    for key in sorted(templates):
        tmpl: dict[str, Any] = templates[key] if isinstance(templates[key], dict) else {}
        raw_versions = tmpl.get("versions")
        versions: dict[str, Any] = raw_versions if isinstance(raw_versions, dict) else {}
        out.append(
            {
                "template_key": key,
                "current_version": tmpl.get("current_version"),
                "trades": sorted(trades_for.get(key, [])),
                "versions": [
                    {"version": v, "legal_review": str((versions[v] or {}).get("legal_review"))}
                    for v in sorted(versions)
                ],
            }
        )
    return out


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
