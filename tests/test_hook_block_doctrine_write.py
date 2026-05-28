"""Deterministic unit tests for the session-close-maintainer PreToolUse backstop.

The maintainer edits living docs (info-gap, memory-archive, tech-debt) but
must not write version-gated doctrine without operator approval. This hook
refuses any Edit/Write whose file_path is under a doctrine/ directory.
"""

import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block-doctrine-write.sh"


def _run(file_path: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_input": {"file_path": file_path}})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
    )


def test_hook_exists():
    assert HOOK.exists(), f"hook missing at {HOOK}"


def test_blocks_doctrine_write():
    r = _run("/Users/sethsmith/its-blueprint/doctrine/operational-standards.md")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "BLOCKED" in r.stderr


def test_allows_references_write():
    r = _run("/Users/sethsmith/its-blueprint/references/claude-code-info-gap.md")
    assert r.returncode == 0, r.stdout + r.stderr


def test_allows_memory_archive_write():
    r = _run("/Users/sethsmith/its-blueprint/references/memory-archive.md")
    assert r.returncode == 0, r.stdout + r.stderr


def test_allows_tech_debt_write():
    r = _run("/Users/sethsmith/its/docs/tech_debt.md")
    assert r.returncode == 0, r.stdout + r.stderr
