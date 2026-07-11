"""Subcontract terms-library + contractor/payment config loader (SC-S3a) — pure functions, no side
effects. A faithful fork of po_materials/terms.py against the subcontracts/terms + subcontracts/config
dirs (the ops-stds note: the subcontracts renderer implements its OWN Layer-A gate; the po_materials
loader is hard-bound to po_materials/terms and won't cover this).

The terms library (ADR-0003) is git-versioned prose: ``terms/manifest.json`` maps profile ids →
immutable, sha256-pinned version files (the 27-article subcontract body). Drafts pin (profile id,
version) at generate time; because version files are immutable and hash-verified on every load, a
pinned draft renders identically forever — a wording change is a NEW ``_vN`` file, never an edit.
Profile ids are the ITS_Subcontractors "Default Terms Profile" vocabulary
(``shared/picklist_validation._SUBCONTRACTOR_TERMS_PROFILE_VALUES``, manifest-derived).

Two profile kinds: ``library`` (versioned text this module loads + token-fills) and ``attach`` (a
negotiated MSA — the renderer emits only the manifest ``render_line``; ``load_terms_text`` refuses).

Substitution tokens (``{{token}}``) are STRICT both ways: rendering with a missing token raises (a
subcontract must never go out with an unfilled contract blank), and the manifest declares each
version's token list. No network, no Smartsheet, no state writes — safe to import anywhere.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TERMS_DIR = Path(__file__).resolve().parent / "terms"
CONFIG_DIR = Path(__file__).resolve().parent / "config"

_TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")


class TermsError(Exception):
    """Raised on any manifest/terms-file integrity or usage error."""


def load_manifest() -> dict[str, Any]:
    """Load + shape-check terms/manifest.json."""
    path = TERMS_DIR / "manifest.json"
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise TermsError(f"subcontract terms manifest missing: {path}") from e
    except json.JSONDecodeError as e:
        raise TermsError(f"subcontract terms manifest is not valid JSON: {path}: {e}") from e
    if manifest.get("manifest_version") != 1:
        raise TermsError(
            f"unsupported terms manifest_version {manifest.get('manifest_version')!r} "
            "(this loader speaks version 1)"
        )
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise TermsError("subcontract terms manifest has no profiles")
    return manifest


def list_profiles() -> dict[str, dict[str, Any]]:
    """All profiles keyed by id (library AND attach kinds)."""
    profiles: dict[str, dict[str, Any]] = load_manifest()["profiles"]
    return profiles


def get_profile(profile_id: str) -> dict[str, Any]:
    """One profile's manifest entry, or TermsError for an unknown id."""
    profiles = list_profiles()
    if profile_id not in profiles:
        raise TermsError(
            f"unknown subcontract terms profile {profile_id!r} (known: {sorted(profiles)})"
        )
    return profiles[profile_id]


def _version_entry(profile_id: str, version: str | None) -> tuple[str, dict[str, Any]]:
    """Resolve (version, entry) for a library profile; default = current_version. Enforces the Layer-A
    legal-review gate (identical contract to po_materials.terms._version_entry)."""
    profile = get_profile(profile_id)
    if profile.get("kind") != "library":
        raise TermsError(
            f"subcontract terms profile {profile_id!r} is kind={profile.get('kind')!r} — it has no "
            "library text to load (attach-kind renders only its manifest render_line)"
        )
    versions = profile.get("versions") or {}
    resolved = version if version is not None else str(profile.get("current_version"))
    if resolved not in versions:
        raise TermsError(
            f"subcontract terms profile {profile_id!r} has no version {resolved!r} "
            f"(known: {sorted(versions)})"
        )
    entry = versions[resolved]
    # Layer A legal-review gate (§50): a version renders on a LIVE subcontract only once the operator
    # has cleared its legal review (config editor "make this version current" → set_current sets
    # legal_review "cleared"). A minted-but-un-cleared version (add_version leaves it "pending") must
    # NOT render binding contract language — it raises here, the single choke point shared by
    # load_terms_text AND required_tokens, whether the version was reached by an explicit pin or the
    # current_version default. TermsError propagates as a per-subcontract fence (subcontract_poll →
    # Review Queue), never a silent skip. THIS is what fences the seeded-pending standard body until
    # the operator legally attests it via make-current.
    if not isinstance(entry, dict) or str(entry.get("legal_review")) != "cleared":
        got = entry.get("legal_review") if isinstance(entry, dict) else "?"
        raise TermsError(
            f"subcontract terms profile {profile_id!r} v{resolved} is legal_review={got!r} — NOT "
            "cleared for live use; a subcontract must not render un-cleared contract language. Clear "
            "it via the config editor (make it current) or keep current_version on the last cleared "
            "version."
        )
    return resolved, entry


def load_terms_text(profile_id: str, version: str | None = None) -> str:
    """The verbatim body text for (profile, version) — sha256-verified on EVERY load. The hash covers
    the RAW file bytes; the returned text has the leading ``<!-- ... -->`` provenance header stripped."""
    resolved, entry = _version_entry(profile_id, version)
    path = TERMS_DIR / str(entry["file"])
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        raise TermsError(f"subcontract terms file missing for {profile_id!r} v{resolved}: {path}") from e
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise TermsError(
            f"subcontract terms file HASH MISMATCH for {profile_id!r} v{resolved} ({path.name}): "
            f"manifest pins {entry['sha256']} but file is {digest} — version files are immutable; a "
            "wording change must be a NEW _vN file + manifest entry"
        )
    return _strip_header_comment(raw.decode("utf-8"))


def _strip_header_comment(text: str) -> str:
    """Drop the leading ``<!-- ... -->`` provenance block from a terms file (only a top comment; a file
    without one passes through; a malformed unterminated comment raises)."""
    stripped = text.lstrip()
    if not stripped.startswith("<!--"):
        return text
    end = stripped.find("-->")
    if end == -1:
        raise TermsError("subcontract terms file has an unterminated leading <!-- comment")
    return stripped[end + len("-->"):].lstrip("\n")


def render_line(profile_id: str) -> str:
    """The attach-kind ``render_line`` (a negotiated-MSA reference line); TermsError for a library
    profile (which carries no render_line)."""
    profile = get_profile(profile_id)
    if profile.get("kind") != "attach":
        raise TermsError(
            f"subcontract terms profile {profile_id!r} is not attach-kind — it has no render_line"
        )
    line = profile.get("render_line")
    if not isinstance(line, str) or not line.strip():
        raise TermsError(f"attach profile {profile_id!r} has no render_line")
    return line


def required_tokens(profile_id: str, version: str | None = None) -> list[str]:
    """The substitution tokens a (profile, version) needs at render time."""
    _, entry = _version_entry(profile_id, version)
    tokens: list[str] = list(entry.get("tokens") or [])
    return tokens


def substitute_tokens(text: str, values: Mapping[str, str]) -> str:
    """Fill every ``{{token}}`` in `text` from `values` — STRICT on missing/blank tokens (a subcontract
    must never render with an unfilled contract blank). Extra keys ignored."""
    missing = {
        m.group(1)
        for m in _TOKEN_RE.finditer(text)
        if not str(values.get(m.group(1), "")).strip()
    }
    if missing:
        raise TermsError(
            f"subcontract terms text has unfilled token(s): {sorted(missing)} — refusing to render "
            "with a blank where contract language belongs"
        )
    return _TOKEN_RE.sub(lambda m: str(values[m.group(1)]), text)


def _load_config(name: str, required_keys: tuple[str, ...]) -> dict[str, Any]:
    path = CONFIG_DIR / name
    try:
        config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise TermsError(f"config missing: {path}") from e
    except json.JSONDecodeError as e:
        raise TermsError(f"config is not valid JSON: {path}: {e}") from e
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise TermsError(f"{name} missing required key(s): {missing}")
    return config


def load_contractor_config() -> dict[str, Any]:
    """The versioned Contractor identity — entity, address block, phone, signature entity, prime default."""
    return _load_config(
        "contractor.json",
        ("config_version", "entity", "address_lines", "phone", "signature_entity", "prime_contractor_default"),
    )


def load_payment_terms_config() -> dict[str, Any]:
    """The §2.5 retention defaults — integer basis points only."""
    config = _load_config(
        "payment_terms.json",
        ("config_version", "retainage_bp", "retainage_reduced_bp", "retainage_reduction_at_pct"),
    )
    for key in ("retainage_bp", "retainage_reduced_bp", "retainage_reduction_at_pct"):
        v = config[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise TermsError(f"payment_terms.json {key} must be a non-negative INTEGER (got {v!r})")
    return config
