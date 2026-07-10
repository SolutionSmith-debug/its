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

Four (artifact, op) domain transforms — kept in LOCKSTEP with the Worker's ``worker/config.ts``
validation AND the ``config_requests.op`` CHECK migration (the three fan-out surfaces must agree; a
new op lands in all three):
  * ``tax`` / ``edit``       — integer-basis-point tax table (NO floats in the money path).
  * ``purchaser`` / ``edit`` — the Purchaser identity + invoice routing (email-ish routing).
  * ``terms`` / ``add_version`` — append a NEW immutable, sha256-pinned terms version file
    with ``legal_review: "pending"``; NEVER mutates an existing version, NEVER bumps
    ``current_version`` (the un-changed pointer + the pending flag are Layer B of the legal
    gate; the render-side Layer-A loader refusal is now ENFORCED in ``terms._version_entry``).
  * ``terms`` / ``set_current`` — the legal-activation op: set an existing version's
    ``legal_review: "cleared"`` AND repoint ``current_version`` to it (the operator's confirmable
    make-current action); mutates ONLY those two fields, never the immutable file/sha256/tokens.

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
_MAX_LABEL = 200  # mirrors the Worker's MAX_LABEL (config.ts create_profile)
_MAX_RENDER_LINE = 2_000  # mirrors the Worker's MAX_RENDER_LINE (config.ts create_profile)

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


def _apply_terms_set_current(
    payload: dict[str, Any], target_version: str | None, root: Path
) -> str:
    """Make an existing terms version the profile's CURRENT version and CLEAR its legal review — the
    operator's confirmable "I've reviewed this — make it live" action (the legal-activation step).

    Sets ``legal_review: "cleared"`` on the target version AND repoints ``current_version`` to it in a
    SINGLE manifest rewrite (both fields together, never a partial two-step). This is the only writer
    that advances ``current_version`` and the
    only permitted mutation of an existing version's ``legal_review`` — the immutable fields
    (``file``/``sha256``/``tokens``) are NEVER touched, so the render-time hash contract still holds.
    The render-side Layer-A gate (``terms._version_entry``) then lets the now-cleared version render;
    an un-cleared version stays fenced. Blank-pinned drafts resolve the new current version; drafts
    that pinned the OLD explicit version keep rendering the old (immutable) text."""
    if not isinstance(target_version, str) or not target_version:
        raise ConfigApplyError("terms set_current: target_version is required")
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ConfigApplyError("terms set_current: profile_id is required")
    manifest_path = _terms_dir(root) / "manifest.json"
    manifest = _load_json_file(manifest_path, "terms/manifest.json")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict) or profile_id not in profiles:
        raise ConfigApplyError(
            f"terms set_current: unknown profile {profile_id!r} "
            f"(known: {sorted(profiles) if isinstance(profiles, dict) else '?'})"
        )
    profile = profiles[profile_id]
    if not isinstance(profile, dict) or profile.get("kind") != "library":
        raise ConfigApplyError(
            f"terms set_current: profile {profile_id!r} is not a library profile "
            "(only library profiles have versioned text to make current)"
        )
    versions = profile.get("versions")
    if not isinstance(versions, dict) or target_version not in versions:
        raise ConfigApplyError(
            f"terms set_current: version {target_version!r} does not exist for {profile_id!r} "
            f"(known: {sorted(versions) if isinstance(versions, dict) else '?'}) — mint it first"
        )
    entry = versions[target_version]
    if not isinstance(entry, dict) or "file" not in entry or "sha256" not in entry:
        raise ConfigApplyError(
            f"terms set_current: version {target_version!r} of {profile_id!r} is malformed "
            "(missing file/sha256) — refusing to make a corrupt version current"
        )
    # The confirmable make-current action IS the legal clearance. Repoint current_version + clear the
    # target's legal_review; leave every other version (and all immutable fields) untouched.
    entry["legal_review"] = "cleared"
    profile["current_version"] = target_version
    manifest_path.write_text(_dump(manifest), encoding="utf-8")
    return f"terms: {profile_id} current_version -> {target_version} (legal_review cleared)"


def _apply_terms_create_profile(payload: dict[str, Any], root: Path) -> str:
    """Mint a BRAND-NEW terms profile in the manifest — a ``library`` profile (a manifest entry + an
    immutable sha256-pinned initial version file, ``legal_review: "pending"``) or an ``attach`` profile
    (a manifest entry with only a ``render_line``). The NEW profile id is validated against live HEAD
    (C3 — the manifest may have moved since the Worker's bundled-manifest check); a duplicate id raises
    (that is an ``add_version``, not a create). The initial library version lands ``pending`` and
    ``current_version`` points at it, so the render-side Layer-A gate (``terms._version_entry``) FENCES
    it — the profile is selectable but cannot render on a PO until a later ``set_current`` clears its
    legal review. Never bypasses the legal gate; never mutates an existing profile.

    The new manifest entry auto-joins the ITS_Vendors "Default Terms Profile" picklist because
    ``shared/picklist_validation._VENDOR_TERMS_PROFILE_VALUES`` is DERIVED from this manifest — so no
    separate picklist edit / shared-module commit is needed (the actuator commits only po_materials/)."""
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not _TARGET_VERSION_RE.match(profile_id):
        raise ConfigApplyError(
            "terms create_profile: profile_id is required and must match /^[a-z0-9_]+$/"
        )
    if len(profile_id) > _MAX_TARGET_VERSION:
        raise ConfigApplyError(
            f"terms create_profile: profile_id {profile_id!r} is > {_MAX_TARGET_VERSION} chars"
        )
    kind = payload.get("kind")
    if kind not in ("library", "attach"):
        raise ConfigApplyError(
            f"terms create_profile: kind must be 'library' or 'attach' (got {kind!r})"
        )
    label = payload.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > _MAX_LABEL:
        raise ConfigApplyError("terms create_profile: label is required (non-empty, bounded)")
    description = payload.get("description")
    if description is not None and (not isinstance(description, str) or len(description) > 1000):
        raise ConfigApplyError("terms create_profile: description must be a string <= 1000 chars")

    manifest_path = _terms_dir(root) / "manifest.json"
    manifest = _load_json_file(manifest_path, "terms/manifest.json")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict):
        raise ConfigApplyError("terms create_profile: manifest has no profiles map")
    if profile_id in profiles:
        raise ConfigApplyError(
            f"terms create_profile: profile {profile_id!r} already exists — use add_version to add a "
            "version, not create_profile"
        )
    reserved = manifest.get("reserved_profile_ids") or {}
    if isinstance(reserved, dict) and profile_id in reserved:
        raise ConfigApplyError(
            f"terms create_profile: {profile_id!r} is a RESERVED profile id (deferred transcription) — "
            "reserved ids are not created via the generic form; escalate to the operator"
        )

    entry: dict[str, Any] = {"kind": kind, "label": label.strip()}
    if isinstance(description, str) and description.strip():
        entry["description"] = description.strip()

    if kind == "library":
        version_id = payload.get("version_id")
        text = payload.get("text")
        if not isinstance(version_id, str) or not _TARGET_VERSION_RE.match(version_id) or len(version_id) > _MAX_TARGET_VERSION:
            raise ConfigApplyError(
                "terms create_profile(library): version_id is required and must match /^[a-z0-9_]+$/"
            )
        if not isinstance(text, str) or not text.strip():
            raise ConfigApplyError("terms create_profile(library): text must be non-empty")
        if len(text) > _MAX_TERMS_TEXT:
            raise ConfigApplyError(
                f"terms create_profile(library): text is {len(text)} chars (> {_MAX_TERMS_TEXT})"
            )
        # Namespace the file by profile id so two profiles' version ids can't collide on disk.
        file_name = f"{profile_id}_{version_id}.md"
        file_path = _terms_dir(root) / file_name
        if file_path.exists():
            raise ConfigApplyError(
                f"terms create_profile: {file_name} already exists on disk — refusing to overwrite"
            )
        tokens = sorted({m.group(1) for m in _TOKEN_RE.finditer(text)})
        file_bytes = text.encode("utf-8")
        digest = hashlib.sha256(file_bytes).hexdigest()
        file_path.write_bytes(file_bytes)
        entry["current_version"] = version_id
        entry["versions"] = {
            version_id: {
                "file": file_name,
                "sha256": digest,
                "tokens": tokens,
                "legal_review": "pending",
            }
        }
        note = (
            f"terms: NEW library profile {profile_id!r} + version {version_id} "
            f"(legal_review pending — fenced until set_current)"
        )
    else:  # attach
        render_line = payload.get("render_line")
        if not isinstance(render_line, str) or not render_line.strip() or len(render_line) > _MAX_RENDER_LINE:
            raise ConfigApplyError(
                "terms create_profile(attach): render_line is required (non-empty, bounded)"
            )
        entry["render_line"] = render_line.strip()
        note = f"terms: NEW attach profile {profile_id!r} (render_line only)"

    profiles[profile_id] = entry
    manifest_path.write_text(_dump(manifest), encoding="utf-8")
    return note


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
        if op == "add_version":
            return _apply_terms_add_version(payload, request.get("target_version"), root)
        if op == "set_current":
            return _apply_terms_set_current(payload, request.get("target_version"), root)
        if op == "create_profile":
            return _apply_terms_create_profile(payload, root)
        raise ConfigApplyError(
            f"terms takes op 'add_version', 'set_current' or 'create_profile', got {op!r}"
        )
    raise ConfigApplyError(f"unknown config artifact {artifact!r}")
