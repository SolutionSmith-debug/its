"""Mechanically enforce the CLAUDE.md state-write rule (was review-only).

CLAUDE.md "What NOT to do":
  "Don't call Path.write_text or Path.write_bytes directly on any file under
   ~/its/state/. All state-file writes must go through shared/state_io.py helpers
   (atomic_write_json / atomic_write_text, wrapped in with_path_lock for
   read-modify-write triples on shared files). Direct write_text skips the
   atomic-write + lock guarantees and is rejected at review."

That rule lived only at review — forensic class #10 (non-atomic / in-place state
mutation: double external send, corruption races, audit-record clobber; worst
instance: an approved WSR emailed to the customer every 15 minutes on a transient
post-send write failure). This promotes it to a CI gate, mirroring the
capability-gating allowlist idiom in tests/test_capability_gating.py: NO module on
the runtime surface (shared/ + safety_reports/) may call .write_text()/.write_bytes()
directly unless it is on STATE_WRITE_ALLOWLIST WITH a one-line rationale confirming
it is the sanctioned atomic-writer OR writes a NON-state path (a liveness marker,
a git-source artifact).

Run with: pytest -q tests/test_state_write_discipline.py
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime surface walked — the same untrusted-content / daemon surface as the F02
# network allowlist (shared/ helpers + the workstream packages). scripts/ is
# operator-run and writes only ~/its/.watchdog markers (not state). progress_reports
# joined at P2 (kept in sync with the F02 WALKED_ROOTS in test_capability_gating.py).
WALKED_ROOTS: tuple[str, ...] = ("shared", "safety_reports", "progress_reports")

_WRITE_METHODS: frozenset[str] = frozenset({"write_text", "write_bytes"})

# Every direct .write_text()/.write_bytes() on the walked surface must be justified
# here. The reviewer adding an entry MUST confirm it either (a) IS the sanctioned
# atomic-writer, or (b) writes a path that is NOT under ~/its/state/.
STATE_WRITE_ALLOWLIST: dict[str, str] = {
    "shared/state_io.py":
        "the canonical atomic-write helper — temp-file write before os.replace IS its job",
    "safety_reports/compile_now_poll.py":
        "writes the ~/its/.watchdog/<job>.last_run liveness marker (NOT ~/its/state/)",
    "safety_reports/send_poll_core.py":
        "writes the ~/its/.watchdog/<job>.last_run liveness marker (NOT ~/its/state/) — "
        "the watchdog marker write moved here from weekly_send_poll.py in P1c",
    "safety_reports/generate_core.py":
        "writes the ~/its/.watchdog/<job>.last_run liveness marker (NOT ~/its/state/) — "
        "the watchdog marker write is parameterized here (P4); both weekly compiles use it "
        "(moved out of weekly_generate.py, which no longer writes directly)",
    "safety_reports/portal_poll.py":
        "writes the ~/its/.watchdog liveness marker (NOT ~/its/state/)",
    "safety_reports/publish_daemon.py":
        "writes the git-source forms catalog/definitions under safety_portal/forms/ (NOT ~/its/state/)",
}


def _has_direct_write(path: Path) -> bool:
    """True iff the module statically calls ``<expr>.write_text(...)`` or
    ``<expr>.write_bytes(...)`` (an attribute-method call — the exact surface the
    CLAUDE.md rule forbids on state files)."""
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _WRITE_METHODS
        for node in ast.walk(tree)
    )


def _modules_with_direct_write() -> set[str]:
    out: set[str] = set()
    for root in WALKED_ROOTS:
        root_dir = REPO_ROOT / root
        if not root_dir.is_dir():
            continue
        for path in sorted(root_dir.rglob("*.py")):
            if _has_direct_write(path):
                out.add(path.relative_to(REPO_ROOT).as_posix())
    return out


def test_no_unallowlisted_direct_state_write():
    """No direct .write_text()/.write_bytes() on the runtime surface outside the
    allowlist (CLAUDE.md state-write rule, forensic class #10)."""
    violations = sorted(_modules_with_direct_write() - set(STATE_WRITE_ALLOWLIST))
    assert not violations, (
        "Direct .write_text()/.write_bytes() outside STATE_WRITE_ALLOWLIST "
        "(CLAUDE.md state-write rule, forensic class #10):\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nRoute every ~/its/state/ write through shared/state_io.py "
        "(atomic_write_json / atomic_write_text under with_path_lock). If this writes a "
        "NON-state path (a liveness marker, a git-source artifact), add the file to "
        "STATE_WRITE_ALLOWLIST WITH a one-line rationale confirming it is not a state write."
    )


def test_state_write_allowlist_has_no_stale_entries():
    """Every allowlisted file must still exist AND still do a direct write — a stale
    entry rubber-stamps nothing, so prune it (keeps the allowlist an honest list)."""
    writers = _modules_with_direct_write()
    for rel in sorted(STATE_WRITE_ALLOWLIST):
        assert (REPO_ROOT / rel).exists(), (
            f"STATE_WRITE_ALLOWLIST names a missing file: {rel} — prune it"
        )
        assert rel in writers, (
            f"{rel} no longer calls .write_text()/.write_bytes() — "
            "stale STATE_WRITE_ALLOWLIST entry, prune it"
        )
