"""The FIXED Class-A editable config registry (WS2 D1-2).

Anything not in `REGISTRY` is read-only — the editor refuses to write it. The
registry is keyed on the `(Setting, Workstream)` PAIR, never the Setting name
alone: `safety_reports.portal.worker_base_url` exists 3× under different
Workstreams, and `progress_reports.intake_enabled` is read under
`Workstream='safety_reports'` (the intake daemon's own workstream — the
documented footgun). Keying on the pair is load-bearing.

Class boundaries (D1-2 handles Class A only):
  - Class A  — pause/resume gates, tuning knobs, behavior/data config. PIN gate
               only (no extra ceremony). Lives here.
  - Class B/C — code-committing daemons (config_actuator), higher-ceremony
               actions. D1-3.
  - Class E  — read-only display only. `safety_reports.external_send_gate` and
               `system.state`: editing them off would disable Invariant 1 / the
               kill switch. NEVER editable — see NON_EDITABLE below.

Send-poller gates carry `first_activation_gated=True`: a `false->true` edit is a
potential dark->live first activation (their Descriptions carry go-live
preconditions) and is routed to the escalate path (D1-3), NOT applied. A
`true->false` pause is always a plain Class-A apply.
"""
from __future__ import annotations

from dataclasses import dataclass

from operator_dashboard.act.validators import (
    KNOWN_MODELS,
    Validator,
    v_bool,
    v_email,
    v_email_list,
    v_enum,
    v_float01,
    v_id,
    v_int,
    v_keychain_key,
    v_schedule,
    v_url,
)


@dataclass(frozen=True)
class ConfigEntry:
    setting: str
    workstream: str
    label: str
    group: str
    validator: Validator
    note: str = ""
    # Send-poller gate: a false->true edit escalates (dark->live first
    # activation) instead of applying; true->false (pause) applies normally.
    first_activation_gated: bool = False


def _e(
    setting: str,
    workstream: str,
    group: str,
    validator: Validator,
    *,
    note: str = "",
    first_activation_gated: bool = False,
    label: str | None = None,
) -> ConfigEntry:
    return ConfigEntry(
        setting=setting,
        workstream=workstream,
        label=label or setting,
        group=group,
        validator=validator,
        note=note,
        first_activation_gated=first_activation_gated,
    )


_GATES = "Operational gates (pause / resume)"
_SEND_GATES = "Send-poller gates (pause = Class A · activation escalates)"
_KNOBS = "Tuning knobs / thresholds"
_BEHAVIOR = "Behavior config"
_WINDOWS = "Scheduled-send windows"
_DATA = "Data / paths"

_ENTRIES: list[ConfigEntry] = [
    # --- operational gates: pause/resume, plain Class A ---
    _e("safety_reports.publish_daemon.polling_enabled", "safety_reports", _GATES, v_bool),
    _e("safety_reports.portal_poll.polling_enabled", "safety_reports", _GATES, v_bool),
    _e("safety_reports.intake.polling_enabled", "safety_reports", _GATES, v_bool),
    _e("safety_reports.intake.box_filing_enabled", "safety_reports", _GATES, v_bool),
    _e("field_ops.fieldops_sync.sync_enabled", "field_ops", _GATES, v_bool),
    _e("field_ops.fieldops_sync.hours_enabled", "field_ops", _GATES, v_bool),
    _e("field_ops.fieldops_sync.equipment_enabled", "field_ops", _GATES, v_bool),
    _e("field_ops.fieldops_sync.materials_enabled", "field_ops", _GATES, v_bool),
    _e("field_ops.fieldops_sync.incidents_enabled", "field_ops", _GATES, v_bool),
    # FOOTGUN: read under Workstream='safety_reports' (intake's own workstream), NOT progress_reports.
    _e(
        "progress_reports.intake_enabled",
        "safety_reports",
        _GATES,
        v_bool,
        note="read under Workstream=safety_reports (intake daemon's workstream), not progress_reports",
    ),
    _e("circuit_breaker.enabled", "global", _GATES, v_bool),
    # --- send-poller gates: pause = Class A; false->true activation escalates ---
    _e(
        "safety_reports.weekly_send.polling_enabled",
        "safety_reports",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="pause anytime; turning ON is a dark->live activation → escalate (D1-3)",
    ),
    _e(
        "po_materials.po_send.polling_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="the vendor External Send Gate — currently dark; turning ON escalates (D1-3)",
    ),
    _e(
        "po_materials.po_poll.polling_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="pause anytime; turning ON escalates (go-live preconditions in Description)",
    ),
    _e(
        "po_materials.po_poll.vendors_sync_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="pause anytime; turning ON escalates",
    ),
    _e(
        "po_materials.po_poll.status_sync_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="pause anytime; turning ON escalates",
    ),
    # --- tuning knobs / thresholds (int-bounded) ---
    _e("circuit_breaker.failure_threshold", "global", _KNOBS, v_int(1, 100)),
    _e("circuit_breaker.cooldown_seconds", "global", _KNOBS, v_int(1, 86_400)),
    _e("circuit_breaker.prolonged_open_alert_seconds", "global", _KNOBS, v_int(1, 86_400)),
    _e("alerting.max_alerts_per_hour", "global", _KNOBS, v_int(1, 1_000)),
    _e("alerting.dedupe_window_minutes", "global", _KNOBS, v_int(1, 1_440)),
    _e("smartsheet.sheet_count_ceiling", "global", _KNOBS, v_int(1, 100_000)),
    _e("smartsheet.sheet_count_margin", "global", _KNOBS, v_int(0, 10_000)),
    _e("picklist_sync.size_hard_halt_threshold", "global", _KNOBS, v_int(1, 100_000)),
    _e("picklist_sync.size_warn_threshold", "global", _KNOBS, v_int(1, 100_000)),
    _e("mail_intake.safety.max_idle_hours", "global", _KNOBS, v_int(1, 8_760)),
    _e("progress_reports.hours_log.row_cap_warn_threshold", "progress_reports", _KNOBS, v_int(1, 1_000_000)),
    _e("progress_reports.equipment_status.row_cap_warn_threshold", "progress_reports", _KNOBS, v_int(1, 1_000_000)),
    _e("progress_reports.material_list.row_cap_warn_threshold", "progress_reports", _KNOBS, v_int(1, 1_000_000)),
    _e("progress_reports.material_incidents.row_cap_warn_threshold", "progress_reports", _KNOBS, v_int(1, 1_000_000)),
    # --- behavior config ---
    _e("safety_reports.intake.confidence_threshold", "safety_reports", _BEHAVIOR, v_float01),
    _e("safety_reports.intake.classification_model", "safety_reports", _BEHAVIOR, v_enum(KNOWN_MODELS)),
    _e("safety_reports.intake.review_queue_on_low_confidence", "safety_reports", _BEHAVIOR, v_bool),
    # --- scheduled-send windows (runtime-read) ---
    _e("safety_reports.weekly_send.scheduled_send_local", "safety_reports", _WINDOWS, v_schedule),
    _e("po_materials.po_send.scheduled_send_local", "po_materials", _WINDOWS, v_schedule),
    # --- data / paths ---
    _e("safety_reports.box.portal_root_folder_id", "safety_reports", _DATA, v_id),
    _e("progress_reports.box.portal_root_folder_id", "progress_reports", _DATA, v_id),
    _e("system.operator_email", "global", _DATA, v_email),
    _e("system.heartbeat_url", "global", _DATA, v_url),
    _e("daemons.heartbeat_sheet_id", "global", _DATA, v_id),
    _e("daemons.health_report_id", "global", _DATA, v_id, note="currently 'TBD'; set to a numeric sheet id"),
    _e("system.sentry_dsn_keychain_key", "global", _DATA, v_keychain_key),
    _e("system.resend_api_keychain_key", "global", _DATA, v_keychain_key),
]

# Per-job recipient lists + the fallback. NOTE: for SAFETY weekly send the live
# send code resolves recipients at SEND time from ITS_Active_Jobs (not these
# rows); their Descriptions say weekly_generate reads them at draft time. So we
# surface + validate them but flag that they may be superseded at send time.
_RECIPIENTS_NOTE = "⚠ safety weekly-send resolves recipients from ITS_Active_Jobs at send time — this row may be superseded; verify before relying on it"
for _job in ("bradley_1", "bradley_2", "brimfield_1", "brimfield_2", "huntley", "rockford", "_default"):
    _ENTRIES.append(
        _e(f"safety_reports.recipients.{_job}", "safety_reports", _DATA, v_email_list, note=_RECIPIENTS_NOTE)
    )

REGISTRY: dict[tuple[str, str], ConfigEntry] = {(e.setting, e.workstream): e for e in _ENTRIES}

# Keys that are DELIBERATELY not editable here — asserted absent by the
# denylist test. Editing these is out of Class-A scope:
#   - external_send_gate / system.state : Class E (would disable Invariant 1 /
#     the kill switch) — read-only display only, never here.
#   - config_actuator.polling_enabled   : gates a code-committing/deploying
#     daemon — Class B (D1-3), high-capability.
#   - *.poll_interval_seconds           : read at INSTALL time, not runtime;
#     editing the cell does NOT hot-reload — deferred to D1-3 alongside the
#     launchctl reinstall action (never imply immediate effect).
NON_EDITABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "safety_reports.external_send_gate",
        "system.state",
        "po_materials.config_actuator.polling_enabled",
    }
)


def is_editable(setting: str, workstream: str) -> bool:
    return (setting, workstream) in REGISTRY


def get_entry(setting: str, workstream: str) -> ConfigEntry | None:
    return REGISTRY.get((setting, workstream))
