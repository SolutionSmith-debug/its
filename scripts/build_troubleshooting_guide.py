"""Generate the printable troubleshooting guide from the tree (deterministic).

``docs/troubleshooting/tree.yaml`` (the single source of truth) → ``docs/troubleshooting/
troubleshooting_guide.md`` (a manifest-registered, branded-PDF-renderable markdown guide). The
same tree drives the dashboard ``/troubleshoot`` view; this is the print rendering.

Usage:
    python -m scripts.build_troubleshooting_guide            # write the guide
    python -m scripts.build_troubleshooting_guide --check    # CI: exit 1 if the committed guide is stale

Deterministic (workflows/steps/failure-modes in tree order; no timestamps/randomness) so the
generated file is stable — run-twice-identical, enforced by tests/test_troubleshooting_tree.py.
Network-free, no state writes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from troubleshooting.loader import CLASSES, FailureMode, Step, Tree, load_tree

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE_PATH = REPO_ROOT / "docs" / "troubleshooting" / "troubleshooting_guide.md"

# Role-based display labels for the (brief-specified) class enum values.
CLASS_LABELS = {
    "daniel_solo": "Operator-resolvable (solo)",
    "seth_coresolve": "Escalate to Seth (co-resolve)",
}
assert set(CLASS_LABELS) == set(CLASSES), "CLASS_LABELS must cover every class enum value"

_FRONTMATTER = """\
---
type: reference
date: 2026-07-15
status: active
workstream: docs
tags: [documentation-corpus, troubleshooting-tree, generated]
---
"""


def _bullets(items: tuple[str, ...]) -> list[str]:
    return [f"- {x}" for x in items]


def _failure_block(fm: FailureMode) -> list[str]:
    out: list[str] = [f"#### {fm.symptom}", ""]
    out.append(f"**Resolution class:** {CLASS_LABELS[fm.cls]}")
    out.append("")
    out.append(f"**Signals:** {', '.join(fm.signals)}")
    out.append("")
    out.append("**Checks (in order):**")
    out.extend(_bullets(fm.checks))
    out.append("")
    out.append("**Resolutions (in order):**")
    out.extend(_bullets(fm.resolutions))
    out.append("")
    refs: list[str] = []
    if fm.runbook:
        refs.append(f"runbook `{fm.runbook}`")
    if fm.watchdog_check:
        refs.append(f"watchdog `{fm.watchdog_check}`")
    if refs:
        out.append(f"**See also:** {' · '.join(refs)}")
        out.append("")
    return out


def _step_block(step: Step) -> list[str]:
    out: list[str] = [f"### {step.title}", ""]
    wh = step.what_happens
    facts: list[str] = []
    if wh.daemon:
        facts.append(f"| Daemon | `{wh.daemon}` |")
    if wh.worker_route:
        facts.append(f"| Worker route | `{wh.worker_route}` |")
    if wh.sheets:
        facts.append(f"| Sheets | {', '.join(f'`{s}`' for s in wh.sheets)} |")
    if wh.config_gates:
        facts.append(f"| Config gates | {', '.join(f'`{g}`' for g in wh.config_gates)} |")
    if facts:
        out.append("| What happens | |")
        out.append("|---|---|")
        out.extend(facts)
        out.append("")
    if step.healthy_signals:
        out.append("**Healthy signals:**")
        out.extend(_bullets(step.healthy_signals))
        out.append("")
    if step.failure_modes:
        for fm in step.failure_modes:
            out.extend(_failure_block(fm))
    else:
        out.append(f"_No failure modes: {step.no_failure_modes}_")
        out.append("")
    return out


def render_guide(tree: Tree) -> str:
    """Render the tree to the full guide markdown (deterministic)."""
    lines: list[str] = [_FRONTMATTER, "# ITS Troubleshooting Guide", ""]
    lines.append(
        "Pick the workflow you are blocked at, then the step, then the symptom that matches. Each "
        "symptom lists the signals, the ordered checks, the ordered resolutions, and who resolves "
        "it. This guide is generated from `docs/troubleshooting/tree.yaml` — the same source that "
        "drives the dashboard troubleshooter — so the two never drift."
    )
    lines.append("")
    lines.append(
        "**Resolution classes:** _Operator-resolvable (solo)_ = documented + low blast radius. "
        "_Escalate to Seth (co-resolve)_ = touches the Send Gate, secrets/auth, doctrine, or a "
        "code/deploy change, or is novel. When unsure, escalate."
    )
    lines.append("")
    # Contents
    lines.append("## Workflows")
    lines.append("")
    for wf in tree.workflows:
        lines.append(f"- **{wf.title}** — {wf.summary}")
    lines.append("")
    for wf in tree.workflows:
        lines.append(f"## {wf.title}")
        lines.append("")
        lines.append(wf.summary)
        lines.append("")
        for step in wf.steps:
            lines.extend(_step_block(step))
    text = "\n".join(lines).rstrip() + "\n"
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the troubleshooting guide from tree.yaml")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed guide is stale")
    args = ap.parse_args(argv)
    tree = load_tree()
    rendered = render_guide(tree)
    if args.check:
        current = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.is_file() else ""
        if current != rendered:
            print(
                "troubleshooting_guide.md is STALE — regenerate with "
                "`python -m scripts.build_troubleshooting_guide` and re-record its manifest sha256.",
                file=sys.stderr,
            )
            return 1
        print("troubleshooting_guide.md is current.")
        return 0
    GUIDE_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {GUIDE_PATH.relative_to(REPO_ROOT)} ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
