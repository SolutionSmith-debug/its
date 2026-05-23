# 2026-05-23 — ITS_Trusted_Contacts + Intake Stage 2 refactor + Header forgery detection

First deliverable of the Phase 1.4 pre-Customer-1 security hardening cluster per V&R v7.2. Builds the sheet-backed trusted-contacts model, retires the JSON-list ITS_Config allowlist (cutover transitional path stays for one Friday cycle), and adds SPF/DKIM/DMARC parsing on every inbound message.

Branch: `feat/its-trusted-contacts` off `main` at `2d44d2f` (PR #69 close).

## Purpose

Move Stage 2 of the safety intake pipeline from "is this email in a JSON list" to "is this email an ACTIVE trusted contact, scoped to this project + this workstream, AND did the message pass SPF/DKIM/DMARC." Per Op Stds v11 §33 + FM v8 Invariant 2 Layer 1.

Why now: the JSON-list allowlist was a Phase 0 expedient — operator-curated, no per-sender status, no per-project scope, no audit columns. Phase 1.4 hardens the intake surface before Customer-1 (Phase 1.5 cutover) lands; the sheet model is the durable design.

## Pre-flight findings

- Baseline test count: **903** (brief estimated 883; ~20 added between brief draft and execution — likely R3 Session 3 deltas).
- HEAD `main = 2d44d2f` (PR #69 — R3 Session 3 close).
- Sibling Session 4 (`feat/box-1111b-materialize`) had three untracked files in the working tree (`scripts/migrations/box_build_1111b_blueprint.py`, `tests/test_box_build_1111b*.py`). Branched off main from the same working tree; never `git add`'d the Session 4 files so the PR diff stays clean.
- `safety_reports/intake.py` Stage 2: `check_sender_allowlist(parsed, allowlist)` thin wrapper over `quarantine.is_allowlisted`; allowlist read at `_run_pipeline` top via `_read_allowed_senders()`. Replace point identified at line ~824.
- `shared/graph_client.py::get_message` returned the default field set (no `$select`); headers are NOT in the default Graph projection — needed the explicit opt-in path.
- `shared/quarantine.py::log_quarantined_message` had no Reason column on the live ITS_Quarantine schema (verified against existing tests). Graceful-degraded to a `[reason: <code>]` tag inside Notes.
- `shared/review_queue.py::ReviewReason` is a StrEnum mapped to live picklist values; adding new values requires operator UI add (Smartsheet accepts unknown picklist strings as plain text but doesn't bucket them in pivots).

## Substance

### Sheet schema

Built via `scripts/migrations/build_its_trusted_contacts_sheet.py`. 10 columns:

  | Column            | Type         | Notes                                 |
  | ----------------- | ------------ | ------------------------------------- |
  | Email             | TEXT_NUMBER  | Primary, exact-match key, case-normed |
  | Display Name      | TEXT_NUMBER  | Operator-facing                        |
  | Role              | PICKLIST     | 6 options (Field PM / Safety Officer / Subcontractor PM / Site Supervisor / Operator / Other) |
  | Project Scope     | TEXT_NUMBER  | JSON list, `["*"]` wildcard            |
  | Workstream Scope  | TEXT_NUMBER  | JSON list, `["*"]` wildcard            |
  | Status            | PICKLIST     | ACTIVE / DISABLED / PENDING_VERIFICATION |
  | Added By          | TEXT_NUMBER  | Operator email                         |
  | Added Date        | DATE         | ISO                                    |
  | Last Verified     | DATE         | ISO                                    |
  | Notes             | TEXT_NUMBER  | Free-form                              |

JSON-list TEXT_NUMBER (not native multi-PICKLIST) per the brief: SDK shape is inconsistent for multi-PICKLIST and the cross-sheet sync from PR #45-51 doesn't cover multi-select reliably. Tech-debt entry tracks graduation after Picklist Hardening #1 lands.

### New shared modules

`shared/trusted_contacts.py` (~210 lines)
- `ContactStatus` StrEnum (ACTIVE / DISABLED / PENDING_VERIFICATION).
- `TrustedContact` frozen dataclass (email case-normalized on read, `project_scope` + `workstream_scope` as tuples).
- `ScopeVerdict` frozen dataclass (`allowed`, `contact`, `reason`).
- `lookup(email)` — case-insensitive primary lookup.
- `check_scope(email, *, workstream, project=None)` — Stage 2 / Stage 4b gate. `project=None` defers the project leg until after `resolve_project`.
- `invalidate_cache()` — test + ad-hoc operator helper.
- 60-second TTL cache, module-level, best-effort invalidation.

`shared/header_forgery.py` (~180 lines)
- `HeaderVerdict` StrEnum (PASS / SOFT_FAIL / HARD_FAIL).
- `HeaderAnalysis` frozen dataclass (verdict + spf/dkim/dmarc + return_path_domain + from_domain + return_path_mismatch + raw_authentication_results).
- `analyze(internet_message_headers)` — parses `Authentication-Results` (RFC 8601 loose form), `Return-Path`, `From`. Multi-hop case uses the closest-to-receiver header (first occurrence). DMARC policy parsed for `p=reject` escalation. No DKIM signature re-validation — trusts inbound MTA's verdict per FM v8 Invariant 2 Layer 1 (tech-debt entry tracks revisit-if-threat-model-demands).

### Stage 2 routing matrix (in `safety_reports/intake.py::check_trusted_sender`)

  | `scope.reason`              | `header.verdict` | sink         | disposition                  |
  | --------------------------- | ---------------- | ------------ | ---------------------------- |
  | allowed                     | PASS             | proceed      | allowed                      |
  | allowed                     | SOFT_FAIL        | review       | header-soft-fail-trusted     |
  | allowed                     | HARD_FAIL        | quarantine   | header_forgery_suspected     |
  | unknown_sender              | any              | quarantine   | unknown_sender               |
  | status_disabled             | any              | quarantine   | sender_disabled              |
  | status_pending_verification | any              | review       | sender-pending-verification  |
  | workstream_out_of_scope     | any              | quarantine   | workstream_out_of_scope      |

`Stage2Decision` dataclass carries the `scope_verdict` + `header_analysis` into the routing branch so the review-queue payload + INFO log can capture full diagnostic context.

### Stage 4b project-scope check

Runs only when Stage 2 found a trusted contact (`scope_verdict.contact is not None`) — the legacy fallback path doesn't have a contact row to gate on. Calls `check_scope` a second time with the resolved `project=` to enforce the per-project leg. `project_out_of_scope` routes to review with the new `PROJECT_OUT_OF_SCOPE` reason.

### Graph client extension

`shared/graph_client.py::get_message` gained `include_headers: bool = False`. Default-false preserves existing call sites. When True, passes `?$select=...,internetMessageHeaders` covering the fields intake reads (id, subject, from, receivedDateTime, hasAttachments, body, internetMessageHeaders). The intake fetch now opts in.

### Legacy fallback (cutover transitional)

`_check_legacy_allowlist` consults the ITS_Config `safety_reports.intake.allowed_senders` JSON list when `ITS_Trusted_Contacts` returns zero rows. Header forgery still applies — even on fallback, HARD_FAIL quarantines. First fallback hit per process emits `trusted_contacts.fallback_to_its_config` INFO so the operator can see cutover hasn't completed. Tracked for removal in a follow-on PR after the operator confirms one Friday cycle clean.

### Taxonomies

- `shared/quarantine.py::QuarantineReason` StrEnum: `UNKNOWN_SENDER`, `SENDER_DISABLED`, `WORKSTREAM_OUT_OF_SCOPE`, `HEADER_FORGERY_SUSPECTED`, `LEGACY_ALLOWLIST_MISS`. `log_quarantined_message` accepts optional `reason=`, writes `[reason: <code>]` into Notes.
- `shared/review_queue.py::ReviewReason` gained `HEADER_SOFT_FAIL_TRUSTED`, `SENDER_PENDING_VERIFICATION`, `PROJECT_OUT_OF_SCOPE`. Operator-side action: add to live picklist via UI (Smartsheet accepts unknown strings — writes succeed pre-UI-add).

### Migrations

- `build_its_trusted_contacts_sheet.py` — idempotent (skip-if-exists by name in `FOLDER_SYSTEM_CONFIG`). Prints sheet ID for manual paste into `shared/sheet_ids.py::SHEET_TRUSTED_CONTACTS` (currently placeholder `0`).
- `seed_its_trusted_contacts.py` — reads legacy JSON, creates one row per email (Display Name derived from local-part, Role=Other, Project=`["*"]`, Workstream=`["safety_reports"]`, Status=ACTIVE). Idempotent (skip-if-Email-present, case-insensitive). `--dry-run`. Skips domain-pattern entries (`@evergreenmirror.com`) with a clear message — sheet schema is per-email, not pattern.

## Tests

- `tests/test_trusted_contacts.py` — 17 tests (lookup hit/miss with case normalize, scope happy + wildcards + denials × 4 reasons, cache hit + TTL expiry, malformed JSON parse, sheet-empty unknown_sender, module hygiene).
- `tests/test_header_forgery.py` — 14 tests (all-pass / SPF fail / DKIM fail / DMARC p=reject vs p=none vs p=quarantine / SPF softfail / no Auth-Results / multi-hop / Return-Path mismatch + empty Return-Path / real M365 + Gmail multi-hop samples).
- `tests/test_intake_stage2_refactor.py` — 10 tests (matrix cells × 6, fallback branches × 2, Stage 4b project-scope, capability-gating AST self-check).
- `tests/test_trusted_contacts_integration.py` — 1 gated integration test (write → cache invalidate → check_scope → cleanup). Auto-skipped when `SHEET_TRUSTED_CONTACTS=0` or no Keychain token.
- `tests/test_graph_client.py` — 2 new tests for `include_headers` (default no $select, True emits projection).
- `tests/test_quarantine.py` — 3 new tests (reason writes to Notes, no reason omits Notes, QuarantineReason enum surface).
- `tests/test_review_queue.py` — 1 updated test (`test_review_reason_values_match_live_picklist` includes the 3 new values).
- `tests/test_intake.py` — `test_sender_allowlist_*` removed (3 tests). `test_process_message_quarantines_non_allowlisted_sender` renamed + rewritten to `test_process_message_quarantines_unknown_sender` against the new boundaries. `patch_all_config` fixture extended to mock `trusted_contacts._load_contacts` + `check_scope`. `_build_graph_message` defaults to a PASS-headers fixture.

Baseline 903 → final 949 (+46). All pass.

## Verification gates

- `pytest -q` — 949 collected, all pass.
- `mypy shared/trusted_contacts.py shared/header_forgery.py ...` — `Success: no issues found in 8 source files`.
- `ruff check ...` — `All checks passed!` (one initial unused `MagicMock` import in `test_intake_stage2_refactor.py` removed).
- `tests/test_capability_gating.py` — `safety_reports/intake.py` still passes the GATED_SCRIPTS check (no `send_mail` / `resend` / `smtplib` / `email.mime` introduced).
- Migration scripts import cleanly (`python -c "import scripts.migrations.build_its_trusted_contacts_sheet; import scripts.migrations.seed_its_trusted_contacts"`).
- Live verification (sheet build + seed + intake smoke) is operator-side per the brief — not run from this session.

## Operator-side actions remaining

Per the brief (replicated here for the operator's checklist):

1. **Run `python3 scripts/migrations/build_its_trusted_contacts_sheet.py`** on the production MacBook. Paste the new sheet ID into `shared/sheet_ids.py::SHEET_TRUSTED_CONTACTS` (replace placeholder `0`); commit as a follow-on.
2. **Add 3 new picklist values to ITS_Review_Queue.Reason via Smartsheet UI**: `header-soft-fail-trusted`, `sender-pending-verification`, `project-out-of-scope`.
3. **Run `python3 scripts/migrations/seed_its_trusted_contacts.py`**. Verify seeded rows; adjust Project Scope / Workstream Scope / Role per sender knowledge.
4. **Live intake smoke** against a known-good sandbox message; confirm processed cleanly.
5. **After one Friday cycle clean post-cutover**, delete the ITS_Config `safety_reports.intake.allowed_senders` row via Smartsheet UI. The fallback path goes inert; queue the follow-on PR per the tech-debt entry to remove the dead code.

## Out-of-scope (per brief, restated)

- Picklist hardening (Phase 1.4 #1 — separate session).
- Attachment screening (Phase 1.4 #3 — separate session).
- Operator-UI Shortcuts for trusted-contacts workflows (Tooling track; tech-debt entry).
- DKIM in-process re-validation (tech-debt entry; revisit if security review demands).
- Native multi-PICKLIST graduation for scope columns (tech-debt entry; gated on Picklist Hardening #1).
- Multi-tenant trusted contacts (Customer 2+ — Phase 1.6 Blueprint Generalization).

## Notes / gotchas surfaced this session

- `ParsedEmail.internet_message_headers` defaulted to `field(default_factory=list)` so pre-Stage-2-refactor unit tests in `test_intake.py` didn't need parameter churn. The Stage-2 path always populates it.
- `_fallback_logged` is a module-level flag, NOT a per-call gate — INFO fires once per process. Test resets it explicitly with `intake_mod._fallback_logged = False`.
- Stage 2 routing-matrix test for SOFT_FAIL sets `security_flag=True` because a SOFT_FAIL on a trusted sender (otherwise authenticated) is an unusual signal worth pivoting on in the review queue.
- The integration test (`test_trusted_contacts_integration.py`) is gated by `SHEET_TRUSTED_CONTACTS != 0` — auto-skips today, becomes live after the operator paste-back.
- **CI re-fire**: first CI run on the branch failed because the `patch_baseline` fixture in `test_intake_stage2_refactor.py` didn't mock `_read_allowed_senders`; `_run_pipeline` calls it unconditionally before the Stage 2 branch picks sheet-vs-fallback, and on Linux CI the underlying keychain read raised. Fix: added the mock to the fixture (commit `293a901`). All 10 matrix tests pass on re-run.
- **Pre-existing CI failure noted, NOT introduced by this PR**: `tests/test_weekly_send_poll.py::test_poll_once_skipped_when_polling_disabled` / `test_poll_once_returns_stats_on_empty_sheet` / `test_poll_once_handles_get_rows_failure` raise the same Linux-no-Keychain error pattern. Same failures are present on PR #68 / #69 / #70 / #71 CI runs (Session 3 + Session 4 landed with them present). Not a regression here — flagged as a separate follow-on cleanup task for whoever owns `weekly_send_poll` test fixtures next.
