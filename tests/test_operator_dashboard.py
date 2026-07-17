"""Smoke + fail-soft + escape/redaction tests for the operator dashboard (D1-1).

Also proves the read-only invariant in code: no route accepts a non-GET
method, and untrusted panel values render inert (HTML-escaped + redacted).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

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
    # enriched: still starts with "ok" (KeepAlive probe) + carries the registry/
    # secret/panel counts so a booted-with-registries-intact state is visible.
    assert resp.text.startswith("ok")
    assert "registry_keys=" in resp.text and "panels=" in resp.text


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
    import shared.smartsheet_client as ss
    from operator_dashboard import cache

    poison = [
        {
            "_row_id": 1,
            "Created At": "2026-07-10T00:00:00+00:00",
            "Severity": "ERROR",
            "Script": "evil",
            "Message": "<script>alert('xss')</script> password=hunter2",
        }
    ]
    cache._store.clear()  # the ITS_Errors fetch is TTL-cached; force a fresh read
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(poison))

    resp = client.get("/panels/errors_recent")
    assert resp.status_code == 200
    body = resp.text
    # XSS: the raw script tag must NOT appear; its escaped form must.
    assert "<script>alert('xss')</script>" not in body
    assert "&lt;script&gt;" in body
    # Secret: the redaction backstop masks the value.
    assert "hunter2" not in body
    assert "&lt;redacted&gt;" in body


def test_mutation_routes_are_the_expected_act_set() -> None:
    # The app has EXACTLY nine mutating routes: Class-A edit, the elevated Class-B
    # edit, Class-C secret rotation, the Class-B interval edit (plist re-install),
    # Class-B daemon control (launchctl), Class-B circuit-breaker clear, the two
    # Class-B error-log verbs (mark-resolved + clear), and the Class-C
    # change-operator-PIN. Any other non-GET route is a regression. (The
    # send-queue/audit panels are GET-only reads, so they do not appear here.)
    app = create_app()
    mutating: list[tuple[str, list[str]]] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue  # e.g. the StaticFiles Mount has no fixed method set
        non_read = set(methods) - {"GET", "HEAD", "OPTIONS"}
        if non_read:
            mutating.append((getattr(route, "path", "?"), sorted(non_read)))
    assert sorted(mutating) == [
        ("/act/config", ["POST"]),
        ("/act/config/elevated", ["POST"]),
        ("/act/daemon/control", ["POST"]),
        ("/act/daemon/interval", ["POST"]),
        ("/act/errors/clear", ["POST"]),
        ("/act/errors/resolve", ["POST"]),
        ("/act/pin/change", ["POST"]),
        ("/act/secret/rotate", ["POST"]),
        ("/act/state/breaker-clear", ["POST"]),
    ], f"unexpected mutating routes: {mutating}"


def test_config_paths_mirror_live_shared_constants() -> None:
    # Drift guard: the dashboard's observation roots must equal the constants
    # owned by the shared modules (which resolve to ~/its/...). If those move,
    # this fails loudly instead of the panels silently reading the wrong tree.
    import shared.error_log as el
    import shared.heartbeat as hb
    from operator_dashboard import config as dash_config

    assert dash_config.STATE_DIR == hb.STATE_DIR
    assert dash_config.LOGS_DIR == el.LOG_DIR


def test_heartbeats_cycles_join_survives_poll_suffix_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the row-id cache keys daemons as "<workstream>.<daemon>"
    # (e.g. 'safety_reports.weekly_send_poll') but the liveness file is
    # 'weekly_send_heartbeat.txt' — the cycles must still join despite the
    # '_poll' suffix mismatch (was silently blanking the busiest daemons).
    import shared.heartbeat as hb
    from operator_dashboard.sources.runtime_state import HeartbeatsSource

    (tmp_path / "weekly_send_heartbeat.txt").write_text("2026-07-10T00:00:00+00:00")
    (tmp_path / "portal_poll_heartbeat.txt").write_text("2026-07-10T00:00:00+00:00")
    (tmp_path / "heartbeat_row_ids.json").write_text(
        json.dumps(
            {
                "safety_reports.weekly_send_poll": {"row_id": 1, "total_cycles": 3533},
                "safety_reports.portal_poll": {"row_id": 2, "total_cycles": 40961},
            }
        )
    )
    monkeypatch.setattr(hb, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hb, "HEARTBEAT_ROW_STATE_PATH", tmp_path / "heartbeat_row_ids.json")

    result = HeartbeatsSource().fetch()
    cycles_by_daemon = {r["daemon"]: r["cycles"] for r in result.rows}
    # '_poll'-suffix daemon joins despite the filename/cache-key name mismatch:
    assert cycles_by_daemon["weekly_send"] == "3533"
    # exact-match daemon still works:
    assert cycles_by_daemon["portal_poll"] == "40961"


def test_watchdog_panel_import_available_under_pytest() -> None:
    # Lock the watchdog panel's success path: `import scripts.watchdog` must
    # resolve (pinned to ITS_HOME on sys.path), so the panel renders rather
    # than degrading to 'unavailable' with a ModuleNotFoundError.
    from operator_dashboard.sources.watchdog_checks import WatchdogChecksSource

    result = WatchdogChecksSource().fetch()
    assert result.available, result.unavailable_reason


def test_send_queue_source_rolls_up_status(monkeypatch: pytest.MonkeyPatch) -> None:
    # Read-only send-queue panel: buckets Send Status across the 4 review sheets,
    # FAILED drives error severity, HELD from a held_* status, PENDING counted.
    import shared.sheet_ids as sid
    import shared.smartsheet_client as ss
    from operator_dashboard.sources.smartsheet_panels import SendQueueSource

    rowsets = {
        sid.SHEET_WSR_HUMAN_REVIEW: [{"Send Status": "PENDING"}, {"Send Status": "SENT"}, {"Send Status": "held_oversized_packet"}],
        sid.SHEET_WPR_HUMAN_REVIEW: [{"Send Status": "FAILED"}],
        sid.SHEET_PO_PENDING_REVIEW: [],
        sid.SHEET_SUBCONTRACT_PENDING_REVIEW: [{"Send Status": "PENDING"}],
    }
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rowsets.get(sheet_id, [])))
    result = SendQueueSource().fetch()
    assert result.available
    joined = " ".join(f"{r.get('status')}={r.get('count')}" for r in result.rows)
    assert "HELD" in joined and "FAILED" in joined and "PENDING" in joined
    assert result.severity == "error"  # a FAILED row makes the panel error-severity


def test_send_queue_source_fail_soft_per_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    # one unreachable sheet degrades to a "(unavailable)" row; the panel still renders
    import shared.sheet_ids as sid
    import shared.smartsheet_client as ss
    from operator_dashboard.sources.smartsheet_panels import SendQueueSource

    def get_rows(sheet_id: int, **kw: object) -> list[dict[str, str]]:
        if sheet_id == sid.SHEET_WSR_HUMAN_REVIEW:
            raise RuntimeError("sheet down")
        return [{"Send Status": "SENT"}]

    monkeypatch.setattr(ss, "get_rows", get_rows)
    result = SendQueueSource().fetch()
    assert result.available  # never crashes
    assert any(r.get("status") == "(unavailable)" for r in result.rows)


def test_open_criticals_panel_counts_only_open_criticals(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fire-surface panel: OPEN CRITICAL = CRITICAL with a blank "Resolved At" (the canonical
    # errors_rotation predicate). A resolved CRITICAL and every WARN/ERROR are terminal → excluded.
    import shared.smartsheet_client as ss
    from operator_dashboard import cache
    from operator_dashboard.sources.smartsheet_panels import OpenCriticalsSource

    rows = [
        {"Severity": "CRITICAL", "Resolved At": "", "Script": "a", "Error": "x", "Timestamp": "2026-07-01", "_row_id": 1},
        {"Severity": "CRITICAL", "Resolved At": "", "Script": "a", "Error": "x", "Timestamp": "2026-07-02", "_row_id": 2},
        {"Severity": "CRITICAL", "Resolved At": "2026-07-03", "Script": "b", "Error": "y", "Timestamp": "2026-07-01", "_row_id": 3},  # resolved → terminal
        {"Severity": "WARN", "Resolved At": "", "Script": "c", "Error": "z", "Timestamp": "2026-07-01", "_row_id": 4},  # WARN → terminal
    ]
    cache._store.clear()
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rows))
    p = OpenCriticalsSource().fetch()
    assert p.severity == "error"
    assert "2 open CRITICAL" in p.summary
    assert p.rows == [{"Script": "a", "Error": "x", "Count": "2", "Oldest": "2026-07-01", "_sev": "error"}]

    # a backlog with no OPEN criticals (all resolved / WARN) reads green + "0 open — clear"
    cache._store.clear()
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: [rows[2], rows[3]])
    clear = OpenCriticalsSource().fetch()
    assert clear.severity == "ok" and clear.summary == "0 open — clear" and clear.rows == []


def test_daemon_running_with_signal_exit_is_ok_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A live pid = healthy NOW; a prior signal exit (-15 SIGTERM = graceful restart) must NOT
    # paint a RUNNING daemon red. A loaded-but-NOT-running daemon with a bad exit stays ERROR.
    from operator_dashboard.sources.daemons import DaemonStatusSource

    src = DaemonStatusSource()
    monkeypatch.setattr(src, "_plist_labels", lambda: [
        "org.solutionsmith.its.dashboard", "org.solutionsmith.its.foo",
    ])
    monkeypatch.setattr(src, "_launchctl_table", lambda: {
        "org.solutionsmith.its.dashboard": ("55622", "-15"),  # running + SIGTERM last-exit
        "org.solutionsmith.its.foo": ("-", "1"),              # NOT running + error exit
    })
    by = {r["daemon"]: r for r in src.fetch().rows}
    assert by["dashboard"]["_sev"] == "ok"        # running → OK despite -15
    assert by["dashboard"]["state"] == "running"
    assert by["dashboard"]["last exit"] == "-15"  # still shown, informational
    assert by["foo"]["_sev"] == "error" and "exited 1" in by["foo"]["state"]


def test_audit_trail_source_filters_to_config_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    # the ACT audit panel shows only the config editor's own rows (accountability
    # where the actions happen), and surfaces denials in the summary.
    import shared.smartsheet_client as ss
    from operator_dashboard.sources.smartsheet_panels import AuditTrailSource

    rows = [
        {"Script": "operator_dashboard.config_editor", "Error": "config_audit", "Message": "edit", "Severity": "WARN"},
        {"Script": "some.other.daemon", "Error": "other_noise", "Message": "noise", "Severity": "ERROR"},
        {"Script": "operator_dashboard.config_editor", "Error": "config_denied", "Message": "denied", "Severity": "WARN"},
    ]
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rows))
    result = AuditTrailSource().fetch()
    assert result.available
    joined = " ".join(r.get("Error", "") for r in result.rows)
    assert "config_audit" in joined and "config_denied" in joined
    assert "other_noise" not in joined  # non-config-editor rows filtered out
    assert "denied" in result.summary  # a denial is surfaced in the summary


def test_manifest_and_icon_served_for_dock_install(client: TestClient) -> None:
    # Installable-as-a-Dock-app assets: the web-app manifest (correct content-type)
    # + the Evergreen-crest icon both serve, so Safari "Add to Dock" / Chrome
    # "Install" produce a standalone window (and the stray favicon 404 is gone).
    m = client.get("/manifest.json")
    assert m.status_code == 200
    assert "application/manifest+json" in m.headers.get("content-type", "")
    assert '"display": "standalone"' in m.text and "ITS" in m.text
    icon = client.get("/static/favicon.png")
    assert icon.status_code == 200
    assert icon.headers.get("content-type", "").startswith("image/")


def test_drilldown_view_shows_more_rows_than_panel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Clicking a panel title opens /view/{panel_id} full-page with detail=True: the
    # capped panels (errors) return far more rows than the 25-row summary card.
    import shared.smartsheet_client as ss
    from operator_dashboard import cache

    cache._store.clear()
    rows = [{"Severity": "WARN", "Message": f"e{i}", "Script": "d"} for i in range(300)]
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rows))
    detail = client.get("/view/errors_recent")
    assert detail.status_code == 200
    # the banner-extension back nav is the way out of a drill-down (Dock app has
    # no browser back button)
    assert "← Back to dashboard" in detail.text and 'class="subnav__back"' in detail.text
    assert "rows shown" in detail.text
    # detail cap (500) renders all 300; the panel card caps at 25
    assert detail.text.count('<tr class="sev-') == 300
    cache._store.clear()
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rows))
    card = client.get("/panels/errors_recent")
    assert card.text.count('<tr class="sev-') == 25
    # the card title is a drill-down link; an unknown panel is fail-soft
    assert 'href="/view/errors_recent"' in card.text
    assert client.get("/view/nonexistent").status_code == 200
