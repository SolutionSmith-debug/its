"""Loader + shape-checker for the troubleshooting tree (``docs/troubleshooting/tree.yaml``).

Pure loader — reads YAML, validates shape, returns frozen dataclasses. No network, no state
writes. Mirrors the ``docs_pdf/manifest.py`` loader style (shape-checked, typed error). A
malformed tree raises :class:`TreeError` with a path-qualified message; ``load_tree`` never
returns a partial. The dashboard ``/troubleshoot`` view calls this at boot and renders a
fail-soft banner naming the ``TreeError`` rather than crashing.

Schema (see ``docs/troubleshooting/schema.md`` for the authored spec):

    workflows:
      - id, title, summary
        steps:
          - id, title
            what_happens: {daemon?, worker_route?, sheets[]?, config_gates[]?}
            healthy_signals: [str]
            failure_modes:
              - id, symptom, signals[], checks[], resolutions[], class
                runbook?         docs/runbooks/<file>.md[#anchor]
                watchdog_check?  a scripts.watchdog CHECKS function name (e.g. _check_row_cap_rotation)
            # a step with no failure modes must instead carry:
            no_failure_modes: <reason str>
    runbook_exemptions: [<runbook filename>, ...]   # runbooks intentionally not linked by a node
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = REPO_ROOT / "docs" / "troubleshooting" / "tree.yaml"

# The two resolution classes (brief-specified enum). Display labels are role-based
# ("Operator-resolvable" / "Escalate to Seth") — see docs/troubleshooting/schema.md.
CLASSES = frozenset({"daniel_solo", "seth_coresolve"})


class TreeError(Exception):
    """Raised on any troubleshooting-tree shape / schema error."""


def tree_path() -> Path:
    """Absolute path to the committed tree.yaml."""
    return TREE_PATH


@dataclass(frozen=True)
class WhatHappens:
    daemon: str | None
    worker_route: str | None
    sheets: tuple[str, ...]
    config_gates: tuple[str, ...]


@dataclass(frozen=True)
class FailureMode:
    id: str
    symptom: str
    signals: tuple[str, ...]
    checks: tuple[str, ...]
    resolutions: tuple[str, ...]
    cls: str  # one of CLASSES ("class" is a keyword, so the field is `cls`)
    runbook: str | None
    watchdog_check: str | None


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    what_happens: WhatHappens
    healthy_signals: tuple[str, ...]
    failure_modes: tuple[FailureMode, ...]
    no_failure_modes: str | None


@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    summary: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Tree:
    workflows: tuple[Workflow, ...]
    runbook_exemptions: tuple[str, ...] = field(default_factory=tuple)

    def all_failure_modes(self) -> list[FailureMode]:
        return [fm for wf in self.workflows for st in wf.steps for fm in st.failure_modes]

    def all_steps(self) -> list[Step]:
        return [st for wf in self.workflows for st in wf.steps]

    def referenced_runbooks(self) -> set[str]:
        """The set of `docs/runbooks/<file>.md` paths (anchor stripped) any node links."""
        out: set[str] = set()
        for fm in self.all_failure_modes():
            if fm.runbook:
                out.add(fm.runbook.split("#", 1)[0])
        return out

    def referenced_daemons(self) -> set[str]:
        return {
            st.what_happens.daemon
            for st in self.all_steps()
            if st.what_happens.daemon
        }

    def referenced_watchdog_checks(self) -> set[str]:
        return {
            fm.watchdog_check for fm in self.all_failure_modes() if fm.watchdog_check
        }


def _req_str(d: dict[str, Any], key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise TreeError(f"{where}: missing/empty required string field {key!r}")
    return v


def _str_list(d: dict[str, Any], key: str, where: str, *, required: bool = False) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        if required:
            raise TreeError(f"{where}: missing required list field {key!r}")
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise TreeError(f"{where}: {key!r} must be a list of non-empty strings")
    return tuple(v)


def _parse_failure_mode(d: Any, where: str) -> FailureMode:
    if not isinstance(d, dict):
        raise TreeError(f"{where}: failure_mode is not a mapping")
    fid = _req_str(d, "id", where)
    cls = _req_str(d, "class", f"{where}[{fid}]")
    if cls not in CLASSES:
        raise TreeError(
            f"{where}[{fid}]: class {cls!r} not one of {sorted(CLASSES)}"
        )
    runbook = d.get("runbook")
    if runbook is not None:
        if not isinstance(runbook, str) or not runbook.startswith("docs/runbooks/"):
            raise TreeError(
                f"{where}[{fid}]: runbook must be a 'docs/runbooks/<file>.md[#anchor]' path"
            )
    wc = d.get("watchdog_check")
    if wc is not None and (not isinstance(wc, str) or not wc.startswith("_check_")):
        raise TreeError(f"{where}[{fid}]: watchdog_check must be a _check_* function name")
    return FailureMode(
        id=fid,
        symptom=_req_str(d, "symptom", f"{where}[{fid}]"),
        signals=_str_list(d, "signals", f"{where}[{fid}]", required=True),
        checks=_str_list(d, "checks", f"{where}[{fid}]", required=True),
        resolutions=_str_list(d, "resolutions", f"{where}[{fid}]", required=True),
        cls=cls,
        runbook=runbook,
        watchdog_check=wc,
    )


def _parse_step(d: Any, where: str) -> Step:
    if not isinstance(d, dict):
        raise TreeError(f"{where}: step is not a mapping")
    sid = _req_str(d, "id", where)
    sw = f"{where}[{sid}]"
    wh_raw = d.get("what_happens") or {}
    if not isinstance(wh_raw, dict):
        raise TreeError(f"{sw}: what_happens must be a mapping")
    what = WhatHappens(
        daemon=(wh_raw.get("daemon") or None),
        worker_route=(wh_raw.get("worker_route") or None),
        sheets=_str_list(wh_raw, "sheets", sw),
        config_gates=_str_list(wh_raw, "config_gates", sw),
    )
    fms_raw = d.get("failure_modes") or []
    if not isinstance(fms_raw, list):
        raise TreeError(f"{sw}: failure_modes must be a list")
    fms = tuple(_parse_failure_mode(fm, f"{sw}.failure_modes") for fm in fms_raw)
    no_fm = d.get("no_failure_modes")
    if not fms and not (isinstance(no_fm, str) and no_fm.strip()):
        raise TreeError(
            f"{sw}: a step with no failure_modes must carry a non-empty 'no_failure_modes' reason"
        )
    # unique failure-mode ids within the step
    ids = [fm.id for fm in fms]
    if len(ids) != len(set(ids)):
        raise TreeError(f"{sw}: duplicate failure_mode id(s)")
    return Step(
        id=sid,
        title=_req_str(d, "title", sw),
        what_happens=what,
        healthy_signals=_str_list(d, "healthy_signals", sw),
        failure_modes=fms,
        no_failure_modes=(no_fm if isinstance(no_fm, str) and no_fm.strip() else None),
    )


def _parse_workflow(d: Any, where: str) -> Workflow:
    if not isinstance(d, dict):
        raise TreeError(f"{where}: workflow is not a mapping")
    wid = _req_str(d, "id", where)
    ww = f"workflow[{wid}]"
    steps_raw = d.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise TreeError(f"{ww}: steps must be a non-empty list")
    steps = tuple(_parse_step(s, f"{ww}.steps") for s in steps_raw)
    sids = [s.id for s in steps]
    if len(sids) != len(set(sids)):
        raise TreeError(f"{ww}: duplicate step id(s)")
    return Workflow(
        id=wid,
        title=_req_str(d, "title", ww),
        summary=_req_str(d, "summary", ww),
        steps=steps,
    )


def load_tree(path: Path = TREE_PATH) -> Tree:
    """Load + shape-check the troubleshooting tree. Raises :class:`TreeError`; never partial."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise TreeError(f"troubleshooting tree missing: {path}") from e
    except yaml.YAMLError as e:
        raise TreeError(f"troubleshooting tree is not valid YAML: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise TreeError(f"troubleshooting tree top level must be a mapping: {path}")
    wfs_raw = raw.get("workflows")
    if not isinstance(wfs_raw, list) or not wfs_raw:
        raise TreeError("troubleshooting tree has no 'workflows' list")
    workflows = tuple(_parse_workflow(w, "workflows") for w in wfs_raw)
    wids = [w.id for w in workflows]
    if len(wids) != len(set(wids)):
        raise TreeError("troubleshooting tree has duplicate workflow id(s)")
    exemptions_raw = raw.get("runbook_exemptions") or []
    if not isinstance(exemptions_raw, list) or not all(isinstance(x, str) for x in exemptions_raw):
        raise TreeError("runbook_exemptions must be a list of runbook filenames")
    return Tree(workflows=workflows, runbook_exemptions=tuple(exemptions_raw))
