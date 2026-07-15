"""Tests for the dashboard troubleshooting view (/troubleshoot) + doc viewer (/doc).

Covers: boot + render, boot fail-soft on a broken tree, the shipped tree validates, htmx
drill-down partials, the keyword filter, the safe markdown viewer, and — prove-it-bites — path
traversal rejection + raw-HTML escaping. Also asserts NO mutation route was added.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import operator_dashboard.troubleshoot as ts
from operator_dashboard.app import create_app
from operator_dashboard.troubleshoot import _MD, _safe_doc_target
from troubleshooting.loader import TreeError, load_tree


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    from operator_dashboard import cache

    cache._store.clear()
    yield
    cache._store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ── boot + render ────────────────────────────────────────────────────────────────────────
def test_shipped_tree_validates() -> None:
    """The committed tree.yaml loads + schema-validates — the dashboard boots on it."""
    tree = load_tree()
    assert tree.workflows


def test_troubleshoot_renders(client: TestClient) -> None:
    resp = client.get("/troubleshoot")
    assert resp.status_code == 200
    # a couple of workflow titles from the committed tree are present
    assert "Safety report" in resp.text
    assert "Daemon plane" in resp.text or "daemon plane" in resp.text.lower()


def test_nav_link_present(client: TestClient) -> None:
    assert '/troubleshoot' in client.get("/").text


def test_boot_fail_soft_on_broken_tree(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TreeError renders a banner, not a 500 — the dashboard never crashes on a bad tree."""
    def _boom() -> object:
        raise TreeError("synthetic: workflow[x]: missing required field 'title'")

    monkeypatch.setattr(ts, "load_tree", _boom)
    resp = client.get("/troubleshoot")
    assert resp.status_code == 200
    assert "failed to load" in resp.text
    assert "synthetic" in resp.text


# ── drill-down partials ──────────────────────────────────────────────────────────────────
def test_drilldown_workflow_step_fm(client: TestClient) -> None:
    tree = load_tree()
    wf = tree.workflows[0]
    step = wf.steps[0]
    r1 = client.get(f"/troubleshoot/wf/{wf.id}")
    assert r1.status_code == 200 and step.title in r1.text
    r2 = client.get(f"/troubleshoot/step/{wf.id}/{step.id}")
    assert r2.status_code == 200
    fm = next((s for s in wf.steps if s.failure_modes), wf.steps[0]).failure_modes[0]
    parent = next(s for s in wf.steps if s.failure_modes)
    r3 = client.get(f"/troubleshoot/fm/{wf.id}/{parent.id}/{fm.id}")
    assert r3.status_code == 200 and fm.symptom[:20] in r3.text
    # class badge is rendered
    assert "Operator-resolvable" in r3.text or "Escalate to Seth" in r3.text


def test_drilldown_unknown_ids_are_soft(client: TestClient) -> None:
    assert "unknown workflow" in client.get("/troubleshoot/wf/nope").text
    assert "unknown step" in client.get("/troubleshoot/step/safety_report/nope").text


def test_filter_narrows(client: TestClient) -> None:
    resp = client.get("/troubleshoot", params={"q": "held_no_recipient"})
    assert resp.status_code == 200
    assert "held_no_recipient" in resp.text
    # a non-matching query yields the no-match note
    assert "No symptoms match" in client.get("/troubleshoot", params={"q": "zzz-nope-xyzzy"}).text


# ── doc viewer: valid render ─────────────────────────────────────────────────────────────
def test_doc_viewer_renders_runbook(client: TestClient) -> None:
    resp = client.get("/doc/runbooks/circuit_breaker.md")
    assert resp.status_code == 200
    assert "circuit_breaker.md" in resp.text
    # markdown rendered to HTML (a heading became <h1>/<h2>…)
    assert "<h" in resp.text


def test_doc_viewer_allows_all_three_dirs(client: TestClient) -> None:
    assert client.get("/doc/references/glossary.md").status_code == 200
    assert client.get("/doc/enablement/README.md").status_code == 200


# ── prove-it-bites: path traversal is rejected ───────────────────────────────────────────
def test_safe_doc_target_rejects_traversal() -> None:
    # escapes docs/ entirely
    assert _safe_doc_target("../../shared/keychain.py") is None
    assert _safe_doc_target("../pyproject.toml") is None
    assert _safe_doc_target("runbooks/../../pyproject.toml") is None
    # outside the allowlisted subdirs
    assert _safe_doc_target("session_logs/2026-07-15_docs-corpus-tranche-a-tier1-references.md") is None
    # non-.md
    assert _safe_doc_target("runbooks/README.md") is not None  # a real .md is fine
    assert _safe_doc_target("runbooks/does_not_exist.md") is None
    # a within-allowlist relative segment that still lands in an allowed dir is OK
    assert _safe_doc_target("runbooks/../references/glossary.md") is not None


def test_doc_viewer_route_rejects_bad_path(client: TestClient) -> None:
    r = client.get("/doc/runbooks/does_not_exist.md")
    assert r.status_code == 404 and "not available" in r.text
    # a path that resolves outside the allowlist → 404, never leaks the file
    r2 = client.get("/doc/session_logs/anything.md")
    assert r2.status_code == 404


# ── prove-it-bites: raw HTML in a doc is escaped, never executed ──────────────────────────
def test_markdown_escapes_raw_html() -> None:
    out = _MD.render("Hello <script>alert('xss')</script> world\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ── read-only invariant: no mutation route added by the troubleshoot surface ─────────────
def test_troubleshoot_surface_is_get_only() -> None:
    app = create_app()
    for route in app.routes:
        path: str = getattr(route, "path", "")
        methods: set[str] = getattr(route, "methods", None) or set()
        if path.startswith("/troubleshoot") or path.startswith("/doc"):
            assert methods <= {"GET", "HEAD"}, f"{path} exposes non-GET methods {methods}"
