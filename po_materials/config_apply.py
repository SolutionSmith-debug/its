"""Apply a config-editor request to the versioned PO config artifacts — the deterministic
domain transform of the Mac config actuator (§50 config editor, slice 2).

The analogue of ``safety_reports/publish_manifest.apply_publish`` for the PO config editor.
The cloud Worker (``worker/config.ts``, slice 1) VALIDATES + ENQUEUES a ``config_requests``
row send-free; this module is the daemon's authoritative RE-VALIDATION-and-WRITE against the
LIVE git HEAD (design C3 — HEAD may have moved since the Worker's enqueue-time check). It
takes a claimed request + the repo ``root`` and writes the new artifact file(s) into the
worktree, returning a human note for the commit message. Any validation failure raises
``ConfigApplyError``; the actuator stamps the request ``failed('validated')`` and the config
is NOT published.

Unlike ``apply_publish`` (pure), ``apply_config`` WRITES its result (a config edit is a
whole-file rewrite / a new terms file + a manifest entry, so bundling read-current + write
into one call against ``root`` keeps the transform + its file I/O in one auditable place).
The actuator's per-cycle ``_reset_to_main`` discards ``po_materials/config`` +
``po_materials/terms`` before each actuation, so an interrupted write never leaks.

Three (artifact, op) domain transforms — kept in LOCKSTEP with the Worker's
``worker/config.ts`` validation (the two C3 layers must agree; a new rule must land in BOTH):
  * ``tax`` / ``edit``       — integer-basis-point tax table (NO floats in the money path).
  * ``purchaser`` / ``edit`` — the Purchaser identity + invoice routing (email-ish routing).
  * ``terms`` / ``add_version`` — append a NEW immutable, sha256-pinned terms version file
    with ``legal_review: "pending"``; NEVER mutates an existing version, NEVER bumps
    ``current_version`` (the un-changed pointer + the pending flag are the legal gate —
    Layer B of terms-versioning; the render-side Layer-A loader gate is a documented
    follow-up, deferred because both live versions are still ``pending`` and turning it on
    now would fence every live PO — see the runbook).

No network, no Smartsheet, no git/deploy — pure filesystem + validation. Safe to import
anywhere; the actuator does the git/CI/deploy around it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

# The Worker's TARGET_VERSION_RE (config.ts:70) + EMAIL_RE (po.ts:104) + STATE_RE (po.ts:105),
# re-checked here so the daemon's authoritative live-HEAD re-validation matches the enqueue gate.
_TARGET_VERSION_RE = re.compile(r"^[a-z0-9_]+$")
_MAX_TARGET_VERSION = 64
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_EMAIL = 254
_STATE_RE = re.compile(r"^[A-Z]{2}$")
_MAX_BP = 10_000  # 100.00% — the Worker's override ceiling (po.ts:397)
_MAX_TERMS_TEXT = 100_000  # mirrors the Worker's MAX_PAYLOAD_BYTES (config.ts)
_MAX_TEXT_FIELD = 2_000  # bound on a single scalar config string (entity / phone / address line)

# The terms substitution-token pattern — IDENTICAL to terms._TOKEN_RE so the tokens this module
# declares in the manifest are exactly the ones terms.substitute_tokens will demand at render.
_TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")


class ConfigApplyError(Exception):
    """The config request cannot be applied to the artifact at live HEAD (invalid payload,
    version, or state). The actuator stamps the request failed('validated') with this message
    as ``failure_reason``; the config is NOT published."""


def _config_dir(root: Path) -> Path:
    return root / "po_materials" / "config"


def _terms_dir(root: Path) -> Path:
    return root / "po_materials" / "terms"


def _dump(obj: Any) -> str:
    """Canonical on-disk JSON: indent=2 + a trailing newline (matches the seeded files)."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _load_json_file(path: Path, what: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigApplyError(f"{what} missing at live HEAD: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigApplyError(f"{what} is not valid JSON: {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigApplyError(f"{what} must be a JSON object: {path}")
    return data


def _parse_payload(request: dict[str, Any]) -> dict[str, Any]:
    """The request's ``payload`` is a JSON STRING (the Worker stores JSON.stringify(payload));
    parse it to a dict. Any non-object / bad-JSON payload is a validation failure."""
    raw = request.get("payload")
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigApplyError("request payload is missing or empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigApplyError(f"request payload is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ConfigApplyError("request payload must be a JSON object")
    return payload


def _next_config_version(current: dict[str, Any]) -> int:
    cur = current.get("config_version")
    if not isinstance(cur, int) or isinstance(cur, bool):
        raise ConfigApplyError(
            f"live config_version must be an integer (got {cur!r}) — refusing to bump"
        )
    return cur + 1


# ── tax / edit ────────────────────────────────────────────────────────────────────────


def _apply_tax_edit(payload: dict[str, Any], root: Path) -> str:
    rates = payload.get("rates_bp")
    names = payload.get("state_names")
    if not isinstance(rates, dict) or not rates:
        raise ConfigApplyError("tax edit: rates_bp must be a non-empty object")
    if not isinstance(names, dict) or not names:
        raise ConfigApplyError("tax edit: state_names must be a non-empty object")
    for state, bp in rates.items():
        if not isinstance(state, str) or not _STATE_RE.match(state):
            raise ConfigApplyError(
                f"tax edit: rates_bp key {state!r} is not a 2-letter USPS state code"
            )
        # INTEGER-ONLY money path: reject bool AND float (a 9.0 is NOT an int here).
        if isinstance(bp, bool) or not isinstance(bp, int):
            raise ConfigApplyError(
                f"tax edit: rates_bp[{state!r}] must be an INTEGER basis point (got {bp!r}) "
                "— no floats in the money path"
            )
        if bp < 0 or bp > _MAX_BP:
            raise ConfigApplyError(
                f"tax edit: rates_bp[{state!r}]={bp} out of range 0..{_MAX_BP} basis points"
            )
    for state, name in names.items():
        if not isinstance(state, str) or not _STATE_RE.match(state):
            raise ConfigApplyError(
                f"tax edit: state_names key {state!r} is not a 2-letter USPS state code"
            )
        if not isinstance(name, str) or not name.strip():
            raise ConfigApplyError(f"tax edit: state_names[{state!r}] must be a non-empty string")
    if set(names) != set(rates):
        raise ConfigApplyError(
            f"tax edit: state_names keys {sorted(names)} must match rates_bp keys "
            f"{sorted(rates)} exactly (every rate needs a display name and vice versa)"
        )
    path = _config_dir(root) / "tax.json"
    current = _load_json_file(path, "tax.json")
    new = dict(current)
    new["config_version"] = _next_config_version(current)
    new["rates_bp"] = {s: rates[s] for s in rates}
    new["state_names"] = {s: names[s] for s in names}
    path.write_text(_dump(new), encoding="utf-8")
    return f"tax: {len(rates)} state rate(s) -> config_version {new['config_version']}"


# ── purchaser / edit ────────────────────────────────────────────────────────────────────


def _require_email(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigApplyError(f"purchaser edit: {label} must be a non-empty string")
    if len(value) > _MAX_EMAIL or not _EMAIL_RE.match(value):
        raise ConfigApplyError(f"purchaser edit: {label} {value!r} is not a valid email address")


def _apply_purchaser_edit(payload: dict[str, Any], root: Path) -> str:
    entity = payload.get("entity")
    if not isinstance(entity, str) or not entity.strip() or len(entity) > _MAX_TEXT_FIELD:
        raise ConfigApplyError("purchaser edit: entity must be a non-empty string")
    address_lines = payload.get("address_lines")
    if not isinstance(address_lines, list) or not address_lines:
        raise ConfigApplyError("purchaser edit: address_lines must be a non-empty list")
    for line in address_lines:
        if not isinstance(line, str) or not line.strip() or len(line) > _MAX_TEXT_FIELD:
            raise ConfigApplyError("purchaser edit: every address line must be a non-empty string")
    phone = payload.get("phone")
    if not isinstance(phone, str) or not phone.strip() or len(phone) > _MAX_TEXT_FIELD:
        raise ConfigApplyError("purchaser edit: phone must be a non-empty string")
    routing = payload.get("invoice_routing")
    if not isinstance(routing, dict) or "to" not in routing or "cc" not in routing:
        raise ConfigApplyError("purchaser edit: invoice_routing must carry 'to' + 'cc'")
    _require_email(routing.get("to"), "invoice_routing.to")
    cc = routing.get("cc")
    if not isinstance(cc, list):
        raise ConfigApplyError("purchaser edit: invoice_routing.cc must be a list")
    for addr in cc:
        _require_email(addr, "an invoice_routing.cc address")
    path = _config_dir(root) / "purchaser.json"
    current = _load_json_file(path, "purchaser.json")
    new = dict(current)
    new["config_version"] = _next_config_version(current)
    new["entity"] = entity
    new["address_lines"] = list(address_lines)
    new["phone"] = phone
    new["invoice_routing"] = {"to": routing["to"], "cc": list(cc)}
    path.write_text(_dump(new), encoding="utf-8")
    return f"purchaser: {entity} -> config_version {new['config_version']}"


# ── terms / add_version ─────────────────────────────────────────────────────────────────


def _apply_terms_add_version(
    payload: dict[str, Any], target_version: str | None, root: Path
) -> str:
    if not isinstance(target_version, str) or not target_version:
        raise ConfigApplyError("terms add_version: target_version is required")
    if not _TARGET_VERSION_RE.match(target_version) or len(target_version) > _MAX_TARGET_VERSION:
        raise ConfigApplyError(
            f"terms add_version: target_version {target_version!r} must match "
            f"/^[a-z0-9_]+$/ and be <= {_MAX_TARGET_VERSION} chars"
        )
    profile_id = payload.get("profile_id")
    text = payload.get("text")
    if not isinstance(profile_id, str) or not profile_id:
        raise ConfigApplyError("terms add_version: profile_id is required")
    if not isinstance(text, str) or not text.strip():
        raise ConfigApplyError("terms add_version: text must be non-empty")
    if len(text) > _MAX_TERMS_TEXT:
        raise ConfigApplyError(
            f"terms add_version: text is {len(text)} chars (> {_MAX_TERMS_TEXT} limit)"
        )
    manifest_path = _terms_dir(root) / "manifest.json"
    manifest = _load_json_file(manifest_path, "terms/manifest.json")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict) or profile_id not in profiles:
        raise ConfigApplyError(
            f"terms add_version: unknown profile {profile_id!r} "
            f"(known: {sorted(profiles) if isinstance(profiles, dict) else '?'})"
        )
    profile = profiles[profile_id]
    if not isinstance(profile, dict) or profile.get("kind") != "library":
        raise ConfigApplyError(
            f"terms add_version: profile {profile_id!r} is kind="
            f"{profile.get('kind') if isinstance(profile, dict) else '?'!r} — only a "
            "library profile has versioned text (attach profiles render only a render_line)"
        )
    versions = profile.get("versions")
    if not isinstance(versions, dict):
        raise ConfigApplyError(f"terms add_version: profile {profile_id!r} has no versions map")
    if target_version in versions:
        raise ConfigApplyError(
            f"terms add_version: version {target_version!r} already exists for {profile_id!r} "
            "— version files are immutable; pick a new version id"
        )
    file_name = f"{target_version}.md"
    file_path = _terms_dir(root) / file_name
    if file_path.exists():
        raise ConfigApplyError(
            f"terms add_version: {file_name} already exists on disk — refusing to overwrite "
            "an existing (possibly sha-pinned) terms file"
        )
    # Extract the {{tokens}} the new text actually uses so the manifest declares exactly what
    # terms.substitute_tokens will demand at render (STRICT both ways).
    tokens = sorted({m.group(1) for m in _TOKEN_RE.finditer(text)})
    file_bytes = text.encode("utf-8")
    digest = hashlib.sha256(file_bytes).hexdigest()
    # Write the NEW immutable version file (never touch an existing one).
    file_path.write_bytes(file_bytes)
    # Append the versions entry with legal_review PENDING; leave current_version UNTOUCHED
    # (Layer B — the new version is inert until the operator clears legal review + bumps
    # current_version, out of scope here).
    versions[target_version] = {
        "file": file_name,
        "sha256": digest,
        "tokens": tokens,
        "legal_review": "pending",
    }
    manifest_path.write_text(_dump(manifest), encoding="utf-8")
    return (
        f"terms: {profile_id} + version {target_version} "
        f"(legal_review pending; current_version unchanged)"
    )


def apply_config(request: dict[str, Any], root: Path) -> str:
    """Validate + WRITE the config request's artifact against live HEAD under ``root``.

    Returns a human note for the commit message. Raises ``ConfigApplyError`` on any
    validation failure (the actuator stamps failed('validated')). Dispatches on
    (artifact_key, op); the (artifact, op) pairing itself is re-checked (the Worker's
    ``config.ts`` is the first gate, this is the authoritative live-HEAD re-check)."""
    artifact = request.get("artifact_key")
    op = request.get("op")
    payload = _parse_payload(request)

    if artifact == "tax":
        if op != "edit":
            raise ConfigApplyError(f"tax takes op 'edit', got {op!r}")
        return _apply_tax_edit(payload, root)
    if artifact == "purchaser":
        if op != "edit":
            raise ConfigApplyError(f"purchaser takes op 'edit', got {op!r}")
        return _apply_purchaser_edit(payload, root)
    if artifact == "terms":
        if op != "add_version":
            raise ConfigApplyError(f"terms takes op 'add_version', got {op!r}")
        return _apply_terms_add_version(payload, request.get("target_version"), root)
    raise ConfigApplyError(f"unknown config artifact {artifact!r}")
