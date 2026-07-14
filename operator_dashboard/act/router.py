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

from operator_dashboard.act import (
    config_write,
    daemon_ops,
    errors_ops,
    pin_change,
    secret_rotate,
    state_ops,
)
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

    @app.post("/act/errors/clear")
    def act_errors_clear(
        request: Request,
        pin: str = Form(...),
        confirm: str = Form(""),
        older_than_days: str = Form(""),
    ) -> Response:
        # Class-B: clear TERMINAL ITS_Errors rows (never an open CRITICAL) — the on-demand
        # complement to watchdog Check O. No per-target name to type, so the fixed
        # confirmation phrase is "clear-error-log". Optional older-than-N-days keeps recent rows.
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, "ITS_Errors", "", "origin")
            return _outcome(templates, request, "refused", f"refused: {exc}", "ITS_Errors", "")
        try:
            verify_elevated(pin, confirm, expected="clear-error-log")
        except PinError as exc:
            audit_denied(operator, "ITS_Errors", "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", "ITS_Errors", "")
        days: int | None = None
        raw = older_than_days.strip()
        if raw:
            if not (raw.isascii() and raw.isdigit()):
                return _outcome(
                    templates, request, config_write.REJECTED,
                    f"days must be a whole number (got {older_than_days!r})", "ITS_Errors", "",
                )
            days = int(raw)
        result = errors_ops.clear_error_log(operator, older_than_days=days)
        return _outcome(templates, request, result.kind, result.message, "ITS_Errors", "")

    @app.post("/act/errors/resolve")
    def act_errors_resolve(
        request: Request,
        pin: str = Form(...),
        confirm: str = Form(""),
        script: str = Form(""),
        error_code: str = Form(""),
        mode: str = Form("commit"),
    ) -> Response:
        # Class-B: stamp "Resolved At" on OPEN CRITICAL ITS_Errors rows matching a Script and/or
        # Error-code filter, making them terminal so clear-error-log can sweep them (the "solve it"
        # half). No per-target name to type, so the fixed confirmation phrase is "mark-resolved".
        # The filter-required guard lives in errors_ops (an unfiltered mass-resolve is refused).
        # mode="preview" runs a DRY RUN (count only, no write) so the operator can see the blast
        # radius against the §3.1 forensic surface before committing; anything else commits. The
        # elevated ceremony is required for BOTH (a preview reads the whole sheet; keep one gate).
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, "ITS_Errors", "", "origin")
            return _outcome(templates, request, "refused", f"refused: {exc}", "ITS_Errors", "")
        try:
            verify_elevated(pin, confirm, expected="mark-resolved")
        except PinError as exc:
            audit_denied(operator, "ITS_Errors", "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", "ITS_Errors", "")
        result = errors_ops.mark_errors_resolved(
            operator,
            script=script.strip() or None,
            error_code=error_code.strip() or None,
            dry_run=(mode.strip() == "preview"),
        )
        return _outcome(templates, request, result.kind, result.message, "ITS_Errors", "")

    @app.post("/act/pin/change")
    def act_pin_change(
        request: Request,
        pin: str = Form(...),
        confirm: str = Form(""),
        new_pin: str = Form(...),
        confirm_pin: str = Form(...),
    ) -> Response:
        # Class-C weight: CHANGE the ACT-gate credential itself. The elevated
        # ceremony (re-enter the CURRENT PIN + type "change-pin") proves authority
        # and intent; the new PIN is entered twice (typo guard). A LOST PIN is not
        # recoverable here — that stays terminal-only (pin_change docstring).
        operator = getpass.getuser()
        try:
            check_origin(request.headers.get("origin"), request.headers.get("referer"))
        except OriginError as exc:
            audit_denied(operator, "ITS_OPERATOR_PIN", "", "origin")
            return _outcome(templates, request, "refused", f"refused: {exc}", "ITS_OPERATOR_PIN", "")
        try:
            verify_elevated(pin, confirm, expected="change-pin")
        except PinError as exc:
            audit_denied(operator, "ITS_OPERATOR_PIN", "", "elevated")
            return _outcome(templates, request, config_write.REJECTED, f"denied: {exc}", "ITS_OPERATOR_PIN", "")
        result = pin_change.change_pin(new_pin, confirm_pin, operator)
        return _outcome(templates, request, result.kind, result.message, "ITS_OPERATOR_PIN", "")


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
