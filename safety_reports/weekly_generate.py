"""Safety Reports weekly generate — draft one WPR per active Forefront project.

Generation half of the External Send Gate two-process model (Foundation
Mission v8 Invariant 1). Runs Friday 14:00 local time via launchd
`StartCalendarInterval`. Reads the week's Daily Reports + Weekly Rollup
rows for each Forefront project, calls Anthropic to draft a Weekly
Project Report (WPR), writes one draft row per (Job, Week) to
`WPR_Pending_Review` with `Approved for Send` unchecked. **Zero send
capability.**

The send half is `safety_reports/weekly_send.py` (R3 Session 3, not yet
created). The send script reads only approved rows and has no
Anthropic capability — capability gating enforced by static AST scan
in `tests/test_capability_gating.py`.

Pipeline (one pass per project per run)
---------------------------------------

  1. Resolve target week = Monday-of-current-week (calendar-week,
     holiday-unaware — `shared.scheduling.monday_of_week`). The run day
     itself can shift via `shift_gen_date` if needed, but the target
     week is the current calendar week regardless of holidays.
  2. Reviewer-chain pre-check via `shared.scheduling.resolve_chain`. If
     the chain is empty (all configured reviewers are out per
     ITS_Time_Off), log CRITICAL via error_log, fire a Resend alert
     (push, subject to alert_dedupe), and exit cleanly. Generating
     drafts no one can approve burns Anthropic credit and obscures the
     real problem.
  3. For each (folder_id, project_name) in
     `shared.sheet_ids.PROJECT_NAME_BY_FOLDER_ID`:
     a. `ensure_current_week_folder` to get the daily/rollup sheet IDs
        (idempotent — creates the scaffold if missing).
     b. Read Daily Reports + Weekly Rollup rows filtered to the week.
     c. Check `WPR_Pending_Review` for an existing (Job, Week) row.
        - Approved → log INFO, skip this project (never overwrite an
          approved draft).
        - Unapproved → mark for replacement (update_rows later).
        - Missing → add_rows later.
     d. ZERO_DATA_WEEK branch: if both Daily Reports + Weekly Rollup
        are empty, skip the Anthropic call and write a placeholder
        draft with `[ZERO_DATA_WEEK]` notes tag. Reviewer decides
        whether to hold, send-as-such, or follow up with field PM.
        Confidence = 1.0 (we are certain there is no data).
     e. Standard branch: wrap rows in `untrusted_content` tags, build
        messages, call Anthropic with the `generate_weekly_project_report`
        tool, project the tool_use response, check anomalies, route
        low-confidence + security triggers to ITS_Review_Queue in
        parallel with writing the WPR row.
     f. Recipients lookup from ITS_Config
        (`safety_reports.recipients.<slug>` — slug =
        `project_name.lower().replace(" ", "_")`). Missing recipients
        do NOT block the write — row lands with `Recipients=""` and a
        `[NO_RECIPIENTS]` notes tag; `weekly_send.py` refuses to send
        rows with empty Recipients by design.
     g. Write or update the row. On add: `Approved for Send=false`,
        `Send Status=PENDING`, `Late Send=false`. On update: only
        replace `Draft Body`, `Recipients`, `Notes` — never touch the
        approval columns.
  4. Write watchdog marker file (`safety_weekly_generate.last_run`
     under `~/its/.watchdog/`).
  5. Return structured summary dict (consumed by `@its_error_log` and
     logged via INFO).

Capability gating
-----------------

Per Foundation Mission v8 Invariant 1. This module:

  - Does NOT import `shared.graph_client` (broad substring forbidden).
  - Does NOT import `send_mail`, `resend` (read-side resend_client for
    operator alert is fine; the AST gate checks for `resend` as a
    module name and `shared.resend_client.send_alert` is the OUT
    direction). Wait — that's actually subtle. Let me clarify: the
    brief's strict list `["graph_client", "send_mail", "resend",
    "smtplib", "email.mime"]` forbids the `resend` substring. So
    THIS module cannot call `resend_client.send_alert` directly. The
    empty-chain alert goes through `shared.error_log._alert_critical`
    instead, which is the canonical CRITICAL surface and is allowed
    because it's the alert-routing layer (not a generation-script
    transmission to customers).
  - `tests/test_capability_gating.py` AST-scans this file to enforce
    the contract.

Adversarial Input Handling
--------------------------

Per Foundation Mission v8 Invariant 2:

  - Daily Reports + Weekly Rollup rows wrapped in
    `<untrusted_content source="…">` tags via
    `shared.untrusted_content.wrap`.
  - System prompt includes
    `shared.untrusted_content.system_boilerplate()` so Claude is
    explicitly told to treat tagged content as data.
  - Output structure enforced via Anthropic tool-use; the model must
    emit JSON matching `schemas/safety_weekly_generate.json` or the
    call surfaces a malformed-output route to ITS_Review_Queue.
  - `shared.anomaly_logger.check()` runs on the tool_use input; the
    model also self-reports anomalies in its `anomaly_flags` array.
    Either signal routes to ITS_Review_Queue with
    `Reason=security-trigger`. The WPR row writes regardless — the
    reviewer makes the disposition call.

Idempotency
-----------

Per (Job, Week):
  - If an unapproved row exists, it is replaced (Draft Body / Recipients
    / Notes updated; approval columns untouched).
  - If an approved row exists, the project is skipped with an INFO log
    — silent overwrites of approved drafts would defeat the human-in-
    loop gate.
  - If no row exists, a new row is added.

A second run on the same Friday (manual rerun + scheduled run) lands
the second draft on top of the first — Push-vs-Record Separation per
Op Stds v11 §3.1. WPR_Pending_Review is a RECORD surface; every run
that finds an unapproved or missing row writes/updates. Reviewers see
the most recent draft.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from safety_reports.week_folder import ensure_current_week_folder
from shared import (
    anomaly_logger,
    anthropic_client,
    error_log,
    review_queue,
    scheduling,
    sheet_ids,
    smartsheet_client,
    untrusted_content,
)
from shared.defaults import FOREFRONT_CUSTOMER_NAME
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

SCRIPT_NAME = "safety_reports.weekly_generate"
WORKSTREAM = "safety_reports"

# ITS_Config keys (no migration script seeds these in this PR — fall back
# to defaults until the operator chooses to override via the Smartsheet UI).
CFG_CONFIDENCE_THRESHOLD = "safety_reports.weekly_generate.confidence_threshold"
CFG_MODEL = "safety_reports.weekly_generate.model"
CFG_RECIPIENTS_PREFIX = "safety_reports.recipients."

DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_MODEL = anthropic_client.DEFAULT_MODEL  # claude-sonnet-4-6

# Watchdog Check C marker. Replicated inline from scripts/watchdog.py per
# preservation-over-refactor (Op Stds v11 §14) — cross-module marker
# helpers are a candidate for shared/runner.py at the second polling
# consumer ship, not this PR.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_weekly_generate"

# Single-shot retry window for transient Smartsheet 404s during per-project
# scaffold creates. PR #51 evidence + the 2026-05-22 smoke observations
# both indicate the SDK in-process staleness clears within ~1 second; a
# 500 ms pause + single retry is sufficient. Bounded to one retry per
# project so the run latency stays bounded — multi-retry loops would
# delay non-transient errors (auth, permissions) by minutes without
# fixing the underlying SDK staleness. Durable fix is the SDK→REST
# swap tracked in docs/tech_debt.md.
RETRY_SLEEP_SECONDS = 0.5

# Anthropic tool wiring. The schema lives in
# schemas/safety_weekly_generate.json; we mirror its name + input_schema
# here for the tool_use call.
GENERATE_WPR_TOOL_NAME = "generate_weekly_project_report"

# Prompt + sample file locations relative to repo root (the package root
# is the parent of `safety_reports/`).
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _PACKAGE_ROOT / "prompts" / "safety_weekly_generate.md"
_SCHEMA_PATH = _PACKAGE_ROOT / "schemas" / "safety_weekly_generate.json"

# Expected value of the `version` key in the schema file above.
# `_load_tool_schema` rejects any other value (see its docstring for the
# Op Stds §42 rationale). Bump this in lockstep with the schema's `version`
# field — schemas/README.md: "bump the version, update the consuming script
# in the same commit."
_EXPECTED_SCHEMA_VERSION = "0.1.0"

# Sentinel that the model uses for non-derivable sections — exposed here so
# tests can assert the prompt preserves the bracket placeholder convention
# without re-parsing the prompt file.
REVIEWER_FILL_SENTINEL = "[REVIEWER TO FILL]"


# ---- Data classes --------------------------------------------------------


@dataclass(frozen=True)
class ProjectInputs:
    """Inputs gathered for one (project, week) pair before the model call."""
    project_name: str
    folder_id: int
    week_start: date
    week_end: date
    daily_reports_sheet_id: int
    weekly_rollup_sheet_id: int
    daily_reports_rows: list[dict[str, Any]]
    weekly_rollup_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class GenerationResult:
    """Projected tool_use output. Mirrors the schema in safety_weekly_generate.json."""
    draft_body: str
    confidence: float
    incident_counts: dict[str, int]
    safety_topics_covered: list[str]
    narrative_summary: str
    anomaly_flags: list[str]
    data_completeness: str  # "complete" | "partial" | "zero_data"


@dataclass
class RunSummary:
    """Per-run counters returned from main(); logged via @its_error_log.

    `drafts_failed` counts projects where the pipeline raised after exhausting
    the single retry — increments alongside `drafts_written` when a
    GENERATION_FAILED placeholder was written, or alone when the placeholder
    write itself failed. `retries_attempted` increments each time the
    transient-404 retry path fires (regardless of whether the retry then
    succeeds or fails). Together they give the operator + watchdog a clean
    signal without needing to grep ITS_Errors.
    """
    projects_processed: int = 0
    drafts_written: int = 0
    drafts_skipped_approved: int = 0
    drafts_zero_data: int = 0
    drafts_failed: int = 0
    retries_attempted: int = 0
    review_queue_entries: int = 0
    anthropic_calls: int = 0
    errors_per_project: dict[str, str] = field(default_factory=dict)


# ---- Config readers (replicated from intake.py per preservation) ---------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except SmartsheetNotFoundError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_float_setting(key: str, fallback: float) -> float:
    raw = _read_str_setting(key, str(fallback))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _read_recipients_for(project_name: str) -> list[str]:
    """Read the recipients JSON list for one project from ITS_Config.

    Key shape: `safety_reports.recipients.<slug>` where slug =
    `project_name.lower().replace(" ", "_")` (e.g., "Bradley 1" →
    "bradley_1"). Returns [] when the row is missing or malformed —
    caller is responsible for tagging the WPR row with [NO_RECIPIENTS]
    and writing a Review Queue entry.
    """
    slug = project_name.lower().replace(" ", "_")
    key = f"{CFG_RECIPIENTS_PREFIX}{slug}"
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except SmartsheetNotFoundError:
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(e) for e in parsed if isinstance(e, str) and e]


# ---- Prompt + schema loaders --------------------------------------------


def _load_prompt() -> str:
    """Return the safety_weekly_generate prompt body (strips YAML front-matter).

    The YAML front-matter is metadata (version, model, notes) — not
    instructions for the model. Strip it so the prompt body sent to
    Anthropic is just the system text.
    """
    text = _PROMPT_PATH.read_text()
    if text.startswith("---"):
        # Strip YAML front-matter delimited by leading and trailing "---" lines.
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _load_tool_schema() -> dict[str, Any]:
    """Return the Anthropic tool-use schema for generate_weekly_project_report.

    Loads `schemas/safety_weekly_generate.json` and projects to the
    Anthropic tools=[...] shape: {name, description, input_schema}.

    Op Stds §42 rationale — why the `version` key is validated, fail-LOUD:
    a wrong/stale schema file would otherwise load silently and produce a
    structurally-wrong WPR draft with no signal. This guard is deliberately
    fail-LOUD (not fail-open like the kill switch): it runs at generation
    time, well before any External Send Gate row is written, so a drifted
    contract surfaces as a hard error here rather than as a malformed draft
    a reviewer might approve. The damage ceiling of raising (no draft this
    cycle, operator alerted via @its_error_log) is strictly better than the
    alternative (bad output entering the review queue). This is the gate
    schemas/README.md already mandates ("scripts reject responses on
    schema-version mismatch") — previously documented but unimplemented.
    """
    payload = json.loads(_SCHEMA_PATH.read_text())
    version = payload.get("version")
    if version != _EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"{_SCHEMA_PATH.name}: schema version {version!r} does not match "
            f"expected {_EXPECTED_SCHEMA_VERSION!r}. Refusing to build the "
            f"{GENERATE_WPR_TOOL_NAME} tool against a drifted schema contract "
            f"— bump _EXPECTED_SCHEMA_VERSION in lockstep when the schema is "
            f"intentionally revised (see schemas/README.md)."
        )
    return {
        "name": payload["name"],
        "description": payload["description"],
        "input_schema": payload["input_schema"],
    }


# ---- WPR_Pending_Review row helpers --------------------------------------


def _resolve_existing_wpr_row(
    project_name: str,
    week_start: date,
) -> tuple[int | None, bool]:
    """Find any existing WPR row for (Job, Week). Returns (row_id, is_approved).

    `(None, False)` when no row exists. When a row exists, the second tuple
    element is True iff `Approved for Send` is truthy. Caller uses this to
    decide between add_rows / update_rows / skip.
    """
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_WPR_PENDING_REVIEW,
        filters={"Job": project_name, "Week": week_start.isoformat()},
    )
    if not rows:
        return None, False
    row = rows[0]
    return row["_row_id"], bool(row.get("Approved for Send"))


def _compose_notes(tags: list[str], generation_ts: datetime) -> str:
    """Build the WPR row's Notes string from tags + a generation timestamp.

    Tags use the canonical `[TAG]` bracket convention so a Smartsheet
    operator filter expression can locate flagged drafts. The trailing
    timestamp is always present so a re-run replaces the prior generation
    stamp in place (callers update_rows the whole Notes field).
    """
    tag_str = " ".join(tags) if tags else ""
    ts = generation_ts.replace(microsecond=0).isoformat()
    if tag_str:
        return f"{tag_str} generated={ts}"
    return f"generated={ts}"


def _write_or_update_wpr_row(
    *,
    project_name: str,
    week_start: date,
    draft_body: str,
    recipients: list[str],
    notes: str,
    existing_row_id: int | None,
) -> None:
    """Add or update the WPR_Pending_Review row for (Job, Week).

    `Recipients` is stored as a JSON-encoded list (same shape the send
    script will consume). On update we touch only Draft Body / Recipients
    / Notes — Approved for Send / Approved By / Approved At / Sent At
    / Send Status / Late Send are preserved so an in-flight approval
    cycle does not lose state when a re-generation lands.
    """
    recipients_payload = json.dumps(recipients)
    if existing_row_id is not None:
        smartsheet_client.update_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            [
                {
                    "_row_id": existing_row_id,
                    "Draft Body": draft_body,
                    "Recipients": recipients_payload,
                    "Notes": notes,
                }
            ],
        )
        return
    smartsheet_client.add_rows(
        sheet_ids.SHEET_WPR_PENDING_REVIEW,
        [
            {
                "Customer": FOREFRONT_CUSTOMER_NAME,
                "Job": project_name,
                "Week": week_start.isoformat(),
                "Draft Body": draft_body,
                "Recipients": recipients_payload,
                "Approved for Send": False,
                "Send Status": "PENDING",
                "Late Send": False,
                "Notes": notes,
            }
        ],
    )


# ---- Empty-chain alert ---------------------------------------------------


def _alert_empty_reviewer_chain(correlation_id: str) -> None:
    """Surface CRITICAL when resolve_chain returns no surviving reviewer.

    Drafts no one can approve are wasted Anthropic spend and obscure the
    real issue. Route through `error_log.log` Severity.CRITICAL — the
    triple-fire path fires Smartsheet (record) + Resend (push to operator
    inbox) + Sentry (structured event). Resend leg is alert_dedupe-aware
    per Op Stds v11 §3.1 push-vs-record separation; the same Friday
    firing twice in a 60-min window will not double-page the operator.

    No `resend_client` import here — the AST capability gate forbids
    `resend` as a substring in this module's imports, and the canonical
    CRITICAL alert path goes through `error_log._alert_critical` anyway.
    """
    error_log.log(
        Severity.CRITICAL,
        SCRIPT_NAME,
        "empty reviewer chain — no one can approve drafts; aborting run",
        error_code="weekly_generate.empty_reviewer_chain",
        correlation_id=correlation_id,
    )


# ---- Anthropic call + tool_use projection --------------------------------


def _build_messages(
    inputs: ProjectInputs,
) -> list[dict[str, Any]]:
    """Build the user message with untrusted_content-wrapped row data.

    The system prompt is built separately by `_build_system` and includes
    the prompt body + Invariant 2 boilerplate. Here we just pack the
    project context + row dumps into one user-turn content payload.
    """
    daily_serialized = json.dumps(inputs.daily_reports_rows, default=str, indent=2)
    rollup_serialized = json.dumps(inputs.weekly_rollup_rows, default=str, indent=2)

    user_text = (
        f"project_name: {inputs.project_name}\n"
        f"week_start: {inputs.week_start.isoformat()}\n"
        f"week_end: {inputs.week_end.isoformat()}\n\n"
        f"{untrusted_content.wrap(daily_serialized, source='daily-reports-rows')}\n\n"
        f"{untrusted_content.wrap(rollup_serialized, source='weekly-rollup-rows')}"
    )
    return [{"role": "user", "content": user_text}]


def _build_system() -> str:
    """Prepend Invariant 2 system boilerplate to the prompt body."""
    return f"{untrusted_content.system_boilerplate()}\n\n{_load_prompt()}"


def _project_tool_use(response: Any) -> GenerationResult | None:
    """Project the Anthropic response's first matching tool_use block.

    Returns None when no tool_use block matches GENERATE_WPR_TOOL_NAME — the
    caller routes to ITS_Review_Queue with Reason=structured-output-edge
    and skips the WPR row write for that project.
    """
    for block in getattr(response, "content", []) or []:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == GENERATE_WPR_TOOL_NAME
        ):
            payload = getattr(block, "input", None)
            if not isinstance(payload, dict):
                return None
            try:
                return GenerationResult(
                    draft_body=str(payload["draft_body"]),
                    confidence=float(payload["confidence"]),
                    incident_counts={
                        k: int(v) for k, v in payload["incident_counts"].items()
                    },
                    safety_topics_covered=[
                        str(s) for s in payload["safety_topics_covered"]
                    ],
                    narrative_summary=str(payload["narrative_summary"]),
                    anomaly_flags=[str(s) for s in payload["anomaly_flags"]],
                    data_completeness=str(payload["data_completeness"]),
                )
            except (KeyError, TypeError, ValueError):
                return None
    return None


# ---- Per-project handlers ------------------------------------------------


def _handle_zero_data_week(
    *,
    inputs: ProjectInputs,
    existing_row_id: int | None,
    recipients: list[str],
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Write a placeholder draft for a project with zero rows for the week.

    Silent skip would look like daemon failure — the operator can't tell
    whether the field PM skipped reporting or weekly_generate crashed.
    Writing the placeholder + the ZERO_DATA_WEEK tag makes the disposition
    decision explicit and gives the reviewer a row to act on.
    """
    placeholder = (
        "No reports submitted for this week — reviewer to confirm whether to "
        "send-as-such, hold, or follow up with field PM."
    )
    tags: list[str] = ["[ZERO_DATA_WEEK]"]
    if not recipients:
        tags.append("[NO_RECIPIENTS]")
        _add_review_queue_entry(
            inputs.project_name,
            inputs.week_start,
            review_queue.ReviewReason.OTHER,
            f"weekly_generate: missing recipients config for {inputs.project_name}",
            correlation_id=correlation_id,
        )
        summary.review_queue_entries += 1
    notes = _compose_notes(tags, datetime.now(UTC))
    _write_or_update_wpr_row(
        project_name=inputs.project_name,
        week_start=inputs.week_start,
        draft_body=placeholder,
        recipients=recipients,
        notes=notes,
        existing_row_id=existing_row_id,
    )
    summary.drafts_written += 1
    summary.drafts_zero_data += 1


def _handle_standard_project(
    *,
    inputs: ProjectInputs,
    existing_row_id: int | None,
    recipients: list[str],
    threshold: float,
    model: str,
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Call Anthropic, project the result, write the row + parallel ITS_Review_Queue rows."""
    messages = _build_messages(inputs)
    system = _build_system()
    tool_schema = _load_tool_schema()

    response = anthropic_client.call(
        messages=messages,
        system=system,
        model=model,
        max_tokens=4096,
        tools=[tool_schema],
    )
    summary.anthropic_calls += 1

    result = _project_tool_use(response)
    if result is None:
        # Model did not emit a valid tool_use block. Route to Review Queue
        # and skip the WPR row write — no draft to show.
        _add_review_queue_entry(
            inputs.project_name,
            inputs.week_start,
            review_queue.ReviewReason.STRUCTURED_OUTPUT_EDGE,
            f"weekly_generate: model emitted no valid tool_use for {inputs.project_name}",
            correlation_id=correlation_id,
        )
        summary.review_queue_entries += 1
        return

    tags: list[str] = []

    # Confidence gate.
    if result.confidence < threshold:
        tags.append(f"[LOW_CONFIDENCE: {result.confidence:.2f}]")
        _add_review_queue_entry(
            inputs.project_name,
            inputs.week_start,
            review_queue.ReviewReason.LOW_CONFIDENCE_EXTRACTION,
            (
                f"weekly_generate: confidence {result.confidence:.2f} < "
                f"threshold {threshold:.2f} for {inputs.project_name}"
            ),
            correlation_id=correlation_id,
        )
        summary.review_queue_entries += 1

    # Anomaly check (logger on bounded subset + model self-report).
    anomaly_signals = _check_anomalies(result, result.anomaly_flags)
    if anomaly_signals.security_trigger:
        tags.append("[SECURITY_TRIGGER]")
        _add_review_queue_entry(
            inputs.project_name,
            inputs.week_start,
            review_queue.ReviewReason.SECURITY_TRIGGER,
            (
                f"weekly_generate: security trigger for {inputs.project_name}: "
                f"{', '.join(anomaly_signals.reasons)}"
            ),
            correlation_id=correlation_id,
            security_flag=True,
        )
        summary.review_queue_entries += 1

    # Recipients edge case.
    if not recipients:
        tags.append("[NO_RECIPIENTS]")
        _add_review_queue_entry(
            inputs.project_name,
            inputs.week_start,
            review_queue.ReviewReason.OTHER,
            f"weekly_generate: missing recipients config for {inputs.project_name}",
            correlation_id=correlation_id,
        )
        summary.review_queue_entries += 1

    notes = _compose_notes(tags, datetime.now(UTC))
    _write_or_update_wpr_row(
        project_name=inputs.project_name,
        week_start=inputs.week_start,
        draft_body=result.draft_body,
        recipients=recipients,
        notes=notes,
        existing_row_id=existing_row_id,
    )
    summary.drafts_written += 1


# ---- Anomaly aggregation -------------------------------------------------


@dataclass(frozen=True)
class _AnomalySignals:
    security_trigger: bool
    reasons: list[str]


_SECURITY_SENTINELS = frozenset(
    {"apparent_injection_attempt", "prompt_injection", "system_prompt_leak"}
)


def _check_anomalies(
    result: GenerationResult,
    self_reported_flags: list[str],
) -> _AnomalySignals:
    """Hybrid anomaly check tailored for generation output (vs. extraction).

    Generation outputs include naturally-long fields (`draft_body`,
    `narrative_summary`) — paragraphs of WPR text routinely exceed the
    anomaly_logger 2 KB per-field ceiling. Passing the whole result would
    false-positive on every legitimate draft, dulling the signal entirely.

    Approach: pass only the bounded short fields (`incident_counts`,
    `safety_topics_covered`, `data_completeness`) to anomaly_logger, and
    rely on the model's self-reported `anomaly_flags` array for catching
    injection signals in the longer text. Anthropic's structured-output
    enforcement is the third line of defense — the model cannot invent
    new fields the schema does not allow, so suspicious-field-name
    patterns cannot fire on the JSON output shape.
    """
    bounded_subset: dict[str, Any] = {
        "incident_counts": result.incident_counts,
        "safety_topics_covered": result.safety_topics_covered,
        "data_completeness": result.data_completeness,
    }
    try:
        logger_flags = anomaly_logger.check(bounded_subset)
    except Exception as exc:  # noqa: BLE001 — defensive: anomaly_logger must never crash the pipeline
        logger_flags = [f"anomaly_logger_error: {exc!r}"]

    self_security_flags = [
        flag for flag in self_reported_flags if flag in _SECURITY_SENTINELS
    ]
    reasons = list(logger_flags) + self_security_flags
    return _AnomalySignals(
        security_trigger=bool(reasons),
        reasons=reasons,
    )


# ---- ITS_Review_Queue helper --------------------------------------------


def _add_review_queue_entry(
    project_name: str,
    week_start: date,
    reason: review_queue.ReviewReason,
    summary_text: str,
    *,
    correlation_id: str,
    security_flag: bool = False,
) -> None:
    """Add one ITS_Review_Queue row for a weekly_generate edge case."""
    try:
        review_queue.add(
            workstream=WORKSTREAM,
            summary=summary_text,
            payload={
                "project": project_name,
                "week_start": week_start.isoformat(),
                "correlation_id": correlation_id,
            },
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=reason,
            severity=Severity.WARN,
            source_file=f"{project_name}-{week_start.isoformat()}",
            security_flag=security_flag,
        )
    except SmartsheetError as exc:
        # Review Queue write failures are loud — they remove the operator's
        # forensic surface for the edge case. Log ERROR and continue; the
        # WPR row write is still the primary value of this pipeline.
        error_log.log(
            Severity.ERROR,
            SCRIPT_NAME,
            f"review_queue.add failed for {project_name}/{week_start}: {exc!r}",
            error_code="review_queue_add_failed",
            correlation_id=correlation_id,
        )


# ---- Watchdog marker (replicated inline per preservation) ----------------


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this run.

    Pattern mirrors `scripts.watchdog.write_last_run_marker` exactly so
    Check C's existing reader logic (looks for `<slug>.last_run` files
    under WATCHDOG_MARKER_DIR) picks this up unchanged.
    """
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:
        # Same fail-soft as scripts.watchdog.write_last_run_marker — a
        # missing marker is operationally less severe than a failed run,
        # and the run itself has already completed by the time this fires.
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- Smoke helper (consumed by scripts/smoke_test_weekly_generate.py) ----


def iter_active_projects() -> list[tuple[int, str]]:
    """Return the list of (folder_id, project_name) pairs the run iterates.

    Stable view of `sheet_ids.PROJECT_NAME_BY_FOLDER_ID` ordered by
    folder_id for reproducible smoke output. Pure read — no Smartsheet
    calls, no Anthropic calls, safe for dry-run.
    """
    return sorted(sheet_ids.PROJECT_NAME_BY_FOLDER_ID.items())


# ---- Per-project work unit + retry wrapper -------------------------------


def _process_one_project(
    *,
    folder_id: int,
    project_name: str,
    week_start: date,
    week_end: date,
    threshold: float,
    model: str,
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Process one (project, week). Pure extract from `_run_pipeline`'s loop body.

    Raises whatever the underlying calls raise — the caller (the per-project
    fence) handles retry semantics and writes a GENERATION_FAILED placeholder
    when this re-raises after retry exhaustion.

    Increments `summary.projects_processed` on every success path
    (real draft, ZERO_DATA placeholder, or approved-skip). Does NOT
    increment on raise — the fence-level placeholder write increments
    `drafts_failed` instead.
    """
    scaffold = ensure_current_week_folder(project_name, week_start)
    daily_rows = smartsheet_client.get_rows(scaffold.daily_reports_sheet_id)
    # Filter rows to the target week. Report Date arrives as either
    # an ISO string or a datetime.date — handle both shapes; rows
    # with un-parseable dates fall through and are excluded (the
    # standard branch's "complete vs partial" check still flags it).
    daily_in_week = [
        row for row in daily_rows if _row_in_week(row, week_start, week_end)
    ]
    rollup_rows = smartsheet_client.get_rows(scaffold.weekly_rollup_sheet_id)

    existing_row_id, is_approved = _resolve_existing_wpr_row(
        project_name, week_start
    )
    if is_approved:
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            f"skipping {project_name} week {week_start}: existing row is approved",
            error_code="weekly_generate.skipped_approved",
            correlation_id=correlation_id,
        )
        summary.drafts_skipped_approved += 1
        summary.projects_processed += 1
        return

    recipients = _read_recipients_for(project_name)
    inputs = ProjectInputs(
        project_name=project_name,
        folder_id=folder_id,
        week_start=week_start,
        week_end=week_end,
        daily_reports_sheet_id=scaffold.daily_reports_sheet_id,
        weekly_rollup_sheet_id=scaffold.weekly_rollup_sheet_id,
        daily_reports_rows=daily_in_week,
        weekly_rollup_rows=rollup_rows,
    )

    if not daily_in_week and not rollup_rows:
        _handle_zero_data_week(
            inputs=inputs,
            existing_row_id=existing_row_id,
            recipients=recipients,
            correlation_id=correlation_id,
            summary=summary,
        )
    else:
        _handle_standard_project(
            inputs=inputs,
            existing_row_id=existing_row_id,
            recipients=recipients,
            threshold=threshold,
            model=model,
            correlation_id=correlation_id,
            summary=summary,
        )
    summary.projects_processed += 1


def _process_with_retry(
    *,
    folder_id: int,
    project_name: str,
    week_start: date,
    week_end: date,
    threshold: float,
    model: str,
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Wrap `_process_one_project` with single-shot retry on transient 404.

    Per Op Stds v11 §30 (SDK-vs-Live discipline): post-create reads against
    just-scaffolded Smartsheet folders can hit transient 404s from SDK
    in-process caching staleness. PR #51 fixed an analogous pattern via
    SDK→REST swap; this wrapper is the lighter-weight mitigation. If the
    retry succeeds the project gets a real draft; if both attempts 404
    the caller writes a GENERATION_FAILED placeholder.

    The retry is narrow: only `SmartsheetNotFoundError` (the specific
    transient class). Auth failures, permission errors, schema mismatches
    are NOT transient and retrying them only delays the error log.
    """
    try:
        _process_one_project(
            folder_id=folder_id,
            project_name=project_name,
            week_start=week_start,
            week_end=week_end,
            threshold=threshold,
            model=model,
            correlation_id=correlation_id,
            summary=summary,
        )
    except SmartsheetNotFoundError as first_exc:
        summary.retries_attempted += 1
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            (
                f"transient 404 on {project_name}; retrying in "
                f"{RETRY_SLEEP_SECONDS}s (first_error={first_exc!r})"
            ),
            error_code="weekly_generate.transient_404_retry",
            correlation_id=correlation_id,
        )
        time.sleep(RETRY_SLEEP_SECONDS)
        # Re-raises on second failure; caller (the per-project fence)
        # handles by writing a GENERATION_FAILED placeholder.
        _process_one_project(
            folder_id=folder_id,
            project_name=project_name,
            week_start=week_start,
            week_end=week_end,
            threshold=threshold,
            model=model,
            correlation_id=correlation_id,
            summary=summary,
        )


def _write_generation_failed_placeholder(
    *,
    project_name: str,
    week_start: date,
    error_class: str,
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Surface a failed project on the reviewer's WPR_Pending_Review queue.

    Closes the silent-gap risk in the per-project fence: without this
    placeholder, a failed project would have NO row at all on Teala's
    queue, looking indistinguishable from "project deliberately skipped."

    One-row-per-(Job, Week) contract is preserved by reusing
    `_resolve_existing_wpr_row`:
      - Approved row exists → log INFO, do NOT touch (manual operator
        review of the existing approval + ITS_Errors trail is the right
        disposition).
      - Unapproved row exists → update Notes only (append
        `[GENERATION_FAILED: ...]` to the existing tags); leave Draft Body
        intact so the operator still sees the prior real draft alongside
        the failure indicator.
      - No existing row → write a placeholder with Recipients deliberately
        empty so `weekly_send` refuses to transmit it. The Draft Body
        spells out the manual-rerun command and the ITS_Errors
        correlation_id.

    Increments `summary.drafts_failed` on every code path (the project
    failed regardless of whether the placeholder physically wrote).
    Increments `summary.drafts_written` only when a row was added or
    updated (mirroring the existing counter semantics elsewhere).
    """
    summary.drafts_failed += 1

    # Best-effort lookup of existing row. The 404 that triggered us was on
    # the per-project scaffold path, not on WPR_Pending_Review, so this
    # read is unlikely to hit the same staleness; still, wrap defensively
    # so a lookup failure doesn't prevent the placeholder fallback.
    try:
        rows = smartsheet_client.get_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            filters={"Job": project_name, "Week": week_start.isoformat()},
        )
    except Exception:  # noqa: BLE001 — fall through to add_rows below
        rows = []

    existing_row = rows[0] if rows else None
    failure_tag = f"[GENERATION_FAILED: {error_class}]"
    generation_ts = datetime.now(UTC)

    if existing_row is not None and bool(existing_row.get("Approved for Send")):
        # Do NOT touch an approved row. Log + leave the operator to inspect.
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            (
                f"{project_name} week {week_start}: existing approved row "
                f"present, NOT writing GENERATION_FAILED placeholder"
            ),
            error_code="weekly_generate.placeholder_skipped_approved",
            correlation_id=correlation_id,
        )
        return

    if existing_row is not None:
        # Unapproved row — append the failure tag to existing Notes; do
        # NOT touch Draft Body (preserve the prior real draft).
        existing_notes = existing_row.get("Notes") or ""
        new_notes = (
            f"{existing_notes} {failure_tag}".strip()
            if existing_notes
            else _compose_notes([failure_tag], generation_ts)
        )
        smartsheet_client.update_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            [
                {
                    "_row_id": existing_row["_row_id"],
                    "Notes": new_notes,
                }
            ],
        )
        summary.drafts_written += 1
        return

    # No existing row — write a fresh placeholder.
    draft_body = (
        f"[GENERATION_FAILED — pipeline error processing {project_name} for "
        f"week of {week_start.isoformat()}. See ITS_Errors row with "
        f"correlation_id={correlation_id}. Reviewer action: hold and rerun "
        f"manually via `python -m safety_reports.weekly_generate "
        f"--week-start {week_start.isoformat()}`, or delete this row if the "
        f"project should be skipped for this week.]"
    )
    notes = _compose_notes([failure_tag], generation_ts)
    smartsheet_client.add_rows(
        sheet_ids.SHEET_WPR_PENDING_REVIEW,
        [
            {
                "Customer": FOREFRONT_CUSTOMER_NAME,
                "Job": project_name,
                "Week": week_start.isoformat(),
                "Draft Body": draft_body,
                "Recipients": "",
                "Approved for Send": False,
                "Send Status": "PENDING",
                "Late Send": False,
                "Notes": notes,
            }
        ],
    )
    summary.drafts_written += 1


def _safe_write_placeholder(
    *,
    project_name: str,
    week_start: date,
    error_class: str,
    correlation_id: str,
    summary: RunSummary,
) -> None:
    """Call `_write_generation_failed_placeholder` with a defensive outer catch.

    If the placeholder write ITSELF fails (e.g. WPR_Pending_Review is
    unreachable), log + continue. We do NOT want a placeholder-write
    failure to tear down the remaining-project loop — every silent gap
    we close is worth at least the row we tried to write. The placeholder
    failure is itself a forensic surface (ITS_Errors row) so the
    operator still has a trace.
    """
    try:
        _write_generation_failed_placeholder(
            project_name=project_name,
            week_start=week_start,
            error_class=error_class,
            correlation_id=correlation_id,
            summary=summary,
        )
    except Exception as placeholder_exc:  # noqa: BLE001 — defensive outer catch
        error_log.log(
            Severity.ERROR,
            SCRIPT_NAME,
            (
                f"failed to write GENERATION_FAILED placeholder for "
                f"{project_name}: {placeholder_exc!r}"
            ),
            error_code="weekly_generate.placeholder_write_failed",
            correlation_id=correlation_id,
        )


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(week_start_override: date | None = None) -> dict[str, Any]:
    """Generate WPR drafts for each active Forefront project for one week.

    Decorated entrypoint — `@require_active` blocks runs during PAUSED /
    MAINTENANCE, `@its_error_log` writes INFO/CRITICAL records around the
    pipeline. Logic lives in `_run_pipeline` so unit tests can call it
    directly without the decorator stack.

    Args:
        week_start_override: When set, treat this Monday as the target
            week (manual backfill or operator debugging). When unset,
            target week = Monday-of-current-week.

    Returns:
        RunSummary as a dict. Per-project soft failures land in
        `errors_per_project`; a hard failure propagates to the decorator
        and surfaces as CRITICAL.
    """
    return _run_pipeline(week_start_override=week_start_override)


def _run_pipeline(*, week_start_override: date | None) -> dict[str, Any]:
    """Inner pipeline body. Called by main() and by tests directly."""
    correlation_id = uuid.uuid4().hex[:12]
    summary = RunSummary()

    today = date.today()
    if week_start_override is not None:
        week_start = scheduling.monday_of_week(week_start_override)
    else:
        week_start = scheduling.monday_of_week(today)
    week_end = week_start + timedelta(days=6)

    # Reviewer-chain pre-check. Empty chain → CRITICAL + exit (do not call Anthropic).
    chain = scheduling.resolve_chain(WORKSTREAM, on_date=today)
    if not chain.slots:
        _alert_empty_reviewer_chain(correlation_id)
        return {
            **summary.__dict__,
            "week_start": week_start.isoformat(),
            "correlation_id": correlation_id,
            "aborted_empty_chain": True,
        }

    threshold = _read_float_setting(CFG_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD)
    model = _read_str_setting(CFG_MODEL, DEFAULT_MODEL)

    # Pre-flight schema-version validation (F20). A drifted/stale schema is a
    # system-level precondition failure — identical for every project — so,
    # exactly like the empty-reviewer-chain check above, abort the whole run
    # LOUDLY rather than discovering it once per project. Raised here (outside
    # the per-project fence below), the ValueError propagates to @its_error_log
    # → CRITICAL triple-fire, instead of degrading to N GENERATION_FAILED
    # placeholders + N ERROR logs for a single root cause — and we never spend
    # Anthropic credit on a run that would fail every project.
    # `_handle_standard_project` re-loads per project; this is the belt to that
    # suspenders.
    _load_tool_schema()

    for folder_id, project_name in iter_active_projects():
        try:
            _process_with_retry(
                folder_id=folder_id,
                project_name=project_name,
                week_start=week_start,
                week_end=week_end,
                threshold=threshold,
                model=model,
                correlation_id=correlation_id,
                summary=summary,
            )
        except SmartsheetError as exc:
            summary.errors_per_project[project_name] = f"{type(exc).__name__}: {exc!r}"
            error_log.log(
                Severity.ERROR,
                SCRIPT_NAME,
                f"Smartsheet error processing {project_name} after retry: {exc!r}",
                error_code="smartsheet_error",
                correlation_id=correlation_id,
            )
            _safe_write_placeholder(
                project_name=project_name,
                week_start=week_start,
                error_class=type(exc).__name__,
                correlation_id=correlation_id,
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 — per-project fence so one bad project doesn't kill the run
            summary.errors_per_project[project_name] = f"{type(exc).__name__}: {exc!r}"
            error_log.log(
                Severity.ERROR,
                SCRIPT_NAME,
                f"unexpected error processing {project_name}: {exc!r}",
                error_code="weekly_generate.project_failed",
                correlation_id=correlation_id,
            )
            _safe_write_placeholder(
                project_name=project_name,
                week_start=week_start,
                error_class=type(exc).__name__,
                correlation_id=correlation_id,
                summary=summary,
            )

    _write_watchdog_marker()
    return {
        **summary.__dict__,
        "week_start": week_start.isoformat(),
        "correlation_id": correlation_id,
        "aborted_empty_chain": False,
    }


def _row_in_week(row: dict[str, Any], week_start: date, week_end: date) -> bool:
    """True iff `row['Report Date']` falls within [week_start, week_end]."""
    raw = row.get("Report Date")
    if raw is None or raw == "":
        return False
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return week_start <= raw <= week_end
    if isinstance(raw, datetime):
        d = raw.date()
        return week_start <= d <= week_end
    if isinstance(raw, str):
        try:
            d = date.fromisoformat(raw[:10])
        except ValueError:
            return False
        return week_start <= d <= week_end
    return False


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safety_reports.weekly_generate",
        description=(
            "Generate WPR drafts for each Forefront project for one week. "
            "Run with --week-start to target a specific past week."
        ),
    )
    parser.add_argument(
        "--week-start",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help=(
            "ISO date for the target week's Monday. Defaults to "
            "Monday-of-current-week."
        ),
    )
    args = parser.parse_args(argv)
    main(week_start_override=args.week_start)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
