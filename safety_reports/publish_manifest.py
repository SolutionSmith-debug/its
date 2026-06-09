"""Apply a Form-editor publish op to the catalog manifest — the deterministic core of
the Mac publish daemon (Phase-2 slice 3b).

`apply_publish` is PURE: it takes the current manifest dict + a claimed publish request
and returns the new manifest + the form files to write — it touches no disk and runs no
git/deploy. The daemon (a separate module) writes the result + commits/deploys. Keeping
the transform pure makes the authoritative re-validation (design C3 — the daemon
re-checks against the LIVE git HEAD, not just the Worker's enqueue-time check) and the
state machine fully unit-testable.

The Worker (`worker/publishValidation.ts`) already validated the definition's STRUCTURE
at enqueue; this re-checks the MANIFEST-level invariants against live HEAD (identity
uniqueness, monotonic version, the variant-mixing rule, a known rollback target) — the
authoritative gate, since HEAD may have moved since enqueue. A violation raises
`PublishApplyError`; the daemon stamps the request `failed` with the reason.

INVARIANT: the returned manifest must satisfy every rule `tests/test_form_catalog.py`
enforces (uniqueness, parent/variant grouping, current-pointer, append-only versions,
display order). The op logic preserves them; the test re-asserts them after each op.
"""
from __future__ import annotations

import copy
from typing import Any

_EM_DASH = "—"


class PublishApplyError(Exception):
    """The publish op cannot be applied to the manifest at live HEAD (invalid op,
    identity, version, or state). The daemon stamps the request failed with this
    message as `failure_reason`; the form is NOT published."""


def _find_parent(manifest: dict, parent_form_code: str) -> dict | None:
    for p in manifest["parents"]:
        if p["parent_form_code"] == parent_form_code:
            return p
    return None


def _find_form(manifest: dict, identity: str) -> tuple[dict | None, dict | None]:
    for p in manifest["parents"]:
        for f in p["forms"]:
            if f["identity"] == identity:
                return p, f
    return None, None


def _all_identities(manifest: dict) -> set[str]:
    return {f["identity"] for p in manifest["parents"] for f in p["forms"]}


def _parent_display_name(definition: dict) -> str:
    """Mirror registry.ts: a variant parent's label is the form_name before the em-dash;
    a no-variant parent's label is the full form_name."""
    name = str(definition.get("form_name", "")).strip()
    if definition.get("variant_label") is not None and _EM_DASH in name:
        return name.split(_EM_DASH)[0].strip()
    return name


def _next_parent_order(manifest: dict) -> int:
    return max((p["display_order"] for p in manifest["parents"]), default=0) + 1


def _next_form_order(parent: dict) -> int:
    return max((f["display_order"] for f in parent["forms"]), default=0) + 1


def apply_publish(
    manifest: dict,
    *,
    op: str,
    identity: str,
    parent_form_code: str,
    target_form_code: str | None = None,
    definition: dict | None = None,
) -> tuple[dict, dict[str, Any], str]:
    """Apply `op` to `manifest`. Returns (new_manifest, files_to_write, note).

    files_to_write maps form_code -> definition dict (the daemon writes each to
    safety_portal/forms/<form_code>.json). Empty for delete/rollback (no new file —
    every historical file is retained on disk; design C1 append-only). The input is
    never mutated (deep-copied)."""
    m = copy.deepcopy(manifest)
    files: dict[str, Any] = {}

    if op in ("create", "add_version"):
        if not isinstance(definition, dict):
            raise PublishApplyError(f"{op} requires a definition")
        version = definition.get("version")
        form_code = definition.get("form_code")
        if form_code != f"{identity}-v{version}":
            raise PublishApplyError(f"form_code {form_code!r} != {identity}-v{version}")
        if identity in _all_identities(m):
            raise PublishApplyError(f"identity {identity!r} already exists (use edit)")
        variant_label = definition.get("variant_label")
        new_form = {
            "identity": identity,
            "variant_label": variant_label,
            "status": "active",
            "current_version": version,
            "current_form_code": form_code,
            "versions": [{"version": version, "form_code": form_code}],
            "display_order": 1,
        }
        parent = _find_parent(m, parent_form_code)
        if parent is None:
            m["parents"].append({
                "parent_form_code": parent_form_code,
                "name": _parent_display_name(definition),
                "display_order": _next_parent_order(m),
                "forms": [new_form],
            })
        else:
            # Variant-mixing guard (registry's binary branch): a parent is EITHER one
            # null-variant form OR all-non-null. Adding must not create a mix.
            existing = [f["variant_label"] for f in parent["forms"]]
            if variant_label is None or any(lbl is None for lbl in existing):
                raise PublishApplyError(
                    f"adding to parent {parent_form_code!r} would mix a null-variant "
                    f"form with variant forms"
                )
            new_form["display_order"] = _next_form_order(parent)
            parent["forms"].append(new_form)
        files[form_code] = definition
        return m, files, f"{op}: added {form_code}"

    if op == "edit":
        if not isinstance(definition, dict):
            raise PublishApplyError("edit requires a definition")
        version = definition.get("version")
        form_code = definition.get("form_code")
        if form_code != f"{identity}-v{version}":
            raise PublishApplyError(f"form_code {form_code!r} != {identity}-v{version}")
        _, form = _find_form(m, identity)
        if form is None:
            raise PublishApplyError(f"identity {identity!r} not found (use create)")
        if not isinstance(version, int) or version <= form["current_version"]:
            raise PublishApplyError(
                f"edit must bump the version above current "
                f"({form['current_version']}); got {version!r}"
            )
        # version > current but already present (only reachable after a rollback left
        # current below an existing higher version) — can't re-add it.
        if any(v["version"] == version for v in form["versions"]):
            raise PublishApplyError(f"version {version} already exists for {identity!r}")
        if definition.get("variant_label") != form["variant_label"]:
            raise PublishApplyError("edit must not change variant_label (use add-version)")
        # Append the new version + swap the active pointer to it (the prior version's
        # file is retained — filed/in-flight submissions still resolve it).
        form["versions"].append({"version": version, "form_code": form_code})
        form["current_version"] = version
        form["current_form_code"] = form_code
        form["status"] = "active"
        files[form_code] = definition
        return m, files, f"edit: {identity} -> v{version}"

    if op == "delete":
        _, form = _find_form(m, identity)
        if form is None:
            raise PublishApplyError(f"identity {identity!r} not found")
        form["status"] = "retired"
        return m, files, f"delete: retired {identity}"

    if op == "rollback":
        if not target_form_code:
            raise PublishApplyError("rollback requires target_form_code")
        _, form = _find_form(m, identity)
        if form is None:
            raise PublishApplyError(f"identity {identity!r} not found")
        ver = next((v for v in form["versions"] if v["form_code"] == target_form_code), None)
        if ver is None:
            raise PublishApplyError(
                f"{target_form_code!r} is not a known version of {identity!r}"
            )
        form["current_version"] = ver["version"]
        form["current_form_code"] = target_form_code
        form["status"] = "active"
        return m, files, f"rollback: {identity} -> {target_form_code}"

    raise PublishApplyError(f"unknown op {op!r}")
