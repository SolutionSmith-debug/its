"""Guard-surface presence — fail-open-guard class (#11).

The propose-only security guards (the PreToolUse hooks + the advisory agents)
protect a developer/subagent session, but they fail OPEN if silently absent in some
layout — a fresh clone, a CI runner, or a non-sibling worktree where blueprint-style
relative symlinks dangle and load ZERO agents AND zero guard hooks. That is the worst
failure mode for a guard (the protection vanishes without a sound).

CI runs in a fresh clone of THIS repo, so these assertions become a mechanical merge
gate for the exec-repo guard surface — exactly the fresh-clone layout that watchdog
Check M (host-only, post-hoc, WARN) explicitly punts on.

Run with: pytest -q tests/test_guard_surface_present.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = REPO_ROOT / ".claude"

# The four PreToolUse guard scripts that must exist on the exec-repo guard surface.
_EXPECTED_HOOKS: tuple[str, ...] = (
    "block-dangerous-git.sh",
    "block-codeql-dismiss.sh",
    "block-doc-reconciliation-write.sh",
    "block-doctrine-write.sh",
)


def test_session_wide_git_guard_is_wired():
    """settings.json wires block-dangerous-git.sh as a session-wide PreToolUse(Bash)
    hook, and the referenced script exists on disk (forensic class #11)."""
    settings = json.loads((CLAUDE_DIR / "settings.json").read_text())
    pretooluse = settings.get("hooks", {}).get("PreToolUse", [])
    bash_commands = [
        hook.get("command", "")
        for entry in pretooluse
        if entry.get("matcher") == "Bash"
        for hook in entry.get("hooks", [])
    ]
    assert any("block-dangerous-git.sh" in c for c in bash_commands), (
        "settings.json no longer wires block-dangerous-git.sh as a PreToolUse(Bash) "
        "hook — the session-wide destructive-git guard is unwired (forensic class #11). "
        f"PreToolUse Bash commands seen: {bash_commands}"
    )
    hook = CLAUDE_DIR / "hooks" / "block-dangerous-git.sh"
    assert hook.exists(), f"wired guard hook missing on disk: {hook}"


def test_expected_guard_hooks_present():
    """The four PreToolUse guard scripts exist (catch an accidental deletion/rename)."""
    hooks_dir = CLAUDE_DIR / "hooks"
    missing = [name for name in _EXPECTED_HOOKS if not (hooks_dir / name).exists()]
    assert not missing, (
        f"Guard hook script(s) missing from .claude/hooks/: {missing} — "
        "a removed/renamed guard fails open (forensic class #11)."
    )


def test_no_dangling_claude_symlinks():
    """No symlink under .claude/ (hook / agent / skill) dangles — a symlink that
    silently resolves to nothing is a fail-open guard absence (class #11). Walk
    without following symlinks so symlinked skill dirs are checked, not descended."""
    dangling: list[str] = []
    for dirpath, dirnames, filenames in os.walk(CLAUDE_DIR, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink() and not p.exists():
                dangling.append(p.relative_to(REPO_ROOT).as_posix())
    assert not dangling, (
        "Dangling symlink(s) under .claude/ — a guard/agent/skill that resolves to "
        "nothing (fail-open, forensic class #11):\n" + "\n".join(f"  {d}" for d in dangling)
    )
