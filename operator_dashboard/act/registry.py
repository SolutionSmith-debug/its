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
    v_reviewer_chain,
    v_schedule,
    v_sender_list,
    v_state,
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
    # Weight tier (D1-3): "A" = plain PIN gate (Class-A); "B" = weighted edit,
    # requires the elevated-confirm ceremony (re-PIN + typed confirmation).
    tier: str = "A"
    elevated_confirm: bool = False


def _e(
    setting: str,
    workstream: str,
    group: str,
    validator: Validator,
    *,
    note: str = "",
    first_activation_gated: bool = False,
    label: str | None = None,
    tier: str = "A",
    elevated_confirm: bool = False,
) -> ConfigEntry:
    return ConfigEntry(
        setting=setting,
        workstream=workstream,
        label=label or setting,
        group=group,
        validator=validator,
        note=note,
        first_activation_gated=first_activation_gated,
        tier=tier,
        elevated_confirm=elevated_confirm,
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
    # compile-now poll gates (the operator "Compile Now" checkbox pollers) — plain Class-A pause/resume.
    _e("safety_reports.compile_now_poll.polling_enabled", "safety_reports", _GATES, v_bool),
    _e("progress_reports.compile_now_poll.polling_enabled", "progress_reports", _GATES, v_bool),
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
    # progress-reports send poller — the progress twin of weekly_send (external send path).
    _e(
        "progress_reports.progress_send.polling_enabled",
        "progress_reports",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="the progress-reports send poller (twin of weekly_send); pause anytime; turning ON is a dark->live activation → escalate",
    ),
    # subcontracts generation poll — ships dark; it feeds a generation→Box-filing pipeline
    # with go-live preconditions, so it mirrors po_poll: pause = plain Class A, false->true
    # activation escalates. (The SC-S4 SEND half shipped 2026-07-16 — its gate is below.)
    _e(
        "subcontracts.subcontract_poll.polling_enabled",
        "subcontracts",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="subcontract generation poll — ships dark; pause anytime; turning ON escalates (go-live preconditions in Description)",
    ),
    _e(
        "subcontracts.subcontract_poll.subcontractors_sync_enabled",
        "subcontracts",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="§51 subcontractor down/up-sync pass; pause anytime; turning ON escalates",
    ),
    _e(
        "subcontracts.subcontract_poll.status_sync_enabled",
        "subcontracts",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="pause anytime; turning ON escalates",
    ),
    # subcontract SEND poller (SC-S4, built dark 2026-07-15). The subcontractor External
    # Send Gate — same posture as po_send: pause is a fast Class-A brake, activation escalates.
    _e(
        "subcontracts.subcontract_send.polling_enabled",
        "subcontracts",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="the subcontractor External Send Gate; pause anytime; turning ON is a FIXED high-class activation → escalate (D1-3)",
    ),
    # --- RFQ / vendor-estimate lane (ADR-0004), all shipped dark ------------------
    # estimate_poll + rfq_poll are generation-half daemons that file to Box and write
    # ITS-owned ledgers, exactly like po_poll: pause = plain Class A, activation escalates.
    _e(
        "po_materials.estimate_poll.polling_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="vendor-estimate importer (ADR-0004 Lane 1) — ships dark; pause anytime; turning ON escalates (go-live preconditions in Description)",
    ),
    _e(
        "po_materials.rfq_poll.polling_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="outbound-RFQ generation (ADR-0004 Lane 2) — ships dark; pause anytime; turning ON escalates",
    ),
    # rfq_send is the RFQ External Send Gate. Posture is deliberately IDENTICAL to
    # po_send / subcontract_send rather than elevated_confirm: `first_activation_gated`
    # REFUSES the dangerous direction outright (false->true escalates to Seth as a FIXED
    # high-capability-class decision), while leaving true->false a fast one-step brake.
    # Putting the elevated ceremony on this row would slow the EMERGENCY STOP without
    # making activation any harder than "already refused".
    _e(
        "po_materials.rfq_send.polling_enabled",
        "po_materials",
        _SEND_GATES,
        v_bool,
        first_activation_gated=True,
        note="the vendor RFQ External Send Gate — currently dark; turning ON is a FIXED high-class decision (Seth) → escalate; pausing is immediate",
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
    _e(
        "po_materials.estimate_poll.max_pages_preview",
        "po_materials",
        _KNOBS,
        v_int(1, 100),
        note="how many estimate pages the importer renders as disposition previews (ADR-0004 E3)",
    ),
    # --- behavior config ---
    _e("safety_reports.intake.confidence_threshold", "safety_reports", _BEHAVIOR, v_float01),
    _e("safety_reports.intake.classification_model", "safety_reports", _BEHAVIOR, v_enum(KNOWN_MODELS)),
    _e("safety_reports.intake.review_queue_on_low_confidence", "safety_reports", _BEHAVIOR, v_bool),
    # §34 attachment/photo ClamAV layer toggles — plain Class-A behavior (a dark security
    # sub-layer; default off, enabling presumes clamd is running on the host).
    _e(
        "safety_reports.photo_screen.clamav_enabled",
        "safety_reports",
        _BEHAVIOR,
        v_bool,
        note="§34 Layer-6 photo-screener ClamAV pass (default off; enabling requires clamd running on the host)",
    ),
    _e(
        "po_materials.po_attach_screen.clamav_enabled",
        "po_materials",
        _BEHAVIOR,
        v_bool,
        note="§34 PO doc-attachment-screener ClamAV pass (read by po_poll; default off; enabling requires clamd running)",
    ),
    # --- scheduled-send windows (runtime-read) ---
    _e("safety_reports.weekly_send.scheduled_send_local", "safety_reports", _WINDOWS, v_schedule),
    _e("po_materials.po_send.scheduled_send_local", "po_materials", _WINDOWS, v_schedule),
    _e("progress_reports.progress_send.scheduled_send_local", "progress_reports", _WINDOWS, v_schedule),
    _e("po_materials.rfq_send.scheduled_send_local", "po_materials", _WINDOWS, v_schedule),
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

# --- Class-B weighted edits (D1-3) — require the elevated-confirm ceremony -----
# Identity / trust / endpoint / global-brake changes: same weight as a credential
# change. tier="B", elevated_confirm=True. Editing them via the plain Class-A
# route is refused; they go through re-PIN + typed confirmation.
_IDENTITY = "Identity — sent-from / read mailbox (Class B · elevated)"
_TRUST = "Trust allowlists (Class B · elevated)"
_ENDPOINT = "Worker endpoint (Class B · elevated)"
_BRAKE = "Global brake + privileged daemon (Class B · elevated)"


def _b(
    setting: str,
    workstream: str,
    group: str,
    validator: Validator,
    *,
    note: str = "",
    first_activation_gated: bool = False,
) -> ConfigEntry:
    return _e(
        setting,
        workstream,
        group,
        validator,
        tier="B",
        elevated_confirm=True,
        note=note,
        first_activation_gated=first_activation_gated,
    )


_ENTRIES += [
    _b(
        "system.state",
        "global",
        _BRAKE,
        v_state,
        note="the GLOBAL brake — ACTIVE|PAUSED|MAINTENANCE; high blast radius (halts scheduled daemons)",
    ),
    _b(
        "po_materials.config_actuator.polling_enabled",
        "po_materials",
        _BRAKE,
        v_bool,
        first_activation_gated=True,  # code-actuation: dark->live activation needs the go-live attestation
        note="gates a code-COMMITTING/DEPLOYING daemon — elevated + go-live attestation to activate",
    ),
    _b(
        "safety_reports.intake.allowed_senders",
        "safety_reports",
        _TRUST,
        v_sender_list,
        note="ingress trust allowlist (emails or @domain patterns)",
    ),
    _b(
        "safety_reports.reviewer_chain",
        "safety_reports",
        _TRUST,
        v_reviewer_chain,
        note="reviewer escalation JSON (primary/secondary/tertiary + delay hours)",
    ),
    _b("safety_reports.weekly_send.from_mailbox", "safety_reports", _IDENTITY, v_email),
    _b("po_materials.po_send.from_mailbox", "po_materials", _IDENTITY, v_email),
    _b("progress_reports.progress_send.from_mailbox", "progress_reports", _IDENTITY, v_email),
    _b("po_materials.rfq_send.from_mailbox", "po_materials", _IDENTITY, v_email),
    _b("safety_reports.intake.mailbox", "safety_reports", _IDENTITY, v_email),
    _b(
        "safety_reports.portal.worker_base_url",
        "safety_reports",
        _ENDPOINT,
        v_url,
        note="redirect target if wrong — validate scheme/host (this pair is one of 3 copies)",
    ),
    _b("safety_reports.portal.worker_base_url", "progress_reports", _ENDPOINT, v_url),
    _b("safety_reports.portal.worker_base_url", "po_materials", _ENDPOINT, v_url),
]

REGISTRY: dict[tuple[str, str], ConfigEntry] = {(e.setting, e.workstream): e for e in _ENTRIES}

# Keys DELIBERATELY not editable on ANY route — asserted absent by the denylist
# test. `external_send_gate` is Class E (editing it would disable Invariant 1) —
# read-only display only, never editable. `system.state` and
# `config_actuator.polling_enabled` moved to Class B (elevated) in D1-3.
# `*.poll_interval_seconds` is install-time (no hot-reload) and stays out.
NON_EDITABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "safety_reports.external_send_gate",
    }
)


def is_editable(setting: str, workstream: str) -> bool:
    return (setting, workstream) in REGISTRY


def get_entry(setting: str, workstream: str) -> ConfigEntry | None:
    return REGISTRY.get((setting, workstream))


# --- Class C: the FIXED rotatable-credential registry (D1-3) -------------------
# Write-only rotation over a fixed list — NOT a free-form secret store. An
# attempt to rotate an unlisted credential is refused. kind:
#   'keychain'    — pasteable secret; write-through via shared.keychain.set_secret
#   'worker'      — a Worker bearer; `wrangler secret put` + dual-write the
#                   byte-equal Keychain mirror from the SAME pasted value
#   'box_guided'  — the Box refresh token: NOT pasteable (only setup_box_oauth.py
#                   may write it); the dashboard guides quiesce, never accepts a value
@dataclass(frozen=True)
class SecretEntry:
    key: str
    label: str
    kind: str
    note: str = ""
    worker_mirror: str = ""  # kind='worker' only: the Keychain mirror to dual-write


_SECRETS: list[SecretEntry] = [
    SecretEntry("ITS_SMARTSHEET_TOKEN", "Smartsheet API token", "keychain"),
    SecretEntry("ITS_RESEND_API_KEY", "Resend API key (operator alerts)", "keychain"),
    SecretEntry("ITS_SENTRY_DSN", "Sentry DSN", "keychain"),
    SecretEntry("ITS_BOX_CLIENT_ID", "Box OAuth client id", "keychain"),
    SecretEntry("ITS_BOX_CLIENT_SECRET", "Box OAuth client secret", "keychain"),
    SecretEntry(
        "ITS_BOX_REFRESH_TOKEN",
        "Box OAuth refresh token",
        "box_guided",
        note="single-consumer + rotates on every use — rotate ONLY via the guided quiesce→setup_box_oauth→smoke flow; never paste a value here",
    ),
    SecretEntry("PORTAL_PO_API_TOKEN", "Worker PO bearer", "worker", worker_mirror="ITS_PORTAL_PO_TOKEN"),
    SecretEntry(
        "PORTAL_CONFIG_API_TOKEN", "Worker config bearer", "worker", worker_mirror="ITS_PORTAL_CONFIG_TOKEN"
    ),
    SecretEntry("PORTAL_ADMIN_API_TOKEN", "Worker admin bearer", "worker", worker_mirror="ITS_PORTAL_ADMIN_TOKEN"),
]

SECRETS: dict[str, SecretEntry] = {s.key: s for s in _SECRETS}


def is_rotatable(key: str) -> bool:
    return key in SECRETS


def get_secret_entry(key: str) -> SecretEntry | None:
    return SECRETS.get(key)


# --- Class E: read-only display rows (NEVER an edit control) -------------------
@dataclass(frozen=True)
class DisplayEntry:
    setting: str
    workstream: str
    label: str
    note: str


CLASS_E_DISPLAY: list[DisplayEntry] = [
    DisplayEntry(
        "safety_reports.external_send_gate",
        "safety_reports",
        "External Send Gate — Invariant 1 mode",
        "Class E — read-only. Changing this off would disable the External Send Gate; it is NEVER editable on any surface.",
    ),
    DisplayEntry(
        "safety_reports.authorized_approvers",
        "safety_reports",
        "F22 authorized approvers (legacy row)",
        "⚠ Legacy — the LIVE F22 approval authority is the §46 workspace-SHARE membership (list_workspace_share_emails), NOT this ITS_Config row. Shown for reference; editing it would not change who can approve a send.",
    ),
]

# The ADR-0004 estimate-extraction ladder (E4-E6). These three gates are DARK and
# UNVALIDATED: no model has been qualified against the production corpus yet, and
# turning one on would let an unqualified extractor put numbers in front of an
# operator as if they were read off the vendor's document. They are surfaced
# read-only ON PURPOSE — the operator can SEE the state (and that it is off) from
# the estimate_poll node rail, but the console offers no control that invites a
# flip. Promoting one to an editable ConfigEntry is gated on
# `scripts/eval_estimate_ladder.py` qualifying a model on the production M2; until
# then the flip is a Developer-Operator action against ITS_Config directly.
_LADDER_NOTE = (
    "Class E — read-only. DARK + UNVALIDATED extraction tier (ADR-0004 E4-E6): no model is "
    "qualified yet. Do NOT enable until scripts/eval_estimate_ladder.py qualifies a model on "
    "the production corpus (M2); until then this is a Developer-Operator (Seth) action, not a "
    "console flip. Human accept-with-preview remains the fidelity control either way."
)
CLASS_E_DISPLAY += [
    DisplayEntry(
        "po_materials.estimate_extract.tier1_enabled",
        "po_materials",
        "Estimate extraction — Tier 1 (deterministic templates)",
        _LADDER_NOTE,
    ),
    DisplayEntry(
        "po_materials.estimate_extract.tier2_enabled",
        "po_materials",
        "Estimate extraction — Tier 2 (local Ollama)",
        _LADDER_NOTE,
    ),
    DisplayEntry(
        "po_materials.estimate_extract.ocr_enabled",
        "po_materials",
        "Estimate extraction — OCR pass",
        _LADDER_NOTE,
    ),
]
