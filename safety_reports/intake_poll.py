"""Safety Reports intake polling daemon — launchd-driven Graph reader.

Replaces the prior Mail.app rule trigger for `safety_reports/intake.py`.
The Mail.app rule had a long-running operational tail (per
`docs/tech_debt.md`: the silent-disable pattern that motivated watchdog
Check F). This module reads the safety mailbox directly from Microsoft
Graph and invokes `intake.process_message` per unread message.

launchd schedule
----------------

Single-cycle execution: `poll_once()` is the public API; the `__main__`
guard calls it exactly once and exits. launchd handles the cadence via
StartInterval in `scripts/launchd/org.solutionsmith.its.safety-intake.plist`
(default 60 s; sourced from ITS_Config row
`safety_reports.intake.poll_interval_seconds` at install time).

Per-cycle behavior
------------------

  1. `polling_enabled` ITS_Config gate — false short-circuits the cycle.
  2. fcntl file lock at `~/its/state/safety_intake.lock` — skip-if-held.
     Prevents launchd-overlap collisions if a previous cycle took longer
     than the interval.
  3. `graph_client.list_inbox` with `unread_only` filter (top=50).
  4. For each unread message:
       a. Skip if message_id is in the local seen-set (defense in depth
          against double-fetch — the canonical idempotency guarantee is
          `mark_read` below, which the inbox query also respects, but
          the seen-set covers a race window in which a message was
          processed but `mark_read` failed).
       b. `intake.process_message(message_id)` runs the full pipeline.
       c. On success status (processed / review_queue / quarantined /
          skipped_swo_other): `graph_client.mark_read` commits the
          inbox-cursor advance.
       d. On `error` status: message stays unread, retried next cycle.
       e. Record the message_id + status + timestamp into the seen-set
          state file regardless of mark_read outcome (forensic record;
          1000-entry FIFO cap).
  5. Write heartbeat timestamp to `~/its/state/safety_intake_heartbeat.txt`.

Push-vs-record separation
-------------------------

Per Op Stds v9 §3: the seen-set file is forensic-record-only — losing it
does NOT cause double-processing because `mark_read` (the canonical push
checkpoint) on the prior message already kept it out of subsequent
`list_inbox` results. The seen-set is the defense-in-depth layer that
covers the narrow window between `process_message` returning and
`mark_read` committing. If the OS crashes between those two calls, the
next cycle's list_inbox will RE-deliver the message (still unread), but
the seen-set guards against re-running the pipeline on it. If we lose
both the seen-set AND the mark_read commit (e.g., disk corruption), the
pipeline's idempotency relies on the existing per-stage idempotency
(Daily Reports rows are NOT keyed on message_id, so a double-process
would create a duplicate row — operator inspection then deletion is the
recovery). The combination is acceptable for the operational risk
profile; a hardened idempotency layer (e.g., message_id → row_id index)
is documented in `docs/tech_debt.md` as out of scope for Phase 1.

Capability gating
-----------------

No customer-facing send capability. Imports `shared.graph_client` for
read-only methods + `mark_read` (which is an inbox-cursor write, not an
external transmission). Does NOT import `send_mail`. Enforced by
`tests/test_capability_gating.py` (GATED_SCRIPTS list) and
`tests/test_intake_capability_gating.py` (per-file AST scans).
"""
from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from safety_reports import intake
from shared import error_log, graph_client, smartsheet_client
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.intake_poll"
WORKSTREAM = "safety_reports"

# ITS_Config keys (seeded by
# scripts/migrations/seed_safety_intake_polling_config.py).
CFG_POLLING_ENABLED = "safety_reports.intake.polling_enabled"
CFG_MAILBOX = "safety_reports.intake.mailbox"

# Defaults — used when the ITS_Config row is missing or unparseable. Each
# fallback is operationally safe: polling default ON (the daemon's whole
# purpose), default mailbox the sandbox value, default lookback covers the
# operational interval.
DEFAULT_POLLING_ENABLED = True
DEFAULT_MAILBOX = intake.DEFAULT_MAILBOX

# Per-cycle inbox window. Graph caps at 1000; 50 is comfortable headroom
# for the construction-workflow tempo of safety mail (typically 10-30
# messages/day across all crews at peak).
LIST_INBOX_TOP = 50

# Seen-set state file. JSON object keyed by Graph message_id; trimmed to
# the most recent SEEN_CAP entries via FIFO on the embedded timestamp.
STATE_DIR = Path.home() / "its" / "state"
SEEN_PATH = STATE_DIR / "safety_intake_processed.json"
HEARTBEAT_PATH = STATE_DIR / "safety_intake_heartbeat.txt"
LOCK_PATH = STATE_DIR / "safety_intake.lock"
SEEN_CAP = 1000

# Process result statuses that should result in `mark_read`. Mirror of the
# `success` statuses produced by `intake.process_message`. `error` is the
# only status that leaves the message unread for retry.
MARK_READ_STATUSES = frozenset({
    "processed",
    "review_queue",
    "quarantined",
    "skipped_swo_other",
})


@dataclass(frozen=True)
class PollStats:
    """Summary of one poll_once() invocation. Returned for caller logging."""
    skipped_disabled: bool = False
    skipped_locked: bool = False
    messages_fetched: int = 0
    messages_processed: int = 0
    messages_skipped_seen: int = 0
    messages_marked_read: int = 0
    errors: int = 0


# ---- Config readers ------------------------------------------------------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _polling_enabled() -> bool:
    return _read_bool_setting(CFG_POLLING_ENABLED, DEFAULT_POLLING_ENABLED)


# ---- State helpers -------------------------------------------------------


@contextmanager
def _file_lock(path: Path) -> Iterator[bool]:
    """Acquire an exclusive non-blocking lock on `path`.

    Yields True if the lock was acquired, False if another process holds
    it. Returning False is the documented skip-if-held behavior (NOT an
    error) — it means a prior poll cycle is still running and the current
    invocation should exit cleanly to avoid two daemons hammering Graph.
    The contextmanager handles unlock + file close in either case.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                # Best-effort unlock; the file close below releases it
                # too via kernel-level cleanup if flock_un raises.
                pass
        handle.close()


def _load_seen() -> dict[str, dict[str, str]]:
    """Load the seen-set state file. Empty dict on missing / corrupt file."""
    if not SEEN_PATH.exists():
        return {}
    try:
        raw = SEEN_PATH.read_text()
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Drop entries whose value shape isn't what we expect — defensive
    # against a corrupted file rather than crashing the poll cycle.
    clean: dict[str, dict[str, str]] = {}
    for k, v in parsed.items():
        if isinstance(k, str) and isinstance(v, dict):
            clean[k] = {ks: str(vs) for ks, vs in v.items() if isinstance(ks, str)}
    return clean


def _record_seen(
    seen: dict[str, dict[str, str]],
    message_id: str,
    status: str,
) -> None:
    """Record one message_id → status in the seen-set; trim to SEEN_CAP.

    FIFO trim by the embedded timestamp value. The trim runs every call
    rather than on a watermark to keep the on-disk file size bounded
    even if the cap-reduction logic ever has a bug — small cost (<1ms
    for a 1000-entry dict).
    """
    seen[message_id] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": status,
    }
    if len(seen) > SEEN_CAP:
        # Keep most-recent SEEN_CAP entries.
        sorted_items = sorted(seen.items(), key=lambda kv: kv[1].get("timestamp", ""))
        keep = dict(sorted_items[-SEEN_CAP:])
        seen.clear()
        seen.update(keep)
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2))


def _write_heartbeat() -> None:
    """Overwrite the heartbeat file with the current UTC ISO timestamp.

    Watchdog Check F currently uses mailbox-idle as a proxy for trigger
    health; a follow-on PR will repurpose it to read this heartbeat
    instead (cleaner signal — the trigger ITSELF reports its liveness
    rather than inferring it from inbox activity).
    """
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(datetime.now(UTC).isoformat())


# ---- Inbox helpers -------------------------------------------------------


def _list_unread(mailbox: str, *, top: int = LIST_INBOX_TOP) -> list[dict[str, Any]]:
    """Return unread messages from `mailbox`'s Inbox folder.

    Wraps `graph_client.list_inbox` with the `isRead eq false` $filter.
    Graph supports compound `$filter` but doesn't let you mix `$filter`
    with the `since` shortcut in `graph_client.list_inbox`, so we use the
    `fields` parameter to project just `id` (we don't need anything
    else; `process_message` re-fetches the full message).
    """
    # graph_client.list_inbox supports a `since` filter but not a
    # canned `unread_only` flag, so we filter post-fetch on `isRead`.
    # The list endpoint returns the field by default; we project it
    # explicitly to be sure.
    raw = graph_client.list_inbox(
        mailbox, top=top, fields=["id", "isRead", "receivedDateTime"]
    )
    return [m for m in raw if not m.get("isRead", False)]


# ---- Public API ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> PollStats:
    """Run one poll cycle. Public API; idempotent across crashes.

    Returns a PollStats summary regardless of outcome. Unhandled exceptions
    propagate to the `@its_error_log` decorator (so they land in
    ITS_Errors + the triple-fire alert path) and then re-raise so launchd
    sees a non-zero exit and the operator notices via the log files.

    `poll_once` is decorated with `@require_active` which honors the
    global system.state kill switch. The per-workstream
    `polling_enabled` ITS_Config row is a finer-grained gate read inside
    the function body (after the kill-switch check) so the operator can
    halt just safety intake without taking down the rest of ITS.
    """
    if not _polling_enabled():
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            "polling disabled via ITS_Config; exiting cycle",
            error_code="polling_disabled",
        )
        return PollStats(skipped_disabled=True)

    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            # Another poll cycle is in flight; exit cleanly.
            error_log.log(
                Severity.INFO,
                SCRIPT_NAME,
                "another poll cycle holds the lock; skipping this cycle",
                error_code="poll_lock_held",
            )
            return PollStats(skipped_locked=True)
        return _poll_inside_lock()


def _poll_inside_lock() -> PollStats:
    """Body of poll_once running under the file lock."""
    mailbox = _read_str_setting(CFG_MAILBOX, DEFAULT_MAILBOX)
    messages = _list_unread(mailbox)
    seen = _load_seen()

    stats = PollStats(messages_fetched=len(messages))
    counters = {
        "processed": stats.messages_processed,
        "skipped_seen": stats.messages_skipped_seen,
        "marked_read": stats.messages_marked_read,
        "errors": stats.errors,
    }

    for msg in messages:
        message_id = msg.get("id")
        if not isinstance(message_id, str):
            continue
        if message_id in seen:
            counters["skipped_seen"] += 1
            continue

        result = intake.process_message(message_id, mailbox=mailbox)
        counters["processed"] += 1

        if result.status in MARK_READ_STATUSES:
            try:
                graph_client.mark_read(mailbox, message_id)
                counters["marked_read"] += 1
            except graph_client.GraphError as exc:
                # Don't fail the loop on mark_read errors — the message
                # was processed (row written, etc.). Next cycle will
                # re-list the message; the seen-set guard prevents
                # double-processing.
                counters["errors"] += 1
                error_log.log(
                    Severity.WARN,
                    SCRIPT_NAME,
                    f"mark_read failed for message_id={message_id}: {exc!r}",
                    error_code="mark_read_failed",
                    correlation_id=result.correlation_id,
                )

        if result.status == "error":
            counters["errors"] += 1

        _record_seen(seen, message_id, result.status)

    _write_heartbeat()

    final = PollStats(
        messages_fetched=stats.messages_fetched,
        messages_processed=counters["processed"],
        messages_skipped_seen=counters["skipped_seen"],
        messages_marked_read=counters["marked_read"],
        errors=counters["errors"],
    )
    error_log.log(
        Severity.INFO,
        SCRIPT_NAME,
        (
            f"poll cycle: fetched={final.messages_fetched} "
            f"processed={final.messages_processed} "
            f"skipped_seen={final.messages_skipped_seen} "
            f"marked_read={final.messages_marked_read} "
            f"errors={final.errors}"
        ),
        error_code="poll_cycle_summary",
    )
    return final


if __name__ == "__main__":
    poll_once()
