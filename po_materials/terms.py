"""Terms-library + purchaser/tax config loader — pure functions, no side effects (S3).

The terms library (decision D6) is git-versioned prose: ``terms/manifest.json`` maps
profile ids → immutable, sha256-pinned version files. Drafts pin (profile id, version)
at generate time; because version files are immutable and hash-verified on every load,
a pinned draft renders identically forever — a wording change is a NEW ``_vN`` file,
never an edit. Profile ids are the same vocabulary as the ITS_Vendors "Default Terms
Profile" picklist (``shared/picklist_validation._VENDOR_TERMS_PROFILE_VALUES``); the
parity is test-pinned in ``tests/test_po_terms.py``.

Two profile kinds:
  * ``library`` — versioned text files this module loads and (optionally) token-fills.
  * ``attach``  — negotiated GTCs (VSUN-class): the renderer emits only the manifest's
    ``render_line``; ``load_terms_text`` refuses (there is no library text to load).

Substitution tokens (``{{token}}``) are STRICT both ways: rendering with a missing
token raises (a PO must never go out with an unfilled blank), and the manifest declares
each version's token list so callers know what to supply.

Consumers: the S4 render pipeline (Mac side) reads everything at render time; the
Worker imports the same JSON at build time (S2 wiring follow-up) — the S4 render-time
totals assert is what catches version skew between the two.

No network, no Smartsheet, no state writes — safe to import anywhere.
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
        raise TermsError(f"terms manifest missing: {path}") from e
    except json.JSONDecodeError as e:
        raise TermsError(f"terms manifest is not valid JSON: {path}: {e}") from e
    if manifest.get("manifest_version") != 1:
        raise TermsError(
            f"unsupported terms manifest_version {manifest.get('manifest_version')!r} "
            "(this loader speaks version 1)"
        )
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise TermsError("terms manifest has no profiles")
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
            f"unknown terms profile {profile_id!r} (known: {sorted(profiles)})"
        )
    return profiles[profile_id]


def _version_entry(profile_id: str, version: str | None) -> tuple[str, dict[str, Any]]:
    """Resolve (version, entry) for a library profile; default = current_version."""
    profile = get_profile(profile_id)
    if profile.get("kind") != "library":
        raise TermsError(
            f"terms profile {profile_id!r} is kind={profile.get('kind')!r} — it has no "
            "library text to load (attach-kind renders only its manifest render_line)"
        )
    versions = profile.get("versions") or {}
    resolved = version if version is not None else str(profile.get("current_version"))
    if resolved not in versions:
        raise TermsError(
            f"terms profile {profile_id!r} has no version {resolved!r} "
            f"(known: {sorted(versions)})"
        )
    return resolved, versions[resolved]


def load_terms_text(profile_id: str, version: str | None = None) -> str:
    """The verbatim terms text for (profile, version) — sha256-verified on EVERY load.

    Hash verification is the immutability contract's teeth: a drifted/edited version
    file raises here rather than silently rendering different words onto a PO whose
    draft pinned the original text. The hash covers the RAW file bytes; the returned
    text has the leading ``<!-- ... -->`` provenance header stripped — that header is
    maintainer documentation, and returning it would put it on a rendered PO.
    """
    resolved, entry = _version_entry(profile_id, version)
    path = TERMS_DIR / str(entry["file"])
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        raise TermsError(
            f"terms file missing for {profile_id!r} v{resolved}: {path}"
        ) from e
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise TermsError(
            f"terms file HASH MISMATCH for {profile_id!r} v{resolved} ({path.name}): "
            f"manifest pins {entry['sha256']} but file is {digest} — version files are "
            "immutable; a wording change must be a NEW _vN file + manifest entry"
        )
    return _strip_header_comment(raw.decode("utf-8"))


def _strip_header_comment(text: str) -> str:
    """Drop the leading ``<!-- ... -->`` provenance block from a terms file.

    Only a comment at the very top is stripped (nothing else is touched); a file
    without one passes through unchanged. A malformed unterminated comment raises —
    silently rendering half a comment onto a PO is worse than refusing."""
    stripped = text.lstrip()
    if not stripped.startswith("<!--"):
        return text
    end = stripped.find("-->")
    if end == -1:
        raise TermsError("terms file has an unterminated leading <!-- comment")
    return stripped[end + len("-->"):].lstrip("\n")


def required_tokens(profile_id: str, version: str | None = None) -> list[str]:
    """The substitution tokens a (profile, version) needs at render time."""
    _, entry = _version_entry(profile_id, version)
    tokens: list[str] = list(entry.get("tokens") or [])
    return tokens


def substitute_tokens(text: str, values: Mapping[str, str]) -> str:
    """Fill every ``{{token}}`` in `text` from `values` — STRICT on missing tokens.

    A PO must never render with an unfilled blank, so an unprovided token raises
    rather than passing through. Extra keys in `values` are ignored (callers may
    pass a superset). Blank/whitespace-only values are rejected for the same reason
    an absent one is.
    """
    missing = {
        m.group(1)
        for m in _TOKEN_RE.finditer(text)
        if not str(values.get(m.group(1), "")).strip()
    }
    if missing:
        raise TermsError(
            f"terms text has unfilled token(s): {sorted(missing)} — refusing to render "
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


def load_purchaser_config() -> dict[str, Any]:
    """The versioned Purchaser identity (D5) — entity, address block, phone, invoice routing."""
    config = _load_config(
        "purchaser.json", ("config_version", "entity", "address_lines", "phone", "invoice_routing")
    )
    routing = config["invoice_routing"]
    if not isinstance(routing, dict) or "to" not in routing or "cc" not in routing:
        raise TermsError("purchaser.json invoice_routing must carry 'to' + 'cc'")
    return config


def load_tax_config() -> dict[str, Any]:
    """The ship-to-state tax table (D8) — integer basis points only."""
    config = _load_config("tax.json", ("config_version", "rates_bp", "state_names"))
    rates = config["rates_bp"]
    if not isinstance(rates, dict) or not rates:
        raise TermsError("tax.json rates_bp must be a non-empty object")
    for state, bp in rates.items():
        if not isinstance(bp, int) or isinstance(bp, bool) or bp < 0:
            raise TermsError(
                f"tax.json rates_bp[{state!r}] must be a non-negative INTEGER of basis "
                f"points (got {bp!r}) — no floats in the money path"
            )
    return config
