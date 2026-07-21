"""§14 cross-daemon heartbeat parity guard (its#338).

`shared/heartbeat.py` is the single source of the `HeartbeatReporter` and the `HeartbeatStatus`
status vocabulary. Every daemon that owns a reporter exposes two module-level delegator seams —
`_write_heartbeat` and `_write_heartbeat_row` — which the daemon test suites patch BY NAME. This
guard asserts those seams stay THIN, 1:1 forwarders to the shared reporter across every daemon, so
the extraction can't silently drift back into per-daemon logic and the status vocabulary can't fork.

It deliberately does NOT assert byte-identity: three daemons legitimately carry a `daemon_name`
keyword-only param and three don't (see the drifted-docstrings note in memory). The robust invariant
is "forwards EVERY param 1:1 to the shared reporter" — insensitive to that difference and to any
future daemon.

Discovery-based (AST parse, no imports of the daemons) so a newly-added daemon is auto-covered —
mirrors the idiom of `tests/test_state_write_discipline.py`. This is the §14 "cheap parity TEST, not
speculative extraction" that its#338 asks for (the shared `HeartbeatReporter` extraction + the A1
self-provision fix already landed). Prove-it-bites: transiently drop a forwarded kwarg from any
delegator, or inline an extra statement, and this suite RED-lights (House Reflex §2).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# `field_ops` is a package too (field_ops/__init__.py). `shared/` is EXCLUDED — it DEFINES the
# reporter, it is not a consumer of the delegator seams.
#
# `po_materials` + `subcontracts` joined 2026-07-21 (coverage-gap audit). They hold 8 of the
# 14 live module-level `_heartbeat_reporter = HeartbeatReporter(` daemons (po_poll,
# po_send_poll, config_actuator, estimate_poll, rfq_poll, rfq_send_poll, subcontract_poll,
# subcontract_send_poll) — i.e. a MAJORITY of the heartbeat surface was unguarded while this
# suite passed green, the exact "hardcoded root list didn't grow with the system" failure.
WALKED_ROOTS = (
    "field_ops",
    "safety_reports",
    "progress_reports",
    "po_materials",
    "subcontracts",
)

# Absolute discovery floor — see test_discovery_found_all_daemons for why this coexists with
# the two self-maintaining structural checks rather than being the only anti-vacuity guard.
MIN_CONSUMERS = 14


def _is_reporter_assign(node: ast.stmt) -> bool:
    """True for a module-level `_heartbeat_reporter = HeartbeatReporter(...)` assignment."""
    return (
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_heartbeat_reporter" for t in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "HeartbeatReporter"
    )


def _consumers_under(root: str) -> list[tuple[str, ast.Module]]:
    """Every module under `root` that owns a module-level reporter, with its parsed tree."""
    out: list[tuple[str, ast.Module]] = []
    root_dir = REPO_ROOT / root
    if not root_dir.is_dir():
        return out
    for path in sorted(root_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(_is_reporter_assign(n) for n in tree.body):
            out.append((str(path.relative_to(REPO_ROOT)), tree))
    return out


def _discover_consumers() -> list[tuple[str, ast.Module]]:
    out: list[tuple[str, ast.Module]] = []
    for root in WALKED_ROOTS:
        out.extend(_consumers_under(root))
    return out


def _first_party_packages() -> list[str]:
    """Top-level first-party Python packages (a directory carrying `__init__.py`).

    Used by the self-maintaining coverage floor below: this is discovered from DISK, so a
    package that grows a heartbeat daemon can never hide from the guard just because nobody
    remembered to append it to WALKED_ROOTS.
    """
    return sorted(
        p.name
        for p in REPO_ROOT.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and p.name != "tests"
        and (p / "__init__.py").is_file()
    )


CONSUMERS = _discover_consumers()
_IDS = [rel for rel, _ in CONSUMERS]


def _toplevel_func(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _body_sans_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def test_walked_roots_cover_every_heartbeat_package() -> None:
    """The self-maintaining anti-vacuity floor: NO package with a heartbeat daemon may sit
    outside WALKED_ROOTS.

    A `len(CONSUMERS) >= N` floor cannot catch the failure this guard actually suffered — a
    hardcoded root list that stopped tracking the packages on disk. The floor was `>= 6` with
    6 discovered consumers, so it stayed green while `po_materials` and `subcontracts` (8
    further live daemons, a MAJORITY of the surface) were never walked at all. Discovering
    the candidate packages from disk instead means a new workstream package is either walked
    or RED-lights here — no magic number to remember to bump.
    """
    unwalked: dict[str, list[str]] = {}
    for pkg in _first_party_packages():
        # `shared` DEFINES the reporter; it is deliberately not a consumer root (see above).
        if pkg in WALKED_ROOTS or pkg == "shared":
            continue
        rels = [rel for rel, _ in _consumers_under(pkg)]
        if rels:
            unwalked[pkg] = rels
    assert not unwalked, (
        "package(s) own a module-level `_heartbeat_reporter = HeartbeatReporter(...)` but are "
        f"NOT in WALKED_ROOTS, so this parity guard never sees them: {unwalked} "
        "— add the package to WALKED_ROOTS in the SAME PR that adds the daemon"
    )


def test_every_walked_root_contributes_a_consumer() -> None:
    """The mirror direction: a walked root that discovers ZERO consumers means the root was
    renamed / deleted / its daemons moved away, and the guard is quietly walking nothing."""
    empty = sorted(root for root in WALKED_ROOTS if not _consumers_under(root))
    assert not empty, (
        f"walked root(s) {empty} contributed ZERO heartbeat consumers — the root list has "
        f"drifted from the packages on disk (discovered: {_IDS})"
    )


def test_discovery_found_all_daemons() -> None:
    # A wiring break (roots renamed, glob returns nothing) must fail LOUD, not vacuously pass.
    # MIN_CONSUMERS is the count MEASURED on disk 2026-07-21 (14: field_ops 1, safety_reports 4,
    # progress_reports 1, po_materials 6, subcontracts 2). The two structural checks above are
    # the self-maintaining half; this absolute floor is the backstop against a bulk regression
    # (a glob/AST-needle change that silently halves discovery). Lower it only deliberately,
    # when a daemon is genuinely retired.
    assert len(CONSUMERS) >= MIN_CONSUMERS, (
        f"expected >={MIN_CONSUMERS} heartbeat consumers, found {len(CONSUMERS)}: {_IDS}"
    )


@pytest.mark.parametrize(("rel", "tree"), CONSUMERS, ids=_IDS)
def test_defines_both_delegator_seams(rel: str, tree: ast.Module) -> None:
    assert _toplevel_func(tree, "_write_heartbeat") is not None, f"{rel}: missing _write_heartbeat"
    assert _toplevel_func(tree, "_write_heartbeat_row") is not None, (
        f"{rel}: missing _write_heartbeat_row"
    )


@pytest.mark.parametrize(("rel", "tree"), CONSUMERS, ids=_IDS)
def test_write_heartbeat_is_thin_liveness_delegator(rel: str, tree: ast.Module) -> None:
    fn = _toplevel_func(tree, "_write_heartbeat")
    assert fn is not None
    body = _body_sans_docstring(fn)
    assert len(body) == 1, f"{rel}: _write_heartbeat is not a single-call delegator"
    stmt = body[0]
    assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), (
        f"{rel}: _write_heartbeat body is not a bare call"
    )
    call = stmt.value
    assert (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "write_liveness"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_heartbeat_reporter"
    ), f"{rel}: _write_heartbeat must call _heartbeat_reporter.write_liveness()"
    assert not call.args and not call.keywords, f"{rel}: write_liveness() must take no args"


@pytest.mark.parametrize(("rel", "tree"), CONSUMERS, ids=_IDS)
def test_write_heartbeat_row_forwards_every_param_1to1(rel: str, tree: ast.Module) -> None:
    fn = _toplevel_func(tree, "_write_heartbeat_row")
    assert fn is not None
    # All params keyword-only (no positional forwarding that a reorder could silently break).
    assert not fn.args.args, f"{rel}: _write_heartbeat_row must take keyword-only params"
    kwonly = {a.arg for a in fn.args.kwonlyargs}
    body = _body_sans_docstring(fn)
    assert len(body) == 1, f"{rel}: _write_heartbeat_row is not a single-call delegator"
    stmt = body[0]
    assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), (
        f"{rel}: _write_heartbeat_row body is not a bare call"
    )
    call = stmt.value
    assert (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "write_row"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_heartbeat_reporter"
    ), f"{rel}: _write_heartbeat_row must call _heartbeat_reporter.write_row(...)"
    assert not call.args, f"{rel}: write_row must be called with keywords only"
    forwarded: set[str] = set()
    for kw in call.keywords:
        assert kw.arg is not None, f"{rel}: no **kwargs splat allowed in the forward"
        assert isinstance(kw.value, ast.Name) and kw.value.id == kw.arg, (
            f"{rel}: {kw.arg}= must forward the param verbatim (1:1), not transform it"
        )
        forwarded.add(kw.arg)
    assert forwarded == kwonly, (
        f"{rel}: forwards {sorted(forwarded)} but params are {sorted(kwonly)} "
        f"— every param must forward 1:1"
    )


@pytest.mark.parametrize(("rel", "tree"), CONSUMERS, ids=_IDS)
def test_imports_vocab_from_shared_heartbeat(rel: str, tree: ast.Module) -> None:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "shared.heartbeat":
            names |= {alias.name for alias in node.names}
    assert {"HeartbeatReporter", "HeartbeatStatus"} <= names, (
        f"{rel}: must import HeartbeatReporter + HeartbeatStatus from shared.heartbeat "
        f"(single status-vocabulary source, no local redefinition); found {sorted(names)}"
    )
