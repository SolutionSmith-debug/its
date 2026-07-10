"""Class-C secret rotation (WS2 D1-3) — WRITE-ONLY, registry-bound.

Hard rules (enforced here + proven by tests):
- Never reads a secret back (no get_secret anywhere in this module).
- Never logs / echoes / persists a value except to its destination (Keychain
  and/or the Worker via wrangler over stdin — never argv). Every message names
  ONLY the credential key + kind; the audit records "<KEY> rotated by <op>",
  never the value.
- Only credentials in registry.SECRETS are rotatable; an unlisted key is refused.
- The Box refresh token is `box_guided`: single-consumer + rotates on every use,
  so it is NEVER pasted here — the dashboard guides the quiesce→setup_box_oauth
  →smoke flow instead.
"""
from __future__ import annotations

import importlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from operator_dashboard.act.registry import SecretEntry, get_secret_entry
from operator_dashboard.config import ITS_HOME

_SAFETY_PORTAL = ITS_HOME / "safety_portal"
_WRANGLER_TIMEOUT = 120


@dataclass
class RotateOutcome:
    kind: str  # rotated | refused | error | guided  (also CSS class + test assertion)
    message: str
    key: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def rotate_secret(key: str, new_value: str, operator: str) -> RotateOutcome:
    entry = get_secret_entry(key)
    if entry is None:
        # registry-bound: an unlisted credential is refused (no free-form store)
        return RotateOutcome("refused", f"{key} is not a rotatable credential", key)
    if entry.kind == "box_guided":
        return RotateOutcome(
            "guided",
            "the Box refresh token rotates ONLY via the guided quiesce → setup_box_oauth.py → smoke "
            "flow; no value is accepted here",
            key,
        )
    if not new_value:
        return RotateOutcome("refused", "no value provided", key)
    if entry.kind == "keychain":
        return _rotate_keychain(entry, new_value, operator)
    if entry.kind == "worker":
        return _rotate_worker(entry, new_value, operator)
    return RotateOutcome("refused", f"unknown credential kind {entry.kind!r}", key)


def _rotate_keychain(entry: SecretEntry, new_value: str, operator: str) -> RotateOutcome:
    kc = _load("shared.keychain")
    try:
        kc.set_secret(entry.key, new_value)  # write-through; -U overwrite IS the rotation
    except Exception as exc:  # NEVER include the value in the message
        return RotateOutcome("error", f"keychain write failed: {type(exc).__name__}", entry.key)
    _audit_rotation(operator, entry.key, "keychain")
    return RotateOutcome("rotated", f"{entry.key} rotated (Keychain)", entry.key)


def _rotate_worker(entry: SecretEntry, new_value: str, operator: str) -> RotateOutcome:
    # `wrangler secret put <NAME>` with the value on STDIN (never argv), cwd
    # safety_portal/, then dual-write the byte-equal Keychain mirror from the
    # SAME value (a Worker secret without its mirror fail-closes the Mac daemon).
    if not _SAFETY_PORTAL.is_dir():
        return RotateOutcome("error", "safety_portal/ not found for wrangler", entry.key)
    try:
        proc = subprocess.run(
            ["npx", "wrangler", "secret", "put", entry.key],
            cwd=str(_SAFETY_PORTAL),
            input=new_value,
            text=True,
            capture_output=True,
            timeout=_WRANGLER_TIMEOUT,
            check=False,
        )
    except Exception as exc:
        return RotateOutcome("error", f"wrangler invocation failed: {type(exc).__name__}", entry.key)
    if proc.returncode != 0:
        # do NOT surface stderr verbatim — name the key + exit code only
        return RotateOutcome("error", f"wrangler secret put failed (exit {proc.returncode})", entry.key)
    if entry.worker_mirror:
        kc = _load("shared.keychain")
        try:
            kc.set_secret(entry.worker_mirror, new_value)
        except Exception as exc:
            # Worker+mirror are now DESYNCED — record it durably (distinct from a
            # clean rotation) so it's never a silent fail-closed-daemon surprise.
            _audit_desync(entry.key, entry.worker_mirror, operator, exc)
            return RotateOutcome(
                "error",
                f"Worker secret set but Keychain mirror {entry.worker_mirror} write failed "
                f"({type(exc).__name__}) — rotate the mirror manually to avoid a fail-closed daemon",
                entry.key,
            )
    _audit_rotation(operator, entry.key, "worker")
    return RotateOutcome("rotated", f"{entry.key} rotated (Worker + Keychain mirror {entry.worker_mirror})", entry.key)


def _audit_rotation(operator: str, key: str, kind: str) -> None:
    # WARN => durable ITS_Errors row, no page. NAMES the key only — never the value.
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"secret ROTATED: {key} ({kind}) by {operator} at {ts}",
            error_code="config_secret_rotated",
            alert=False,
        )
    except Exception:
        pass


def _audit_desync(key: str, mirror: str, operator: str, exc: Exception) -> None:
    # Distinct from the success audit — a half-completed rotation (Worker set,
    # mirror not), so the operator knows to fix the mirror. NAMES only, no value.
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"secret DESYNCED: {key} rotated on Worker but Keychain mirror {mirror} write FAILED "
            f"({type(exc).__name__}) by {operator} at {ts} — rotate mirror manually",
            error_code="config_secret_mirror_desync",
            alert=False,
        )
    except Exception:
        pass
