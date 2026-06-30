"""Resolve a portal form_code to its workstream category (safety | progress).

Purpose
-------
The Python counterpart of the Worker/SPA resolver in `safety_portal/src/forms/registry.ts`
(`parent.category ?? "safety"`). Maps every historical `form_code` (every version under every
catalog parent) to its parent's workstream `category`, so intake routing (P3) can pick the
safety vs progress workspace for a submission. The category lives ONLY at the catalog PARENT
level (field-ops P1b #304); per-form definition files (`safety_portal/forms/<code>.json`) do
NOT carry it. The reader contract (`safety_portal/catalog.schema.json`) is **absent `category`
defaults to "safety"**.

Invariants
----------
- Reads `safety_portal/catalog.json` FRESH on every call (no cache) so a newly-published
  progress form routes correctly WITHOUT restarting the long-running intake daemon; the
  catalog is ~10 KB and intake is low-frequency, so the per-call read is negligible.
- `resolve_category` returns a registered workflow id — today "safety" | "progress", but the
  valid SET is registry-driven (workflows.json) — defaulting unknown/absent to the conservative
  "safety" floor, and NEVER raises: a routing key must never be able to break intake.
- Deny-by-route: only a positively-catalogued progress form ever routes to progress.

Failure modes
-------------
ANY read/parse failure — missing file, malformed JSON, a mid-publish partial read, a non-dict
root, an unexpected schema, a non-string/invalid `category` — resolves to "safety", i.e.
today's behavior (everything routes safety). A catalog problem therefore manifests as
"everything routes safety", NEVER as an intake crash or a silently-misrouted safety submission.

Consumers
---------
- `safety_reports.intake.py::_run_portal_pipeline` (P3) — calls `resolve_category` to route the
  Smartsheet week-sheet by the resolved category, gated by `progress_reports.intake_enabled`.
- `safety_reports.publish_manifest.apply_publish` — calls `is_valid_category` to reject a
  publish / recategorize to an unregistered workflow (the authoritative re-check behind the
  Worker's `publishValidation.ts` validateCategory).
"""
from __future__ import annotations

import json
from pathlib import Path

# A workflow category id (e.g. "safety", "progress"). A plain str, NOT a Literal: the valid SET
# is single-sourced in safety_portal/workflows.json (config-driven, so a future workflow is data,
# not a code change across the stack) and therefore can't be a compile-time Literal.
Category = str

# The conservative ROUTING fallback for an unknown/absent category — DELIBERATELY the hardcoded
# `safety` floor, NOT workflows.json's `default` field. The registry `default` is a UI concern
# (which workflow a NEW form is pre-selected into — mirrored by registry.ts DEFAULT_WORKFLOW); this
# is a security concern: an unrecognized form_code must route to the most-audited workspace, never
# wherever the UI default happens to point. The two are decoupled by intent — do not couple them.
DEFAULT_CATEGORY: Category = "safety"
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "safety_portal" / "catalog.json"
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "safety_portal" / "workflows.json"
# Fail-safe floor: even if workflows.json is missing/corrupt, these always count as valid so a
# progress form keeps resolving. The registry can only ADD to this known set, never break it.
_FALLBACK_IDS: frozenset[str] = frozenset({"safety", "progress"})


def _load_workflow_ids() -> frozenset[str]:
    """The valid workflow id set = safety_portal/workflows.json ids ∪ the fail-safe floor. Read
    fresh; ANY read/parse problem → just the floor (never raises)."""
    try:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        ids = {
            w["id"]
            for w in raw.get("workflows", [])
            if isinstance(w, dict) and isinstance(w.get("id"), str) and w["id"]
        }
        return (frozenset(ids) | _FALLBACK_IDS) if ids else _FALLBACK_IDS
    except (OSError, ValueError, KeyError, TypeError):
        return _FALLBACK_IDS


# Import-time valid set for resolve_category's deny-by-route normalize. The floor always covers
# the known workflows; a brand-new workflow needs a Python routing binding + a daemon restart
# anyway, so import-time (vs per-call) staleness here is harmless.
_VALID: frozenset[str] = _load_workflow_ids()


def workflow_ids() -> frozenset[str]:
    """The current set of registered workflow ids (fresh from workflows.json ∪ the floor).
    Passed INTO `safety_reports.publish_manifest.apply_publish` (which is pure and must not
    read disk) so the transform can re-validate a category against the registry."""
    return _load_workflow_ids()


def is_valid_category(value: object) -> bool:
    """Whether `value` is a registered workflow id — read FRESH from workflows.json so the
    validation always reflects the current registry. Mirrors
    safety_portal/worker/publishValidation.ts validateCategory."""
    return isinstance(value, str) and value in _load_workflow_ids()


def _normalize(value: object) -> Category:
    """A catalog category value → a valid Category, defaulting unknown/absent to safety."""
    return value if isinstance(value, str) and value in _VALID else DEFAULT_CATEGORY


def _form_code_to_category() -> dict[str, Category]:
    """Build form_code → category from the live catalog. Read fresh; fail to {} (→ safety)."""
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers json.JSONDecodeError
        return {}
    # The catalog is {"manifest_version": N, "parents": [...]}; tolerate a bare list too.
    parents = raw.get("parents") if isinstance(raw, dict) else raw
    if not isinstance(parents, list):
        return {}
    out: dict[str, Category] = {}
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        category = _normalize(parent.get("category"))
        # Index every form_code we might ever see: the parent code, each form's current
        # code, and every historical version code (a submission can carry an older version).
        codes: list[object] = [parent.get("parent_form_code")]
        for form in parent.get("forms", []) or []:
            if not isinstance(form, dict):
                continue
            codes.append(form.get("current_form_code"))
            codes.append(form.get("identity"))
            for ver in form.get("versions", []) or []:
                if isinstance(ver, dict):
                    codes.append(ver.get("form_code"))
        for code in codes:
            if isinstance(code, str) and code:
                out[code] = category
    return out


def resolve_category(form_code: str) -> Category:
    """Return 'safety' | 'progress' for a portal form_code.

    Unknown / uncatalogued / blank form_code → 'safety' (the deny-by-route default — only a
    positively-catalogued progress form routes to the progress workspace). Never raises.
    """
    code = (form_code or "").strip()
    if not code:
        return DEFAULT_CATEGORY
    return _form_code_to_category().get(code, DEFAULT_CATEGORY)
