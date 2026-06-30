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


# ── Required-content legal floor (Brief 1 PR-1) ───────────────────────────────────────
# Structure alone (the meta-schema) never required a JHA to keep its "REVIEW AND REVISE THE
# PLAN" footer, an equipment form its lock/tag-out line, or a roster a signature section — so
# an operator edit could ship a legally-broken form. safety_portal/required-content.json is
# that missing requirement, enforced at BOTH C3 layers: the Worker enqueue gate
# (worker/publishValidation.ts validateRequiredContent) and apply_publish below (the daemon's
# authoritative re-check vs live HEAD). Reason strings start "required content missing:" so the
# editor's explainPublish surfaces them verbatim and docs/runbooks/safety_portal_forms.md keys
# on them. This mirrors validateRequiredContent exactly — the two layers MUST agree.


def _iter_field_objects(definition: dict) -> list[dict]:
    """Every field/column object across a definition's sections (header fields + table cols)."""
    out: list[dict] = []
    for s in definition.get("sections", []):
        if not isinstance(s, dict):
            continue
        for f in s.get("fields", []) or []:
            if isinstance(f, dict):
                out.append(f)
        for c in s.get("columns", []) or []:
            if isinstance(c, dict):
                out.append(c)
    return out


def _required_content_spec(required_content: dict, *, identity: str, parent_form_code: str) -> dict:
    """Effective spec: parents[parent] shallow-merged with identities[identity] (identity wins
    per key). If NEITHER exists, defaults_for_new_identities (the brand-new-form-type path)."""
    parent_spec = (required_content.get("parents") or {}).get(parent_form_code)
    identity_spec = (required_content.get("identities") or {}).get(identity)
    if parent_spec is None and identity_spec is None:
        return dict(required_content.get("defaults_for_new_identities") or {})
    spec: dict = {}
    if isinstance(parent_spec, dict):
        spec.update(parent_spec)
    if isinstance(identity_spec, dict):
        spec.update(identity_spec)
    return spec


def check_required_content(
    definition: dict,
    *,
    identity: str,
    parent_form_code: str,
    required_content: dict,
) -> None:
    """Raise PublishApplyError if `definition` violates its required-content entry. Mirrors
    worker/publishValidation.ts validateRequiredContent (the two C3 layers must agree)."""
    spec = _required_content_spec(
        required_content, identity=identity, parent_form_code=parent_form_code
    )
    if not spec:
        return
    sections = [s for s in definition.get("sections", []) if isinstance(s, dict)]
    section_types = {s.get("type") for s in sections}
    for req_type in spec.get("required_section_types", []):
        if req_type not in section_types:
            raise PublishApplyError(
                f"required content missing: {identity} must contain a {req_type!r} section"
            )
    min_sigs = spec.get("required_signature_inputs_min", 0)
    if isinstance(min_sigs, int) and min_sigs > 0:
        sig_count = sum(1 for f in _iter_field_objects(definition) if f.get("input") == "signature")
        if sig_count < min_sigs:
            raise PublishApplyError(
                f"required content missing: {identity} needs at least {min_sigs} signature input(s)"
            )
    legal_texts = [
        str(s.get("text", ""))
        for s in sections
        if s.get("type") == "static_text" and s.get("emphasis") in ("legal", "footer")
    ]
    for required in spec.get("required_static_text", []):
        if not any(required in t for t in legal_texts):
            raise PublishApplyError(
                f'required content missing: the mandatory legal/footer line "{required}" '
                f"is absent from {identity}"
            )
    req_keys = spec.get("required_field_keys", [])
    if req_keys:
        keys = {str(f["key"]) for f in _iter_field_objects(definition) if "key" in f}
        for s in sections:
            if "key" in s:
                keys.add(str(s["key"]))
            for g in s.get("groups", []) or []:
                if isinstance(g, dict):
                    if "key" in g:
                        keys.add(str(g["key"]))
                    for it in g.get("items", []) or []:
                        if isinstance(it, dict) and "key" in it:
                            keys.add(str(it["key"]))
        for req_key in req_keys:
            if req_key not in keys:
                raise PublishApplyError(
                    f"required content missing: core field {req_key!r} absent from {identity}"
                )


def apply_publish(
    manifest: dict,
    *,
    op: str,
    identity: str,
    parent_form_code: str,
    target_form_code: str | None = None,
    definition: dict | None = None,
    required_content: dict | None = None,
    category: str | None = None,
    valid_categories: frozenset[str] | None = None,
) -> tuple[dict, dict[str, Any], str]:
    """Apply `op` to `manifest`. Returns (new_manifest, files_to_write, note).

    files_to_write maps form_code -> definition dict (the daemon writes each to
    safety_portal/forms/<form_code>.json). Empty for delete/rollback (no new file —
    every historical file is retained on disk; design C1 append-only). The input is
    never mutated (deep-copied)."""
    m = copy.deepcopy(manifest)
    files: dict[str, Any] = {}

    # Legal-floor re-check (Brief 1 PR-1): a create/edit/add_version definition must satisfy
    # its required-content entry. Passed in to keep apply_publish pure; None skips (back-compat
    # for callers/tests that don't exercise the legal floor). The daemon always passes it.
    if required_content is not None and op in ("create", "add_version", "edit") and isinstance(definition, dict):
        check_required_content(
            definition, identity=identity, parent_form_code=parent_form_code,
            required_content=required_content,
        )

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
            # A brand-new parent's first form is its ONLY variant, so it must be the
            # no-variant kind (variant_label null). registry.ts branches binary on
            # variant_label; a lone form WITH a label renders a degenerate one-option
            # 3rd picklist (the invariant tests/test_form_catalog.py
            # test_single_form_parent_is_null_variant guards). Enforce it HERE — the
            # authoritative manifest-level re-check — so a junk publish (e.g. a create-
            # flow test with variant_label "test") is stamped `failed` at the daemon
            # rather than committed to a branch and only caught (reddening CI) downstream.
            if variant_label is not None:
                raise PublishApplyError(
                    f"a brand-new form type must have variant_label null (it is its own "
                    f"only variant); got {variant_label!r} — add variants later via a "
                    f"create under the existing parent"
                )
            # The new parent's workflow category (form-builder workflow selector). The
            # registry SET is passed in (apply_publish stays pure — no disk read); a provided
            # category is re-validated against it (authoritative C3 re-check behind the
            # Worker's validateCategory). A legacy/category-less create defaults to safety.
            if (
                category is not None
                and valid_categories is not None
                and category not in valid_categories
            ):
                raise PublishApplyError(f"unknown workflow category {category!r}")
            m["parents"].append({
                "parent_form_code": parent_form_code,
                "name": _parent_display_name(definition),
                "display_order": _next_parent_order(m),
                "category": category or "safety",
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
        if form["status"] == "retired":
            # Already retired → a no-op mutation. Reject here (the validate stage) with a
            # clear reason, rather than letting an empty catalog diff reach the daemon's
            # `git commit`, which exits 1 with a confusing "nothing to commit" message.
            raise PublishApplyError(f"{identity!r} is already retired")
        form["status"] = "retired"
        return m, files, f"delete: retired {identity}"

    if op == "recategorize":
        # Parent-wide workflow change (the form-builder "Change workflow" control): flips the
        # catalog PARENT's `category`, moving EVERY form/variant under it between workflows at
        # once. No definition, no file — a manifest-only flip, like delete/rollback. The
        # Worker's validateCategory is the first gate; this is the authoritative re-check.
        if not category:
            raise PublishApplyError("recategorize requires a category")
        if valid_categories is not None and category not in valid_categories:
            raise PublishApplyError(f"unknown workflow category {category!r}")
        parent = _find_parent(m, parent_form_code)
        if parent is None:
            raise PublishApplyError(f"parent {parent_form_code!r} not found")
        if parent.get("category", "safety") == category:
            # Already in this workflow → an empty manifest diff. Reject at this validate stage
            # so a no-op never reaches the daemon's `git commit` (mirrors delete's guard).
            raise PublishApplyError(f"{parent_form_code!r} is already in workflow {category!r}")
        parent["category"] = category
        return m, files, f"recategorize: {parent_form_code} -> {category}"

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
