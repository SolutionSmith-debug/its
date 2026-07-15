"""The interactive troubleshooting view (`/troubleshoot`) + safe runbook viewer (`/doc/...`).

READ-ONLY. Renders `docs/troubleshooting/tree.yaml` (loaded + schema-validated via the shared
`troubleshooting` package) as a server-rendered, htmx-driven expand/collapse tree: workflow
cards → step chain → failure modes → detail. The `/doc/{path}` route renders a markdown doc
(runbooks / enablement / references only, path-allowlisted, traversal-rejected) so a runbook
opens in-dashboard.

NO mutation routes are added here. The tree is loaded fail-soft: a `TreeError` renders a banner
naming the error rather than crashing the dashboard.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from troubleshooting.loader import FailureMode, Step, Tree, TreeError, load_tree

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
# The ONLY directories the /doc viewer will serve, and only `.md` files within them.
ALLOWED_DOC_DIRS = ("runbooks", "enablement", "references")

# html=False → raw HTML in the source is ESCAPED (not passed through), so the rendered output is
# safe to mark |safe in the template. `linkify` off (no auto-linking of bare URLs). Trusted repo
# docs, but we defend in depth: the renderer never emits attacker-controlled HTML.
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False})


def _load() -> tuple[Tree | None, str | None]:
    """Load + validate the tree, fail-soft. Returns (tree, None) or (None, error_message)."""
    try:
        return load_tree(), None
    except TreeError as e:  # pragma: no cover - exercised by the boot-fail-soft test
        return None, str(e)
    except Exception as e:  # defensive: never let the tree crash the dashboard
        return None, f"unexpected error loading the troubleshooting tree: {e}"


def _fm_matches(fm: FailureMode, q: str) -> bool:
    hay = " ".join([fm.symptom, *fm.signals, *fm.checks, *fm.resolutions]).lower()
    return q in hay


def _filter_tree(tree: Tree, q: str) -> list[dict[str, object]]:
    """Return a view: [{workflow, steps:[{step, matched_fms}]}] limited to nodes matching q.

    Empty q → everything. A step is kept if it has ≥1 matching failure mode; a workflow is kept
    if it has ≥1 kept step. Matching is on symptom/signals/checks/resolutions, case-insensitive.
    """
    ql = q.strip().lower()
    view: list[dict[str, object]] = []
    for wf in tree.workflows:
        kept_steps: list[dict[str, object]] = []
        for st in wf.steps:
            fms = [fm for fm in st.failure_modes if not ql or _fm_matches(fm, ql)]
            if not ql or fms:
                kept_steps.append({"step": st, "fms": fms})
        if kept_steps:
            view.append({"workflow": wf, "steps": kept_steps})
    return view


def _find_step(tree: Tree, workflow_id: str, step_id: str) -> Step | None:
    wf = next((w for w in tree.workflows if w.id == workflow_id), None)
    if wf is None:
        return None
    return next((s for s in wf.steps if s.id == step_id), None)


def _safe_doc_target(rel: str) -> Path | None:
    """Resolve a `/doc/{rel}` path to a real docs file, or None if not allowlisted / unsafe.

    `rel` is relative to docs/ (e.g. "runbooks/circuit_breaker.md"). Rejects traversal
    (`..`), any file outside docs/<allowed-dir>/, and non-.md files.
    """
    if not rel or "\x00" in rel:
        return None
    target = (DOCS_ROOT / rel).resolve()
    try:
        relative = target.relative_to(DOCS_ROOT.resolve())
    except ValueError:
        return None  # escaped docs/ via ../
    parts = relative.parts
    if len(parts) < 2 or parts[0] not in ALLOWED_DOC_DIRS:
        return None
    if target.suffix != ".md" or not target.is_file():
        return None
    return target


def register_troubleshoot_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.get("/troubleshoot")
    def troubleshoot(request: Request, q: str = "") -> Response:
        tree, err = _load()
        view = _filter_tree(tree, q) if tree is not None else []
        return templates.TemplateResponse(
            request,
            "troubleshoot.html",
            {"view": view, "q": q, "error": err},
        )

    @app.get("/troubleshoot/wf/{workflow_id}")
    def ts_workflow(request: Request, workflow_id: str) -> Response:
        tree, err = _load()
        wf = None if tree is None else next(
            (w for w in tree.workflows if w.id == workflow_id), None
        )
        return templates.TemplateResponse(
            request, "_ts_workflow.html", {"wf": wf, "error": err}
        )

    @app.get("/troubleshoot/step/{workflow_id}/{step_id}")
    def ts_step(request: Request, workflow_id: str, step_id: str) -> Response:
        tree, err = _load()
        step = None if tree is None else _find_step(tree, workflow_id, step_id)
        return templates.TemplateResponse(
            request,
            "_ts_step.html",
            {"wf_id": workflow_id, "step": step, "error": err},
        )

    @app.get("/troubleshoot/fm/{workflow_id}/{step_id}/{fm_id}")
    def ts_fm(request: Request, workflow_id: str, step_id: str, fm_id: str) -> Response:
        tree, err = _load()
        step = None if tree is None else _find_step(tree, workflow_id, step_id)
        fm = None if step is None else next(
            (f for f in step.failure_modes if f.id == fm_id), None
        )
        return templates.TemplateResponse(
            request, "_ts_fm.html", {"fm": fm, "error": err}
        )

    @app.get("/doc/{doc_path:path}")
    def doc_view(request: Request, doc_path: str) -> Response:
        target = _safe_doc_target(doc_path)
        if target is None:
            return HTMLResponse(
                templates.get_template("doc.html").render(
                    request=request,
                    title="Document not available",
                    body_html=None,
                    rel=doc_path,
                ),
                status_code=404,
            )
        # Render markdown → HTML. `html=False` guarantees no raw-HTML passthrough, so the
        # output is safe to mark |safe in the template.
        rendered = _MD.render(target.read_text(encoding="utf-8"))
        return templates.TemplateResponse(
            request,
            "doc.html",
            {"title": target.name, "body_html": rendered, "rel": doc_path},
        )
