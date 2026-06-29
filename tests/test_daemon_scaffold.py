"""Daemon-scaffold robustness — launchd / daemon-environment footgun class (#15).

Two mechanical pins, both GREEN on the current fleet (they lock in the post-#327 /
post-#241 state and fail loudly on the next divergence):

  A. Every INTERVAL (StartInterval) launchd plist sets RunAtLoad=true. On the
     single-host architecture a reboot must NOT leave an interval daemon dead until
     a manual reload (#327: all interval daemons silently dead after reboot).
     Calendar (StartCalendarInterval) jobs are exempt — they fire on their schedule
     and their reboot/crash catch-up is a SEPARATE concern (watchdog Check I for the
     weekly_generate Friday-crash gap). template.plist is excluded (it is a scaffold,
     not a loaded job).

  B. No daemon module spawns a subprocess with a bare "python"/"python3" as argv[0].
     launchd's minimal PATH has no `python` (macOS ships only python3; the real
     interpreter is ~/its/.venv/bin/python), so a bare interpreter raised the #241
     half-committed-publish footgun. Daemons must use [sys.executable, ...].

This test deliberately does NOT mechanize the other two members of the class (the
calendar-job external catch-up detector, the empty-commit guard) — those are
behavioural, covered by their own daemon tests, and out of scope for a scaffold pin.

Run with: pytest -q tests/test_daemon_scaffold.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHD_DIR = REPO_ROOT / "scripts" / "launchd"
DAEMON_ROOT = REPO_ROOT / "safety_reports"

_SUBPROCESS_FUNCS: frozenset[str] = frozenset(
    {"run", "Popen", "call", "check_call", "check_output"}
)
_BARE_PYTHON: frozenset[str] = frozenset({"python", "python3"})


# --------------------------------------------------------------------------
# A — RunAtLoad on every interval daemon plist
#
# The in-repo plists are TEMPLATES (install.sh substitutes placeholders like
# `<integer>__POLL_INTERVAL_SECONDS__</integer>` at install time), so they are not
# valid plists to plistlib. Detect the two keys textually — exactly what install.sh
# leaves verbatim — instead of parsing.
# --------------------------------------------------------------------------
_RUN_AT_LOAD_RE = re.compile(r"<key>RunAtLoad</key>\s*<(true|false)\s*/>", re.IGNORECASE)


def _is_interval(text: str) -> bool:
    # Exact tag — `<key>StartInterval</key>` does NOT match `<key>StartCalendarInterval</key>`.
    return "<key>StartInterval</key>" in text


def _run_at_load(text: str) -> bool | None:
    m = _RUN_AT_LOAD_RE.search(text)
    return None if m is None else m.group(1).lower() == "true"


def _interval_plists() -> list[Path]:
    out: list[Path] = []
    for p in sorted(LAUNCHD_DIR.glob("*.plist")):
        if p.name == "template.plist":
            continue
        if _is_interval(p.read_text()):
            out.append(p)
    return out


def test_interval_daemons_run_at_load():
    """RunAtLoad=true on every interval (StartInterval) launchd plist (#327)."""
    plists = _interval_plists()
    assert plists, "no interval plists found under scripts/launchd/ — test wiring broke"
    bad = [p.name for p in plists if _run_at_load(p.read_text()) is not True]
    assert not bad, (
        "Interval (StartInterval) launchd plist(s) missing RunAtLoad=true:\n"
        + "\n".join(f"  {n}" for n in bad)
        + "\n\nOn the single-host architecture a reboot leaves these daemons dead "
        "until a manual reload (forensic class #15 / #327). Set "
        "<key>RunAtLoad</key><true/>."
    )


# --------------------------------------------------------------------------
# B — daemon subprocesses use sys.executable, never a bare python interpreter
# --------------------------------------------------------------------------
def _first_arg_is_bare_python(call: ast.Call) -> bool:
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        return isinstance(head, ast.Constant) and head.value in _BARE_PYTHON
    return False


def _spawns_bare_python(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SUBPROCESS_FUNCS
            and _first_arg_is_bare_python(node)
        ):
            return True
    return False


def test_no_daemon_spawns_bare_python_subprocess():
    """Daemon subprocess interpreters use sys.executable, never a bare 'python'/'python3'
    argv[0] (#241 half-committed publish — launchd's PATH has no `python`)."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted(DAEMON_ROOT.rglob("*.py"))
        if _spawns_bare_python(p)
    ]
    assert not offenders, (
        "Daemon module(s) spawn a subprocess with a bare 'python'/'python3' argv[0]:\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nlaunchd's minimal PATH has no `python` (macOS ships python3; the real "
        "interpreter is ~/its/.venv/bin/python). Use [sys.executable, ...] (forensic "
        "class #15 / #241)."
    )
