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
- `resolve_category` returns ONLY "safety" | "progress" and NEVER raises — a routing key
  must never be able to break intake.
- Deny-by-route: only a positively-catalogued progress form ever routes to progress.

Failure modes
-------------
ANY read/parse failure — missing file, malformed JSON, a mid-publish partial read, a non-dict
root, an unexpected schema, a non-string/invalid `category` — resolves to "safety", i.e.
today's behavior (everything routes safety). A catalog problem therefore manifests as
"everything routes safety", NEVER as an intake crash or a silently-misrouted safety submission.

Consumers
---------
- `safety_reports.intake.py::_run_portal_pipeline` (P3) — the only caller; routes the
  Smartsheet week-sheet by the resolved category, gated by `progress_reports.intake_enabled`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

Category = Literal["safety", "progress"]

DEFAULT_CATEGORY: Category = "safety"
_VALID: frozenset[str] = frozenset({"safety", "progress"})
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "safety_portal" / "catalog.json"


def _normalize(value: object) -> Category:
    """A catalog category value → a valid Category, defaulting unknown/absent to safety."""
    return value if isinstance(value, str) and value in _VALID else DEFAULT_CATEGORY  # type: ignore[return-value]


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
