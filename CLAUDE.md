# CLAUDE.md — Project Context for Claude Code

You are working inside the execution layer of **ITS — Integrated Technical System**, a
Claude-powered computer employee. The planning layer lives in a separate Claude.ai project;
this repo implements what is decided there.

## Product context

ITS is a **white-glove custom-development practice**. Each customer gets a fully-customized
build forked from the ITS blueprint and maintained in their own private repository. Evergreen
Renewables is **Customer 0** — the first deployment and design partner, receiving the build
at no cost during validation. Solution Smith retains the right to fork the blueprint for
additional construction and renewables customers; the blueprint itself is the reusable
artifact, not a multi-tenant SaaS product. This repo is Evergreen-specific.

This is **production-quality, defensively-built** work. Appropriate for a deployable system
at 10–50 person construction firm scale. High availability is not required, but failures must
be observable, recoverable, and never silent. Permanent human-in-loop on all external send paths.

## Architectural model

Two layers, deliberately separated:

1. **Planning & Foundation** (Claude.ai project, not in this repo). Mission files, architectural
   decisions, owner-facing artifacts, prompt designs, schemas. Canonical docs: Foundation Mission
   v8, Operational Standards v11, Vision & Roadmap v7.2, Handover Plan v6.3.
2. **Execution** (this repo). Claude Code scripts on a MacBook, triggered by launchd, Mail.app
   rules, and Shortcuts. Reads/writes Smartsheet (structured data), Box (documents), Outlook
   (communication) via APIs. Calls Anthropic API for reasoning steps.

Smartsheet, Box, Outlook are systems of record — unchanged by ITS.

## System-wide invariants (Foundation Mission v8)

These are non-negotiable. Every workstream inherits both.

### Invariant 1 — External Send Gate (permanent)

No external transmission without explicit human approval. **Permanent, not time-bounded.**
Earlier framing in Op Stds v4 that described review as a 30–60 day window is superseded.

- Every workstream that produces customer-facing output uses a `<Workstream>_Pending_Review`
  Smartsheet sheet with `Approved for Send` / `Approved By` / `Approved At` / `Sent At` /
  `Send Status` columns.
- **Two-process model.** Generation scripts (which call the Anthropic API) have zero send
  capability. Send scripts (which transmit) have zero AI step.
- Successful prompt injection at the AI layer cannot cause external transmission, because the
  AI is in a different process from the transmitter.
- Enforced at the code level by `tests/test_capability_gating.py` — add every generation script
  and every send script to the appropriate list there.

### Invariant 2 — Adversarial Input Handling

All content originating outside the operating customer tenant is untrusted data. Six-layer defense:

1. **Sender allowlist + scope enforcement + header-forgery detection.** Polling daemon
   (canonical pattern per Op Stds v11 §31; `safety_reports/intake_poll.py` is the first
   consumer) fetches from allowlisted senders via Graph; non-allowlisted email routes to
   Quarantine. ITS_Trusted_Contacts sheet (Op Stds v11 §33) is the canonical allowlist
   mechanism, replacing ITS_Config JSON lists at Phase 1.4 cutover. Header-forgery detection
   (SPF/DKIM/DMARC + Return-Path validation) precedes allowlist lookup. Helpers in
   `shared/quarantine.py`.
2. **Untrusted-content tagging.** Every Anthropic API call processing external content uses
   `shared.untrusted_content.wrap()` and the canonical system-prompt boilerplate.
3. **Capability gating.** AI has no permission to send or take action (see Invariant 1).
4. **Structured output enforcement.** Anthropic tool-use forces JSON-schema-conforming
   responses; non-conforming rejected.
5. **Output validation and anomaly logging.** `shared.anomaly_logger.check()` runs on every
   extraction output. Anomalies route to `ITS_Review_Queue` with `security_flag=True`.
6. **Attachment screening pipeline.** Every attachment passes through four sub-layers per
   Op Stds v11 §34: (a) static signatures (magic-number, size, filename); (b) format-aware
   structural inspection (PDF JS/embedded, Office macros); (c) ClamAV scan via pyclamd;
   (d) optional VirusTotal hash check (Phase 2+ enhancement). Malicious → ITS_Quarantine +
   CRITICAL triple-fire + sender DISABLED in ITS_Trusted_Contacts pending operator review.
   Implementation scheduled Phase 1.4 pre-Customer-1 hardening.

Residual risk: prompt injection is an unsolved research problem. The architecture assumes
injection might succeed at the AI layer and ensures the damage ceiling is "extracted data is
wrong" rather than "data exfiltrated" or "external action taken on attacker's behalf."

## Operational conventions — load-bearing

Every workstream script MUST follow these. Deviations get raised in the planning project first,
not invented locally.

- **Kill switch first.** Call `shared.kill_switch.check_system_state()` (or use
  `@require_active`) at script entry. PAUSED or MAINTENANCE → exit cleanly.
- **Error log decorator.** Wrap every script's main function in `@its_error_log(script_name=...)`.
  Catches unhandled exceptions, writes to `ITS_Errors` sheet, surfaces CRITICAL via email + SMS.
- **Confidence scoring on extractions.** Default threshold 0.85. Below threshold → routes to
  `ITS_Review_Queue`, not silent success.
- **External Send Gate.** Per Invariant 1. No generation script imports `graph_client.send_mail`.
  No send script imports `anthropic_client` or any AI capability.
- **Adversarial Input Handling.** Per Invariant 2. Every prompt processing external content
  includes the untrusted-content boilerplate. Every extraction output passes through
  `anomaly_logger.check()` before being trusted.
- **Credentials from macOS Keychain.** Never env files, never committed. Use
  `shared.keychain.get_secret(name)`.
- **Schemas in `schemas/`. Prompts in `prompts/`.** Both version-controlled. JSON schemas have
  a `version` field; scripts reject responses on schema mismatch.

## Sandbox-first build pattern

ITS is built in a sandbox tenant (M365 `evergreenmirror.com`, Smartsheet, Box) before cutover
to live tenants. The mirror has matching subscription tiers and is populated with
closed/expired Evergreen documents for end-to-end validation without touching production.
Cutover happens at the Phase 1 → 1.5 gate, then again at Florida → customer-site hardware
shipment.

## What's stubbed vs. real (current scaffold state)

| Module | State | Notes |
|--------|-------|-------|
| `shared/keychain.py` | Working, tested | macOS-only; uses `security` CLI. |
| `shared/error_log.py` | Working, tested | Local file + Smartsheet `ITS_Errors` write (recursion-guarded; INFO env-gated via `ITS_ERROR_LOG_INFO=1`) + triple-fire CRITICAL path (Resend operator email + Sentry structured event). Each alert leg has its own recursion guard and broad-except failure isolation — a failure of one leg does NOT prevent the other. Correlation-ID threading shared across all three legs (`Correlation_ID` column on ITS_Errors); Resend-leg dedupe via `shared/alert_dedupe.py` on `(script, error_code)` key per Op Stds v9 §3 push-vs-record separation. PR #42 (PR α). |
| `shared/alert_dedupe.py` | Working, tested | Resend-leg dedupe state at `~/its/state/alert_dedupe.json` under `fcntl.LOCK_EX|LOCK_NB` with bounded retry. Public API: `should_fire(key)` / `record_fire(key)` (PR α) + `list_expired_summaries()` / `mark_summarized(key)` / `delete_entry(key)` (PR β consumed by watchdog Check G). Window value from `alerting.dedupe_window_minutes` ITS_Config row (default 60 min via `defaults.ALERTING_DEDUPE_WINDOW_MINUTES`). Fail-open on every state error — false positives (extra emails) acceptable, false negatives (missed wake-ups) not. PR #42 (PR α) + PR #44 (PR β). |
| `shared/resend_client.py` | Working, tested | Transactional-email client for operator alerts. API key from Keychain (`ITS_RESEND_API_KEY`). Used by `error_log._alert_critical`. NOT for customer email — that's `graph_client.send_mail` (Invariant 1). Live smoke green 2026-05-18 using Resend's sandbox sender (`onboarding@resend.dev`). |
| `shared/sentry_client.py` | Working, tested | Sentry SDK wrapper for CRITICAL-event structured capture. DSN from Keychain (`ITS_SENTRY_DSN`). Used by `error_log._alert_critical`. Performance monitoring off (`traces_sample_rate=0.0`); send_default_pii=False. Live smoke green 2026-05-18 — events arrive at the operator's Sentry project. |
| `shared/kill_switch.py` | Working, tested | Reads `system.state` from ITS_Config via `smartsheet_client.get_setting`; fail-open on three modes (sheet unreachable / row missing / invalid value) with distinguishable WARN. Wired 2026-05-18. |
| `shared/anthropic_client.py` | Working, live-validated end-to-end | Reads `ITS_ANTHROPIC_KEY` from Keychain. First production consumer is `safety_reports/weekly_generate.py` (R3 Session 2) — manual smoke run 2026-05-22 confirmed live tool-use call against Sonnet 4.6 with the `generate_weekly_project_report` schema produced a 4000-char structured WPR draft. No dedicated test file; covered transitively by `tests/test_weekly_generate.py`. |
| `shared/smartsheet_client.py` | Working, tested | SDK wrapper with title-keyed reads/writes, typed exception hierarchy, lazy keychain-backed client. Wired 2026-05-18. |
| `shared/box_client.py` | Working, tested | boxsdk OAuth2 User Authentication. Refresh tokens rotate on every exchange — `_store_tokens` callback persists the new token to Keychain (CRITICAL invariant; if `_store_tokens` ever stops writing, ITS dies in 60 days; `test_store_tokens_persists_refresh_token` locks the invariant). Auth as operator user in sandbox (seths@evergreenmirror.com); dedicated ITS user at Phase 1.5 cutover per Permissions Ask v4 + Handover Plan v6.3. Setup: `scripts/setup_box_oauth.py` (one-time, interactive). Smoke: `scripts/smoke_test_box.py`. PR #39 / commit 2ce6ece. |
| `shared/graph_client.py` | Working, tested | MSAL client-credentials + Mail API wrappers (`list_inbox`, `get_message`, `list_attachments`, `download_attachment`, `mark_read`, `move_message`, `send_mail`). Sandbox tenant `evergreenmirror.com` verified 2026-05-17 via `scripts/smoke_test_graph.py`. |
| `shared/review_queue.py` | Working, tested | `add()` writes a row to `ITS_Review_Queue` and returns the row ID; `get_status()` reads back by Item ID. Item ID format: `<workstream>-<YYYYMMDD>-<HHMMSS>` UTC. Smartsheet failures propagate so workstream callers can fire CRITICAL via error_log. Live schema differed from brief: `Reason` is PICKLIST (added `ReviewReason` enum) + new `Severity` and `Source File` columns. |
| `shared/untrusted_content.py` | Working, tested | Invariant 2 — XML tagging + system boilerplate. |
| `shared/anomaly_logger.py` | Working, tested | Invariant 2 — sentinel pattern checks. |
| `shared/quarantine.py` | Working, tested | Both `is_allowlisted` and `log_quarantined_message` wired. The logger writes to ITS_Quarantine with sender / subject / received_at / summary / workstream cells; Smartsheet failures propagate (silent failure here loses an audit record so callers must elevate). Workstream picklist catch-all is `other` (NOT `global` — differs from ITS_Review_Queue). |
| `shared/scheduling.py` | Holiday shifts + reviewer chain + PTO fetcher working, tested. Chain-override fetcher (`_no_override`) still stubbed. | `ITS_Time_Off` + `ITS_Config` sheets provisioned 2026-05-17; `smartsheet_client.py` wired 2026-05-18. `_live_fetcher` reads `ITS_Time_Off` with per-instance caching (PR #35, 2026-05-20). Chain-override real fetcher PR queued — separate PR when a workstream actually exercises overrides per planning decision D-i.1a. |
| `shared/sheet_ids.py` | Working | Bootstrap module. Holds workspace/folder/sheet IDs for the three workspaces + master DB sheet constants (`SHEET_VENDOR_DB`, `SHEET_SUBCONTRACTOR_DB`, `SHEET_EQUIPMENT_MASTER`) + `SHEET_PICKLIST_SYNC_CONFIG`. |
| `shared/picklist_sync.py` | Working, tested | Cross-sheet PICKLIST option sync from master DBs. Pure-function core (`extract_unique_values`, `compute_diff`, `compute_hash`, `_resolve_size_thresholds`) + `sync_one_mapping` / `sync_all` driver. Reference-checked removals (live cell usage blocks delete → Review Queue row, `Reason=mismatched-reference`). Two-stage size guardrails (200 WARN, 400 HARD-HALT, configurable). Triple-fire on ≥3 mappings failed via correlation_id-threaded `_alert_critical`. Idempotency via SHA-256 of sorted source values stored in `Picklist_Sync_Config.last_run_hash`. Hourly cron via `scripts/run_picklist_sync.py`. PR #46. |
| `shared/defaults.py` | Working | Module-level constants for cross-cutting fallbacks. `DEFAULT_REVIEWER_CHAINS` (reviewer identity), `ALERTING_DEDUPE_WINDOW_MINUTES`, `PICKLIST_SIZE_WARN_THRESHOLD` / `PICKLIST_SIZE_HARD_HALT_THRESHOLD` / `PICKLIST_SIZE_THRESHOLD_MAX`. ITS_Config rows override at runtime; these are the fallback used when the row is missing or invalid. |
| `scripts/watchdog.py` | Working, tested. 6 of 7 checks operational (E deferred). | Checks A (stale review queue) + B (open CRITICALs) shipped Session 1 (PR #33). Checks C (scheduled-jobs marker scaffold + `write_last_run_marker` helper; `TRACKED_JOBS=[]` by design until a second scheduled job ships), D (14-day reviewer-chain forward scan per Op Stds v9 §18), F (mail-intake silent-disable) shipped Session 2 (PR #36). Check G (alert-dedupe summary sweep — Resend-only push, fires summary email for expired+suppressed entries; two-phase deletion for crash safety; defers phase-1 during MAINTENANCE per V1 fix) shipped PR #44 (PR β) + PR #52 (MAINTENANCE defer). Check E (Anthropic spend trend) deferred to a follow-on PR (the Check E shipping PR) / Phase 1.5 — Admin API key prerequisite, architectural choice not capability gap (see `docs/tech_debt.md`). Live smoke at `scripts/smoke_test_watchdog.py` + Check G live smoke at `scripts/smoke_test_watchdog_summary.py`. |
| `scripts/run_picklist_sync.py` | Working, tested | Hourly launchd-driven entry point for picklist sync. CLI: `--dry`, `--mapping <id>`, `--smoke-test`. `@require_active` (kill-switch-aware) outer + `@its_error_log` inner. Sandbox-only smoke mode bootstraps + exercises full add/remove-safe/remove-blocked flow + tears down. PR #46 / hardened PR #50. |
| `safety_reports/intake.py` | Working, live-validated end-to-end | 12-stage pipeline (PR #57, c4c4bc9). `process_message(message_id)` extracted in PR #59 as the public API invoked by `intake_poll.py` per message. `SmartsheetError`/`GraphError` soft-fail returns rather than raise. Stages 1-9 + 11-12 live; Stage 10 (attachment screening per Op Stds v11 §34) planned for Phase 1.4 pre-Customer-1 hardening. 1083 lines. |
| `safety_reports/intake_poll.py` | Working, live in production | Polling daemon (PR #59, f1e724f). Replaces Mail.app rule trigger per Op Stds v11 §31. Per-cycle: `polling_enabled` ITS_Config gate, fcntl file lock at `~/its/state/safety_intake.lock`, `graph_client.list_inbox` unread_only top=50, seen-set idempotency guard, `intake.process_message`, `mark_read` on success, heartbeat write to ITS_Daemon_Health. 632 lines. 60s launchd cadence; 242+ confirmed cycles. |
| `safety_reports/week_folder.py` | Working, tested | Per-project per-week Field Reports folder + Daily Reports + Weekly Rollup scaffolding (PR #54, ed46a96). Idempotent find-or-create. Race-condition tech-debt entry tracks the find-after-create gap. 168 lines. |
| `safety_reports/weekly_generate.py` | Working, live-validated end-to-end | Generation half of the External Send Gate two-process model per FM v8 Invariant 1 (R3 Session 2). Friday 14:00 launchd `StartCalendarInterval`. Per-cycle: monday_of_week target resolution, empty-chain CRITICAL abort, per-project ensure_current_week_folder + Daily Reports + Weekly Rollup reads, `WPR_Pending_Review` add/update with `Approved for Send=false`, idempotent replace-if-unapproved + refuse-if-approved, ZERO_DATA_WEEK placeholder branch, low-confidence + security-trigger dual writes to `ITS_Review_Queue`. Watchdog Check C marker `safety_weekly_generate.last_run` with 8-day per-job window. **Per-project fence: single-shot retry on `SmartsheetNotFoundError` (500 ms) — bumps `summary.retries_attempted`; retry exhaustion OR any non-404 error writes a `GENERATION_FAILED` placeholder row so the operator queue never has a silent gap** (one-row-per-(Job,Week) invariant; respects existing approved rows). Capability-gated: `graph_client`, `send_mail`, `resend`, `smtplib`, `email.mime` AST-forbidden. Manual smoke 2026-05-22 confirmed real draft (Bradley 1 backfill week) + 4 ZERO_DATA placeholders + soft-fail per-project fence. ~900 lines. |
| `safety_reports/weekly_summary.py` | DEPRECATED | Stub kept in-tree for one cycle so any orphan launchd reference surfaces as explicit NotImplementedError. Delete in follow-on cleanup PR once `org.solutionsmith.its.weekly-generate` plist is loaded on the production MacBook. |
| `safety_reports/weekly_send.py` | Working, live-validated end-to-end | Send half of the External Send Gate two-process model per FM v8 Invariant 1 (R3 Session 3). `send_one_row(row_id)` is the per-event handler invoked by `weekly_send_poll` per approved row. 7-stage pipeline: fetch / state-gate / recipients-validate / build / Graph send / late-send compute / row→SENT. Capability-gated: `anthropic_client`, `anthropic` AST-forbidden. Refuses on `[GENERATION_FAILED:` tag (belt-and-suspenders) and on empty Recipients (skip silently — `[NO_RECIPIENTS]` design hold). Advisory tags (`[ZERO_DATA_WEEK]`, `[LOW_CONFIDENCE]`, `[SECURITY_TRIGGER]`) do NOT gate — reviewer approval is the gate. Retry-state tag-encoded in Notes (`[SEND_RETRY_COUNT: N]`, `[LAST_SEND_ERROR: …]`) because the live `WPR_Pending_Review` schema lacks dedicated columns — graceful degrade per Op Stds v11 §23.3. MAX_SEND_RETRIES=3; CRITICAL triple-fire on Graph auth failure OR retry exhaustion. Manual smoke 2026-05-23 confirmed live send to seths@evergreenmirror.com with row marked SENT. ~480 lines. |
| `safety_reports/weekly_send_poll.py` | Working, smoke-validated | Polling daemon (R3 Session 3) — default 15-min `StartInterval`. Scans `WPR_Pending_Review` for rows with `Approved for Send=True` AND `Send Status ∈ {PENDING, FAILED}` AND `[SEND_RETRY_COUNT: N]` < MAX_SEND_RETRIES; dispatches each to `weekly_send.send_one_row`. Per-row fence (one bad row doesn't kill the cycle). Heartbeat helpers replicated VERBATIM from `intake_poll.py` per preservation-over-refactor — `shared/heartbeat.py` extraction is the next consolidation PR's job (tech-debt entry). Heartbeat row state file SHARED with intake_poll (`~/its/state/heartbeat_row_ids.json` keyed by daemon_name). Watchdog Check C marker `safety_weekly_send_poll.last_run` with 30-min freshness window (= 2 poll cycles). Smoke 2026-05-23 confirmed all stages green. ~470 lines. |

## Adding a new workstream

1. Draft a mission file in the planning Claude.ai project. Resolve open questions with owner.
2. Draft an engineering brief in the planning project.
3. Create `<workstream>/` directory here. Mirror the `safety_reports/` shape.
4. Schemas go in `schemas/`. Prompts go in `prompts/`. Reuse `shared/` helpers.
5. **Generation script and send script are separate files** (Invariant 1). Add both to the
   appropriate list in `tests/test_capability_gating.py`.
6. Every prompt that processes external content includes
   `shared.untrusted_content.system_boilerplate()` in the system prompt.
7. Every extraction output passes through `shared.anomaly_logger.check()` before use.
8. launchd plists live in `scripts/launchd/` as templates; `install.sh` copies them to
   `~/Library/LaunchAgents/` and loads them. **Polling daemons via launchd are canonical for
   intake-bearing workstreams** (Op Stds v11 §31; `safety_reports/intake_poll.py` is the
   canonical example). Shortcuts remain for manual operator-triggered jobs. Mail.app rules
   deprecated.

## Model selection

Default for reasoning calls: `claude-sonnet-4-6`. Use `claude-haiku-4-5-20251001` for
high-volume classification (Email Triage). Use `claude-opus-4-7` only where reasoning depth
genuinely justifies the cost (rare in this project).

Revisit model selection quarterly — Anthropic ships new models on a roughly six-month cadence.

## Observability stack (pre-Phase-1 add-ons)

Per the 2026-05-13 add-ons roadmap, the following ship in Phase 0:

- **Sentry** — exception tracking, wired into `shared/error_log.py`. Free tier.
- **UptimeRobot** — external heartbeat from `scripts/watchdog.py`. Catches "MacBook is dead"
  since the watchdog can't alert about itself.
- **Resend** — out-of-band CRITICAL alert path. Covers M365 outage suppressing its own
  outage alert.
- **GitHub Actions** — `ruff` + `pytest` on every push.

Deferred to Customer 2+: Better Stack (log aggregation), 1Password CLI (multi-customer
secrets), Helicone (LLM observability). Permanent skip: HashiCorp Vault, Snowflake,
LangChain, Kubernetes.

## Operator visibility surface

ITS_Daemon_Health sheet (System workspace / folder 04 — Daemons / sheet 4529351700729732) is
the canonical operator-visibility surface for all polling daemons. One row per daemon,
update-in-place per cycle. Push surface per Op Stds v11 §3.1 + §32.

- Schema: 12 columns per `shared.sheet_ids.DAEMON_HEALTH_COLUMNS` dict. See
  `ITS_Daemon_Health_Schema_2026-05-21.docx` in the planning project for full schema reference.
- Heartbeat write must NEVER block daemon primary work. Failure path: log to ITS_Errors
  category `daemon_health_write_failed`; daemon continues.
- ARCH-1: Enabled checkbox is report-filter metadata only. Canonical runtime gate is
  `<workstream>.<daemon>.polling_enabled` in ITS_Config.
- ARCH-2: Row-id cache persists to `~/its/state/heartbeat_row_ids.json`.
- ARCH-3: Total Cycles is lifetime monotonic, NOT daily reset.

## What NOT to do

- Don't add cloud-server execution. The architecture is local-first on MacBook through Phase 4.
  This repo is Evergreen-specific; future customers get their own private repos forked from
  the blueprint. Multi-tenant SaaS is not the model.
- Don't add a vector store before Phase 4. Premature.
- Don't expose SSH or any service to the public internet. Tailscale-only.
- Don't auto-approve at low confidence. Always route ambiguity to human review.
- Don't auto-send for any external recipient. Per Invariant 1. Permanent.
- Don't trust any external input. Per Invariant 2. All external content is untrusted data.
- Don't reproduce copyrighted material from any Box document or web fetch.

## Useful references in this repo

- `shared/` — start here when implementing a new workstream.
- `shared/untrusted_content.py` and `shared/anomaly_logger.py` — Invariant 2 mechanics.
- `tests/test_keychain.py` — canonical pattern for mocking an external CLI.
- `tests/test_error_log.py` — covers the CRITICAL surfacing path.
- `tests/test_capability_gating.py` — enforces Invariant 1 at the import level.
- `scripts/watchdog.py` — the daily watchdog skeleton.
- `scripts/launchd/template.plist` + `install.sh` — launchd trigger pattern.
- `docs/session_logs/` — durable narrative log of during-execution decisions. Write one at end of any session that lands ≥1 commit and involves a non-obvious decision. See `docs/session_logs/README.md` for the convention.
- `docs/operations/pr_merge_discipline.md` — canonical four-part verification protocol for landing a PR on main. The original three-assertion verify (`state=MERGED` / `mergedAt` non-null / `mergeCommit.oid` present) catches GitHub-side ghost merges (PR #34 case) but misses the post-merge `push: main` workflow failure that propagated PR #68→#73's red main. Step 4 (verify main-branch CI on the merge commit) is the new fourth gate; a PR that passes steps 1-3 but fails step 4 is **functionally not landed**.

Session-log line convention extended to four parts:
```
- pytest: <N> passed / <M> skipped / <D> deselected
- mypy: <E> errors / <F> source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

If something here contradicts the planning project's canonical docs (Foundation Mission v8,
Operational Standards v11), the planning project wins. Flag the inconsistency.
