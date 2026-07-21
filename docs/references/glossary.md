---
type: reference
date: 2026-07-15
status: active
workstream: null
tags: [documentation-corpus, tier-1, glossary]
---

# ITS Glossary

## Purpose

The controlled vocabulary of ITS — the terms an operator meets in the dashboard, in alerts,
in the runbooks, and in the other Tier-1 references. Definitions are operational (what the
term means when you are running the system), not academic. Where a term is a literal value or
symbol in code, the invisible source comment points at the definition site.

This is a companion to [documentation_index.md](documentation_index.md) (the corpus map) and is
cross-referenced by every other Tier-1 reference. When a doc uses a term of art, it means what
this file says it means.

## The two invariants (the non-negotiables)

<!-- src: CLAUDE.md "## System-wide invariants (Foundation Mission v11)" | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **External Send Gate** / **Invariant 1** | No external transmission without explicit human approval — permanent, not time-boxed. Realized as the **two-process model**. The single most important security boundary in ITS. |
| **Two-process model** | Generation scripts (which call the LLM) have **zero** send capability; send scripts (which transmit) have **zero** AI. A prompt injection at the AI layer cannot cause a send because the transmitter is a different process. Enforced in code by `tests/test_capability_gating.py`. |
| **Adversarial Input Handling** / **Invariant 2** | All content originating outside the operating customer tenant is untrusted data. Six-layer defense (see [security_trust_model.md](security_trust_model.md)); the damage ceiling is "extracted data is wrong," never "data exfiltrated" or "external action taken." |

## Send-gate & approval vocabulary

<!-- src: shared/approval_verification.py; safety_reports/send_poll_core.py (verify_approval) | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **F22** | The send-approval **attestation** gate. Before any send poller dispatches a row, `verify_approval` re-checks that the driving approval checkbox was genuinely set by an authorized approver and stamps the verified `Approved By` / `Approved At`. **Fail-closed**: an empty approver set blocks all sends. |
| **§46 authorization-by-workspace-share** | The approver set for a workstream **is** the Smartsheet workspace's share list. To grant someone send-approval authority you share them into the workstream's ITS workspace; to revoke it you unshare them. |
| **`*_Pending_Review` / `*_human_review`** | The human-in-the-loop approval sheet for a workstream: `WSR_human_review` (safety), `WPR_human_review` (progress), `PO_Pending_Review` (purchase orders), `RFQ_Pending_Review` (outbound RFQs), `Subcontract_Pending_Review`. A row carries `Approved for Send` / `Approved By` / `Approved At` / `Sent At` / `Send Status`, plus a `Workstream` lane tag (see [Workstream tags](#workstream-tags)). |
| **Send Now / Approve for Scheduled Send** | The two approval checkboxes on a review row: immediate dispatch vs the batched Monday-morning (or per-workstream) scheduled window. |

## HELD send states

<!-- src: safety_reports/weekly_send.py (held_* Send Status values) | verified 2026-07-15 -->

A **HELD** row is one the send poller deliberately did **not** send — an operator-actionable
stop, never a silent drop. The `Send Status` value names the reason:

| Value | Meaning / operator action |
|-------|---------------------------|
| `held_no_recipient` | The row's resolved TO address is empty/unknown (recipients resolve at send time from the active-jobs sheet). Fix the job's contact, then re-approve. |
| `held_missing_pdf` | The compiled packet PDF is absent. Re-compile the week, then re-approve. |
| `held_missing_envelope` | Required addressing/envelope metadata is missing. |
| `held_oversized_packet` | The compiled packet exceeds the upload-session ceiling (~150 MB). Operator-actionable — never silently sent. |
| `held_workstream_mismatch` | The row's `Workstream` tag is not the one this sender serves (the cross-workstream contamination guard). Hard-held + CRITICAL. |
| `held_failed` | A transient send failure that exhausted retries and is now held for operator attention. |

Watchdog **Check T** is the daily backstop that surfaces any row stuck HELD past its staleness
window; **Check U** surfaces approver-set drift.

## Daemon, liveness & control vocabulary

<!-- src: shared/kill_switch.py:4-8 (ACTIVE/PAUSED/MAINTENANCE); shared/heartbeat.py; scripts/watchdog.py (Check C); shared/circuit_breaker.py | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **Kill switch** | The `system.state` row in ITS_Config: **ACTIVE** (run normally), **PAUSED** (scheduled scripts skip; watchdog still alerts on missed runs), **MAINTENANCE** (skip; watchdog does not alert). **Fail-open** by design (unreachable/missing/invalid → ACTIVE + WARN) — an operator convenience, **not** the security boundary. |
| **`@require_active`** | The decorator that calls the kill switch at daemon entry. |
| **`polling_enabled` (runtime gate)** | The canonical per-daemon on/off switch: `<workstream>.<daemon>.polling_enabled` in ITS_Config. This — **not** the ITS_Daemon_Health `Enabled` checkbox (ARCH-1) — is what a running daemon reads. |
| **Dark gate / ships dark** | A capability whose `polling_enabled` (or `sync_enabled`) row is **false** at merge, so the code is present but inert. A dark gate has a **seeded** config row (value `false`) so activation is a visible cell-flip, not a phantom. |
| **Heartbeat** | The ITS_Daemon_Health row a daemon updates in place each cycle (`shared/heartbeat.py`). The operator-visibility surface; failure to write it never blocks the daemon's real work. |
| **Marker (Check-C marker)** | The `~/its/.watchdog/<slug>.last_run` timestamp file a `TRACKED_JOBS` daemon writes each cycle. Watchdog **Check C** WARNs when a marker goes stale — the staleness floor that catches a silently-dead poller. |
| **Circuit breaker** | `shared/circuit_breaker.py`. Repeated Smartsheet failures **open** the breaker, pausing writes for a short cooldown; it auto-recovers. A prolonged-open breaker is watchdog **Check J**. Clearing a stuck breaker (delete the local state file) is a low-capability-class Successor-Operator action. |
| **Triple-fire CRITICAL** | A CRITICAL alert fans out to three independent legs — ITS_Errors row + Resend email + Sentry capture — each independently guarded so one failing never blocks the others; deduped per `(script, error_code)`. |
| **Correlation ID** | The identifier threaded across all three CRITICAL legs and into ITS_Daemon_Health, so an alert email links back to its ITS_Errors row. |

## Data stores & review surfaces

<!-- src: shared/sheet_ids.py; shared/review_queue.py; shared/quarantine.py; see data_model_reference.md | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **D1** | The Cloudflare SQLite database at the edge — the **system of record** for portal submissions and field-ops capture (send-free queue). |
| **Worker** | The Cloudflare (Hono) edge application — the send-free D1 queue + HMAC-signing/validation layer for every workstream. |
| **SPA** | The React single-page app the field/office users interact with (`safety_portal/src`). |
| **ITS_Config** | The Smartsheet settings sheet. Reads are **workstream-scoped** (Setting name AND Workstream cell). The canonical runtime gates live here. |
| **ITS_Errors** | The per-occurrence error record sheet (one row per occurrence; the sole per-occurrence record). |
| **ITS_Review_Queue** | The below-confidence-threshold / flagged-item queue. `Status` ∈ PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED. Ambiguity routes here, never to silent success. |
| **ITS_Quarantine** | Where non-allowlisted or malicious inbound content is parked (Invariant 2, Layer 1/6). |
| **ITS_Active_Jobs / _Progress** | The current-jobs sheets from which send recipients resolve at send time (safety and progress twins). |
| **Rollup row / Compile Now** | On a week sheet, the Rollup snapshot row carries a **Compile Now** checkbox that triggers the on-demand compile poller. |
| **Confidence threshold** | Default 0.85 on any LLM extraction; below it the item routes to the Review Queue. |

## Security & adversarial-input vocabulary

<!-- src: shared/untrusted_content.py; shared/anomaly_logger.py; safety_reports/photo_screen.py; po_materials/po_attach_screen.py; shared/portal_hmac.py | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **Untrusted-content wrap** | `shared.untrusted_content.wrap()` + the canonical system boilerplate around every LLM call that processes external content (Invariant 2, Layer 2). |
| **Anomaly logger (tripwire)** | `shared.anomaly_logger.check()` — a **post-hoc detection tripwire**, NOT a barrier (Layer 5, reframed audit F13). It raises a signal that an output matched a suspicious pattern; prevention is Layers 2–4 + the Send Gate. |
| **§34 attachment/photo screening** | The Layer-6 pipeline: magic-number/size → format-aware structural inspection → optional ClamAV. Realized for portal photos (`photo_screen.py`) and PO documents (`po_attach_screen.py`); malicious → CRITICAL + refused before filing. |
| **HMAC trust boundary** | Every queued submission/photo is signed by the Worker and **re-verified on the Mac** (constant-time) before intake files it; domain-separated per protocol (`item_photo:v1`, `daily_photo:v1`, `po:v1`, `sub:v1`) so cross-protocol confusion is impossible. |
| **Tailscale** | The private mesh network over which localhost-only services (the dashboard) are reached. ITS exposes **nothing** to the public internet. |

## Operator roles & escalation

<!-- src: CLAUDE.md "## Maintenance & successor-operator model (FM v11 · Op Stds v21 §§43–44)" | verified 2026-07-15 -->

| Term | Meaning |
|------|---------|
| **Developer-Operator** | Seth — git/CC/shell-fluent; performs all code changes, Keychain access, doctrine work. The Tier-3 escalation asset. |
| **Successor-Operator** | The trained Tier-2 operator who runs Claude Code and performs **low-capability-class**, **documented** repairs (re-run a daemon, toggle a documented config value, re-send an approval, re-seed a row, clear a stuck lock). Writes no code, touches no secrets. |
| **Tier 1 / 2 / 3** | Self-heal (launchd + watchdog) / Claude-assisted Successor-Operator repair / escalate to the Developer-Operator. |
| **The both-rule** | A fault is Successor-Operator-resolvable **only if** documented (has a §43 runbook entry) **AND** low-capability-class. **Novel OR high-class → escalate.** |
| **Four fixed high-capability-class categories** | (1) External Send Gate, (2) secrets/auth, (3) doctrine, (4) code changes — always escalate, regardless of documentation. The golden rule: **when unsure, escalate.** |

See [escalation_matrix.md](escalation_matrix.md) for the full symptom→class table.

## Operationally-cited doctrine sections (§N)

<!-- src: CLAUDE.md (Op Stds §N citations, resolved against Operational Standards v21) | verified 2026-07-15 -->

These `§N` references resolve against **Operational Standards v21** (canonical in the planning
repo). Paraphrased operationally here; the blueprint holds the authoritative text.

| § | Operational meaning |
|---|---------------------|
| **§1** | Kill switch is a fail-open operator convenience, not a security control. |
| **§14** | Preservation-over-refactor; **parameterize, not clone**. |
| **§30** | SDK-vs-Live integration discipline — a live smoke, not just mocks. |
| **§31** | The polling-daemon-via-launchd pattern is canonical for intake-bearing workstreams. |
| **§34** | The four-sub-layer attachment-screening pipeline. |
| **§43** | Every Tier-2-reachable failure ships a successor-remediation runbook entry (definition-of-done). |
| **§44** | The three-tier maintenance model + the both-rule (training-bounded, not structurally enforced). |
| **§46** | Authorization-by-workspace-share (approver set = workspace share list). |
| **§50** | Privileged code-actuation gate — the cloud enqueues, the Mac's privileged actuator commits/deploys. |
| **§51** | ITS-owned structured-SoR write-back (the field-ops → Active-Jobs mirror, the `*_Log` sheets). |
| **§52–§54** | Narrated-not-enforced; sandbox-masks-production; runtime secret/PII-leak backstop. |
| **§55** | Verification & Truthful-Reporting Discipline (verify-before-asserting; prove-the-control-bites; four-part landing verify; faithful reporting). |

## Workstream tags

There are **two** closed "workstream" vocabularies in ITS. They are not the same list, and
conflating them is a real bug class — one governs *documents*, the other governs *send routing*.

### 1. The doc-frontmatter taxonomy

<!-- src: scripts/lint_doc_conventions.py:77-92 (CANONICAL_WORKSTREAMS); docs/operations/doc_conventions.md "## Workstream taxonomy"; docs/doctrine_manifest.yaml workstream_tags | verified 2026-07-19 -->

The `workstream:` value in every doc's YAML frontmatter. The closed set is
`safety_reports`, `safety_portal`, `progress_reports`, `field_ops`, `po_materials`,
`subcontracts`, `operator_dashboard`, `box`, `ci`, `security`, `docs`, `infrastructure`,
and `null` (cross-cutting). It lives in **three** copies that must stay in sync —
`scripts/lint_doc_conventions.py` `CANONICAL_WORKSTREAMS`, the table in
`docs/operations/doc_conventions.md`, and `docs/doctrine_manifest.yaml` `workstream_tags`.
`po_materials_rfq` is **not** in this set: the RFQ/estimate lane is a `po_materials`
sub-lane, so its docs (`rfq_generation_path.md`, `rfq_send.md`,
`estimate_import_path.md`) carry `workstream: po_materials`.

### 2. The send-lane row tag

<!-- src: safety_reports/weekly_send.py:249,288 (SendConfig.workstream_tag + guard); progress_reports/progress_send.py:111; po_materials/po_send.py:135; po_materials/rfq_send.py:191 (rfq_review.WORKSTREAM_TAG); subcontracts/subcontract_send.py:149; shared/picklist_validation.py:122,133,265,324,441 (the per-review-sheet Workstream value sets) | verified 2026-07-19 -->

The value in a review sheet's **`Workstream` cell**, matched by the shared send engine's
**contamination guard**: a row whose tag ≠ the dispatching `SendConfig.workstream_tag` is
HARD-HELD (`held_workstream_mismatch` + CRITICAL) before the write-ahead `SENDING` marker,
so cross-lane dispatch is structurally impossible. Five values, one per send lane:

| Review sheet | Row tag | Sender |
|---|---|---|
| `WSR_human_review` | `safety` | `safety_reports.weekly_send` |
| `WPR_human_review` | `progress` | `progress_reports.progress_send` |
| `PO_Pending_Review` | `po_materials` | `po_materials.po_send` |
| `RFQ_Pending_Review` | `po_materials_rfq` | `po_materials.rfq_send` |
| `Subcontract_Pending_Review` | `subcontracts` | `subcontracts.subcontract_send` |

Each set is registered per sheet in `shared/picklist_validation.REGISTRY`, which gates every
`update_rows` write — a wrong tag raises `PicklistViolationError` rather than silently
routing. Note these are **report-family / lane** names, not the frontmatter taxonomy:
`safety` and `progress` are row tags with no frontmatter counterpart, and `po_materials_rfq`
is a row tag only. The RFQ **ledger** (`RFQ_Log`) is the exception that proves the
distinction — its `Workstream` column takes `po_materials` (it is not a send surface); only
the `RFQ_Pending_Review` send surface carries `po_materials_rfq`.

## Related docs

- [system_architecture.md](system_architecture.md) — the machine these terms describe
- [daemon_reference.md](daemon_reference.md) — every daemon by name
- [data_model_reference.md](data_model_reference.md) — the sheets, tables, and Box topology
- [integration_reference.md](integration_reference.md) — the external systems
- [security_trust_model.md](security_trust_model.md) — the invariants and defense layers in depth
- [escalation_matrix.md](escalation_matrix.md) — the symptom→class resolution table
- [documentation_index.md](documentation_index.md) — the full corpus map
