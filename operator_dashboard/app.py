"""FastAPI app factory for the operator dashboard (D1-1, read-only)."""
from __future__ import annotations

from pathlib import Path

import jinja2
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

    # --- D1-2 ACT surface mount point (INTENTIONALLY ABSENT in D1-1) ------
    # The Tier-2 action set (toggle ITS_Config gates, clear a stuck lock,
    # re-seed a row, re-send an approval, daemon controls) plus its auth
    # (Keychain PIN, CSRF/Origin checks) is D1-2. D1-1 is READ-ONLY: zero
    # mutation routes, zero send capability, no Keychain access, no
    # @require_active. D1-2 mounts an authenticated `act` router HERE without
    # refactoring this read surface. Do not add any write/act route to D1-1.
    return app
