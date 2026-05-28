# ITS — Tech Debt

Items deliberately deferred. Each carries the rationale for deferral and the trigger for revisiting. The repo-side companion to Master Checklist §6 (planning project) — this file holds execution-layer tech debt; the Master Checklist holds owner-decision tech debt.

When to add an entry: a session deliberately chooses preservation-over-refactor (per Op Stds v11 §14), discovers an external-API constraint that forced a workaround, or defers a non-trivial cleanup that's larger than the current session can absorb. When to mark CLOSED: the underlying item is resolved in a commit; preserve the entry with resolution detail rather than deleting (history is cheap, context is expensive).

## Invariant 2 Layer 6 (attachment screening) is doctrine-only [OPEN 2026-05-28]

FM v8 Invariant 2 Layer 6 (Op Stds v11 §34) mandates that every attachment pass four sub-layers — (a) static signature/magic-number/size, (b) format-aware structural inspection (PDF JS/embedded files, Office macros, EXIF anomalies), (c) ClamAV via a clamd socket, (d) optional VirusTotal hash — before it is uploaded to Box or referenced in any AI call. V&R names completion a **Phase 1.5 cutover precondition**. As of 2026-05-28 this is unimplemented: a grep across `shared/` `safety_reports/` `scripts/` `tests/` for `clamav|pyclamd|virustotal|magic.number|macro|attachment.screen` returns zero implementation hits, and attachments flow `_fetch_message_via_graph` (`safety_reports/intake.py`, raw download) → `upload_attachments_to_box` (raw upload) with no screening stage between fetch and either the Stage 5 AI call or the Stage 10 Box upload. The Graph-declared `mime_type` is attacker-controlled, so a real implementation must sniff bytes, not trust it.

Confirmed against HEAD `4431bae` (forensic-audit HIGH-2; see `docs/audits/2026-05-28_forensic-evaluation.md`). Blast radius is bounded by Layer 1 (trusted senders), but a trusted-sender-gone-bad or credential-compromise writes unscanned bytes straight into customer Box.

**Decision required before build (blocked-on-decision):**
- **Option A — build:** implement `shared/attachment_screening.py` (a NOT-WIRED stub with the intended `screen(filename, content, mime_type) -> ScreenVerdict` signature is committed alongside this entry) and insert it as an intake stage between Stage 1 (fetch) and the AI call / Box upload. Dispositions per doctrine (malicious → ITS_Quarantine + CRITICAL triple-fire + sender DISABLED; suspicious → ITS_Review_Queue; clean → proceed). Sub-layers (a)/(b) are pure Python; (c) needs `pyclamd` + a running `clamd` socket (operator prerequisite); (d) VirusTotal is Phase 2+ (stub). Parallel SDK-vs-Live integration test per Op Stds §30 for any sub-layer calling an external surface. Recommended as its own dedicated session.
- **Option B — defer:** file a dated doctrine exception in its-blueprint stating Layer 6 is unbuilt and the conditions under which Safety Reports may run without it; **delete** the `shared/attachment_screening.py` stub (the signature should not outlive a decision not to build).

**Revisit when:** the operator picks Option A or B. This is a Phase 1.5 cutover gate — must be resolved (built or formally excepted) before Safety Reports is marked cutover-ready.

## State-file atomic-write + concurrent-writer lock [CLOSED 2026-05-25]

`safety_reports/intake_poll.py` (seen-set + heartbeat-row state) and `safety_reports/weekly_send_poll.py` (heartbeat-row state) used raw `Path.write_text`; the heartbeat-row file (`~/its/state/heartbeat_row_ids.json`) is shared between the two daemons with no locking. Failure modes: mid-write crash leaves a truncated file; concurrent read-modify-write between the two daemons can clobber an entry (intake_poll writes its row_id while weekly_send_poll holds a stale read, then weekly_send_poll writes back, erasing intake_poll's update).

Closed by `shared/state_io.py` with `atomic_write_json` / `atomic_write_text` / `with_path_lock` (sidecar-flock pattern: lock lives at `{path}.lock`, never replaced by `os.replace`). Seven callsites migrated — one seen-set + two local-heartbeat + four heartbeat-row read-modify-write triples. The two heartbeat-row triples per daemon are wrapped under `with_path_lock`; lock-timeout fails open per the heartbeat-never-blocks-daemon contract (`error_log.log` WARN with `error_code="daemon_health_write_failed"` + skip the cycle's write — next cycle re-tries).

Audit findings F19 + F23 (atomic-write seen-set + heartbeat-row state + concurrent-writer lock) in `its-blueprint/audits/2026-05-25_forensic-audit.md`. `shared/alert_dedupe.py` migration to the same helper is a separate follow-on PR (sequencing decision 2026-05-25); `shared/heartbeat.py` consolidation tech-debt entry remains open below — this PR is the correctness floor.

## Conftest mock surface coverage [OPEN 2026-05-23]

`tests/conftest.py` (PR #74) autouse-mocks `shared.keychain.get_secret` and `shared.kill_switch.check_system_state`. The keychain mock at the source attribute covers all 7 credentialed surfaces transitively (smartsheet_client / graph_client / box_client / resend_client / sentry_client / anthropic_client / alert_dedupe). Two opt-out lists guard test files that exercise these surfaces directly (`test_keychain.py` + `test_helpers.py` for keychain; `test_kill_switch.py` for kill_switch).

Latent risk: future credentialed surfaces (a new client wrapper for a new external service) might need parallel opt-outs if a corresponding `tests/test_<service>_client.py` lands. Action trigger: any new Linux-CI failure with a `*Error: macOS-only` signature, OR a CI-fix follow-on PR that adds a fixture beyond the keychain + kill_switch pair, OR a new credentialed client module added to `shared/`.

**Revisit when:** next CI-hygiene pass, or any of the above triggers.

## Pre-conftest-fix unit-test network leak to Smartsheet sandbox [CLOSED 2026-05-23]

Between PR #68 merge (2026-05-23T02:02:33Z; Run #229) and PR #73 merge (2026-05-23T15:00:02Z; Run #251), unit tests on macOS dev machines were making live API calls against the sandbox Smartsheet tenant via the unmocked `kill_switch.smartsheet_client.get_setting` path. On macOS the keychain returned a real token, so `_get_client()` built a working SDK client and the kill_switch's `check_system_state` made a real network call on EVERY test that exercised `@require_active`. Volume small (one ITS_Config read per affected test invocation) and benign (read-only against a sandbox tenant).

Closed by `tests/conftest.py` keychain + kill_switch fixtures in PR #74.

## Structural fix: lazy keychain loading + DI-injected kill_switch [OPEN 2026-05-23]

The conftest fix (PR #74) closes the immediate CI hole. A durable structural fix would:

- `shared/smartsheet_client.py::_get_client` — defer the `keychain.get_secret("ITS_SMARTSHEET_TOKEN")` call from build time to first-API-call time, so a test that never makes a real network call never hits the keychain.
- `shared/kill_switch.py` — accept a `get_setting` callable via dependency injection (with the module-level `smartsheet_client.get_setting` as default), so tests can inject without monkeypatching the source module.

Both are non-trivial refactors with cross-call-site impact. Deferred from PR #74 to keep scope focused on the CI fix. Trigger: next session that touches either module for an unrelated reason, fold the refactor in.

**Revisit when:** smartsheet_client or kill_switch refactor session lands.

## parse_job_v3.py:656 — `existing_keys` dead code [CLOSED 2026-05-17]

Resolved in commit **`1fd6751`**. The unfinished de-dup attempt was removed and F841 came off the `box_migration/*` per-file-ignores. Originating commit (which suppressed it) was `8dfc6e8`; ground was tracked in `docs/session_logs/2026-05-17_ruff_and_doc_refresh.md`.

The fix was a deliberate departure from Op Stds v11 §14 (preservation-over-refactor) because the F841 was real dead code rather than a stylistic false positive, and the cleanup was five lines with zero behavior change. The preservation rule remains in effect for the rest of `box_migration/*`.

## Smartsheet API constraint: DATETIME columns require system column type [OPEN]

Discovered 2026-05-17 evening while provisioning `ITS_Errors`, `ITS_Quarantine`, and other sheets. The Smartsheet "Create Sheet" endpoint accepts `DATETIME` columns only when paired with `systemColumnType: MODIFIED_DATE | CREATED_DATE`. User-defined DATETIME columns (e.g., "Timestamp", "Surfaced At", "Resolved At", "Received At", "Reviewed At") are rejected with a generic HTTP 500 / error code 4000 and no descriptive message.

**Workaround:** Use `DATE` for all user-defined date columns. Time-of-day precision is lost from the in-sheet representation.

**Mitigation:** Smartsheet's intrinsic row-level `created_at` (and `modified_at`) attributes are full datetimes and are queryable via the API. Code-side ordering and time-of-day inspection use those fields rather than the in-sheet DATE columns. The in-sheet DATE columns serve human readability; the intrinsic timestamps serve programmatic precision.

**Revisit when:** Smartsheet API surfaces user-editable DATETIME columns, or a workstream finds DATE-only resolution genuinely insufficient and the `created_at` fallback isn't viable for the use case.

## Smartsheet API constraint: AUTO_NUMBER columns rejected at sheet creation [OPEN]

Discovered same session. `systemColumnType: AUTO_NUMBER` is rejected at the "Create Sheet" endpoint, whether or not the column is primary, with or without an `autoNumberFormat` config. Other system column types (`MODIFIED_DATE`, `MODIFIED_BY`) are accepted in the same payload — so the rejection is specific to AUTO_NUMBER, not a generic system-column-at-create issue.

**Workaround:** Each system sheet's primary column is a plain `TEXT_NUMBER` that code populates with a descriptive label ("Error", "Quarantined Message", "Entry"). Smartsheet's intrinsic row IDs serve as the unique identity for any code-side references.

**Mitigation:** Code-side row references use the Smartsheet row ID (returned in every API response). The human-readable primary column gives operators a meaningful label in the UI without needing auto-numbering.

**Revisit when:** A workstream requires user-visible auto-IDs (e.g., a customer-facing ticket number) and the code-populated label pattern is insufficient. Likely never — the intrinsic row IDs cover the technical need and labels cover the human need.

## parse_job_v3: V/S vendor-sub enumeration unclaimed [CLOSED 2026-05-19]

Resolved by adding `parse_vendor_sub(raw) -> Optional[VendorSubParse]` to `box_migration/parse_job_v3.py` and inserting it into the reconcile harness's claim chain between `subsubject` and `canonical_non_job`. Regex shape `^(?P<letter>[VS])(?P<index>\d{2})\.\s+(?P<name>.+?)\s*$` — capped at two digits so single-digit V1./S1. stay in `SUBJOB_LETTER_UC`'s domain.

Coverage delta when re-running the reconcile against the live 10-portfolio listings: **212 unique names** moved from unclaimed to `vendor_sub` (the original tech_debt estimate of 60–90 was an under-count; estimate was based on unique-occurrence math but the actual unique-name count is higher). Unclaimed share dropped 54.9% → 51.1%. Full 33-test coverage in `tests/test_parse_vendor_sub.py`.

Resolution: see commit on the `feature/vendor-sub-parser` branch (squash-merged), and `docs/session_logs/2026-05-19_chore_sweep_and_mypy_lockdown.md`.

## parse_job_v3: ISO date prefix (YYYY-MM-DD) unclaimed [CLOSED 2026-05-19]

Resolved by extending `parse_date_prefix` in-place with a new `DATE_PREFIX_ISO` regex (`^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<topic>.+?)\s*$`). ISO matches return `DatePrefixParse` with `direction='ISO'`, joining the existing `R` / `S` discriminators in the same `direction` field. R./S. behavior is preserved unchanged; covered by regression tests in `tests/test_parse_date_prefix.py`.

Reconcile claim chain extended with a new `date_prefix` claim between `vendor_sub` and `canonical_non_job` — needed because the existing chain had no date-prefix claim at all, so ISO matches wouldn't have shown up in reconcile output otherwise. Side effect: existing uppercase R./S. and chaos-flagged lowercase r./s. forms now also get claimed structurally (chaos detection is orthogonal — same name can be both `date_prefix` claimed AND `date_prefix_lowercase` chaos-flagged).

Coverage delta when re-running the reconcile: **11 unique names** in the new `date_prefix` claim (mix of ISO + R./S. + lowercase r./s. forms; tech_debt entry estimated ~13 ISO uniques, close enough). Unclaimed share dropped 51.1% → 50.9%.

24 tests cover the new ISO form, R./S. regression, lowercase r./s. warning preservation, direction discriminator, and negatives. Tests at `tests/test_parse_date_prefix.py`.

Resolution: see commit on the `feature/iso-date-prefix` branch (squash-merged), and `docs/session_logs/2026-05-19_chore_sweep_and_mypy_lockdown.md`.

## parse_job_v3: person_tag_in_subject chaos over-match [CLOSED 2026-05-20]

Resolved by adopting **Direction (A)** from `docs/audits/person_tag_audit_2026-05-19.md`: the third alternation (`-\s*[A-Z][a-z]+\s*$`, "trailing-capitalized-word after dash") was removed from `PERSON_TAG_IN_SUBJECT` in `box_migration/parse_job_v3.py`. The refined regex keeps the two alternations that the audit confirmed as high-precision:

```python
PERSON_TAG_IN_SUBJECT = re.compile(
    r'(\bfor\s+[A-Z]{3,}\b|'                            # "for ZACK"
    r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b)'  # "Teala Organize folder"
)
```

Consumer path (`detect_chaos` in the same file) is unchanged — the chaos flag still surfaces for alt-1 / alt-2 matches; alt-3 over-matches no longer fire. `m.group(0)` is the only match-object accessor downstream, so removing one alternation has no group-index ripple.

**Coverage delta (projection from the 2026-05-19 audit; live listings under `~/Downloads/Box_listings_for_Seth/` not present locally to re-measure):** ~138 person_tag chaos hits → ~2–4 hits across the 10-portfolio corpus. The 2–4 retained hits are alt-1 / alt-2 forms only (explicit "for XXX" and "First Organize/Cleanup/Notes/Files"); the ~95% noise from alt 3 is gone. A few real-or-leaning-real person-tag cases from the audit (samples #15–#20: `Structural - Bowman`, `R. Bowman-Pungo`, etc.) lose their flag by design — operator triages those visually in the folder tree. The audit doc has the full FP-vs-TP tradeoff analysis.

27 tests cover the refinement in `tests/test_person_tag.py`:
- Group A (7 tests): alt 1 + alt 2 positive-regression coverage across the audit's TPs.
- Group B (13 tests): every confirmed FP from the audit (rows #1–#12 + sample #19) — negative locks so reintroducing alt 3 fails the suite.
- Group C (5 tests): `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` acceptance lock — audit samples #15, #16, #17, #18, #20. The list and its comment block point a future maintainer back to the audit doc before they "re-add the missing coverage."
- Consumer-path integration (2 tests): `detect_chaos()` surfaces the flag for a TP and skips it for the most-common audit FP (`-Tracking` suffix).

**Redo history:** an earlier attempt (PR #34) implemented this same change but was closed-without-merge during a 2026-05-20 branch-cleanup pass where the head branch was deleted before verifying the merge had actually landed. The chore PR #37 explicitly preserved this entry's `[OPEN]` status; the present resolution comes from the redo PR. The cleanup-pass mistake is captured as a private feedback memory (`feedback_verify_merge_before_branch_delete`): always `gh pr view <N> --json mergedAt` before `git push origin --delete`, do not infer merge from "I saw CI green."

Resolution: see commit on the `feature/person-tag-regex-refinement-redo` branch (squash-merged), and `docs/session_logs/2026-05-20_person_tag_regex_refinement_redo.md`. Audit context preserved at `docs/audits/person_tag_audit_2026-05-19.md` (not modified by this PR).

## smartsheet_migration: import-time side effects in three scripts [CLOSED 2026-05-19]

Resolved by wrapping each script's top-level API work in a `main()` function behind `if __name__ == "__main__":`. Module-level constants (`SOURCE`, `DEST`, `SRC_TO_DEST_TITLE`) stay at module scope (cheap and pure). Imports refactored from `import os, sys` to PEP 8 form. No behavior change when invoked from the shell.

`tests/test_migration_import_hygiene.py` (new) locks the regression in: parametrized test imports each of the three modules with `SMARTSHEET_TOKEN` un-set; all 3 pass. If a future edit accidentally puts API-calling code back at module scope, the test will catch it.

The per-file-ignores `["E401", "I001", "F401", "B007", "UP035"]` in `pyproject.toml` for `smartsheet_migration/*` were NOT removed — 3 other files in the directory (`build_human_review.py`, `classify_closeout.py`, `migrate_schedule.py`) still use `import os, sys` and need the E401 ignore. Documented this in the session log so the ignores aren't mistaken for unnecessary on a future audit.

Resolution: see commit on the `fix/smartsheet-migration-import-time` branch (squash-merged), and `docs/session_logs/2026-05-19_chore_sweep_and_mypy_lockdown.md`.

## mypy: import-untyped noise from vendor SDKs without stubs [CLOSED 2026-05-19]

Resolved by adding the proper stub package for `requests` (`types-requests` added to dev dependencies in `pyproject.toml`) and a `[[tool.mypy.overrides]]` block silencing missing-stub errors for `msal` and `smartsheet` (neither publishes type information upstream as of 2026-05).

After applying, `mypy .` reports **zero errors** across all 64 source files. Brought the baseline from 4 → 0.

Locked in by adding mypy as a **blocking CI step** in `.github/workflows/ci.yml` — silent type drift across PRs is no longer possible. Mypy now runs in parallel with ruff and pytest; failure of any step blocks merge.

Resolution: see commit on the `feature/mypy-zero-and-ci` branch (squash-merged), and `docs/session_logs/2026-05-19_chore_sweep_and_mypy_lockdown.md`.

## parse_job_v3.py: matched needs type annotation [CLOSED 2026-05-18]

Resolved by adding the explicit annotation `matched: dict[Schema, list[str]] = {...}` in `classify_schema()`. Inferred type from `_V3_SIGNATURES` keys (Schema enum members) and the `.append(name)` call site where `name` is a `str`. One-line annotation change; zero behavior change. Preservation-over-refactor §14 honored — only the annotation line was modified.

Resolution: see commit on the `fix/parse-job-v3-matched-annotation` branch (squash-merged), and `docs/session_logs/2026-05-18_alert_critical_and_mypy_closure.md`.

Originally surfaced 2026-05-18 in the mypy baseline reconciliation; see `docs/reports/2026-05-18_mypy_baseline.md` for the lifecycle context.

## smartsheet_migration/ss_api.py: api body arg type mismatch [CLOSED 2026-05-18]

Resolved by widening the `body` parameter annotation on `api()` from `dict | None` to `dict | list | None`. Single-character-class edit on the signature line; all existing call sites continue to type-check (the `add_rows()` caller that passed `list[dict]` now matches). Real-bug carve-out under Op Stds v11 §14.

Resolution: see commit on the `fix/ss-api-body-arg-type` branch (squash-merged), and `docs/session_logs/2026-05-18_alert_critical_and_mypy_closure.md`.

Originally surfaced 2026-05-18 in the mypy baseline reconciliation; see `docs/reports/2026-05-18_mypy_baseline.md` for the lifecycle context.

## smartsheet_migration/migrate_fl.py: warnings list type annotation [CLOSED 2026-05-18]

Resolved by adding the explicit annotation `warnings: list[str] = []` in `derive_payment_method()`. Element type inferred from the `.append(...)` call sites which pass string literals describing payment-method derivation warnings. One-line annotation change; zero behavior change.

Resolution: see commit on the `fix/migrate-fl-warnings-annotation` branch (squash-merged), and `docs/session_logs/2026-05-18_alert_critical_and_mypy_closure.md`.

Originally surfaced 2026-05-18 in the mypy baseline reconciliation; see `docs/reports/2026-05-18_mypy_baseline.md` for the lifecycle context.

## Mail.app rule silent disable on macOS updates [PARTIALLY MITIGATED 2026-05-22]

macOS updates have a known pattern of silently disabling Mail.app rules without warning. Affects any workstream whose intake depends on Mail.app rules routing messages to the Claude Code script.

**Mitigation in place (Watchdog Check F, PR #36):** Watchdog has an inbound-mail-activity check across all intake-bearing workstreams, surfacing WARN when no recent intake activity is observed.

**Architectural cutover (safety_reports, PR #59, 2026-05-22):** safety_reports migrated off the Mail.app rule trigger to a launchd-driven Graph polling daemon (`safety_reports/intake_poll.py`). This eliminates the silent-disable risk for safety_reports specifically — no Mail.app rule exists in the trigger path anymore. Future workstreams should use the same polling pattern rather than Mail.app rules; this tech-debt entry stays OPEN until that becomes the documented standard for new intake-bearing workstreams (likely Email Triage Brief v5 update + a shared/runner.py abstraction at PR #60 when the second polling consumer ships).

Watchdog Check F still polls mailbox-idle as a proxy for trigger health — works unchanged for safety_reports after PR #59 because the inbox-activity signal is the same regardless of trigger mechanism. A cleaner heartbeat-based replacement (read `~/its/state/safety_intake_heartbeat.txt`) is queued as a follow-up PR after PR #60.

Resolves fully when: every intake-bearing workstream is on a polling daemon (no Mail.app rule trigger remains anywhere in ITS), and Watchdog Check F is repurposed to read the per-daemon heartbeat files instead of mailbox-idle.

Originally captured in Foundation Scaffold v4 "Outstanding Gotchas"; carried forward through v5; re-surfaced via Cascade Audit Errata 2026-05-19; mitigation lifecycle landed via PR #36 (Watchdog Check F) + PR #59 (safety_reports cutover).

## PowerShell macOS Gatekeeper deprecation 2026-09-01 [OPEN]

The powershell@preview cask path used for EXO ServicePrincipal management (Connect-ExchangeOnline; New-ServicePrincipal) is scheduled for macOS Gatekeeper deprecation on 2026-09-01. Without intervention, post-deprecation runs will fail Gatekeeper signature verification on the cutover MacBook.

Plan B: Azure Cloud Shell. Same Connect-ExchangeOnline + New-ServicePrincipal commands run in a browser shell instead of local PowerShell. No code change required; runbook change only.

Cutover impact: Handover Plan v6 Step 4 verification currently assumes local PowerShell. If Phase 1.5 cutover lands after 2026-09-01, runbook needs the Azure Cloud Shell variant.

Resolves when: 2026-08-15 calendar check confirms status (still scheduled / postponed / cask alternative emerged). Runbook updated based on findings.

## anomaly_logger: SUSPICIOUS_FIELD_PATTERNS will false-positive on legitimate system_* fields [OPEN 2026-05-20]

`shared/anomaly_logger.py` flags any extraction field name matching `^system_` as a security anomaly (Phase 1 starter sentinel list for prompt-injection detection). The pattern is correct against the threat model — a legitimate workstream extraction schema shouldn't include `system_*` field names, so their presence suggests the AI invented them under injection.

**The risk:** this is a forward-dated FP source. As workstream extraction schemas mature, any legitimate field with a `system_` prefix (e.g., `system_version`, `system_id`, `system_serial_number` on machine pre-inspections) will fire `security_flag=True` on every extraction, polluting `ITS_Review_Queue` with noise and training operators to dismiss the flag.

Tuning belongs to the first 30 days of sandbox operation against real extraction outputs (per Safety Reports Brief v6 — "Phase 1 sentinel list, extend as patterns emerge"). The sentinel list should be re-audited once `safety_reports/weekly_generate.py` has run against the migrated closed-project corpus and produced a representative extraction sample.

**Specific suggested follow-ups when tuning lands:**
- Narrow `^system_` to specific known-bad names (`system_prompt`, `system_role`, `system_instruction`) rather than the prefix glob.
- Same audit for `^role_` and `^ignore_` — both have similar FP-on-legitimate-naming risk.
- Add a `tests/test_anomaly_logger.py` case for any legitimate field name that ends up in a real extraction schema, so the sentinel list and the schemas can't drift apart.

Surfaced 2026-05-20 in a senior-dev audit pass; not yet triggered in practice because no workstream extraction has shipped.

## R2 Watchdog Check E (Anthropic spend trend) deferred to Phase 1.5 [OPEN 2026-05-20]

Check E of R2 Watchdog (Anthropic API spend trend analysis) deferred to a follow-on PR (the Check E shipping PR) at Phase 1.5 production cutover. **Architectural choice, not capability gap.** Individual Anthropic orgs DO expose Admin keys once a formal Organization is created (Settings → Organization with business address; verified 2026-05-20). Deferral rationale: sandbox spend signal-to-noise is too low at $5-credit scale for trend analysis to produce meaningful alerts. Re-evaluate at production cutover when spend is real and recurring. Implementation will add `shared/anthropic_billing.py` + `_check_spend_trend` in `scripts/watchdog.py`, seed the 4 `spend.*` `ITS_Config` rows + the `system.anthropic_admin_api_keychain_key` row, and convert the existing smoke runner's Phase E from a SKIPPED placeholder into a real exerciser.

Originally surfaced 2026-05-20 in R2 Session 2 pre-flight (the Keychain `ITS_ANTHROPIC_ADMIN_API_KEY` held a workspace key, `sk-ant-api03-…` prefix, not an Admin key). Session 2 shipped Checks A/B/C/D/F via PR #36; Check E is the only outstanding piece of the R2 Watchdog spec.

## PowerShell `Get-ApplicationAccessPolicy -Identity <friendly-name>` directory lookup fails [OPEN 2026-05-20]

`Get-ApplicationAccessPolicy -Identity <friendly-name>` fails with a directory-object-not-found error in Exchange Online PowerShell, even when the policy exists and is valid.

**Workaround:** call the bare cmdlet (no `-Identity`) and filter the result set client-side. Pattern: `Get-ApplicationAccessPolicy | Where-Object { $_.Description -match '<keyword>' }` or pipe to `Select` and pattern-match the returned rows.

Captured 2026-05-20 during M365 sandbox re-verification while validating the `ITS Scoped Mailboxes` policy for R2 Watchdog Check F. The bare-cmdlet form returned a valid record with `IsValid: True` despite the friendly-name lookup failing seconds earlier on the same policy.

## voice@ mailbox AppAccessPolicy scope addition pending [OPEN 2026-05-20]

`voice@evergreenmirror.com` is one of 5 ITS-intake mailboxes (per the mailbox roster) but is NOT currently in the `ITS Scoped Mailboxes` ApplicationAccessPolicy scope. Confirmed by `Get-ApplicationAccessPolicy` on 2026-05-20 — current scope covers `safety / procurement / subcontracts / its`, no `voice@`.

**Resolves when:** an ITS workstream activates the `voice@` mailbox as an intake source. At that point: add `voice@evergreenmirror.com` to the AppAccessPolicy scope via Exchange Online PowerShell, and register the corresponding `mail_intake.voice.max_idle_hours` row in `ITS_Config` so R2 Watchdog Check F starts monitoring it. No code change required for the policy update; the watchdog already iterates `mail_intake.*` rows via `smartsheet_client.get_settings_with_prefix` (PR #36).

## Stale Anthropic Service Account `svac_…SR7vDMJ` for archival [OPEN 2026-05-20]

Stale Anthropic Service Account `svac_…SR7vDMJ` (created during R2 Watchdog Check E investigation 2026-05-20) flagged for archival. The associated workspace API key has already been deleted from macOS Keychain. No urgency; clean up when next in the Anthropic Console (Settings → Service Accounts → Archive). Captured here so the cleanup isn't forgotten at the next Anthropic-Console visit.

## Remove unused `[jwt]` extra from boxsdk dependency [OPEN 2026-05-20]

`pyproject.toml` currently pins `boxsdk[jwt]>=3.10.0,<4.0.0`. The `[jwt]` extra pulls in `PyJWT` and `cryptography` transitively. ITS uses OAuth 2.0 User Authentication (per PR #39, commit `2ce6ece`) and never exercises the JWT auth path; the extra dependencies are dead weight in the install tree.

**Action:** change to plain `boxsdk>=3.10.0,<4.0.0`. Run `scripts/smoke_test_box.py` after the change to confirm the OAuth path still works.

**Urgency:** low. No functional impact, just install-tree hygiene.

Surfaced: PR #39 review, 2026-05-20.

## Eventually migrate from legacy boxsdk to `box_sdk_gen` (Gen API) [OPEN 2026-05-20]

The `boxsdk` PyPI package jumped to a renamed Gen API at 10.x (imports as `box_sdk_gen`, with a substantially different surface). PR #39 pins to `<4.0.0` to use the legacy 3.x API. The Gen API is the future direction per Box; legacy 3.x will eventually be deprecated.

**Action:** re-evaluate when (a) Box announces a deprecation timeline for 3.x, (b) the legacy API lacks something the Gen API offers, or (c) annual dependency-hygiene sweep.

**Migration scope:** `shared/box_client.py`, `tests/test_box_client.py`, `scripts/setup_box_oauth.py`, `scripts/smoke_test_box.py`. Probably non-trivial (~half day of work).

**Urgency:** low. Pin holds until Box deprecation pressure or capability gap.

Surfaced: PR #39 review, 2026-05-20.

## Add Box refresh-token age check to R2 Watchdog [OPEN 2026-05-20]

`ITS_BOX_REFRESH_TOKEN` rotates on every Box API call and stays valid as long as ITS makes at least one Box call every 60 days. If ITS goes dark for >60 days (extended outage, post-handover period without activity), the refresh token expires and re-running `scripts/setup_box_oauth.py` is required.

A watchdog check would warn the operator before the token expires:
- **Warn** at 50 days since last rotation
- **Critical** at 58 days

**Mechanism:** track last-rotation timestamp via either
- (a) a sidecar Keychain entry `ITS_BOX_REFRESH_TOKEN_LAST_ROTATED` updated by the `store_tokens` callback in `shared/box_client.py`, or
- (b) a row in `ITS_Config` (`system.box_refresh_token_last_rotated`).

**Implementation venue:** R2 Watchdog Session 2 (planning pass needed first) or later. Not blocking; absence of this check is documented in the handover runbook as a known operator-touch requirement.

**Urgency:** medium. Real risk if ITS goes dark for an extended period post-handover. Pre-handover is fine because ITS runs daily.

Surfaced: PR #39 brief, 2026-05-20.

## Phase 1.5 — provision dedicated ITS Box user account, re-auth [OPEN 2026-05-20]

ITS currently authenticates to Box as `seths@evergreenmirror.com` (operator account). All API actions attribute to that user in Box audit trails, and all ITS-created files are owned by that user.

At Phase 1.5 cutover, provision a dedicated ITS Box user account (e.g., `its@evergreenrenewables.com` once the production tenant is live) and re-authenticate ITS as that user. No code changes needed — just re-run `scripts/setup_box_oauth.py` while logged into Box as the new user.

**Concerns to handle at migration time:**
- File ownership of anything ITS created under the operator account may need to be transferred to the new user.
- Collaborator permissions on existing folders must be granted to the new user before re-auth.
- Old refresh token under the operator account should be revoked in the Box account settings.

**Urgency:** Phase 1.5 cutover task. Not before.

Surfaced: PR #39 brief, 2026-05-20.

## Confirm `canonical_job_path()` format with owner [OPEN 2026-05-20]

`shared/box_client.py` exposes `canonical_job_path(customer, job_number, job_name, year)` which returns `"/Customer/JobNum — JobName/YYYY/"`. This is the WRITE-path format for new ITS-created content.

Owner confirmation has not happened yet — the format is the legacy-stub placeholder, never validated against owner preference. `box_migration/parse_job_v3.py` handles read-side recognition of the 4 active Box schemas, so this only affects what ITS creates going forward, not what it can recognize.

**Action:** surface to owner at next opportunity, confirm or adjust format, update `shared/box_client.py` + tests if needed.

**Urgency:** low until the first workstream consumes `canonical_job_path`. At that point the decision becomes blocking and locks the format for all future ITS-created content.

Surfaced: PR #39 brief, Open Question Q2, 2026-05-20.

## Seed `system.box_smoke_folder_id` in ITS_Config [OPEN 2026-05-20]

`scripts/smoke_test_box.py` supports a `--write-test` opt-in flag that does a write-read-delete loop against a known sandbox folder. The folder ID comes from an `ITS_Config` row at `system.box_smoke_folder_id`.

The row is not yet seeded. The read-only smoke (default invocation) works without it; only the opt-in write-test path requires it.

**Action:** create a dedicated "ITS Smoke" folder in Box, copy its folder ID, seed the `ITS_Config` row. After seeding, run `python3 scripts/smoke_test_box.py --write-test` once to confirm.

**Urgency:** low. Read-only smoke is sufficient for most operator checks. Write-test is useful only when diagnosing suspected scope or permission issues.

Surfaced: PR #39 brief, Open Question Q4, 2026-05-20.

## Alert-routing dedupe key granularity [OPEN 2026-05-20]

(Naming gloss for this entry and several below: "PR α" = PR #42 — alert-dedupe core; "PR β" = PR #44 — watchdog Check G summary sweep. Greek-letter aliases predate the actual PR numbers landing.)

`shared/alert_dedupe.py` keys dedupe windows on `(script, error_code)` (built at the `_fire_resend_leg` call site). Today's only call path uses `error_code="uncaught_exception"`, so all decorator-driven CRITICALs from a given script collapse into one window. If production shows distinct underlying exception classes inside one script collapsing within a window — and the operator misses the second bug because the first one suppressed its alert — upgrade the key to `(script, error_code, exc_class)`.

**Action:** one-line change at the `dedupe_key = f"{script}::{error_code}"` site in `shared/error_log._fire_resend_leg`. Thread `exc_class` from the decorator's `except Exception as e:` path via `type(e).__name__`.

**Urgency:** low until production surfaces the collapse-different-bugs failure mode. Bounded blast radius — Smartsheet ITS_Errors + Sentry still record each bug separately, so the operator sees the second bug eventually; only the wake-up email is delayed.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Cross-leg dedupe activation [OPEN 2026-05-20]

PR α suppresses only the Resend leg. Sentry events and Smartsheet ITS_Errors rows always write (per Op Stds v11 §3.1 — dedupe applies only to push, never to records). Today this is the right choice: Sentry's own alert rules and Smartsheet's sheet-level notifications are NOT configured.

**Resolves when:** the operator configures Sentry alert rules (or Smartsheet notifications) that themselves wake the operator on every event. At that point, those legs become "push" surfaces too and need their own dedupe layer. The shared `correlation_id` is already wired through all three legs, so a future cross-leg dedupe (or alert-aggregator) has the join key it needs.

**Urgency:** activates only when external alert rules are configured. No risk while Sentry/Smartsheet stay record-only.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Alert-dedupe state is per-machine [OPEN 2026-05-20]

`~/its/state/alert_dedupe.json` lives on the local MacBook. The dedupe window is per-host. If ITS ever runs on multiple hosts (Phase 4+ blueprint generalization, or a hot-spare during MacBook RMA), each host would dedupe independently — and an operator-facing flapping CRITICAL on two hosts would produce one email per host instead of one total.

**Resolves when:** ITS gains multi-host execution. The state needs to move into a centralized store. Smartsheet itself can't host it (Smartsheet IS a triple-fire leg; circular dependency). Likely candidates: a dedicated S3 prefix, a Redis sidecar, or a per-customer SQLite that lives on whichever host happens to be authoritative.

**Urgency:** low. Phase 1 through Phase 3 is single-host on a designated MacBook. Multi-host is a Phase 4+ blueprint-generalization decision.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Alert-dedupe state file grows unboundedly until PR β lands [CLOSED 2026-05-21]

PR α (#42) wrote one entry per `(script, error_code)` key to `~/its/state/alert_dedupe.json` and never deleted. The follow-up PR β (watchdog summary sweep) was queued to delete entries once their summary email had fired and `summarized=true` had been set. Until PR β landed, the file grew (one entry per distinct dedupe key across the ITS lifetime — operationally acceptable bound).

**Closed by PR #44 (PR β — watchdog Check G — alert-dedupe summary sweep).** Two-phase deletion landed: phase 1 (sweep N) fires the summary email + `mark_summarized`; phase 2 (sweep N+1) deletes the now-`summarized=true` entry. State-file growth bound improved to ≤1 day per `(script, error_code)` key pair (further detailed in the successor entry below). Crash-safe: a crash between Resend send and `mark_summarized` causes the next sweep to re-fire (duplicate email is acceptable; silent loss is not).

Subsequent V1 fix (PR #52) added MAINTENANCE-aware defer behavior — phase-1 fires defer during the MAINTENANCE window, phase-2 deletion proceeds regardless. Bounded delay = MAINTENANCE window + one watchdog cadence.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20. Closed by PR #44 + #52, 2026-05-21.

## Smoke harness pattern divergence between dedupe smoke and Resend/Sentry smokes [OPEN 2026-05-20]

`scripts/smoke_test_alert_dedupe.py` uses the full `@its_error_log` decorator path so all three triple-fire legs fire (Smartsheet `log()` write + Resend + Sentry). `scripts/smoke_test_sentry.py` and `scripts/smoke_test_resend.py` call `shared.error_log._alert_critical` directly, which deliberately bypasses `log()` and therefore does NOT write to ITS_Errors.

The divergence is acceptable because the older two scripts validate narrower scopes (the Sentry leg, the Resend leg), and the alert-dedupe smoke validates the cross-leg integration. The trap is that the `_alert_critical`-direct pattern silently skips the Smartsheet leg — if a future smoke claims to exercise full triple-fire but uses that pattern, the ITS_Errors assertion will pass vacuously (zero rows match, zero rows expected by the harness).

**Action:** any new smoke that intends to verify all three legs MUST go through the `@its_error_log` decorator. Smoke that targets a single leg can keep the `_alert_critical`-direct pattern.

**Urgency:** low. No active failure; this entry is forward-protection for the next time someone writes a triple-fire smoke. Discovered post-PR-#42 merge when the operator's live run produced 0 ITS_Errors rows.

Surfaced: PR α (alert-dedupe-core) live verification, 2026-05-20.

## Alert-dedupe state-file growth in pathological flap-with-new-error-code scenarios [OPEN 2026-05-20]

PR β's two-phase deletion bounds state-file growth at ≤1 day per `(script, error_code)` key pair across the sweep cadence: an entry is fired-and-marked on sweep N, deleted on sweep N+1. Worst-case file growth across the ITS lifetime is one entry per distinct dedupe key.

The pathological scenario the bound assumes against: a script that flaps repeatedly with a NEW `error_code` each window, producing unbounded distinct keys per day. `_alert_critical` today always uses `error_code="uncaught_exception"`, so the bound holds. If `_fire_resend_leg` is ever upgraded to a richer key (e.g., `(script, error_code, exc_class)` per the existing tech-debt entry on key granularity), AND the underlying script raises a wide variety of exception classes within short windows, growth could accelerate.

**Action:** monitor state-file row count. If it grows past ~100 persistent entries between sweeps, investigate before tuning sweep cadence or compacting the state schema.

**Urgency:** none today. Bounded blast radius; sweep cadence is the lever if the file ever balloons.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Watchdog sweep cadence vs dedupe window length [OPEN 2026-05-20]

Default `alerting.dedupe_window_minutes = 60`. Watchdog runs once daily at 7:00 AM ET. Worst-case operator-visible summary delay = ~24 hours from window close (a window that closes at 7:01 AM waits until the next morning's sweep).

This is intentional: operators on the daily-rhythm cadence don't need real-time summary push, and the 24h delay only applies to the close-the-loop notification — the original CRITICAL email + the suppressed-marker log lines fire in real time.

**Resolves if:** operator wants tighter feedback. Lever 1 — increase watchdog cadence to hourly via launchd. Lever 2 — separate the summary sweep into its own scheduled script with its own cadence. No code change to dedupe core in either case.

**Urgency:** none. Re-evaluate if operator triage workflow shows ≥24h-delayed summaries causing problems.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Summary email content depth (filter-criteria vs inline correlation IDs) [OPEN 2026-05-20]

PR β summary email body lists aggregate counts + window timestamps + filter criteria pointing at ITS_Errors (Script + Surfaced At range). It does NOT enumerate per-suppressed-event correlation IDs inline, because the state file stores only aggregates per dedupe key — individual UUIDs live in ITS_Errors rows.

If operator triage workflow shows excessive Smartsheet lookups when triaging a summary, the upgrade path is: grow the state schema to retain a list of correlation IDs per window (capped at N most recent to bound file size), and inline those in the summary body. State migration would be needed; existing entries lack the field.

**Action:** track operator triage patterns. If "open the summary → open ITS_Errors → copy filter → run filter" becomes a frequent friction point, upgrade the schema.

**Urgency:** none today. Pull-from-source-of-truth pattern is cleaner if operator only triages a handful of summaries per week.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Picklist_Sync_Config mixes config and runtime state [OPEN 2026-05-20]

`Picklist_Sync_Config` holds both configuration (mapping_id, source/target sheet+column, enabled, notes) and runtime state (last_run_at, last_run_hash) on the same sheet. Architecturally a small smell — runtime state evolving on a "config" sheet means operators editing the sheet can accidentally clear hash/timestamp, forcing a full re-sync.

**Why kept as-is:** §14 preservation-over-refactor. Phase 1.5 doesn't need the split. The convenience of "one sheet per concern" outweighs the purity cost while there's only one consumer.

**Resolves if:** picklist_sync grows complex enough to need migration/versioning (multi-customer fork edge cases, schema evolution of per-mapping state, etc.). At that point: move `last_run_at` + `last_run_hash` to a separate `Picklist_Sync_State` sheet keyed on `mapping_id`, leave `Picklist_Sync_Config` purely declarative.

**Urgency:** none. Watch for operator-edit accidents that wipe hash/timestamp — first such incident is the resolution trigger.

Surfaced: Picklist sync hardening review, 2026-05-20.

## SDK-vs-live body-shape mismatches need integration coverage [OPEN 2026-05-20]

PRs #47/#48/#49 each surfaced one body-shape mismatch the Smartsheet SDK accepted silently but the live API rejected, in successive iterations:

- **PR #47**: `id` in body — errorCode 1032 ("attribute(s) column.id are not allowed for this operation").
- **PR #48**: `type` missing from body — errorCode 1090 ("Column.type is required when changing options").
- **PR #49**: `type` present but wrapped as `EnumeratedValue`, SDK silently strips it — wire body becomes `{"options": [...]}` with no `type`, API rejects same as #48.

Class of bug: `SimpleNamespace`-based mocks at the SDK boundary don't enforce the live API's contract on body shape, required fields, or value wrapping. Mock tests passed; live calls failed.

**Mitigation landed in this PR (2026-05-21):** `tests/test_smartsheet_client_integration.py` runs create → list → update → delete round-trips against live sandbox sheets. Registered as `@pytest.mark.integration`; default `pytest` skips them (pyproject.toml `addopts = -m 'not integration'`). Operator runs `pytest -m integration` pre-deployment after any `shared/smartsheet_client.py` or `shared/picklist_sync.py` change.

**Pattern to extend:** any future `shared/*` SDK wrapper that exercises a non-trivial verb (update/create/delete) on typed columns or rows should gain a parallel integration test. The pattern: create the minimum live state required, exercise the verb, assert post-state, tear down in `finally`.

**Urgency:** addressed. Note kept open for visibility — any new wrapper that lands without parallel integration coverage re-introduces the class of bug.

Surfaced: PR #46 → #47 → #48 → #49 iteration, 2026-05-20/21.

## Smartsheet MULTI_PICKLIST type doesn't survive sheet-creation round-trip [OPEN 2026-05-21]

Creating a sheet with `{"type": "MULTI_PICKLIST", "options": [...]}` via `Folders.create_sheet_in_folder` (or the equivalent REST POST `/folders/{id}/sheets`) returns 200 OK, but a subsequent `GET /sheets/{id}?include=columns` shows the column's type as `TEXT_NUMBER`, not `MULTI_PICKLIST`. The column doesn't behave as MULTI_PICKLIST either.

Probed live during the PR #51 integration-test run. Adding the column via a separate `POST /sheets/{id}/columns` after the sheet exists DOES return `"type": "MULTI_PICKLIST"` in the immediate response — but the subsequent GET still shows TEXT_NUMBER. The discrepancy is consistent enough that "sheet creation with MULTI_PICKLIST" appears to be a Smartsheet API behavior, not a transient race.

**Impact on `shared/picklist_sync.py`:** none today. The picklist sync's only target columns are PICKLIST (master DBs → downstream forms). MULTI_PICKLIST is a defensive code path in `update_column_options` (accepts the type, unit-tested via `test_update_column_options_accepts_multi_picklist`) but no production mapping uses it.

**Action if MULTI_PICKLIST becomes a real use case:** investigate whether the column needs to be created with additional flags (`validation`, `width`, …) or via a different REST endpoint. May require a Smartsheet support ticket — their column-type matrix isn't fully self-documenting.

**Urgency:** none. Tracked for visibility so a future operator looking at the integration test's missing MULTI_PICKLIST coverage understands why.

Surfaced: PR #51 integration test run, 2026-05-21.

## Smartsheet UI-only constraints (Forms, CF, Filter Views, Restrict-to-dropdown) [OPEN]

Several Smartsheet features are exposed only through the Smartsheet web UI and have NO REST/SDK surface — meaning Claude Code can NOT provision, audit, or sync these per-customer settings during deployment. Operator must configure each manually at deployment time and document the choices.

The known UI-only surfaces (as of 2026-05):

- **Form creation + configuration** — `Smartsheet → Forms` panel. Forms are the primary intake surface for several workstreams; no API equivalent. Form rules (required fields, conditional logic, custom thank-you page, branding) are all UI-only.
- **Conditional Formatting** (cell-color rules based on cell values or row state) — UI-only.
- **Filter Views** (saved per-user filter definitions over a sheet) — UI-only.
- **Restrict to dropdown values only** (PICKLIST column validation toggle) — UI-only. Critical for `shared/picklist_sync.py` activation: the sync writes the option list, but the "reject free-text entries" enforcement toggle must be set manually per column. Without it, picklist sync still works but users can type values that aren't in the master DB (canonical-name drift).

**Impact on `shared/picklist_sync.py`:** the `Restrict to dropdown values only` toggle must be manually set on each downstream PICKLIST column at deployment time. Without it, the sync still works (options stay in sync) but the strict-mode validation that prevents users from typing vendor-name drift is absent. Documented in `docs/references/picklist_sync.md` activation checklist step 5.

**Impact on form-and-clone cascade:** every form requires manual UI setup. The cascade flow assumes operator builds forms in the UI as the final cutover step.

**Resolves if:** Smartsheet exposes any of these surfaces via API. Worth re-checking annually — Smartsheet's API surface expands slowly. No action item today; this entry exists so future operators / new customer forks know the manual-deployment-step list without rediscovering it.

**Urgency:** none. Operationally accepted; manual deployment steps documented per-customer.

Surfaced: Phase-0 architecture review 2026-05; referenced from `docs/references/picklist_sync.md` activation checklist.

## safety_reports week-folder create-find race condition [OPEN 2026-05-21]

`safety_reports/week_folder.ensure_current_week_folder` performs a find-or-create on the per-week folder under each project's Field Reports subtree. Two concurrent callers (e.g., a same-week intake.py and a Friday weekly_generate.py firing within the same minute) could both pass the initial `find_folder_by_name_in_folder` step and both create the folder; Smartsheet does not enforce folder-name uniqueness, so both creates succeed.

The helper detects the duplicate on a post-create find: if the post-create lookup returns a different folder ID than the just-created one, it logs a WARN to ITS_Errors with `error_code="week_folder_race_duplicate"` and proceeds with the first match (the survivor). The orphan folder ID appears in the WARN message for operator triage.

**Workaround:** operator manually deletes orphan folders via short-lived sandbox token + curl per Op Stds v11 §25 MCP-gap REST fallback (`curl -X DELETE https://api.smartsheet.com/2.0/folders/<orphan_id> -H "Authorization: Bearer <token>"`). No automatic cleanup — race is rare at single-machine cadence, and the safer move is operator visibility (WARN → review) over an automated delete that could race against legitimate concurrent writes.

**Why not auto-clean:** the orphan folder is initially empty (the losing-race caller hasn't created its sheets yet at the moment of duplicate detection). But a subsequent run on the orphan side WOULD create sheets, and an auto-delete couldn't safely distinguish "empty orphan" from "filled-by-another-thread orphan." Operator visibility wins.

**Resolves if:** observed in practice (no incident expected at single-machine cadence; multi-machine ops would trigger this).

Surfaced: R3 foundation PR brief, 2026-05-21.

## Daily Reports schema gap — no Box Link column [OPEN 2026-05-21]

The `Daily Reports — Week of <date>` sheet schema (cloned forward by `safety_reports/week_folder.ensure_current_week_folder` from the Bradley 1 / Week of 2026-03-09 template, sheet ID 7282977254887300) has no explicit column for the filed Box document URL.

When `safety_reports/intake.py` lands in R3 session 1, each inbound safety email will be filed to Box; the Box URL is the audit trail back to the source document. Without a dedicated column, intake.py will embed the URL inside the existing `Notes / Action Items` cell — workable but harder to query and prone to cell-truncation as notes grow.

**Action at R3 session 1:** the session's brief should include a schema edit adding a `Box Link` (TEXT_NUMBER) column to the Bradley 1 / Week of 2026-03-09 template sheet (the canonical source for clones). The auto-gen helper will then carry the column forward into every new week's clone. Until that lands, intake.py embeds the URL in `Notes / Action Items`.

**Workaround in the interim:** intake.py's notes-embedding pattern. Once the column lands, the migration is a one-pass extraction of URLs from existing notes into the new column for any rows written between R3 session 1 start and the schema edit.

**Resolves at:** R3 session 1 (the intake.py wiring brief).

Surfaced: R3 foundation PR brief, 2026-05-21.

## `find_sheet_by_name_in_folder` switched from SDK to REST [CLOSED 2026-05-21]

Original PR #45 implementation used `smartsheet.Folders.get_folder()` — deprecated upstream AND returns stale folder data within a single SDK client session. A sheet created via the SDK's `create_sheet_in_folder()` does not appear in a subsequent `get_folder()` from the same client; direct REST sees it immediately.

PR #51 swapped the helper to direct REST. Unit tests updated to mock `requests.get` instead of the SDK shape. Removes the DeprecationWarning AND fixes the same-session-create-then-find bug. The picklist sync migration script's earlier success was a happy accident: it didn't exercise back-to-back create + find in the same Python process, so the SDK cache never tripped.

Closed by PR #51.

## Picklist-hardening pre-Customer-1 [CODE DELIVERED 2026-05-23 / operator UI work tracked in docs/audits/picklist_hardening_audit.md]

Code side shipped on `feat/picklist-hardening` branch:

- `shared/picklist_validation.py` — `PicklistViolationError` + `REGISTRY` (composed from `Severity`/`ReviewReason`/`SlaTier`/`ReviewStatus`/`QuarantineReason`/`ContactStatus` StrEnums) + `validate_cell` / `validate_row`. Opt-in semantics: unregistered (sheet, column) pairs pass-through; None and bool values bypass picklist check.
- `shared/smartsheet_client.py::add_rows` + `update_rows` — late-import `picklist_validation` (circular-import safe) and call `validate_row` BEFORE any payload construction. Invalid values raise `PicklistViolationError` pre-API-call.
- `scripts/audit_picklist_drift.py` — programmatic registry-vs-live drift audit; `--update-audit-doc` placeholder; writes `~/its/.watchdog/safety_picklist_audit.last_run` marker.
- `scripts/watchdog.py::TRACKED_JOBS` — added `safety_picklist_audit` with 8-day freshness window (weekly cadence).
- `docs/audits/picklist_hardening_audit.md` — operator's UI conversion checklist; one row per bounded-enum column with conversion status emojis (⬜ ✅ ⚠️ 🟦).

`shared/kill_switch.py` Phase 3 was a no-op: existing `SystemState` StrEnum + try/except fail-open (returns ACTIVE on unknown value per Op Stds v11 §1 — never silently halt) IS the per-key registry pattern. The brief's suggested change to return PAUSED would have inverted the fail-open behavior; preserved existing.

Tests: 949 → 1004 (+55: 20 validation + 8 smartsheet integration + 8 drift audit + transitive coverage). mypy 0, ruff clean. Capability gating intact.

Operator-side conversion items remain in `docs/audits/picklist_hardening_audit.md` — ~21 UI passes (toggle "Restrict to picklist values only" + add 3 PR #72 ReviewReason values + add ITS_Quarantine Disposition + Reason columns + 6 per-project template conversions). Audit doc IS the operator's checklist; after each batch, run `python -m scripts.audit_picklist_drift --update-audit-doc` to refresh status emojis.

Subsumes PR #72 leftover step #2 — the three new ITS_Review_Queue.Reason picklist values are now part of this audit's checklist.

**Closes when:** all rows in `docs/audits/picklist_hardening_audit.md` show ✅. At that point the watchdog's drift WARN-threshold can flip to ERROR.

## ITS_Trusted_Contacts sheet replaces ITS_Config JSON allowlists [DELIVERED 2026-05-23]

Code shipped on `feat/its-trusted-contacts` branch:

- `shared/trusted_contacts.py` — TrustedContact / ScopeVerdict / ContactStatus + 60s-TTL cache (`lookup`, `check_scope`).
- `shared/header_forgery.py` — Authentication-Results parser + Return-Path-vs-From mismatch (PASS / SOFT_FAIL / HARD_FAIL verdicts; trusts inbound MTA's DKIM — no local re-validation).
- `shared/graph_client.py::get_message` — opt-in `include_headers=True` projects `internetMessageHeaders` via `$select`.
- `safety_reports/intake.py` — Stage 2 refactored to `check_trusted_sender` (routing matrix); Stage 4b project-scope re-check after project resolves. Old `check_sender_allowlist` removed; legacy ITS_Config `allowed_senders` JSON list survives as the dead-fallback path (`trusted_contacts.fallback_to_its_config` INFO once per process) until operator deletes the row.
- `shared/quarantine.py` — `QuarantineReason` StrEnum added; `log_quarantined_message` accepts `reason=`, writes `[reason: <code>]` into Notes (no Reason column on live sheet).
- `shared/review_queue.py::ReviewReason` — three new picklist values (header-soft-fail-trusted / sender-pending-verification / project-out-of-scope) awaiting operator UI add.

Migrations: `scripts/migrations/build_its_trusted_contacts_sheet.py` (idempotent sheet create), `scripts/migrations/seed_its_trusted_contacts.py` (legacy → sheet seed, `--dry-run`).

Tests: +46 (12 trusted_contacts, 14 header_forgery, 10 intake_stage2_refactor, 2 graph_client include_headers, 3 quarantine reason, 1 integration, +4 regression deltas across test_intake / test_review_queue) — baseline 903 → 949.

Operator-side cutover items, all required before legacy fallback removal:
1. Run `build_its_trusted_contacts_sheet.py`, paste sheet ID into `shared/sheet_ids.py::SHEET_TRUSTED_CONTACTS`.
2. Add the 3 ITS_Review_Queue.Reason picklist values via UI.
3. Run `seed_its_trusted_contacts.py`, adjust seeded rows.
4. Live smoke against sandbox message.
5. After one Friday cycle clean, delete the ITS_Config `safety_reports.intake.allowed_senders` row.

## Fallback path removal after ITS_Config cutover [OPEN 2026-05-23]

Per the ITS_Trusted_Contacts delivery above, the legacy ITS_Config allowed_senders fallback stays in `safety_reports/intake.py` (`_check_legacy_allowlist` + the `sheet_contacts` branch in `_run_pipeline`) until the operator confirms one full Friday cycle clean post-cutover. Then:

- Remove `_check_legacy_allowlist`.
- Remove the `sheet_contacts = trusted_contacts._load_contacts()` / `if sheet_contacts:` branch in `_run_pipeline`; replace with direct `check_trusted_sender(...)` call.
- Delete `_fallback_logged` + the once-per-process INFO log.
- Drop the `CFG_ALLOWED_SENDERS` constant + `_read_allowed_senders` helper.
- Update `test_intake_stage2_refactor.py::test_empty_sheet_falls_back_to_its_config_allowlist` + `test_sheet_with_rows_is_authoritative_skips_legacy_allowlist` accordingly.

**Effort:** ~30-min session.

**Revisit when:** operator confirms one Friday cycle clean post-cutover.

## Native multi-PICKLIST graduation for Trusted Contacts scope columns [OPEN 2026-05-23]

`Project Scope` and `Workstream Scope` columns on `ITS_Trusted_Contacts` are TEXT_NUMBER JSON-lists, not native multi-PICKLIST. Rationale (per the Phase 1.4 brief): the Smartsheet SDK returns inconsistent shapes for multi-PICKLIST (sometimes comma-string, sometimes list) and the cross-sheet picklist sync from PR #45-51 doesn't cover multi-select reliably. Once the Phase 1.4 picklist-hardening deliverable lands:

- Convert column types to MULTI_PICKLIST.
- Update `shared/trusted_contacts.py::_parse_scope` to accept either form during the transition.
- Add reference-checked sync to the picklist_sync.py registry.

**Effort:** ~1 hour session.

**Revisit when:** Picklist Hardening #1 deliverable lands.

## DKIM in-process re-validation [OPEN 2026-05-23]

`shared/header_forgery.py` trusts the inbound MTA's `Authentication-Results` DKIM verdict — no local DNS TXT lookup + RSA verify. Acceptable for Phase 1: the only path delivering messages is via the verified inbound MTA chain. If a future threat-model session demands cryptographic re-validation:

- Add `dkimpy` (or `python-dkim`) to requirements.
- Replace the `dkim=tokens.get(...)` path with a re-validation step (parse `DKIM-Signature` → DNS TXT lookup → RSA verify).
- Cache DNS TXT records per (selector, domain) for the poll cycle.

**Effort:** ~half-day session.

**Revisit when:** security review or threat-model session flags the in-MTA-trust assumption.

## Operator-UI Shortcuts for trusted-contacts workflows [OPEN 2026-05-23]

`ITS_Trusted_Contacts` operator edits today require direct Smartsheet UI. A Shortcuts-track addition could wrap common flows:

- "Approve pending sender" — picks PENDING_VERIFICATION rows, prompts operator, flips to ACTIVE + sets Last Verified=today.
- "Disable sender" — by Email or row pick, flips Status to DISABLED + notes the reason.
- "Verify identity" — re-stamps Last Verified=today for ACTIVE rows.

**Effort:** ~half-day session.

**Revisit when:** Tooling-track session has bandwidth.

## Attachment screening pipeline Layers 1-3 [OPEN 2026-05-22]

Implement 4-layer attachment screening per Op Stds v11 §34 + FM v8 Invariant 2 Layer 6 (Layers 1-3 for Phase 1.5; Layer 4 VirusTotal deferred Phase 2+):
- Layer 1 (static): magic-number verification, size sanity, filename pattern matching.
- Layer 2 (structural): PyMuPDF or pypdf for PDF JS/embedded-file detection; python-docx/openpyxl for Office macro/OLE detection; EXIF anomalies; embedded URL extraction.
- Layer 3 (ClamAV): pyclamd + clamd daemon + freshclam auto-update. Homebrew install on operator Mac.
- Layer 4 (VirusTotal): defer.

EICAR test signature fixtures verify pipeline health without real malware. Integration test against corpus of legitimate DFR samples.

Disposition: malicious → ITS_Quarantine + CRITICAL triple-fire + sender DISABLED in ITS_Trusted_Contacts; suspicious → ITS_Review_Queue; clean → proceed.

**Effort:** ~half-day to one-day session (operator-side ClamAV install + code + tests).

**Revisit when:** Phase 1.4 security hardening session lands; required before Phase 1.5 cutover.

## 5-duplicate ITS_Errors sheets in System/02-Logs [OPEN 2026-05-22 — operator UI delete required]

Bootstrap drift from 2026-05-18 sheet creation: 5 ITS_Errors sheets created within ~75 seconds. Canonical sheet is 27291433258884 per Op Stds v11 §23. The four duplicates are dead and require operator UI delete:
- 2704945844277124
- 470411799121796
- 4505679602601860
- 4195780532326276

Smartsheet MCP has no delete-sheet primitive; operator UI is the only path.

**Revisit when:** next operator Smartsheet UI session; not blocking any code or workflow.

## 1 empty duplicate ITS_Daemon_Health sheet [OPEN 2026-05-22 — operator UI delete required]

Parallel chat build of ITS_Daemon_Health surface created an extra empty sheet 3717381690969988 in System / 04 — Daemons. Canonical sheet is 4529351700729732. Empty duplicate requires operator UI delete (Smartsheet MCP no delete-sheet primitive).

**Revisit when:** next operator Smartsheet UI session.

## Watchdog Check F retirement / Check H heartbeat-staleness successor [OPEN 2026-05-22]

Check F (Mail.app rule silent disable, PR #36) polls safety@evergreenmirror.com mailbox idle hours as a proxy for Mail.app-rule trigger health. Post-PR-#59, safety_reports is on a polling daemon and writes a heartbeat to ITS_Daemon_Health every 60 seconds. The mailbox-idle proxy is now redundant for safety_reports.

Check H (successor): read ITS_Daemon_Health for every Enabled=true daemon; flag rows where Last Heartbeat is older than 2 × Interval Seconds. Retire Check F when (a) Check H is operational and (b) no remaining workstream depends on Mail.app rules.

**Effort:** ~1-2 hour session.

**Revisit when:** second polling-daemon consumer ships (Email Triage or weekly_generate) — at that point shared/runner.py extraction + Check H consolidation become joint opportunity.

## safety_weekly_generate prompt v0.1.0 calibration [OPEN 2026-05-22]

Initial WPR generation prompt (`prompts/safety_weekly_generate.md` v0.1.0) anchors on the 2016-03-12 Gates Solar legacy WPR captured at `prompts/samples/legacy_wpr_gates_solar_2016-03-12.md`. Per Safety Reports Brief v6.1, calibrate v0.2.0 after the first 30 days of real Evergreen cycles — areas to watch:

- Whether reviewers consistently keep the [REVIEWER TO FILL] sentinels (vs. editing them out), suggesting prompt should drop or move those sections.
- Confidence threshold tuning. Default 0.85 was inherited from intake.py extraction; generation may warrant a different threshold once we see real distribution.
- Subcontractor-list extraction quality — currently derived from `Crew or Subcontractor` column values; might miss subs mentioned only in `Summary of Events` narrative.
- `narrative_summary` length tuning — model defaults to one paragraph but reviewer feedback may push for terser or denser summaries.
- Anomaly self-report sentinel coverage — current set (`apparent_injection_attempt`, `inconsistent_dates`, `crew_name_special_chars`) may need expansion.

**Effort:** ~half-day session including reviewer-feedback synthesis + v0.2.0 prompt edit + before/after diff documentation.

**Revisit when:** ~30 days of real Friday cycles have run (2026-06-22 plus or minus a week).

## Smartsheet transient 404 on first-project sheet/folder create [PARTIALLY MITIGATED 2026-05-22]

Two `weekly_generate` smoke runs on 2026-05-22 each surfaced exactly one transient 404 during per-project iteration:

- Smoke #1 (`--week-start 2030-01-07`): `SmartsheetNotFoundError('HTTP 404 (code 1006): Not Found')` on Bradley 2. Folder DID get created (cleanup confirmed it existed).
- Smoke #2 (`--week-start 2026-02-16`): same error on Rockford.

Different project each run; both error-and-continue per the weekly_generate per-project fence. Pattern: the FIRST project to need a fresh `ensure_current_week_folder` scaffold creation in a fresh process consistently 404s; subsequent projects in the same run succeed. Same class as PR #51's `find_sheet_by_name_in_folder` SDK staleness — both look like SDK in-process caching missing a just-created object.

**Mitigation shipped (2026-05-22 follow-on PR):** single-shot retry on `SmartsheetNotFoundError` inside the per-project fence (`_process_with_retry` wrapper in `safety_reports/weekly_generate.py`, 500 ms sleep + one retry, bumps `summary.retries_attempted`). When retry exhausts (or any non-404 error fires), the fence writes a `GENERATION_FAILED` placeholder row to `WPR_Pending_Review` so the operator's queue surfaces the failed project instead of leaving a silent gap. The placeholder respects the existing-row contract: approved rows are left untouched, unapproved rows have a `[GENERATION_FAILED: <ErrorClass>]` tag appended to Notes (Draft Body preserved), and missing rows get a fresh placeholder with the manual-rerun command embedded in Draft Body. Op Stds v11 §30 SDK-vs-Live discipline.

**Durable fix still deferred:** SDK→REST swap on the `ensure_current_week_folder` / `get_rows` paths to eliminate the staleness window entirely. Trigger condition: 3+ observed `weekly_generate.transient_404_retry` events in production cycles (meaning the retry IS firing in real runs, not just smoke). The `summary.retries_attempted` counter is the canonical signal — watchdog Check C or a follow-on metric scrape can surface the count without operator log-grep.

**Effort to swap:** ~1-2 hour session (mirror PR #51's pattern; ~6 unit tests around the find-after-create REST flow).

**Revisit when:** retries_attempted >= 3 in any consecutive 4-week window, OR a real Friday cycle surfaces a `GENERATION_FAILED` placeholder (the user-visible signal).

## Intake stream extension for Weather + Labor + Mobilization metadata [OPEN 2026-05-22]

The WPR draft sections Weather Report, Construction Labor Report, Mobilization Date, and Location are currently `[REVIEWER TO FILL]` because the intake.py Daily Reports stream doesn't capture them — operator-side reviewers add the data during approval per Safety Reports Brief v6.1. Phase 1.4+ option: extend `safety_reports/intake.py` to capture weather (via a public weather API or `Summary of Events` extraction) and labor counts (via a new Daily Reports column or field PM submission convention), eliminating those `[REVIEWER TO FILL]` placeholders.

Mobilization Date is project-scoped not week-scoped — better captured as a project-level metadata sheet (a "Projects" master sheet keyed by `project_name`) rather than threaded through every Daily Reports row. Same for Location.

**Effort:** 1-2 sessions (intake-side weather + labor extension, projects-metadata-sheet schema + read-side wire-up).

**Revisit when:** Phase 1.4 security hardening cluster ships and operator feedback drives WPR template v0.2.0 calibration.

## `shared/heartbeat.py` + `shared/runner.py` extraction [OPEN 2026-05-23]

R3 Session 3 (`weekly_send_poll.py`) is the 2nd polling-daemon consumer that triggers the polling-daemon doctrine's 2nd-consumer extraction signal (Op Stds v11 §14). The heartbeat helpers (`_load_heartbeat_row_state`, `_persist_heartbeat_row_state`, `_invalidate_heartbeat_row_state`, `_resolve_heartbeat_row_id`, `_write_heartbeat`, `_write_heartbeat_row`, `_log_heartbeat_failure`) were copied VERBATIM from `safety_reports/intake_poll.py` into `weekly_send_poll.py` rather than extracted, to keep the R3 Session 3 ship focused on the send-capability code.

Both consumers now share the same heartbeat-row-state file at `~/its/state/heartbeat_row_ids.json` (keyed by daemon_name) so the file format is already shape-compatible. Extraction is mechanical: pull the seven helpers into `shared/heartbeat.py`, parameterize on `daemon_name` + `state_path`, replace inline copies with imports.

**Effort:** ~half-day session including +6-10 unit tests for the new shared module + the migration of both `intake_poll` and `weekly_send_poll` to use it.

**Risk of premature extraction:** if a 3rd polling consumer surfaces a different shape need (e.g. multi-row heartbeat per daemon, or a heartbeat surface that's not ITS_Daemon_Health), the API churns. Mitigate by waiting until `weekly_send` stabilizes through 1-2 real Friday cycles, then extract.

**Revisit when:** weekly_send has completed 1-2 real Friday cycles (≥ ~2 weeks of production traffic), OR a 3rd polling daemon is queued for a workstream.

## HTML email rendering for weekly_send [OPEN 2026-05-23]

`weekly_send.py` v0.1.0 sends `Draft Body` as inline text via `content_type="Text"`. Sponsors may prefer HTML formatting (paragraph breaks, bullet lists, the WPR layout's table structure rendered properly). Calibrate with Teala after the first 30 days of real Friday cycles — same 30-day window as the `safety_weekly_generate` prompt v0.1.0 calibration entry.

Implementation: render `Draft Body` (currently plain text with `[REVIEWER TO FILL]` placeholders) into minimal HTML via a small template, pass `content_type="HTML"` to `graph_client.send_mail`. Same recipient flow.

**Effort:** ~half-day session including +2-4 unit tests for the rendering function + a smoke run.

**Revisit when:** Teala provides feedback on the v0.1.0 inline-text format (after first 30 days of real cycles).

## Word-doc / PDF attachment generation for weekly_send [OPEN 2026-05-23]

Legacy WPRs (the Gates Solar 2016-03-12 anchor in `prompts/samples/`) were Word documents. Current `weekly_send` v0.1.0 sends `Draft Body` as inline text — no attachment. Sponsors who archive correspondence as document attachments may explicitly request a formatted attachment.

Phase 1.4+ extension: render `Draft Body` to PDF (via reportlab or similar) or DOCX (via python-docx), attach via the existing `graph_client.send_mail(..., attachments=[...])` signature. Box upload + Smartsheet link-update for the sent PDF could ride alongside.

**Effort:** 1-2 sessions depending on which format(s) sponsors want and whether Box archival ships in the same PR.

**Revisit when:** explicit sponsor feedback requesting formatted attachment.

## Automated mailbox cleanup for weekly_send integration smoke [OPEN 2026-05-23]

`tests/test_weekly_send_integration.py` test seed sends a real email to `seths@evergreenmirror.com` per run. Cleanup currently deletes the `WPR_Pending_Review` row in `finally`, but the email itself sits in the recipient's inbox until manually deleted. Acceptable for first few integration runs (rare; operator-driven) but eventually deserves programmatic cleanup.

Implementation: after assert SENT, use `graph_client.list_inbox` + `graph_client.delete_message` (would need to add `delete_message` to `graph_client.py` — currently not exposed) to remove the ITS-SMOKE-tagged message from the sandbox inbox.

**Effort:** ~hour or two including a new `delete_message` helper in `graph_client.py` + the test wire-up.

**Revisit when:** integration runs accumulate noticeable smoke clutter in the sandbox mailbox (estimate: after ~10-20 runs).

## Doc-conventions lint strict-mode flip after retrofit window closes [OPEN 2026-05-24]

`scripts/lint_doc_conventions.py` ships warn-only. Two follow-on items track the retrofit window's close:

1. **Bulk-retrofit sweep** of grandfathered docs (~36 session logs + a handful of pre-existing audits / references) — add YAML frontmatter to each. Target window: ~60 days (2026-07-24). Lazy retrofit per `docs/operations/doc_conventions.md` is the interim policy; this sweep is the optional bulk-migration option.
2. **Flip lint to `--strict`** in CI after the sweep completes. `.github/workflows/ci.yml` currently invokes the lint without `--strict`; one-line change to add the flag once the sweep lands and all violations clear.

Trigger conditions:
- Auto-trigger #1: 2026-07-24 reached (default sweep target).
- Manual-trigger #1: operator decides to skip the bulk sweep and accept indefinite grandfather state. In that case strict-mode flip is also skipped; the conventions doc's "Retrofit policy" section should be updated to mark the policy as permanent.

**Effort:** ~2 hours for bulk sweep (mostly automatable — frontmatter generation from filename/git-log); ~5 min for the strict-mode flip.

**Revisit when:** 2026-07-24, or sooner if operator opens a doc-retrofit session.

## Nightly auto-index regen wiring [DEFERRED 2026-05-24]

`docs/operations/doc_conventions.md` mentions a "nightly regeneration" path for `scripts/regen_doc_indexes.py` via `scripts/watchdog.py::TRACKED_JOBS`. Not wired in the initial ship: regen runs in CI (`--check` mode) on every PR, which is the load-bearing enforcement. A nightly launchd job would add freshness for un-merged branches sitting on the operator's MacBook, but the CI gate is sufficient for `main`.

**Action when triggered:**
1. Add launchd plist `org.solutionsmith.its.doc-index-regen.plist` (StartCalendarInterval, daily 03:00 local).
2. Have the script write a watchdog marker on successful regen.
3. Append `doc_index_regen` to `scripts/watchdog.py::TRACKED_JOBS` with 36-hour freshness window.

**Effort:** ~30 min.

**Revisit when:** operator notes drift between local doc state and CI's view, OR a third polling daemon ships and the watchdog wiring patterns are being touched anyway.

## Hardcoded BOX_PROJECT_FOLDERS dict requires code change per project [OPEN 2026-05-24]

`shared/defaults.py:73` defines `BOX_PROJECT_FOLDERS: dict[str, str]` — a hardcoded mapping from project name to Box folder ID. Every new project added to Box requires editing this file and redeploying. `shared/defaults.py` is also the documented fallback layer for ITS_Config (per existing convention in the module — `BOX_PROJECT_FOLDERS` references "1111B-derived clones post-cutover" suggesting it gets manually edited at each Box cutover).

**Failure mode:** non-developer operator cannot onboard a new project without CC involvement (code edit + PR + deploy). Risk of typo in folder ID silently routing uploads to the wrong project. Stale entries accumulate as projects close out. Project-onboarding is a routine ops task that should not require a deploy cycle.

**Proposed fix:** migrate to a Smartsheet lookup (suggest a dedicated `ITS_Project_Routing` sheet with columns `Project Name`, `Box Folder ID`, `Active` bool, `Notes`). Code reads at daemon startup, caches in-process, refreshes on interval. Add startup validation that every active row's folder ID resolves via Box API — warn (don't fail) on resolution miss so a single bad row doesn't crash the daemon. Once live, `BOX_PROJECT_FOLDERS` becomes the empty-dict fallback or is removed entirely.

**Effort:** ~half-day session (new sheet schema + `ITS_Project_Routing` migration script + reader in `shared/defaults.py` or new `shared/project_routing.py` + tests + Box resolution validation helper + operator runbook).

**Phase target:** 1.5 — blocks first-customer onboarding cleanliness; every new customer's project set is different.

**Tag:** `config-migration`.

**Revisit when:** Phase 1.5 hardening cluster, or operator hits the "I need to add a project but can't without a code change" friction.

Surfaced: 2026-05-24 hardcoded-values audit brief, §A2.

## Hardcoded BOX_SUBPATH_BY_CATEGORY in safety_reports/intake.py [OPEN 2026-05-24]

`safety_reports/intake.py:172` defines `BOX_SUBPATH_BY_CATEGORY: dict[str, tuple[str, ...] | None]` — hardcoded mapping from inbound email category to Box subfolder path. `VALID_CATEGORIES` (line 195) is derived from this dict's keys. Adding a new safety-reports category requires code change.

**Failure mode:** same shape as `BOX_PROJECT_FOLDERS` (config-migration sibling): operator can't add a category without a PR. Lower change cadence than projects (categories churn slowly — the safety-reports taxonomy is more stable than the project set), but same redeploy-for-ops-task problem.

**Proposed fix:** migrate to either (a) `ITS_Config` rows with key prefix `BOX_SUBPATH_<category>` and tuple values JSON-encoded, or (b) a dedicated `ITS_Category_Routing` sheet alongside the project-routing sheet from the A2 entry. Same caching pattern. Same Box-resolution validation. Coupled enough with A2 that landing both in one PR pair makes sense (a `shared/routing.py` module covering both lookups).

**Effort:** ~2 hours, lower than A2 because category set is smaller and the schema is simpler (no `Active` bool needed if categories are append-only).

**Phase target:** 1.6 — lower priority than A2 because category set is stable. Bundle with A2 only if the routing-module shape benefits from co-design.

**Tag:** `config-migration`.

**Revisit when:** A2 lands (do A3 right after, sharing the routing-module pattern), OR a new safety category needs adding before A2 lands (force the move at that point).

Surfaced: 2026-05-24 hardcoded-values audit brief, §A3.

## Hardcoded default fallbacks for ITS_Config-sourced timing constants [OPEN 2026-05-24]

`safety_reports/weekly_send_poll.py:97-98` defines `DEFAULT_POLLING_ENABLED = True` and `DEFAULT_POLL_INTERVAL = 900` (15 minutes). The authoritative runtime values come from ITS_Config rows `safety_reports.weekly_send.polling_enabled` and `safety_reports.weekly_send.poll_interval_seconds` — the hardcoded constants are fallback defaults when those rows are missing or malformed. Other timing-bearing files (intake_poll, watchdog) follow the same pattern.

This is partially good (already ITS_Config-sourced) and partially fragile: silent fallback to a hardcoded default when an operator typos an ITS_Config row means the daemon "works" but on the wrong schedule, with no operator-visible signal that the override didn't take.

**Failure mode:** operator edits ITS_Config to change poll interval from 900 to 1800. Typos the key name. Daemon silently uses the hardcoded 900 default. Operator believes the new value is in effect; isn't. Costs and responsiveness are both off the operator's mental model.

**Proposed fix (two layers):**

1. **Startup log line** in every daemon: log the *resolved* values at startup (`[startup] poll_interval_seconds = 900 (source: default fallback)` vs `(source: ITS_Config)`). Cheap; makes the silent-fallback observable in launchd stdout/stderr logs.
2. **Optional but stronger:** convert silent fallback to WARN-loud fallback when the ITS_Config row is unexpectedly missing for keys the daemon documented as "should be configured." A dedicated registry of "expected ITS_Config keys" per daemon, checked at startup, surfaced via Sentry WARN if missing. Same shape as the validation-at-startup proposal in C1.

**Effort:** ~1 hour for layer 1 (startup-log only) across the 2-3 polling daemons. Layer 2 folds into C1's startup-validation module.

**Phase target:** 1.6 alongside C1 (config validation cluster).

**Tag:** `config-migration`.

**Revisit when:** C1 startup-validation work begins, OR an operator hits the silent-fallback-after-typo failure mode in real ops.

Surfaced: 2026-05-24 hardcoded-values audit brief, §A5. Note: the brief's framing assumed full hardcoding of timing constants; actual state is ITS_Config-sourced with hardcoded defaults as fallback. The fragility is the silent fallback, not the constants themselves.

## Severity-tiered + multi-recipient alert routing [OPEN 2026-05-24]

Current state: `shared/resend_client.send_alert()` sends to a single recipient resolved from `system.operator_email` in ITS_Config at runtime (per `shared/resend_client.py:164`). No multi-recipient distribution. No severity gating — every CRITICAL via `_alert_critical` fires the same Resend leg to the same single recipient regardless of severity.

Adequate for the solo-operator stage. Becomes a gap when:

- Team composition expands (on-call rotation, multiple operators in different timezones).
- Severity stratification matters (CRITICAL to phone-via-Resend, WARN to a digest sheet only).
- Customer 2+ onboarding lands and per-customer recipient lists need separation.

**Proposed fix:** new `ITS_Alert_Routing` sheet with columns `Email` (TEXT_NUMBER, primary), `Severity Threshold` (PICKLIST: CRITICAL/WARN/INFO), `Workstream Filter` (TEXT_NUMBER, JSON list — `["*"]` for all), `Active` (bool), `Notes`. `send_alert()` reads the sheet, filters rows by severity ≥ threshold AND workstream match, fans out to each matching recipient. Email validation at sheet load (basic `^[^@]+@[^@]+\.[^@]+$`). Keep `system.operator_email` as the single-recipient fallback when the sheet is empty or unreachable.

**Effort:** ~half-day session including schema migration script (mirror the trusted-contacts pattern) + `shared/alert_routing.py` reader + `send_alert()` rewiring + tests.

**Phase target:** 2 (post-Customer-1 cutover). Single-recipient is sufficient for the solo + Customer-0 stage and shouldn't preempt Phase 1.4/1.5 critical-path work.

**Tag:** `config-migration`.

**Revisit when:** team expansion is concrete, OR Customer 2 onboarding begins.

Surfaced: 2026-05-24 hardcoded-values audit brief, §A4. Note: the brief's premise (hardcoded recipients in `shared/alert.py`) was inaccurate — that file doesn't exist; recipient is already ITS_Config-sourced. This entry reframes the spirit of the concern: future multi-recipient + severity-tiered routing, not present-day hardcoding.

## Allowlist drift detection — typo'd trusted-contacts entry silently quarantines [OPEN 2026-05-24]

`ITS_Trusted_Contacts` entries with a typo in the Email field silently route legitimate senders to quarantine. Operator has no signal that the list itself is wrong vs. the sender being legitimately untrusted. Same shape applies to the legacy `safety_reports.intake.allowed_senders` JSON list still alive as the dead-fallback path (per the existing "Fallback path removal after ITS_Config cutover [OPEN 2026-05-23]" entry — that fallback should be removed soon, narrowing this surface).

**Failure mode:** field PM emails a JHA from `joe.smith@evergreenrenewables.com`. Trusted-contacts row was seeded with `joe.smtih@evergreenrenewables.com` (transposed). Message routes to ITS_Quarantine instead of intake. Operator assumes everything is fine until a missed safety report surfaces downstream.

**Proposed fix (two-layer):**

1. **Validation at sheet read:** `shared/trusted_contacts._load_contacts()` adds basic email regex validation when materializing rows from `ITS_Trusted_Contacts`. Rows with malformed emails get logged to `ITS_Errors` with `error_code='trusted_contacts_row_malformed'` and skipped. Cheap; surfaces typos in the email format itself.
2. **Reconciliation sweep:** weekly job that lists distinct senders in `ITS_Quarantine` over the last 7 days. For each, compute Levenshtein distance against every active `ITS_Trusted_Contacts` Email. Distance ≤ 2 surfaces as a `near_miss_quarantine` row in `ITS_Review_Queue` with the two emails side-by-side. Low-urgency review-queue item, not an alert. Catches typos that pass basic regex (`joe.smtih@...` is a valid email format).

**Effort:** ~3 hours for layer 1 (regex validation + 5-6 unit tests). ~half-day for layer 2 (sheet read + Levenshtein + review-queue integration + tests + watchdog cadence wiring).

**Phase target:** 1.6 (lands cleanly post-Customer-0-cutover; layer 1 can ship immediately once `_load_contacts` is being touched anyway).

**Revisit when:** layer 1 — next touch of `shared/trusted_contacts.py`. Layer 2 — Phase 1.6 hardening, or operator first encounters a near-miss-typo incident.

Surfaced: 2026-05-24 hardcoded-values audit brief, §B1.

## Box folder delete-and-recreate breaks folder ID resolution [OPEN 2026-05-24]

Box folder IDs are stable across renames but NOT across delete-and-recreate. If someone deletes a project folder in Box and recreates it with the same name, uploads to the stale ID will land in the wrong place (or fail, depending on SDK behavior against trashed folders — needs verification: the boxsdk 3.x trashed-folder upload path returns success or error?).

**Failure mode (silent variant — needs SDK verification):** if Box returns 2xx on upload-to-trashed-folder, ITS-generated files land in trash invisibly. Operator sees no upload error; thinks files are filed correctly. Real-world impact: documents lost until someone notices missing files in the active folder.

**Failure mode (loud variant):** Box returns error; intake daemon surfaces via triple-fire CRITICAL alert. Operator gets the alert but the failure cause ("404 folder not found" against a folder that "exists" in Box UI under a new ID) is opaque without tribal knowledge of the delete-recreate gotcha.

**Proposed fix (depends on A2 landing first):**

1. **Startup validation** in the new `shared/project_routing.py` (or whatever lands from A2): every active row's `Box Folder ID` must resolve via Box API to a non-trashed folder. Validation runs at daemon startup AND in a weekly reconciliation watchdog check. Log WARN + skip routing to invalid folders rather than crash.
2. **Operator runbook entry**: "If a Box folder is recreated, update the routing sheet with the new ID. The old ID will WARN in watchdog within 24 hours regardless."
3. **SDK trashed-folder behavior verification:** one-off smoke test against a deliberately-trashed sandbox folder to confirm whether boxsdk 3.x upload returns error or silently lands in trash. Document the answer in `docs/references/box_sdk_gotchas.md` (or similar).

**Effort:** ~2 hours for validation logic + watchdog wiring (mostly straightforward once A2's routing sheet exists). ~30 min for the SDK behavior smoke test.

**Phase target:** Phase 2 — depends on A2 landing first, since this is the validation layer for that routing config.

**Revisit when:** A2 lands; bundle this immediately after as the second PR in the config-migration cluster.

Surfaced: 2026-05-24 hardcoded-values audit brief, §B2.

## Future PDF/JHA field extraction needs found-flag pattern [OPEN 2026-05-24]

Phase 1.5 work introduces PDF-form-field extraction (and possibly free-text regex extraction) for JHA documents inbound from field PMs. Different field PMs format dates, names, and other fields inconsistently — one types `5/24/26`, another types `2026-05-24`, another writes `May 24`. Naive regex or PDF-form-field-by-name lookup silently extracts blank when the format doesn't match.

(Note: this is NOT an extension of `box_migration/parse_job_v3.py`, despite the audit brief's framing. `parse_job_v3` parses Box folder *names* against the 4 active project-folder taxonomies — see `tests/test_parse_*.py` for its scope. JHA field extraction is a distinct future workstream that hasn't been built yet.)

**Failure mode:** blank field in Smartsheet row. Downstream consumers (`safety_reports.weekly_generate`, reports, rollups) silently skip the row or compute wrong totals. No alert fires because "blank field" is not an error from the parser's perspective — it just didn't match. Worst case: a weekly safety report omits a critical incident because the date field was blank.

**Proposed fix:**

1. **Each extracted field returns a `(value, found: bool, confidence: float)` triple, not a bare value.** Existing anomaly_logger + review_queue + confidence-threshold convention (Op Stds §35) already covers the routing — if a *required* field comes back `found=False`, the row routes to `ITS_Review_Queue` with a flag instead of silently writing blank.
2. **Build a corpus of real JHA samples** at the Phase 1.5 PDF-extraction workstream's design phase. Run extraction across the corpus, measure miss rate per field. Iterate format detection (multi-pattern regex, fuzzy date parser like `dateutil.parser`, etc.) until miss rate is acceptable for required fields.
3. **Customer-facing JHA template** — produce a fillable form template that constrains the format at submission time, so future fields are pre-canonicalized. Reduces extraction burden for everyone.

**Effort:** large — this is part of the Phase 1.5 PDF-extraction workstream design itself, not a separable cleanup. Multi-session work. The found-flag pattern alone is small (a few hours) but the corpus + iteration + customer-template + downstream-consumer wiring all add up to ~2-3 sessions.

**Phase target:** 1.5 — directly part of PDF extraction workstream design. Solve found-flag + corpus + template together; don't ship PDF extraction without them.

**Revisit when:** Phase 1.5 PDF-extraction workstream brief gets drafted (the regex-side concerns belong in that brief).

Surfaced: 2026-05-24 hardcoded-values audit brief, §B3. Cross-ref Op Stds v11 §35 (confidence-scored extraction → review queue routing pattern).

## No retry / backoff / circuit-breaker layer across Smartsheet call sites [OPEN 2026-05-24]

Smartsheet API calls across `shared/smartsheet_client.py` and its consumers (intake_poll, weekly_send_poll, weekly_generate, watchdog, picklist_sync) have point-by-point exception catches (e.g., `intake_poll.py:406` catches `SmartsheetError`, `intake_poll.py:439` catches `SmartsheetNotFoundError`, weekly_generate's `_process_with_retry` does a one-shot retry on `SmartsheetNotFoundError`) but no unified retry-with-backoff decorator and no circuit-breaker pattern. A Smartsheet incident (5xx, timeout, rate-limit) means each call site degrades independently with no aggregate signal to the daemon ("Smartsheet is currently degraded — back off the whole loop").

**Failure mode:** Smartsheet returns 5xx on a string of consecutive API calls during an incident. Each call-site catch logs to ITS_Errors and either soft-fails (`SmartsheetError as exc` returns rather than raises in many handlers) or — for un-caught paths — bubbles to the daemon-wide catch. The daemon doesn't crash today (current catches are broad enough), but ITS_Errors fills with N rows per cycle and the alert-dedupe state file grows. Worse: the daemon keeps hammering the degraded service, contributing to the incident's tail.

**Proposed fix (layered):**

1. **Retry-with-exponential-backoff decorator** in `shared/smartsheet_client.py`: wraps every public API method. 3 retries with 1s / 4s / 16s sleep, only on retryable errors (5xx, timeouts, 429 rate-limit). Existing exception classes (`SmartsheetError`, etc.) get a new `is_retryable: bool` property. The 2026-05-22 weekly_generate `_process_with_retry` becomes the prior-art that this decorator replaces.
2. **Circuit-breaker pattern**: simple counter in `shared/smartsheet_client.py` state. N consecutive failures across the module pause new calls for a longer interval (5-10 min). Resume on a probe-call success. Coexists with retry — retry handles transient, circuit-breaker handles sustained.
3. **Dedicated `SS_API_UNAVAILABLE` error code** in `_alert_critical`. The existing alert-dedupe (per `feedback_pr_scoping_narrow.md` and the alert-dedupe entries) handles spam-suppression — verify the dedupe key works for this case.

**Effort:** ~half-day for the retry decorator + ~2 hours for circuit-breaker + ~1 hour to verify dedupe interaction. Integration tests against sandbox required (Op Stds v11 §30) — the SDK-vs-Live class of bug is the main risk here.

**Phase target:** 1.5 — reliability gate for ship-and-leave threshold. ITS goes operator-untouched for stretches post-handover; a Smartsheet incident during one of those stretches shouldn't degrade ITS unboundedly.

**Revisit when:** Phase 1.5 hardening cluster, OR a real Smartsheet incident exercises the call sites and surfaces concrete failure shapes worth designing for.

Surfaced: 2026-05-24 hardcoded-values audit brief, §B4. Cross-ref Op Stds v11 §30 (SDK-vs-Live integration discipline — retry decorator is a candidate for parallel integration test coverage).

## Configuration validation at daemon startup [OPEN 2026-05-24]

Once items A2 / A3 / A5 (and the existing trusted-contacts work) migrate config into Smartsheet, daemons fetch config at startup with no formal validation step. A malformed row, missing key, or unresolvable folder ID can let the daemon enter its main loop with broken config — it'll fail per-cycle at unpredictable points instead of failing loud at startup.

**Failure mode:** operator typos an ITS_Config row. Daemon starts. First poll cycle runs. Per-cell-write fails in some downstream call. ITS_Errors fills with cryptic errors. Operator's mental model: "ITS broke, why is the watchdog quiet?" — because the watchdog can't distinguish "broken config" from "broken external API."

**Proposed fix:** new `shared/config_validation.py` with a single `validate_all()` entry point called from every daemon's `main()` before the loop starts. Per-daemon manifest of required keys + validators:

- All required ITS_Config keys present (per a per-daemon registry — `intake_poll.REQUIRED_CONFIG`, etc.).
- All email addresses pass `^[^@]+@[^@]+\.[^@]+$`.
- All Box folder IDs resolve via Box API to non-trashed folders (depends on A2 landing).
- All referenced Smartsheet sheet IDs exist (cheap `get_sheet_summary`-style probe).

On failure: log full report to Sentry + ITS_Errors, exit non-zero. **Do not enter the loop with broken config — fail loud.**

**Effort:** ~half-day session including the validation module + per-daemon registries + tests + integration smoke + runbook update ("if a daemon fails to start, check the Sentry / ITS_Errors entry for the validation report").

**Phase target:** 1.6 — lands after A2/A3/A5 migrate config into Smartsheet. Sequence: config-migration cluster → validation layer.

**Tag:** `config-migration` (the consumer side).

**Revisit when:** A2 lands, AND a third polling daemon is queued, OR operator hits the silent-fallback-into-bad-config failure mode in real ops.

Surfaced: 2026-05-24 hardcoded-values audit brief, §C1.

## Config-change audit trail [OPEN 2026-05-24]

Once configuration lives in Smartsheet (ITS_Config rows + future `ITS_Trusted_Contacts` / `ITS_Project_Routing` / `ITS_Alert_Routing` sheets), changes happen without a git commit. For security-relevant config — `ITS_Trusted_Contacts` especially — this is an audit gap. Smartsheet has cell-history natively, but that history is bounded to the Smartsheet tenant; if a customer ever needs an external audit copy independent of Smartsheet (compliance requirement, post-incident forensics, vendor risk), there's no out-of-band record.

**Failure mode (low-frequency):** post-incident, operator wants to know "who added `acme@external-domain.com` to trusted contacts on 2026-XX-XX." Smartsheet cell history covers it. But if the question is "show me the entire trusted-contacts state on 2026-XX-XX" — Smartsheet's history surface is per-cell, not point-in-time-snapshot; reconstructing requires manual scrubbing.

**Proposed fix (layered):**

1. **Runbook entry:** document Smartsheet's built-in cell-history view as the canonical audit trail. Train operator on the per-cell-history surface. Low-cost, covers the common case.
2. **Weekly diff-export job** for high-stakes sheets (`ITS_Trusted_Contacts`, future `ITS_Alert_Routing`): snapshot to a versioned file in Box on a weekly cadence. Filename `<sheet_name>_<YYYY-MM-DD>.json`. Gives a point-in-time snapshot independent of Smartsheet. Watchdog Check writes a marker; missing snapshots WARN.
3. **Higher-stakes-yet option (deferred):** route trusted-contacts edits through a PR-style approval flow in a separate sheet (`ITS_Trusted_Contacts_Proposed` → operator-approval column → applied to canonical sheet). Likely overkill for solo-operator stage.

**Effort:** ~1 hour for layer 1 (runbook). ~half-day for layer 2 (snapshot script + Box upload + watchdog wiring + tests). Layer 3 is a separate workstream if it ever lands.

**Phase target:** 2 (post-Customer-1 cutover, when audit-as-deliverable becomes a customer-facing concern). Not a launch blocker for Customer 0.

**Revisit when:** first customer raises compliance / audit requirements explicitly, OR a security review session formally surfaces the gap.

Surfaced: 2026-05-24 hardcoded-values audit brief, §C2.
