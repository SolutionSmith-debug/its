"""ACT router (WS2 D1-2 + D1-3): the config editor + the mutating routes.

Mutating routes (each renders a uniform HTTP-200 htmx outcome partial — no
status-code oracle; the real control is that no write happens unless every check
passes):
  POST /act/config          — Class-A edits (plain PIN gate)
  POST /act/config/elevated — Class-B weighted edits + send-poller activation
                              (elevated-confirm: re-PIN + typed confirmation)
  POST /act/secret/rotate   — Class-C secret rotation (elevated-confirm; write-only)
Class D (build-time) and Class E (Invariant-1 mode, legacy approvers) are
read-only — no edit control is rendered for them.
"""
from __future__ import annotations

import getpass
from typing import Any

from fastapi import FastAPI, Form, Request, Response
from fastapi.templating import Jinja2Templates

from operator_dashboard.act import config_write, daemon_ops, secret_rotate, state_ops
from operator_dashboard.act.config_write import (
    apply_edit,
    apply_elevated_edit,
    audit_denied,
    read_display_state,
    read_registry_state,
)
from operator_dashboard.act.registry import SECRETS
from operator_dashboard.auth import OriginError, PinError, check_origin, verify_elevated, verify_pin


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
        display: list[dict[str, Any]] = []
        try:
            display = read_display_state()
        except Exception:
            display = []
        secrets = [
            {"key": s.key, "label": s.label, "kind": s.kind, "note": s.note, "mirror": s.worker_mirror}
            for s in SECRETS.values()
        ]
        intervals: list[dict[str, Any]] = []
        try:
            intervals = daemon_ops.read_interval_state()
        except Exception:  # fail-soft: the interval panel degrades, page still renders
            intervals = []
        controls: list[dict[str, Any]] = []
        try:
            controls = daemon_ops.read_control_state()
        except Exception:  # fail-soft: the control panel degrades, page still renders
            controls = []
        return templates.TemplateResponse(
            request,
            "config.html",
            {
                "groups": groups,
                "error": error,
                "secrets": secrets,
                "display": display,
                "intervals": intervals,
                "controls": controls,
            },
        )

    @app.post("/act/config")
    def act_config(
        request: Request,
        setting: str = Form(...),
        workstream: str = Form(...),
        value: str = Form(...),
        pin: str = Form(...),
    ) -> Response:
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, setting, workstream, "origin")
            return _outcome(templates, request, config_write.NOT_EDITABLE, f"refused: {exc}", setting, workstream)
        try:
            verify_pin(pin)
        except PinError as exc:
            audit_denied(operator, setting, workstream, "pin")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", setting, workstream)
        outcome = apply_edit(setting, workstream, value, operator)
        if outcome.kind == config_write.NOT_EDITABLE:
            audit_denied(operator, setting, workstream, "not_editable")
        return _outcome(templates, request, outcome.kind, outcome.message, setting, workstream)

    @app.post("/act/config/elevated")
    def act_config_elevated(
        request: Request,
        setting: str = Form(...),
        workstream: str = Form(...),
        value: str = Form(...),
        pin: str = Form(...),
        confirm: str = Form(""),
        attest: str = Form(""),
    ) -> Response:
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, setting, workstream, "origin")
            return _outcome(templates, request, config_write.NOT_EDITABLE, f"refused: {exc}", setting, workstream)
        # elevated-confirm: re-PIN + type the exact setting name to confirm
        try:
            verify_elevated(pin, confirm, expected=setting)
        except PinError as exc:
            audit_denied(operator, setting, workstream, "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", setting, workstream)
        outcome = apply_elevated_edit(setting, workstream, value, operator, attested=(attest.strip().lower() == "yes"))
        if outcome.kind == config_write.NOT_EDITABLE:
            audit_denied(operator, setting, workstream, "not_editable")
        return _outcome(templates, request, outcome.kind, outcome.message, setting, workstream)

    @app.post("/act/secret/rotate")
    def act_secret_rotate(
        request: Request,
        key: str = Form(...),
        value: str = Form(""),
        pin: str = Form(...),
        confirm: str = Form(""),
    ) -> Response:
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, key, "", "origin")
            return _outcome(templates, request, "refused", f"refused: {exc}", key, "")
        # elevated-confirm: re-PIN + type the exact credential name to confirm
        try:
            verify_elevated(pin, confirm, expected=key)
        except PinError as exc:
            audit_denied(operator, key, "", "elevated")
            return _outcome(templates, request, "refused", f"denied: {exc}", key, "")
        result = secret_rotate.rotate_secret(key, value, operator)
        return _outcome(templates, request, result.kind, result.message, result.key, "")

    @app.post("/act/daemon/interval")
    def act_daemon_interval(
        request: Request,
        label: str = Form(...),
        interval: str = Form(...),
        pin: str = Form(...),
        confirm: str = Form(""),
    ) -> Response:
        # Class-B: change an interval daemon's cadence (ITS_Config row + plist
        # re-install). Same elevated ceremony as a Class-B config edit — it mutates
        # launchctl AND ITS_Config. The label is confirmation-typed + allowlisted.
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, label, "", "origin")
            return _outcome(templates, request, config_write.NOT_EDITABLE, f"refused: {exc}", label, "")
        try:
            verify_elevated(pin, confirm, expected=label)
        except PinError as exc:
            audit_denied(operator, label, "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", label, "")
        result = daemon_ops.edit_interval(label, interval, operator)
        if result.kind == config_write.NOT_EDITABLE:
            audit_denied(operator, label, "", "not_editable")
        return _outcome(templates, request, result.kind, result.message, result.label, "")

    @app.post("/act/daemon/control")
    def act_daemon_control(
        request: Request,
        label: str = Form(...),
        action: str = Form(...),
        pin: str = Form(...),
        confirm: str = Form(""),
    ) -> Response:
        # Class-B: start / stop / kickstart an allowlisted ITS daemon via launchctl.
        # Elevated (re-PIN + typed label); the label allowlist + audit are in daemon_ops.
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, label, "", "origin")
            return _outcome(templates, request, config_write.NOT_EDITABLE, f"refused: {exc}", label, "")
        try:
            verify_elevated(pin, confirm, expected=label)
        except PinError as exc:
            audit_denied(operator, label, "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", label, "")
        result = daemon_ops.control_daemon(label, action, operator)
        if result.kind == config_write.NOT_EDITABLE:
            audit_denied(operator, label, "", "not_editable")
        return _outcome(templates, request, result.kind, result.message, result.label, "")

    @app.post("/act/state/breaker-clear")
    def act_breaker_clear(
        request: Request,
        pin: str = Form(...),
        confirm: str = Form(""),
    ) -> Response:
        # Class-B: reset the circuit breaker to CLOSED (skip cooldown). No per-target
        # name to type, so the fixed confirmation phrase is "clear-breaker".
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, "circuit_breaker", "", "origin")
            return _outcome(templates, request, "refused", f"refused: {exc}", "circuit_breaker", "")
        try:
            verify_elevated(pin, confirm, expected="clear-breaker")
        except PinError as exc:
            audit_denied(operator, "circuit_breaker", "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", "circuit_breaker", "")
        result = state_ops.clear_circuit_breaker(operator)
        return _outcome(templates, request, result.kind, result.message, "circuit_breaker", "")


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
