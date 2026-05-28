"""Deterministic unit tests for the doc-reconciliation-auditor PreToolUse backstop.

The doc-reconciliation-auditor is PROPOSE-ONLY; this hook structurally refuses any
Edit/Write/MultiEdit/NotebookEdit and any mutating Bash command, while allowing
read-only inspection (cat/grep, git log|diff|show, gh ... view|list, and the
mechanical checker). Mirrors tests/test_hook_block_codeql_dismiss.py (#93).
"""

import json
import subprocess
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / ".claude" / "hooks" / "block-doc-reconciliation-write.sh"
)


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _bash(cmd: str) -> subprocess.CompletedProcess:
    return _run({"tool_name": "Bash", "tool_input": {"command": cmd}})


def _write(tool: str, path: str) -> subprocess.CompletedProcess:
    return _run({"tool_name": tool, "tool_input": {"file_path": path}})


def test_hook_exists():
    assert HOOK.exists(), f"hook missing at {HOOK}"


# --- write tools are always refused ------------------------------------------


def test_blocks_edit():
    r = _write("Edit", "docs/tech_debt.md")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "BLOCKED" in r.stderr


def test_blocks_write():
    assert _write("Write", "docs/doctrine_manifest.yaml").returncode == 2


def test_blocks_multiedit():
    assert _write("MultiEdit", "CLAUDE.md").returncode == 2


def test_blocks_notebook_edit():
    r = _run({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "x.ipynb"}})
    assert r.returncode == 2


# --- mutating Bash is refused ------------------------------------------------


def test_blocks_git_commit():
    assert _bash("git commit -m drift").returncode == 2


def test_blocks_git_push():
    assert _bash("git push origin main").returncode == 2


def test_blocks_git_add():
    assert _bash("git add -A").returncode == 2


def test_blocks_gh_pr_create():
    assert _bash("gh pr create --title x --body y").returncode == 2


def test_blocks_gh_api_patch():
    assert _bash("gh api -X PATCH repos/o/r/x -f a=b").returncode == 2


def test_blocks_sed_inplace():
    assert _bash("sed -i 's/v13/v14/' CLAUDE.md").returncode == 2


def test_blocks_redirect_to_file():
    assert _bash("echo drift > docs/report.md").returncode == 2


def test_blocks_append_redirect_to_file():
    assert _bash("cat x >> docs/tech_debt.md").returncode == 2


# --- read-only Bash is allowed -----------------------------------------------


def test_allows_cat():
    assert _bash("cat docs/doctrine_manifest.yaml").returncode == 0


def test_allows_grep():
    assert _bash('grep -rn "Op Stds" CLAUDE.md').returncode == 0


def test_allows_git_log():
    assert _bash("git log --oneline -5").returncode == 0


def test_allows_git_diff():
    assert _bash("git diff origin/main").returncode == 0


def test_allows_git_show():
    assert _bash("git show origin/main:CLAUDE.md").returncode == 0


def test_allows_gh_pr_view():
    assert _bash("gh pr view 101 --json state").returncode == 0


def test_allows_gh_run_list():
    assert _bash("gh run list --branch main --limit 5").returncode == 0


def test_allows_mechanical_script():
    assert _bash("python -m scripts.check_doctrine_drift --json").returncode == 0


def test_allows_devnull_redirect():
    assert _bash("python -m scripts.check_doctrine_drift >/dev/null 2>&1").returncode == 0


def test_does_not_match_substring_confirm():
    # "confirm" contains "rm" but is not the `rm` mutation verb.
    assert _bash("echo confirm the value").returncode == 0
