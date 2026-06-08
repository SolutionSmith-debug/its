# CLAUDE.md — Project Context for Claude Code

You are working inside the execution layer of **ITS — Integrated Technical System**, a
Claude-powered computer employee. The planning layer lives in a separate Claude.ai project;
this repo implements what is decided there.

## Product context

ITS is a **white-glove custom-development practice**. Each customer gets a fully-customized
build forked from the ITS blueprint, maintained in their own private repository. Evergreen
Renewables is **Customer 0** — first deployment and design partner, build at no cost during
validation. Solution Smith retains the right to fork the blueprint for additional construction
and renewables customers; the blueprint is the reusable artifact, not a multi-tenant SaaS
product. This repo is Evergreen-specific.

This is **production-quality, defensively-built** work, for a deployable system at 10–50 person
construction firm scale. High availability is not required, but failures must be observable,
recoverable, and never silent. Permanent human-in-loop on all external send paths.

## Architectural model

Two layers, deliberately separated:

1. **Planning & Foundation** (Claude.ai project, not in this repo). Mission files, architectural
   decisions, owner-facing artifacts, prompt designs, schemas. Canonical docs: Foundation Mission
   v11, Operational Standards v16, Vision & Roadmap v9, Handover Plan v8.

   _Operational Standards is canonically at **v16** (`../its-blueprint/doctrine/operational-standards.md`,
   `status: canonical`); **v16 is the governing version — every `Op Stds §N` citation in this file
   resolves against it.** Numbering is append-only since v11, so no cited `§N` renumbered. Still-load-bearing
   reframes: §1 kill switch is an operator-convenience pause, fail-open by design, explicitly **not** a
   security control (audit F07) — the External Send Gate (FM Invariant 1) is the real security boundary;
   §44's Tier-2 boundary is **training-bounded co-resolution**, no structural maintenance enforcement layer
   built or required (see "Maintenance & successor-operator model" below); and FM v11 Invariant 2's Layer 5
   anomaly logging is a post-hoc detection tripwire, not a co-equal defense layer (audit F13). §§37–41, §42
   (code-level self-documentation), §43 (successor-remediation docs) all carried forward._
2. **Execution** (this repo). Claude Code scripts on a MacBook, triggered by launchd, Mail.app
   rules, and Shortcuts. Reads/writes Smartsheet (structured data), Box (documents), Outlook
   (communication) via APIs. Calls Anthropic API for reasoning steps.

Smartsheet, Box, Outlook are systems of record — unchanged by ITS.

## System-wide invariants (Foundation Mission v11)

These are non-negotiable. Every workstream inherits both.

### Invariant 1 — External Send Gate (permanent)

No external transmission without explicit human approval. **Permanent, not time-bounded.**
Earlier framing in Op Stds v4 that described review as a 30–60 day window is superseded.

- Every workstream that produces customer-facing output uses a `<Workstream>_Pending_Review`
  Smartsheet sheet with `Approved for Send` / `Approved By` / `Approved At` / `Sent At` /
  `Send Status` columns.
- **Two-process model.** Generation scripts (which call the Anthropic API) have zero send
  capability. Send scripts (which transmit) have zero AI step. Successful prompt injection at
  the AI layer cannot cause external transmission — the AI is in a different process from the
  transmitter.
- Enforced at the code level by `tests/test_capability_gating.py` — add every generation script
  and every send script to the appropriate list there.

### Invariant 2 — Adversarial Input Handling

All content originating outside the operating customer tenant is untrusted data. Six-layer defense —
but **Layer 5 is a post-hoc detection tripwire, not a co-equal defense layer** (reframed FM v9, audit
F13); the actual prevention is Layers 2–4 plus the two-process External Send Gate (Invariant 1, the
real security boundary):

1. **Sender allowlist + scope enforcement + header-forgery detection.** The polling-daemon
   pattern (canonical per Op Stds v16 §31; first exercised by the now-retired
   `safety_reports/intake_poll.py`, carried forward by Email Triage) fetches from allowlisted
   senders via Graph; non-allowlisted email routes to Quarantine. ITS_Trusted_Contacts sheet (Op Stds v16 §33) is the canonical allowlist
   mechanism, replacing ITS_Config JSON lists at Phase 1.4 cutover. Header-forgery detection
   (SPF/DKIM/DMARC + Return-Path validation) precedes allowlist lookup. Helpers in
   `shared/quarantine.py`.
2. **Untrusted-content tagging.** Every Anthropic API call processing external content uses
   `shared.untrusted_content.wrap()` and the canonical system-prompt boilerplate.
3. **Capability gating.** AI has no permission to send or take action (see Invariant 1).
4. **Structured output enforcement.** Anthropic tool-use forces JSON-schema-conforming
   responses; non-conforming rejected.
5. **Anomaly logging — detection tripwire, NOT a defense layer** (reframed FM v9, audit F13).
   `shared.anomaly_logger.check()` runs on every extraction output but does NOT *prevent* a
   successful injection — it raises a post-hoc signal that an output matched a known-suspicious
   pattern (exact-substring sentinel matching, trivially evaded by paraphrase), routing the item to
   `ITS_Review_Queue` with `security_flag=True`. Never rely on it as a barrier; prevention is
   Layers 2–4 + Invariant 1. The code (`shared/anomaly_logger.py`) is unchanged.
6. **Attachment screening pipeline.** Every attachment passes through four sub-layers per
   Op Stds v16 §34: (a) static signatures (magic-number, size, filename); (b) format-aware
   structural inspection (PDF JS/embedded, Office macros); (c) ClamAV scan via pyclamd;
   (d) optional VirusTotal hash check (Phase 2+ enhancement). Malicious → ITS_Quarantine +
   CRITICAL triple-fire + sender DISABLED in ITS_Trusted_Contacts pending operator review.
   Implementation scheduled Phase 1.4 pre-Customer-1 hardening.

   _Portal pivot (2026-05-28): for **safety reports** this layer is N/A — the Safety Portal
   (blueprint `workstreams/safety-portal/mission.md` v1 §7) replaces PDF-email with form-fill
   (SVG vector signatures, no arbitrary-file attachment). Layer 6's load-bearing surface is
   **Email Triage** (arbitrary inbound mail/attachments); implementation reassigned there. See
   `docs/tech_debt.md`._

Residual risk: prompt injection is an unsolved research problem. The architecture assumes
injection might succeed at the AI layer and ensures the damage ceiling is "extracted data is
wrong" rather than "data exfiltrated" or "external action taken on attacker's behalf."

## Maintenance & successor-operator model (FM v11 · Op Stds v16 §§43–44)

ITS is built to be maintained after the developer (Seth) departs. The model (FM v11; Op Stds v16
§44) has **three tiers**:

1. **Tier 1 — self-heal.** Interval daemons recover via launchd re-invocation (one-shot-per-
   `StartInterval`); watchdog **Check C** marker-file staleness floor catches a stale daemon across
   all four tracked jobs; the external UptimeRobot ping (audit F16) is the dead-man's switch for
   total-host death. No human acts. (No "Check H" — naming artifact; Check C is the staleness floor.
   The lone residual `weekly_generate` Friday-crash gap is closed by watchdog **Check I** catch-up;
   see `scripts/watchdog.py`.)
2. **Tier 2 — Claude-assisted repair by the Successor-Operator.** A *trained* operator who runs
   Claude Code, follows the §43 runbook, and carries out a **low-capability-class** repair (re-run a
   daemon, toggle an ITS_Config value, re-send an approval, re-seed a row, clear a stuck lock). He is
   **not** a developer — writes no code, does no §§37–41 work, touches no secrets/Keychain.
3. **Tier 3 — escalate to the Developer-Operator (Seth).** A reachable escalation asset, not the
   day-to-day operator.

**Two named roles.** Every unqualified "operator" resolves to exactly one: the **Developer-Operator**
(Seth — git/CC/shell/worktree-fluent; all §§37–41 operations, Keychain access, code changes) or the
**Successor-Operator** (the trained Tier-2 role above).

**The both-rule (Tier-2/Tier-3 boundary).** A fault is Tier-2-eligible only if **documented (has a §43
entry) AND low-capability-class**. Anything **novel OR high-class** escalates to Seth. The four
**high-capability-class categories are FIXED**: (1) External Send Gate, (2) secrets / auth, (3)
doctrine, (4) code changes — high-class always escalates regardless of documentation.

**Training-enforced, NOT structurally enforced** (the Op Stds v16 / FM v11 reframe). No
"non-developer-safe enforcement layer" is built or required — the verified-in-code capability gating
(Invariant 1, `tests/test_capability_gating.py`) and `.claude/hooks` guards protect developer /
subagent sessions and fall *open* for the operator's own session, so they do not confine a Tier-2
repair. The boundary holds by the operator's judgment, the both-rule, and co-resolution with Seth on
the four high-class categories until per-category clearance.

**§43 document-as-you-build (definition-of-done).** Every capability with a Tier-2-reachable failure
mode ships a plain-language **successor-remediation runbook entry** as DoD — symptom, low-class repair
steps, and the explicit escalate-to-Seth boundary in observable terms. Where §42 records *why the code
is the way it is* (developer audience), §43 records *what the Successor-Operator does when it
misbehaves*. CC briefs reference §43 when scoping any such capability.

## Operational conventions — load-bearing

Every workstream script MUST follow these. Deviations get raised in the planning project first,
not invented locally.

- **Kill switch first.** Call `shared.kill_switch.check_system_state()` (or use `@require_active`)
  at script entry. PAUSED or MAINTENANCE → exit cleanly. `@require_active` is an operator-convenience
  pause, **not** a security control — it is fail-open by design (sheet-unreachable / row-missing /
  invalid-value all resolve to ACTIVE-with-WARN), so the External Send Gate (Invariant 1), not the
  kill switch, is the security boundary (Op Stds v16 §1).
- **Error log decorator.** Wrap every script's main function in `@its_error_log(script_name=...)`.
  Catches unhandled exceptions, writes to `ITS_Errors` sheet, surfaces CRITICAL via email + SMS.
- **Confidence scoring on extractions.** Default threshold 0.85. Below threshold → routes to
  `ITS_Review_Queue`, not silent success.
- **External Send Gate.** Per Invariant 1. No generation script imports `graph_client.send_mail`.
  No send script imports `anthropic_client` or any AI capability.
- **Adversarial Input Handling.** Per Invariant 2. Every prompt processing external content includes
  the untrusted-content boilerplate. Every extraction output passes through `anomaly_logger.check()`
  before being trusted.
- **Credentials from macOS Keychain.** Never env files, never committed. Use
  `shared.keychain.get_secret(name)`.
- **Schemas in `schemas/`. Prompts in `prompts/`.** Both version-controlled. JSON schemas have a
  `version` field; scripts reject responses on schema mismatch.

## Sandbox-first build pattern

ITS is built in a sandbox tenant (M365 `evergreenmirror.com`, Smartsheet, Box) before cutover to
live tenants. The mirror has matching subscription tiers and is populated with closed/expired
Evergreen documents for end-to-end validation without touching production. Cutover happens at the
Phase 1 → 1.5 gate, then again at Florida → customer-site hardware shipment.

## What's stubbed vs. real (current scaffold state)

| Module | State | Notes |
|--------|-------|-------|
| `shared/keychain.py` | Working, tested | macOS-only; uses `security` CLI. |
| `shared/error_log.py` | Working, tested | Local file + `ITS_Errors` write (recursion-guarded; INFO env-gated via `ITS_ERROR_LOG_INFO=1`) + triple-fire CRITICAL (Resend email + Sentry). Each leg independently recursion-guarded + broad-except isolated; one leg failing never blocks the others. `Correlation_ID` threaded across all three; Resend-leg dedupe via `alert_dedupe` on `(script, error_code)` (Op Stds v16 §3.1). |
| `shared/alert_dedupe.py` | Working, tested | Resend-leg dedupe state at `~/its/state/alert_dedupe.json` via `state_io` atomic-write + path-lock. Window from `alerting.dedupe_window_minutes` ITS_Config (default 60). **Fail-open on every state error incl. `StateLockTimeoutError`** — false positives (extra emails) OK, false negatives (missed wake-ups) NOT. Watchdog Check G consumes the summary API. |
| `shared/state_io.py` | Working, tested | **Canonical entry point for all `~/its/state/` writes.** `atomic_write_json`/`atomic_write_text` = temp-file + `os.replace` (crash-safe); `with_path_lock` = non-blocking `fcntl` flock on a **sidecar `.lock`** (load-bearing: `os.replace` swaps the inode, invalidating a lock on the data file itself) + bounded retry → typed `StateLockTimeoutError`. Closes audit F19 + F23. |
| `shared/resend_client.py` | Working, tested | Transactional-email client for **operator alerts only**. Key from Keychain (`ITS_RESEND_API_KEY`). NOT for customer email — that's `graph_client.send_mail` (Invariant 1). |
| `shared/sentry_client.py` | Working, tested | Sentry SDK wrapper for CRITICAL capture. DSN from Keychain (`ITS_SENTRY_DSN`). Perf monitoring off; `send_default_pii=False`. |
| `shared/kill_switch.py` | Working, tested | Reads `system.state` from ITS_Config; **fail-open** on three modes (sheet unreachable / row missing / invalid value) with distinguishable WARN. |
| `shared/anthropic_client.py` | Working, live-validated | Reads `ITS_ANTHROPIC_KEY` from Keychain. First consumer `weekly_generate.py`. No dedicated test — covered transitively by `tests/test_weekly_generate.py`. |
| `shared/smartsheet_client.py` | Working, tested | SDK wrapper: title-keyed reads/writes, typed exception hierarchy, lazy keychain-backed client. |
| `shared/box_client.py` | Working, tested | boxsdk OAuth2 User Auth. **CRITICAL invariant: refresh tokens rotate every exchange; the `_store_tokens` callback must persist the new token to Keychain or ITS dies in 60 days — `test_store_tokens_persists_refresh_token` locks it.** Dedicated ITS user at Phase 1.5 cutover. Setup `scripts/setup_box_oauth.py`. |
| `shared/graph_client.py` | Working, tested | MSAL client-credentials + Mail API wrappers (incl. `send_mail`). Sandbox tenant `evergreenmirror.com`; smoke `scripts/smoke_test_graph.py`. |
| `shared/review_queue.py` | Working, tested | `add()`→`ITS_Review_Queue` (returns row ID); `get_status()` reads back by Item ID (`<workstream>-<YYYYMMDD>-<HHMMSS>` UTC). Smartsheet failures propagate so callers can fire CRITICAL. `Reason` is PICKLIST (`ReviewReason` enum). |
| `shared/untrusted_content.py` | Working, tested | Invariant 2 — XML tagging + system boilerplate. |
| `shared/anomaly_logger.py` | Working, tested | Invariant 2 — sentinel pattern checks. |
| `shared/quarantine.py` | Working, tested | `is_allowlisted` + `log_quarantined_message` → ITS_Quarantine. Smartsheet failures propagate (silent failure loses an audit record — callers must elevate). **Workstream picklist catch-all is `other`, NOT `global`** (differs from ITS_Review_Queue). |
| `shared/scheduling.py` | Holiday shifts + reviewer chain + PTO fetcher working, tested; **chain-override fetcher (`_no_override`) stubbed** | `_live_fetcher` reads `ITS_Time_Off` with per-instance caching. Chain-override real fetcher is a separate queued PR — built when a workstream actually exercises overrides (decision D-i.1a). |
| `shared/sheet_ids.py` | Working | Bootstrap module: workspace/folder/sheet IDs for the three workspaces + master-DB sheet constants + picklist-sync config. |
| `shared/picklist_sync.py` | Working, tested | Cross-sheet PICKLIST option sync from master DBs. **Reference-checked removals** (live cell usage blocks delete → Review Queue row, `Reason=mismatched-reference`); two-stage size guardrails (200 WARN, 400 HARD-HALT, configurable); SHA-256 idempotency; triple-fire on ≥3 mappings failed. Hourly via `scripts/run_picklist_sync.py`. |
| `shared/defaults.py` | Working | Cross-cutting fallback constants (reviewer chains, dedupe window, picklist thresholds, `BOX_PROJECT_FOLDERS` — **now 1111B-derived clones post-cutover**, legacy 1111A clones archived). ITS_Config rows override at runtime; these are the missing/invalid-row fallback. |
| `scripts/watchdog.py` | Working, tested. 6 of 7 checks operational (E deferred). | Checks A (stale review queue), B (open CRITICALs), C (scheduled-jobs marker staleness; `write_last_run_marker`), D (14-day reviewer-chain forward scan, §18), F (mail-intake silent-disable), G (alert-dedupe summary sweep; two-phase delete; defers during MAINTENANCE), I (`weekly_generate` Friday-crash catch-up). **Check E (Anthropic spend) deferred to Phase 1.5** — Admin API key prerequisite, not a capability gap (`docs/tech_debt.md`). |
| `scripts/run_picklist_sync.py` | Working, tested | Hourly launchd entry point. CLI `--dry`/`--mapping`/`--smoke-test`. `@require_active` outer + `@its_error_log` inner. |
| `safety_reports/intake.py` | Working, live-validated (engine) | 12-stage pipeline; `process_message(message_id)` is the public API. The legacy email caller `intake_poll` is RETIRED (2026-06-05); the email-PDF ingestion stages are LEGACY/dormant — superseded by the now-live portal-marker branch driven by `portal_poll.py` (built + live-validated 2026-06-08 mirror). `SmartsheetError`/`GraphError` soft-fail (return, not raise). Stages 1-9 + 11-12 live; Stage 10 (attachment screening, §34) planned Phase 1.4. **Portal transport (2026-06-05, supersedes the 2026-05-28 email-shim pivot):** the Safety Portal feeds `intake.py` via a **Python PULL model** (`decision_phase5-portal-transport`), NOT an email shim. The Cloudflare Worker signs + queues each submission in D1 (send-free) and serves it over `GET /api/internal/pending`; the `portal_poll.py` daemon (built, loaded 60s, live-validated 2026-06-08) pulls over HTTPS, verifies the `X-ITS-Portal-HMAC` via `shared/portal_hmac.py`, hands the structured submission to `intake.py`, then POSTs `/api/internal/mark-filed` (the receipt). No `portal-noreply@` mailbox, no unified-`safety@` email shim. The intake portal-marker branch (HMAC verify → UUID dedupe → Sat→Fri Job-ID week/Box → render via `form_pdf` → file → receipt) is **built + live-validated (2026-06-08 mirror: submit → portal_poll pull → intake → Box mirror ROOT→job→week → weekly_generate compile → WSR staged → unattended timed send)**. Stage 10 N/A for safety reports (Layer 6 reassigned to Email Triage). |
| `safety_reports/intake_poll.py` | **RETIRED 2026-06-05** (tombstone) | The safety email-intake poller is RETIRED — superseded by the Safety Portal PULL model (`portal_poll.py`, built + live; `decision_phase5-portal-transport`). The Graph email-polling engine (`list_inbox`/seen-set/`mark_read`/heartbeat) is REMOVED; the module is a tombstone whose `main()` raises `NotImplementedError` (visible non-zero exit, deliberately NOT `@its_error_log`-wrapped so the 60s launchd cadence doesn't CRITICAL-spam) — kept in-tree so an orphan-loaded job surfaces the retirement. **Operator-manual:** unload the launchd job (`scripts/uninstall_safety_intake_daemon.sh`). The shared Graph plumbing (`shared/graph_client.py`) is PRESERVED untouched for Email Triage. Watchdog Check F + Check-C `safety_intake` tracking removed with it. |
| `safety_reports/portal_poll.py` | Working, live-validated (2026-06-08 mirror) | Portal PULL daemon (60s launchd, `org.solutionsmith.its.portal-poll`). `GET /api/internal/pending` (bearer Keychain `ITS_PORTAL_INTERNAL_TOKEN`) → per row recompute the canonical HMAC (`shared/portal_hmac.py`, constant-time) → `intake.process_message` → on DRAIN `POST /api/internal/mark-filed` (receipt); also `POST /api/internal/sync` full-replace of `ITS_Active_Jobs` → the D1 dropdown. Runtime gate `safety_reports.portal_poll.polling_enabled`; bad-HMAC one-shot-flagged (never filed, never mark-filed); self-provisions its `ITS_Daemon_Health` row. Worker base from ITS_Config `safety_reports.portal.worker_base_url` — **repointed to `https://safety.evergreenmirror.com` 2026-06-08** (PR-J's `custom_domain` route disabled the `*.workers.dev` URL on deploy; see `docs/tech_debt.md`). |
| `safety_reports/week_folder.py` | Working, tested | Per-project per-week Field/Daily/Rollup folder scaffolding. Idempotent find-or-create (find-after-create race tracked in tech-debt). |
| `safety_reports/weekly_generate.py` | Working, live-validated (2026-06-08 mirror) | **DETERMINISTIC weekly compile** (Anthropic narrative core retired). Generation half of the External Send Gate (Invariant 1). Friday 14:00 launchd. Per Active job's Sat→Fri week: gather the week sheet's per-submission PDFs → `form_pdf.merge_pdfs` → file the packet to an `ITS`-prefixed Box week folder → DUAL-WRITE the week-sheet Rollup snapshot row + one `WSR_human_review` row per (job,week) (Email Body seeded from a fixed template; Send Status PENDING). Friday-fire + `Compile Now` checkbox + skip-if-already-compiled-and-no-new-docs + empty-week-still-writes + never-closes-the-week. Per-job fence → Review Queue. **Capability-gated: `anthropic`/`graph_client`/`send_mail`/`resend`/`smtplib`/`email.mime` AST-forbidden** (no LLM, no send). |
| `safety_reports/weekly_summary.py` | DEPRECATED | Stub kept in-tree one cycle so any orphan launchd reference surfaces as `NotImplementedError`. Delete once the `weekly-generate` plist is loaded on the production MacBook. |
| `safety_reports/weekly_send.py` | Working, live-validated (2026-06-08 mirror) | **Send half of the two-process model** (Invariant 1), repointed `WPR_Pending_Review`→`WSR_human_review`. `send_one_row(row_id)` per approved row. **RECIPIENTS RESOLVED AT SEND TIME from `ITS_Active_Jobs`** via the row's Job ID (TO = safety-reports contact, CC = CC 1–5; stakeholder excluded) — NOT the WSR display columns. Body = the WSR `Email Body` (human source of truth); compiled Box PDF attached. **HELD** (no send) on empty/unknown TO or missing PDF; **FAILED**+retry on transient Graph/Box error. **Capability-gated: `anthropic_client`/`anthropic` AST-forbidden.** Retry-state Notes-encoded (§19). MAX_SEND_RETRIES=3; CRITICAL on Graph-auth failure / retry exhaustion / post-send-update failure. |
| `safety_reports/weekly_send_poll.py` | Working, live-validated (2026-06-08 mirror) | Polling daemon (15-min). Dispatches `WSR_human_review` rows with `Send Now` (immediate) OR `Approve for Scheduled Send` (Mon ≥07:00 Pacific window) checked AND `Send Status ∈ {PENDING,FAILED}` AND retry-count < MAX. Runs the **F22** `verify_approval` gate on the driving checkbox, stamps the verified approver (Approved By/At), then dispatches `weekly_send.send_one_row`; per-row fence. Heartbeat helpers replicated VERBATIM (preservation; `shared/heartbeat.py` extraction is tech-debt). |

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
   intake-bearing workstreams** (Op Stds v16 §31; `safety_reports/intake_poll.py` is the
   canonical example). Shortcuts remain for manual operator-triggered jobs. Mail.app rules
   deprecated.
9. **Ship the §43 successor-remediation runbook entry** for any capability with a Tier-2-reachable
   failure mode (Op Stds v16 §43) — symptom, low-class repair steps, and escalate-to-Seth boundary.
   This is part of definition-of-done, not a follow-up. See "Maintenance & successor-operator model".

## Model selection

Default for reasoning calls: `claude-sonnet-4-6`. Use `claude-haiku-4-5-20251001` for
high-volume classification (Email Triage). Use `claude-opus-4-7` only where reasoning depth
genuinely justifies the cost (rare). Revisit quarterly — Anthropic ships new models on a
roughly six-month cadence.

## Observability stack (pre-Phase-1 add-ons)

Ship in Phase 0:

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
update-in-place per cycle. Push surface per Op Stds v16 §3.1 + §32.

- Schema: 12 columns per `shared.sheet_ids.DAEMON_HEALTH_COLUMNS` dict. See
  `references/daemon-health-schema.md` in the its-blueprint repo for full schema reference.
- Heartbeat write must NEVER block daemon primary work. Failure path: log to ITS_Errors
  category `daemon_health_write_failed`; daemon continues.
- ARCH-1: Enabled checkbox is report-filter metadata only. Canonical runtime gate is
  `<workstream>.<daemon>.polling_enabled` in ITS_Config.
- ARCH-2: Row-id cache persists to `~/its/state/heartbeat_row_ids.json`. The file is SHARED across daemons (intake_poll + weekly_send_poll); writes go through `shared.state_io.atomic_write_json` under `state_io.with_path_lock` (sidecar `.lock`). Path and semantics stable; only the write mechanism is hardened.
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
- Don't call `Path.write_text` or `Path.write_bytes` directly on any file under `~/its/state/`. All state-file writes must go through `shared/state_io.py` helpers (`atomic_write_json` / `atomic_write_text`, wrapped in `with_path_lock` for read-modify-write triples on shared files). Direct `write_text` skips the atomic-write + lock guarantees and is rejected at review.

## Skills usage (mattpocock/skills, repo-local)

Installed skills physically live in `.agents/skills/` (source of truth; `skills-lock.json`
pins upstream revisions); `.claude/skills/` holds per-skill symlinks. 15 skills installed —
enumerated in `skills-lock.json`. Most are safe to invoke as needed (`grill-me`,
`grill-with-docs`, `to-prd`, `to-issues`, `diagnose`, `tdd`, `handoff`, `caveman`, `zoom-out`,
`triage`, `prototype`, `write-a-skill`, `setup-matt-pocock-skills`). Exceptions below.

**Constrained — require explicit operator approval before invoking:**
- `improve-codebase-architecture` — conflicts with preservation-over-refactor (Op Stds §14).
  Do not invoke speculatively. Operator must confirm the refactor target meets the
  ≥4 real reuse cases threshold before this runs.

**Auto-recommended on specific triggers:**
- `diagnose` — any bug investigation touching an SDK boundary (Smartsheet, Box, Graph). The
  reproduce → minimise → hypothesise → instrument → fix → regression-test loop is the standard
  response to the SDK-vs-Live bug class (Op Stds §30).
- `tdd` — any new `shared/*` SDK wrapper with create/update/delete on typed columns/rows
  (Op Stds §30 integration discipline).

**Active guardrail hook — `git-guardrails-claude-code`:** hook script at
`.claude/hooks/block-dangerous-git.sh`, wired via `.claude/settings.json` `PreToolUse` on `Bash`.
Customized from upstream:

- BLOCKED: `git push --force` / `-f` / `--force-with-lease`; `git push --delete` / `-d` /
  colon-prefix delete (`origin :branch`); `git reset --hard`; `git clean -f` (also `-fd`);
  `git branch -D` (force-delete); `git checkout .`; `git restore .`.
- ALLOWED (carved out from upstream default): plain `git push <branch>` (canonical PR-feature
  push); `git branch -d` (safe-delete, canonical post-merge cleanup); refspec push
  (`git push origin feature:main`); `gh pr merge --delete-branch` (gh-side branch cleanup).

This hook does **not** prevent direct push to `main` — that defense belongs at the GitHub branch
protection layer (server-side, authoritative), to be verified separately as a follow-up.

Adding skills on demand: `npx skills@latest add mattpocock/skills --skill <name> -y` (add
`--full-depth` for `misc/`-scope skills, as used for `git-guardrails-claude-code`).
`request-refactor-plan` (carries the same §14 constraint) and `qa` (pre-merge verification) are
available but not in the default install.

## Agent skills

Repo-specific config the planning / engineering skills above (`to-issues`, `to-prd`, `triage`,
`grill-with-docs`, `improve-codebase-architecture`) consume — where issues live, what triage
labels mean, how to read domain docs. Each subsection points to the canonical file under
`docs/agents/`.

### Issue tracker

Issues and PRDs are tracked in GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical triage labels (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Git workflow

- After every PR merge, `git checkout main && git pull origin main` before the next task. Lets
  `gh pr merge --delete-branch` auto-clean the local feature branch on the next merge; avoids
  squash-merge residue that needs force-delete.

## Useful references in this repo

- `shared/` — start here when implementing a new workstream.
- `shared/untrusted_content.py` and `shared/anomaly_logger.py` — Invariant 2 mechanics.
- `tests/test_keychain.py` — canonical pattern for mocking an external CLI.
- `tests/test_error_log.py` — covers the CRITICAL surfacing path.
- `tests/test_capability_gating.py` — enforces Invariant 1 at the import level.
- `scripts/watchdog.py` — the daily watchdog skeleton.
- `scripts/launchd/template.plist` + `install.sh` — launchd trigger pattern.
- `docs/session_logs/` — durable narrative log. Write one at end of any session that lands ≥1 commit and involves a non-obvious decision. Convention in `docs/session_logs/README.md`.
- `docs/operations/pr_merge_discipline.md` — canonical **four-part** PR-landing verify. The original three assertions (`state=MERGED` / `mergedAt` non-null / `mergeCommit.oid` present) catch GitHub-side ghost merges but miss a post-merge `push: main` workflow failure. Step 4 (main-branch CI on the merge commit) is the fourth gate; a PR passing steps 1-3 but failing step 4 is **functionally not landed**.
- `docs/operations/doc_conventions.md` — canonical frontmatter / section / filename / workstream conventions for every doc. **Consult when creating any new doc** under `docs/` or `prompts/`. Existing docs grandfathered (lazy retrofit); new docs MUST conform. Lint `scripts/lint_doc_conventions.py` (warn-only in CI); index regen `scripts/regen_doc_indexes.py` (`--check` in CI).
- `docs/operations/worktree_discipline.md` — canonical procedure for parallel CC sessions via `git worktree` without colliding on a shared checkout or pushing un-reviewed code into the live `~/its` daemon tree. Covers the exec-repo PYTHONPATH/editable-install import gotcha, the blueprint-repo isolation rule (never two doctrine-touching sessions on one checkout), operator-run cleanup (force-delete is hook-blocked inside CC), and the serialization fallback.

Session-log line convention, four parts:
```
- pytest: <N> passed / <M> skipped / <D> deselected
- mypy: <E> errors / <F> source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
```

## Agents

Repo-local subagents live in `.claude/agents/`, auto-discovered; each agent's `description` frontmatter is its dispatch signal. Invocation *moments* wired here:

- **`session-close-maintainer`** — at session close (see [Session-close maintenance](#session-close-maintenance)).
- **`doc-reconciliation-auditor`** — propose-only cross-repo doctrine-vs-code drift audit (opus); a `PreToolUse` hook blocks any write. Invoke after a blueprint doctrine version bump, after a doctrine-touching PR (version strings / sheet-IDs / workstream scope), or at session close. Reads `docs/doctrine_manifest.yaml`, runs `scripts/check_doctrine_drift.py`, emits a dated findings doc to `docs/audits/`. Heavy half of the cross-repo drift guard; lightweight half is the `session-close-maintainer` check + the "Cross-repo supersession drift" note in `docs/operations/doc_conventions.md`.

Remaining agents have no fixed invocation moment — dispatched by `description` frontmatter; listed so a fresh CC session can discover them:

- **`brief-validator`** — before acting on a chat brief naming specific files/functions/line-ranges or current-state claims; verify every code-shape claim against `~/its` + `~/its-blueprint` first.
- **`codeql-fp-triager`** — triaging open CodeQL alerts on `SolutionSmith-debug/its`; propose-only dismissals (operator applies) for the 3 known weekly FP patterns with quoted evidence, escalate the rest. A `PreToolUse` hook blocks any dismissal.
- **`ops-stds-enforcer`** — reviewing a diff (working tree / staged / PR) against Operational Standards for invariant violations (Send Gate, adversarial input, push-vs-record dedupe, preservation-over-refactor, workspace topology, SDK-vs-Live, version-bump, §42 self-documentation).
- **`pr-landed-verifier`** — after merging a PR, or when a brief / session log / chat memory claims a PR landed; runs the four-part verify, emits "four-part verify clean" or names the failing leg.
- **`sdk-integration-test-scaffold`** — right after creating/significantly changing a `shared/<client>.py` SDK wrapper with create/update/delete on typed columns/rows; scaffolds `tests/test_<client>_integration.py` per Op Stds §30.
- **`session-log-writer`** — at session close, drafts the session log per the canonical scaffold, quoting `pr-landed-verifier` output verbatim (operator invokes directly — subagents can't spawn subagents).
- **`smartsheet-rest-fallback`** — when a Smartsheet op is missing from the MCP surface and needs a direct REST call (e.g. `create_report`, certain filters); file-based payload, verify-after via MCP, no token persistence.

## Session-close maintenance

At session close, invoke `session-close-maintainer` (in `.claude/agents/`). It:

- Surveys recent git activity in both repos
- Delegates session-log generation to `session-log-writer` (writes to `docs/session_logs/` here and `../its-blueprint/session-logs/` when planning-side decisions surface)
- Updates the info-gap doc (`../its-blueprint/references/claude-code-info-gap.md` — §1 / §5 / §6 / §8 + `Last refreshed:` frontmatter)
- Appends a `§G<N>` section to `../its-blueprint/references/memory-archive.md` when operational detail surfaced
- Adds tech-debt entries to `docs/tech_debt.md`
- Proposes new/updated auto-memory entries

Convention canonical in `../its-blueprint/CLAUDE.md` (planning layer wins). Don't skip — the info-gap doc and memory archive bridge chat-only context to what a fresh CC session can reach on disk.

For a **deeper cross-repo pass**, invoke `doc-reconciliation-auditor` (see [Agents](#agents)) — the heavy/on-demand counterpart to the lightweight session-close supersession check, not a replacement.

If something here contradicts the planning project's canonical docs (Foundation Mission v11,
Operational Standards v16), the planning project wins. Flag the inconsistency.
