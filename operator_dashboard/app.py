"""FastAPI app factory for the operator dashboard.

D1-1 shipped the read-only observability core; D1-2 adds the ACT surface — the
Class-A runtime config editor (the one and only mutating route), registered at
the marked mount point below.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import jinja2
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from operator_dashboard.act.router import register_act_routes
from operator_dashboard.config import PANEL_REFRESH_SECONDS
from operator_dashboard.sources import PANELS, PANELS_BY_ID
from operator_dashboard.sources.base import SEV_UNAVAILABLE, PanelResult
from operator_dashboard.system_view import register_system_routes
from operator_dashboard.troubleshoot import register_troubleshoot_routes

# The pulse strip's one-glance chips: panel id -> short chip name, in display
# order. Reuses the panels' own fetches (local reads + the shared TTL caches).
_PULSE_PANELS: list[tuple[str, str]] = [
    ("daemons", "daemons"),
    ("watchdog", "watchdog"),
    ("circuit_breaker", "breaker"),
    ("open_criticals", "criticals"),
    ("review_queue", "review"),
    ("send_queue", "sends"),
]

_BASE = Path(__file__).resolve().parent


def _asset_version() -> str:
    """Content hash over the CSS/JS assets, computed once at boot.

    Templates append `?v=<this>` to every stylesheet/script URL so a browser
    cache can never pair NEW HTML with an OLD stylesheet — the failure mode
    that made a Safari Dock app render the post-#614 pages blank (web apps
    keep their own cache store, and unversioned subresource URLs are served
    from it without revalidation). The URL only changes when the asset content
    changes, so caching stays effective between deploys."""
    digest = hashlib.sha256()
    static = _BASE / "static"
    for path in sorted(static.glob("*.css")) + sorted(static.glob("*.js")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()

# Build the Jinja environment explicitly so autoescape is GUARANTEED on: it is
# the load-bearing XSS defense over every rendered value (Smartsheet cells and
# raw local log lines are untrusted/adversarial content).
_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_BASE / "templates")),
    autoescape=True,
)
_ENV.globals["asset_v"] = ASSET_VERSION
_TEMPLATES = Jinja2Templates(env=_ENV)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ITS Operator Dashboard (D1-1 · read-only)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")

    @app.middleware("http")
    async def _html_no_store(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Pages must always revalidate: a cached HTML shell paired with live
        # htmx fragments (or a newer deploy) renders wrong. Static assets are
        # exempt — their URLs are content-versioned (?v=ASSET_VERSION), so they
        # may cache freely and bust naturally on change.
        response = await call_next(request)
        if not request.url.path.startswith("/static"):
            # `no-store`, not `no-cache`: a Safari Dock web app imports Safari's
            # HTTP cache at creation and revalidates unreliably on in-app
            # navigation, so a `no-cache` page can still be served stale after a
            # deploy (2026-07-22 stale-map/config-tab incident — the SECOND
            # Safari-app cache bite after the blank-page one this middleware was
            # born from). Pages are small and localhost-served; never storing
            # them costs nothing. Assets stay exempt: versioned URLs cache freely.
            response.headers.setdefault("Cache-Control", "no-store")
        return response

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

    @app.get("/view/{panel_id}")
    def view(request: Request, panel_id: str, col: str = "", eq: str = "") -> Response:
        # Drill-down: the same panel rendered FULL-PAGE with detail=True (capped
        # panels — errors / logs / audit — return far more rows). Read-only, one
        # more GET; the ACT surface stays on /config. Optional ?col=&eq= filters
        # the rendered rows to those whose displayed cell equals `eq` (used by
        # the system map's per-node "recent errors" links) — a post-filter on the
        # already-cleaned display values, never a query pushed to a data source.
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
            result = source.fetch(detail=True)
        filter_note = ""
        if col and eq and result.available and col in result.columns:
            result.rows = [r for r in result.rows if r.get(col, "") == eq]
            filter_note = f"filtered: {col} = {eq}"
        return _TEMPLATES.TemplateResponse(
            request, "view.html", {"p": result, "filter_note": filter_note}
        )

    @app.get("/pulse")
    def pulse(request: Request) -> Response:
        # The one-glance strip above the status grid: one chip per key surface,
        # each deep-linking to its panel's drill-down. Fail-soft per chip.
        chips = []
        for panel_id, name in _PULSE_PANELS:
            source = PANELS_BY_ID.get(panel_id)
            if source is None:
                continue
            p = source.fetch()
            chips.append(
                {
                    "name": name,
                    "summary": p.summary if p.available else "unavailable",
                    "sev": p.severity,
                    "href": f"/view/{panel_id}",
                }
            )
        return _TEMPLATES.TemplateResponse(
            request, "_pulse.html", {"chips": chips, "refresh": PANEL_REFRESH_SECONDS}
        )

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        # Enriched so a KeepAlive prober / operator sees something meaningful: the
        # editable-registry size, rotatable-secret count, and panels loaded. Still
        # a 200 text/plain — the launchd KeepAlive only needs the process alive,
        # but the counts confirm the app booted with its registries intact.
        from operator_dashboard.act.registry import REGISTRY, SECRETS

        return f"ok\nregistry_keys={len(REGISTRY)}\nsecrets={len(SECRETS)}\npanels={len(PANELS)}"

    @app.get("/manifest.json")
    def manifest() -> Response:
        # Served with the correct type so Chrome/Edge offer "Install" (Safari
        # "Add to Dock" uses the apple-touch-icon). Makes the dashboard a first-class
        # Dock app — a standalone window with the Evergreen-crest icon.
        return FileResponse(_BASE / "static" / "manifest.json", media_type="application/manifest+json")

    # --- D1-2 ACT surface -------------------------------------------------
    # The Class-A runtime config editor: GET /config (read) + POST /act/config
    # (the ONLY mutating route). PIN-gated (fail-closed) + Origin-allowlisted,
    # per-key validated, first-activation-escalated, audited on every write. It
    # writes ONLY to ITS_Config — an internal system-of-record write, NOT an
    # external send; the External Send Gate (Invariant 1) stays with the
    # daemons. Higher-ceremony actions (Class B/C, launchctl, secrets) are D1-3.
    register_act_routes(app, _TEMPLATES)

    # --- Troubleshooting view (read-only) ---------------------------------
    # GET /troubleshoot (the tree, htmx drill-down) + GET /doc/{path} (a safe,
    # path-allowlisted markdown viewer for runbooks/enablement/references). NO
    # mutation routes; the tree loads fail-soft (a TreeError renders a banner,
    # never crashes the dashboard).
    register_troubleshoot_routes(app, _TEMPLATES)

    # --- System map (read-only) -------------------------------------------
    # GET /system (the live machine-room schematic) + GET /system/node/{id}
    # (the htmx detail rail). Deep-linked from error rows, panels, and the
    # troubleshooting tree; every live join is fail-soft.
    register_system_routes(app, _TEMPLATES)
    return app
