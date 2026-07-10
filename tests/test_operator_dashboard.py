"""Smoke + fail-soft + escape/redaction tests for the operator dashboard (D1-1).

Also proves the read-only invariant in code: no route accepts a non-GET
method, and untrusted panel values render inert (HTML-escaped + redacted).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.app import create_app
from operator_dashboard.sources import PANELS_BY_ID


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    # The Smartsheet panels share a process-wide TTL cache; clear it around
    # each test so a value cached by one test can't bleed into another.
    from operator_dashboard import cache

    cache._store.clear()
    yield
    cache._store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_index_returns_200_with_all_panel_slots(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    for panel_id in PANELS_BY_ID:
        assert f"/panels/{panel_id}" in resp.text


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


@pytest.mark.parametrize("panel_id", list(PANELS_BY_ID))
def test_every_panel_renders_or_degrades_never_500(
    client: TestClient, panel_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the Smartsheet panels hermetic + fast: force their reads to raise
    # so they exercise the fail-soft ('unavailable') path instead of hitting
    # live Smartsheet. Local-file panels read the real ~/its tree (or degrade
    # if absent, e.g. in CI). Both must yield 200 — never a 500.
    import shared.review_queue as rq
    import shared.smartsheet_client as ss

    def _boom(*args: object, **kwargs: object) -> object:
        raise ConnectionError("network disabled in test")

    monkeypatch.setattr(ss, "get_rows", _boom, raising=False)
    monkeypatch.setattr(rq, "get_pending", _boom, raising=False)

    resp = client.get(f"/panels/{panel_id}")
    assert resp.status_code == 200
    assert "panel" in resp.text


def test_unknown_panel_degrades_not_crashes(client: TestClient) -> None:
    resp = client.get("/panels/does-not-exist")
    assert resp.status_code == 200
    assert "unknown panel" in resp.text


def test_untrusted_smartsheet_values_render_inert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inject an adversarial cell value (a script tag) AND a secret-shaped
    # value into ITS_Errors, then prove the rendered HTML neutralizes both:
    # the <script> is HTML-escaped (autoescape) and the secret is redacted
    # (shared.redact) — neither reaches the browser live.
    from operator_dashboard.sources.smartsheet_panels import ErrorsRecentSource

    poison = [
        {
            "_row_id": 1,
            "Created At": "2026-07-10T00:00:00+00:00",
            "Severity": "ERROR",
            "Script": "evil",
            "Message": "<script>alert('xss')</script> password=hunter2",
        }
    ]
    monkeypatch.setattr(ErrorsRecentSource, "_load", lambda self: poison)

    resp = client.get("/panels/errors_recent")
    assert resp.status_code == 200
    body = resp.text
    # XSS: the raw script tag must NOT appear; its escaped form must.
    assert "<script>alert('xss')</script>" not in body
    assert "&lt;script&gt;" in body
    # Secret: the redaction backstop masks the value.
    assert "hunter2" not in body
    assert "&lt;redacted&gt;" in body


def test_no_mutation_routes_exist() -> None:
    # D1-1 is read-only: every route must be GET/HEAD only. This is the
    # in-code proof of 'zero mutation routes'.
    app = create_app()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue  # e.g. the StaticFiles Mount has no fixed method set
        assert methods <= {"GET", "HEAD"}, f"non-read route: {route!r} {methods}"


def test_config_paths_mirror_live_shared_constants() -> None:
    # Drift guard: the dashboard's observation roots must equal the constants
    # owned by the shared modules (which resolve to ~/its/...). If those move,
    # this fails loudly instead of the panels silently reading the wrong tree.
    import shared.error_log as el
    import shared.heartbeat as hb
    from operator_dashboard import config as dash_config

    assert dash_config.STATE_DIR == hb.STATE_DIR
    assert dash_config.LOGS_DIR == el.LOG_DIR
