---
type: reference
status: active
generated_by: scripts/generate_config_dictionary.py
workstream: null
tags: [reference, a8, its-config, data-dictionary, generated]
---

<!-- GENERATED FILE — do not hand-edit. Regenerate with:
       python -m scripts.generate_config_dictionary
     Then re-record its sha256 in docs/enablement/manifest.yaml. -->

# ITS_Config Data Dictionary

This is the operator reference for **ITS_Config** — the Smartsheet sheet where every runtime setting ITS reads is stored, one row per setting. It lists every key ITS looks up while running: what it controls, which **Workstream** row it lives under, its **default** (the value used when the row is missing, blank, or unreadable), and its type. It is generated from the code itself, so it always matches what the daemons actually read.

> **This page is generated.** It is produced by `scripts/generate_config_dictionary.py` from the daemons' own config declarations — never hand-edit it. If a value here looks wrong, the fix is in the code, not this page.

## How to read this

- **Setting** is the exact value in the ITS_Config **Setting** column. **Workstream** is the value in the **Workstream** column — ITS matches on *both*, so the same Setting name can appear under two Workstreams and mean two different rows.
- **Default** is what ITS uses when the row is **missing, blank, or unreadable** — every read is fail-open to this value, and a *missing* row is logged loudly (a config that "ships dark" has no row to flip until it is seeded).
- **Read by** names the daemon(s) that resolve the key at runtime — where to look when a setting is not taking effect.

## Global / shared-infrastructure keys

| Setting | Type | Default | Purpose | Read by |
|---|---|---|---|---|
| `alerting.dedupe_window_minutes` | int | 60 | How long (minutes) a repeated CRITICAL alert is suppressed on the push legs (email + Sentry) before it can fire again. The per-occurrence ITS_Errors record is never suppressed. | shared.alert_dedupe |
| `alerting.max_alerts_per_hour` | int | 15 | Global ceiling on operator alert emails per hour across all keys, so a flapping failure cannot fire unbounded email. The record is never capped — only the email fan-out. | shared.alert_dedupe |
| `circuit_breaker.cooldown_seconds` | int | 300 | How long (seconds) the breaker stays open before a trial half-open call. | shared.circuit_breaker |
| `circuit_breaker.enabled` | bool | true | Whether the Smartsheet circuit breaker is armed. When tripped it short-circuits Smartsheet calls during an outage to fail fast. | shared.circuit_breaker |
| `circuit_breaker.failure_threshold` | int | 5 | Consecutive Smartsheet failures before the breaker opens (trips). | shared.circuit_breaker |
| `circuit_breaker.prolonged_open_alert_seconds` | int | 600 | How long (seconds) the breaker may stay open before the watchdog fires a prolonged-open CRITICAL page. | watchdog |
| `picklist_sync.size_hard_halt_threshold` | int | 400 | Option count that HARD-HALTS that one mapping's sync (a runaway guardrail). | run_picklist_sync, shared.picklist_sync |
| `picklist_sync.size_warn_threshold` | int | 200 | Option count on a synced picklist that triggers a WARN (a large but still-processed list). | run_picklist_sync, shared.picklist_sync |
| `smartsheet.sheet_count_ceiling` | int | 1500 | Per-workspace sheet-count ceiling; a new week/period sheet that would land past it routes to the Review Queue instead of being created silently. | shared.sheet_capacity |
| `smartsheet.sheet_count_margin` | int | 50 | Headroom below the ceiling at which the sheet-capacity guard starts warning. | shared.sheet_capacity |
| `system.heartbeat_url` | str | *(unset)* | The external UptimeRobot heartbeat URL the watchdog pings each run so a total MacBook-death (the watchdog can't alert about itself) is caught. | watchdog |
| `system.operator_email` | str | seths@evergreenmirror.com | Where out-of-band operator alerts (Resend) are sent when ITS_Config cannot be read — the last-resort page recipient during a Smartsheet outage. | shared.resend_client |
| `system.state` | str | ACTIVE | The system kill switch. ACTIVE = normal; PAUSED / MAINTENANCE make every daemon exit cleanly at entry. Fail-open: an unreadable value is treated as ACTIVE. This is an operator-convenience pause, not a security control. | shared.kill_switch |

## Field-Ops (portal → Smartsheet mirror)

| Setting | Type | Default | Purpose | Read by |
|---|---|---|---|---|
| `field_ops.fieldops_sync.equipment_enabled` | bool | false | Per-stream gate: mirror equipment status from the portal into Smartsheet. | field_ops.fieldops_sync |
| `field_ops.fieldops_sync.hours_enabled` | bool | false | Per-stream gate: mirror crew hours from the portal into Smartsheet. | field_ops.fieldops_sync |
| `field_ops.fieldops_sync.incidents_enabled` | bool | false | Per-stream gate: mirror material incidents from the portal into Smartsheet. | field_ops.fieldops_sync |
| `field_ops.fieldops_sync.materials_enabled` | bool | false | Per-stream gate: mirror material receipts from the portal into Smartsheet. (Activation is gated on the §51 rider — read the row's Description before flipping.) | field_ops.fieldops_sync |
| `field_ops.fieldops_sync.sync_enabled` | bool | false | Master gate for the portal→Smartsheet job mirror (fieldops_sync). Ships OFF; the operator flips it on at cutover after the mirror slices land. | field_ops.fieldops_sync |

## Purchase Orders & Materials

| Setting | Type | Default | Purpose | Read by |
|---|---|---|---|---|
| `po_materials.config_actuator.polling_enabled` | bool | false | Runtime gate for the §50 config actuator daemon (applies approved workstream-config changes on the Mac). | po_materials.config_actuator |
| `po_materials.po_poll.polling_enabled` | bool | false | Runtime gate for the PO pull daemon (pulls submitted POs from the Worker). Ships dark. | po_materials.po_poll |
| `po_materials.po_poll.status_sync_enabled` | bool | false | Sub-gate: sync PO statuses back to the portal. Ships dark. | po_materials.po_poll |
| `po_materials.po_poll.vendors_sync_enabled` | bool | false | Sub-gate: push the vendor list down to the portal PO dropdown. Ships dark. | po_materials.po_poll |
| `po_materials.po_send.from_mailbox` | str | procurement@evergreenmirror.com | The M365 mailbox the po_materials.po_send send daemon sends approved email FROM. | po_materials.po_send, po_materials.po_send_poll |
| `po_materials.po_send.polling_enabled` | bool | true | Runtime on/off gate for the po_materials.po_send daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | po_materials.po_send_poll |
| `po_materials.po_send.scheduled_send_local` | str | MON 07:00 | Local-time window (e.g. `MON 07:00`) at/after which a row approved with **Approve for Scheduled Send** may dispatch on the po_materials.po_send path. | po_materials.po_send_poll |
| `safety_reports.portal.worker_base_url` | str | *(unset)* | Base URL of the Safety Portal Cloudflare Worker. The portal pull / PO / progress daemons hit its send-free internal API here. Repointed to the custom domain (safety.evergreenmirror.com) after deploy. | po_materials.config_actuator |

## Progress Reports

| Setting | Type | Default | Purpose | Read by |
|---|---|---|---|---|
| `progress_reports.box.portal_root_folder_id` | str | *(unset)* | Box root folder ID under which progress-report packets are filed. | progress_reports.progress_weekly_generate |
| `progress_reports.compile_now_poll.polling_enabled` | bool | true | Runtime on/off gate for the progress_reports.compile_now_poll daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | safety_reports.compile_now_poll |
| `progress_reports.equipment_status.row_cap_warn_threshold` | int | 15000 | Row-count on the mirror sheet at which progress_reports.equipment_status WARNs that the sheet is approaching the Smartsheet per-sheet row cap. | field_ops.fieldops_sync |
| `progress_reports.evergreen_contact_name` | str | the Evergreen Renewables office | The name ITS uses for the Evergreen Renewables office/contact in this workstream's report copy. | progress_reports.progress_weekly_generate |
| `progress_reports.hours_log.row_cap_warn_threshold` | int | 15000 | Row-count on the mirror sheet at which progress_reports.hours_log WARNs that the sheet is approaching the Smartsheet per-sheet row cap. | field_ops.fieldops_sync |
| `progress_reports.material_incidents.row_cap_warn_threshold` | int | 15000 | Row-count on the mirror sheet at which progress_reports.material_incidents WARNs that the sheet is approaching the Smartsheet per-sheet row cap. | field_ops.fieldops_sync |
| `progress_reports.material_list.row_cap_warn_threshold` | int | 15000 | Row-count on the mirror sheet at which progress_reports.material_list WARNs that the sheet is approaching the Smartsheet per-sheet row cap. | field_ops.fieldops_sync |
| `progress_reports.progress_send.from_mailbox` | str | progress@evergreenmirror.com | The M365 mailbox the progress_reports.progress_send send daemon sends approved email FROM. | progress_reports.progress_send, progress_reports.progress_send_poll |
| `progress_reports.progress_send.polling_enabled` | bool | true | Runtime on/off gate for the progress_reports.progress_send daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | progress_reports.progress_send_poll |
| `progress_reports.progress_send.scheduled_send_local` | str | MON 07:00 | Local-time window (e.g. `MON 07:00`) at/after which a row approved with **Approve for Scheduled Send** may dispatch on the progress_reports.progress_send path. | progress_reports.progress_send_poll |
| `progress_reports.progress_weekly_generate.job_timeout_seconds` | int | 600 | Per-job wall-clock ceiling (seconds) for the progress_reports.progress_weekly_generate weekly compile; a job exceeding it is fenced to the Review Queue, not left to hang. | progress_reports.progress_weekly_generate |
| `progress_reports.progress_weekly_generate.merge_memory_ceiling_bytes` | int | 268435456 | Memory ceiling (bytes) for the progress_reports.progress_weekly_generate PDF-merge step; a packet whose merge would exceed it is refused rather than risk OOMing the daemon. | progress_reports.progress_weekly_generate |
| `safety_reports.portal.worker_base_url` | str | *(unset)* | Shared Worker base URL, read under progress_reports for the P6 rollup page. | progress_reports.progress_weekly_generate |

## Safety Reports

| Setting | Type | Default | Purpose | Read by |
|---|---|---|---|---|
| `progress_reports.intake_enabled` | bool | false | FOOTGUN: the progress-intake gate is read under Workstream='safety_reports' (intake's own workstream), NOT 'progress_reports' — seed it there. | safety_reports.intake |
| `safety_reports.box.portal_root_folder_id` | str | *(unset)* | Shared Box mirror-tree root; owned by safety_reports. The drafts pass files PO PDFs under ROOT→<job>→'Purchase Orders'. | po_materials.po_poll, safety_reports.intake, safety_reports.portal_poll, safety_reports.weekly_generate |
| `safety_reports.compile_now_poll.polling_enabled` | bool | true | Runtime on/off gate for the safety_reports.compile_now_poll daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | safety_reports.compile_now_poll |
| `safety_reports.evergreen_contact_name` | str | the Evergreen Renewables office | The name ITS uses for the Evergreen Renewables office/contact in this workstream's report copy. | safety_reports.weekly_generate |
| `safety_reports.intake.allowed_senders` | str | *(unset)* | Comma-separated sender allowlist for the intake extraction path (the retired email-PDF intake; the live path is the portal PULL). Empty = none set. | safety_reports.intake |
| `safety_reports.intake.box_filing_enabled` | bool | true | Whether intake files the rendered safety PDF to Box. Off keeps the pipeline running but skips the Box upload. | safety_reports.intake |
| `safety_reports.intake.classification_model` | str | claude-sonnet-4-6 | The Anthropic model the intake extraction/classification step uses. Legacy email-intake path; dormant. | safety_reports.intake |
| `safety_reports.intake.confidence_threshold` | float | 0.75 | Extraction-confidence floor (0–1). Below it, an item routes to the Review Queue instead of being trusted (Op Stds confidence scoring). | safety_reports.intake |
| `safety_reports.intake.mailbox` | str | safety@evergreenmirror.com | The mailbox the (now-dormant, legacy) safety email-intake path read from. The live path is the portal PULL model; this remains for the retired email caller. | safety_reports.intake |
| `safety_reports.intake.review_queue_on_low_confidence` | bool | true | Whether a below-threshold extraction is routed to the Review Queue (true) rather than dropped. | safety_reports.intake |
| `safety_reports.photo_screen.clamav_enabled` | bool | false | Turns on the ClamAV leg of the §34 photo screen (magic + Pillow verify + re-encode always run; this adds the AV scan). Default OFF. | safety_reports.intake, safety_reports.portal_poll |
| `safety_reports.portal.worker_base_url` | str | *(unset)* | Shared Worker base URL; owned by safety_reports, read here too. | field_ops.fieldops_sync, po_materials.po_poll, safety_reports.portal_poll, safety_reports.publish_daemon, watchdog |
| `safety_reports.portal_poll.polling_enabled` | bool | true | Runtime on/off gate for the safety_reports.portal_poll daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | safety_reports.portal_poll |
| `safety_reports.publish_daemon.polling_enabled` | bool | false | Runtime on/off gate for the safety_reports.publish_daemon daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | safety_reports.publish_daemon |
| `safety_reports.weekly_generate.job_timeout_seconds` | int | 600 | Per-job wall-clock ceiling (seconds) for the safety_reports.weekly_generate weekly compile; a job exceeding it is fenced to the Review Queue, not left to hang. | safety_reports.weekly_generate |
| `safety_reports.weekly_generate.merge_memory_ceiling_bytes` | int | 268435456 | Memory ceiling (bytes) for the safety_reports.weekly_generate PDF-merge step; a packet whose merge would exceed it is refused rather than risk OOMing the daemon. | safety_reports.weekly_generate |
| `safety_reports.weekly_send.from_mailbox` | str | safety@evergreenmirror.com | The M365 mailbox the safety_reports.weekly_send send daemon sends approved email FROM. | safety_reports.weekly_send, safety_reports.weekly_send_poll |
| `safety_reports.weekly_send.polling_enabled` | bool | true | Runtime on/off gate for the safety_reports.weekly_send daemon. False pauses it without unloading its launchd job (the canonical runtime gate, distinct from the report-filter Enabled checkbox). | safety_reports.weekly_send_poll |
| `safety_reports.weekly_send.scheduled_send_local` | str | MON 07:00 | Local-time window (e.g. `MON 07:00`) at/after which a row approved with **Approve for Scheduled Send** may dispatch on the safety_reports.weekly_send path. | safety_reports.weekly_send_poll |

## Where this comes from

Each daemon declares the keys it reads in a `REQUIRED_CONFIG` list in its own source file (the observable-config ledger, issue #336); the shared-infrastructure keys are read by shared helpers. This dictionary is the union of those declarations, so it stays in step with the code. To refresh it after a config change, run `python -m scripts.generate_config_dictionary` and re-record its sha256 in the enablement manifest.
