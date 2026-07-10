"""ACT router (WS2 D1-2): the config-editor read page + the single write route.

Mounted on the D1-1 app at its marked D1-2 mount point. This is the ONLY
mutating route in the dashboard. Every request passes the Origin allowlist then
the PIN (fail-closed) before `apply_edit` validates, first-activation-gates,
writes, and audits. Outcomes render as an htmx partial (uniform HTTP 200 — no
status-code oracle; the real control is that no write happens unless every
check passes).
"""
from __future__ import annotations

import getpass
from typing import Any

from fastapi import FastAPI, Form, Request, Response
from fastapi.templating import Jinja2Templates

from operator_dashboard.act import config_write
from operator_dashboard.act.config_write import apply_edit, audit_denied, read_registry_state
from operator_dashboard.auth import OriginError, PinError, check_origin, verify_pin


def register_act_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.get("/config")
    def config_editor(request: Request) -> Response:
        error = ""
        groups: dict[str, list[dict[str, Any]]] = {}
        try:
            for row in read_registry_state():
                groups.setdefault(row["group"], []).append(row)
        except Exception as exc:  # fail-soft: show the read error, never crash
            error = f"could not read ITS_Config: {type(exc).__name__}: {exc}"
        return templates.TemplateResponse(request, "config.html", {"groups": groups, "error": error})

    @app.post("/act/config")
    def act_config(
        request: Request,
        setting: str = Form(...),
        workstream: str = Form(...),
        value: str = Form(...),
        pin: str = Form(...),
    ) -> Response:
        operator = getpass.getuser()
        # (a) Origin allowlist — defense-in-depth CSRF check (PIN is the real one).
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, setting, workstream, "origin")
            return _outcome(templates, request, config_write.NOT_EDITABLE, f"refused: {exc}", setting, workstream)
        # (b) PIN — primary control, fail-closed.
        try:
            verify_pin(pin)
        except PinError as exc:
            audit_denied(operator, setting, workstream, "pin")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", setting, workstream)
        # (c) apply: validate → first-activation-gate → write LAST → audit.
        outcome = apply_edit(setting, workstream, value, operator)
        if outcome.kind == config_write.NOT_EDITABLE:
            audit_denied(operator, setting, workstream, "not_editable")
        return _outcome(templates, request, outcome.kind, outcome.message, setting, workstream)


def _outcome(
    templates: Jinja2Templates,
    request: Request,
    kind: str,
    message: str,
    setting: str,
    workstream: str,
) -> Response:
    return templates.TemplateResponse(
        request,
        "_act_outcome.html",
        {"kind": kind, "message": message, "setting": setting, "workstream": workstream},
    )
