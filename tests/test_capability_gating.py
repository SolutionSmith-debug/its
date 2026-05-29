"""Capability-gating tests for Foundation Mission v8 Invariant 1 (External Send Gate).

The architectural invariant: scripts that call the Anthropic API to generate customer-facing
content MUST NOT have the capability to send externally. Scripts that send externally MUST
NOT have an AI step. Enforced by static import inspection.

A successful prompt injection at the AI layer cannot cause external transmission, because
the AI is in a different process from the transmitter.

How to extend this test:
- When a new generation script lands, add it to GATED_SCRIPTS.
- When a new send script lands, add it to SEND_SCRIPTS.

The Safety Reports two-process refactor (`weekly_generate.py` + `weekly_send.py`) has landed,
so both lists are populated. Adding new entries is the entire enforcement mechanism for new
workstreams.

Run with: pytest -q tests/test_capability_gating.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Generation scripts: must NOT import any send capability.
# Each entry: (relative path from repo root, list of forbidden import substrings)
#
# Note on `graph_client` granularity: intake.py and intake_poll.py legitimately
# need `graph_client` READ methods (`get_message`, `list_attachments`,
# `download_attachment`, `mark_read`) to ingest mail from the safety mailbox.
# `mark_read` is an inbox-side write (isRead=True) but is NOT an external
# transmission — the External Send Gate covers customer-facing email, not
# inbox-cursor management. So `graph_client` (broad substring) is allowed for
# the intake pair; `send_mail` (narrow substring) remains forbidden. The
# per-substring AST check below catches `shared.graph_client.send_mail` via
# the "send_mail" needle even when `shared.graph_client` is imported. Future
# generation scripts that do NOT need Graph reads can use a stricter list
# that includes `graph_client` (see the commented templates).
GATED_SCRIPTS: list[tuple[str, list[str]]] = [
    (
        "safety_reports/intake.py",
        ["send_mail", "resend", "smtplib", "email.mime"],
    ),
    (
        "safety_reports/intake_poll.py",
        ["send_mail", "resend", "smtplib", "email.mime"],
    ),
    (
        # weekly_generate does NOT need Graph reads (it only reads Smartsheet
        # rows, not mail) so `graph_client` is forbidden in addition to the
        # narrower send substrings — stricter list than the intake pair.
        "safety_reports/weekly_generate.py",
        ["graph_client", "send_mail", "resend", "smtplib", "email.mime"],
    ),
    # ("po_materials/standard_rfq_generate.py", ["graph_client", "send_mail"]),
    # ("po_materials/racking_module_rfq_generate.py", ["graph_client", "send_mail"]),
    # ("subcontracts/subcontract_generate.py", ["graph_client", "send_mail"]),
]

# Send scripts: must NOT import any AI capability.
SEND_SCRIPTS: list[tuple[str, list[str]]] = [
    (
        "safety_reports/weekly_send.py",
        ["anthropic_client", "anthropic"],
    ),
    (
        # weekly_send_poll imports safety_reports.weekly_send which
        # transitively brings in graph_client.send_mail — that's the
        # intended send capability for the workstream. The AST gate
        # checks THIS file's imports specifically; anthropic / anthropic_client
        # must not appear at all.
        "safety_reports/weekly_send_poll.py",
        ["anthropic_client", "anthropic"],
    ),
    # ("po_materials/rfq_send.py", ["anthropic_client", "anthropic"]),
    # ("subcontracts/subcontract_send.py", ["anthropic_client", "anthropic"]),
]


def _imports_in(path: Path) -> set[str]:
    """Return the set of imported module names + from-imports in a Python file."""
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                for alias in node.names:
                    imports.add(f"{node.module}.{alias.name}")
    return imports


@pytest.mark.parametrize("rel_path,forbidden", GATED_SCRIPTS)
def test_generation_script_does_not_import_send(rel_path: str, forbidden: list[str]):
    """Generation scripts must not import any send capability (Invariant 1)."""
    path = REPO_ROOT / rel_path
    assert path.exists(), f"missing: {rel_path}"
    imports = _imports_in(path)
    for needle in forbidden:
        for imp in imports:
            assert needle not in imp, (
                f"{rel_path} imports {imp!r}, which contains forbidden {needle!r}. "
                "External Send Gate violation — generation scripts cannot have send capability."
            )


@pytest.mark.parametrize("rel_path,forbidden", SEND_SCRIPTS)
def test_send_script_does_not_import_ai(rel_path: str, forbidden: list[str]):
    """Send scripts must not import any AI capability (Invariant 1)."""
    path = REPO_ROOT / rel_path
    assert path.exists(), f"missing: {rel_path}"
    imports = _imports_in(path)
    for needle in forbidden:
        for imp in imports:
            assert needle not in imp, (
                f"{rel_path} imports {imp!r}, which contains forbidden {needle!r}. "
                "External Send Gate violation — send scripts cannot have AI capability."
            )


def test_lists_documented():
    """Sanity check — both lists exist and are typed correctly."""
    assert isinstance(GATED_SCRIPTS, list)
    assert isinstance(SEND_SCRIPTS, list)


# =========================================================================
# F02 — repo-wide network-capability allowlist (additive defensive layer)
# =========================================================================
#
# The GATED_SCRIPTS / SEND_SCRIPTS checks above are the LANDED Invariant-1
# enforcement: per-script, forbidden-substring, narrow. This block ADDS an
# orthogonal second layer (audit finding F02) and does NOT modify them.
#
# It inverts the question: instead of "these NAMED scripts must not import
# send capability," it asserts "NO module on the untrusted-content surface
# may import a network-egress or process-spawn library UNLESS it is on an
# explicit allowlist." The point is that a module which should never touch
# the network CANNOT acquire that capability undetected — a future
# generation script that quietly `import requests` to exfiltrate fails this
# check at CI time, before it can ship.
#
# ---- Walk scope (operator decision 2026-05-29) --------------------------
#
# Walked roots = the Invariant-1 untrusted-content surface:
#   shared/          — the helper layer every workstream imports.
#   safety_reports/  — the only workstream package today (the AI generation
#                      scripts that process untrusted inbound content).
# Future workstream dirs (po_materials/, subcontracts/, …) get appended to
# WALKED_ROOTS as they land — same per-workstream extension discipline as
# GATED_SCRIPTS above.
#
# Deliberately NOT walked (each with reason — surfaced, not silently picked):
#   scripts/ (incl. scripts/migrations/) — operator-run launchd/CLI entry
#       points, one-shot config seeders, OAuth setup, and smoke tests. These
#       legitimately call REST directly (Smartsheet/Box seeding via
#       `requests`, the Graph smoke's `subprocess`, the OAuth catcher's
#       `http.server`/`urllib`) and are NOT in the untrusted-content path.
#       Walking them would force ~8 operational allowlist entries that
#       dilute the security signal and churn CI on every new migration.
#   smartsheet_migration/, box_migration/ — one-shot data-migration
#       utilities, not runtime workstream code.
#   tests/, docs/, prompts/, schemas/ — non-source / no runtime egress.
#
# ---- Allowlist membership (each entry justified — no entry without one) --
#   shared/graph_client.py      — Microsoft Graph REST (intake mail + sends).
#   shared/resend_client.py     — Resend REST (the canonical CRITICAL push leg).
#   shared/smartsheet_client.py — Smartsheet REST (SDK + direct REST helpers).
#   shared/heartbeat_client.py  — Healthchecks.io outbound beacon (F16 / PR #114).
#   shared/keychain.py          — `subprocess` for the macOS `security` CLI.
#                                 NOT network egress; the secret-store boundary.
#                                 Included because `subprocess` is a needle and
#                                 keychain is the one legitimate non-*_client
#                                 subprocess user on the walked surface.
NETWORK_LIB_ALLOWLIST: frozenset[str] = frozenset({
    "shared/graph_client.py",
    "shared/resend_client.py",
    "shared/smartsheet_client.py",
    "shared/heartbeat_client.py",
    "shared/keychain.py",
})

# Import needles that constitute network-egress or process-spawn capability.
# Matched on DOTTED-SEGMENT boundaries (not bare substring) — see
# `_import_matches_needle` — so `socket` does NOT collide with `socketserver`,
# and `http.client` does NOT collide with `http.server`. `urllib.request`
# (network) is gated but `urllib.parse` (pure string work) is not.
NETWORK_NEEDLES: frozenset[str] = frozenset({
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "socket",
    "subprocess",
    "http.client",
})

# Source roots walked by the network allowlist. See the scope rationale above.
WALKED_ROOTS: tuple[str, ...] = ("shared", "safety_reports")


def _import_matches_needle(imported: str, needle: str) -> bool:
    """True iff `imported`'s leading dotted segments equal `needle`'s segments.

    Segment-boundary match, NOT substring:
      _import_matches_needle("socket", "socket")          -> True
      _import_matches_needle("socketserver", "socket")    -> False
      _import_matches_needle("requests.adapters", "requests") -> True
      _import_matches_needle("http.server", "http.client") -> False
      _import_matches_needle("urllib.request", "urllib.request") -> True
      _import_matches_needle("urllib.parse", "urllib.request")   -> False
    """
    imp_segments = imported.split(".")
    needle_segments = needle.split(".")
    return imp_segments[: len(needle_segments)] == needle_segments


def _network_needles_in(path: Path) -> list[str]:
    """Return the sorted network/subprocess needles a file directly imports."""
    imports = _imports_in(path)
    hits = {
        needle
        for imp in imports
        for needle in NETWORK_NEEDLES
        if _import_matches_needle(imp, needle)
    }
    return sorted(hits)


def test_no_unallowlisted_network_imports():
    """No module on the untrusted-content surface imports a network/subprocess
    library unless it is on NETWORK_LIB_ALLOWLIST (audit F02).

    This is the additive defensive inversion of the External Send Gate. It
    is orthogonal to GATED_SCRIPTS/SEND_SCRIPTS and must pass independently.
    """
    violations: list[tuple[str, list[str]]] = []
    for root in WALKED_ROOTS:
        root_dir = REPO_ROOT / root
        if not root_dir.is_dir():
            continue
        for path in sorted(root_dir.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            needles = _network_needles_in(path)
            if needles and rel not in NETWORK_LIB_ALLOWLIST:
                violations.append((rel, needles))

    assert not violations, (
        "Network/subprocess import outside the allowlist (audit F02):\n"
        + "\n".join(f"  {rel} imports {needles}" for rel, needles in violations)
        + "\n\nA module under shared/ or safety_reports/ acquired network or "
        "process-spawn capability. Either (a) remove the import and route the "
        "call through an audited shared/*_client.py, or (b) if the capability "
        "is genuinely legitimate, add the file to NETWORK_LIB_ALLOWLIST WITH a "
        "one-line rationale comment (see the allowlist block in this file)."
    )


def test_network_allowlist_has_no_stale_entries():
    """Every allowlisted file must still exist AND still import a needle.

    A stale entry (file deleted, or no longer imports a network/subprocess
    lib) is dead allowlist surface that rubber-stamps nothing — prune it so
    the allowlist stays an honest, scrutinized list.
    """
    for rel in sorted(NETWORK_LIB_ALLOWLIST):
        path = REPO_ROOT / rel
        assert path.exists(), (
            f"allowlisted file missing: {rel} — prune it from NETWORK_LIB_ALLOWLIST"
        )
        assert _network_needles_in(path), (
            f"allowlisted file {rel} imports no network/subprocess library — "
            "stale allowlist entry, prune it"
        )
