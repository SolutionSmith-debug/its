"""FastAPI app factory for the operator dashboard.

D1-1 shipped the read-only observability core; D1-2 adds the ACT surface — the
Class-A runtime config editor (the one and only mutating route), registered at
the marked mount point below.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from operator_dashboard.act.router import register_act_routes
from operator_dashboard.config import PANEL_REFRESH_SECONDS
from operator_dashboard.sources import PANELS, PANELS_BY_ID
from operator_dashboard.sources.base import SEV_UNAVAILABLE, PanelResult

_BASE = Path(__file__).resolve().parent
# Build the Jinja environment explicitly so autoescape is GUARANTEED on: it is
# the load-bearing XSS defense over every rendered value (Smartsheet cells and
# raw local log lines are untrusted/adversarial content).
_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_BASE / "templates")),
    autoescape=True,
)
_TEMPLATES = Jinja2Templates(env=_ENV)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ITS Operator Dashboard (D1-1 · read-only)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")

    # Sync `def` endpoints run in Starlette's threadpool, so the blocking
    # Smartsheet SDK / launchctl subprocess calls never stall the event loop.

    @app.get("/")
    def index(request: Request) -> Response:
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"panels": PANELS, "refresh": PANEL_REFRESH_SECONDS}
        )

    @app.get("/panels/{panel_id}")
    def panel(request: Request, panel_id: str) -> Response:
        source = PANELS_BY_ID.get(panel_id)
        if source is None:
            result = PanelResult(
                panel_id=panel_id,
                title=panel_id,
                available=False,
                unavailable_reason="unknown panel",
                severity=SEV_UNAVAILABLE,
            )
        else:
            result = source.fetch()
        return _TEMPLATES.TemplateResponse(
            request, "_panel.html", {"p": result, "refresh": PANEL_REFRESH_SECONDS}
        )

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    # --- D1-2 ACT surface -------------------------------------------------
    # The Class-A runtime config editor: GET /config (read) + POST /act/config
    # (the ONLY mutating route). PIN-gated (fail-closed) + Origin-allowlisted,
    # per-key validated, first-activation-escalated, audited on every write. It
    # writes ONLY to ITS_Config — an internal system-of-record write, NOT an
    # external send; the External Send Gate (Invariant 1) stays with the
    # daemons. Higher-ceremony actions (Class B/C, launchctl, secrets) are D1-3.
    register_act_routes(app, _TEMPLATES)
    return app
