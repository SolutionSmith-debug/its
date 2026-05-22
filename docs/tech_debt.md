# ITS — Tech Debt

Items deliberately deferred. Each carries the rationale for deferral and the trigger for revisiting. The repo-side companion to Master Checklist §6 (planning project) — this file holds execution-layer tech debt; the Master Checklist holds owner-decision tech debt.

When to add an entry: a session deliberately chooses preservation-over-refactor (per Op Stds v9 §14), discovers an external-API constraint that forced a workaround, or defers a non-trivial cleanup that's larger than the current session can absorb. When to mark CLOSED: the underlying item is resolved in a commit; preserve the entry with resolution detail rather than deleting (history is cheap, context is expensive).

## parse_job_v3.py:656 — `existing_keys` dead code [CLOSED 2026-05-17]

Resolved in commit **`1fd6751`**. The unfinished de-dup attempt was removed and F841 came off the `box_migration/*` per-file-ignores. Originating commit (which suppressed it) was `8dfc6e8`; ground was tracked in `docs/session_logs/2026-05-17_ruff_and_doc_refresh.md`.

The fix was a deliberate departure from Op Stds v7 §14 (preservation-over-refactor) because the F841 was real dead code rather than a stylistic false positive, and the cleanup was five lines with zero behavior change. The preservation rule remains in effect for the rest of `box_migration/*`.

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

Resolved by adopting **Direction (A)** from `docs/person_tag_audit_2026-05-19.md`: the third alternation (`-\s*[A-Z][a-z]+\s*$`, "trailing-capitalized-word after dash") was removed from `PERSON_TAG_IN_SUBJECT` in `box_migration/parse_job_v3.py`. The refined regex keeps the two alternations that the audit confirmed as high-precision:

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

Resolution: see commit on the `feature/person-tag-regex-refinement-redo` branch (squash-merged), and `docs/session_logs/2026-05-20_person_tag_regex_refinement_redo.md`. Audit context preserved at `docs/person_tag_audit_2026-05-19.md` (not modified by this PR).

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

Resolved by widening the `body` parameter annotation on `api()` from `dict | None` to `dict | list | None`. Single-character-class edit on the signature line; all existing call sites continue to type-check (the `add_rows()` caller that passed `list[dict]` now matches). Real-bug carve-out under Op Stds v8 §14.

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

PR α suppresses only the Resend leg. Sentry events and Smartsheet ITS_Errors rows always write (per Op Stds v9 §27 — dedupe applies only to push, never to records). Today this is the right choice: Sentry's own alert rules and Smartsheet's sheet-level notifications are NOT configured.

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

**Impact on `shared/picklist_sync.py`:** the `Restrict to dropdown values only` toggle must be manually set on each downstream PICKLIST column at deployment time. Without it, the sync still works (options stay in sync) but the strict-mode validation that prevents users from typing vendor-name drift is absent. Documented in `docs/picklist_sync.md` activation checklist step 5.

**Impact on form-and-clone cascade:** every form requires manual UI setup. The cascade flow assumes operator builds forms in the UI as the final cutover step.

**Resolves if:** Smartsheet exposes any of these surfaces via API. Worth re-checking annually — Smartsheet's API surface expands slowly. No action item today; this entry exists so future operators / new customer forks know the manual-deployment-step list without rediscovering it.

**Urgency:** none. Operationally accepted; manual deployment steps documented per-customer.

Surfaced: Phase-0 architecture review 2026-05; referenced from `docs/picklist_sync.md` activation checklist.

## safety_reports week-folder create-find race condition [OPEN 2026-05-21]

`safety_reports/week_folder.ensure_current_week_folder` performs a find-or-create on the per-week folder under each project's Field Reports subtree. Two concurrent callers (e.g., a same-week intake.py and a Friday weekly_generate.py firing within the same minute) could both pass the initial `find_folder_by_name_in_folder` step and both create the folder; Smartsheet does not enforce folder-name uniqueness, so both creates succeed.

The helper detects the duplicate on a post-create find: if the post-create lookup returns a different folder ID than the just-created one, it logs a WARN to ITS_Errors with `error_code="week_folder_race_duplicate"` and proceeds with the first match (the survivor). The orphan folder ID appears in the WARN message for operator triage.

**Workaround:** operator manually deletes orphan folders via short-lived sandbox token + curl per Op Stds v10 §25 MCP-gap REST fallback (`curl -X DELETE https://api.smartsheet.com/2.0/folders/<orphan_id> -H "Authorization: Bearer <token>"`). No automatic cleanup — race is rare at single-machine cadence, and the safer move is operator visibility (WARN → review) over an automated delete that could race against legitimate concurrent writes.

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

## Picklist-hardening pre-Customer-1 [OPEN 2026-05-22]

All bounded-enum Smartsheet columns currently TEXT_NUMBER should convert to PICKLIST or CHECKBOX before Customer 1 handover. Targets:
- ITS_Config: system.state {ACTIVE/PAUSED/MAINTENANCE}, all *.polling_enabled flags, any setting with an enumerated domain.
- ITS_Errors: Severity {INFO/WARN/ERROR/CRITICAL}, Workstream, status fields.
- ITS_Review_Queue: Status enum, Workstream, urgency tiers, reviewer-chain selectors.
- ITS_Quarantine: quarantine reason enum, disposition {release/delete/escalate}, Workstream.
- Per-project sheets (Daily Reports, Weekly Rollups): status, category fields.

Codified in Op Stds v11 §35 (standing rule going forward; retrofit audit triggered before Customer 1 handover). kill_switch.py fail-open logic stays as belt-and-suspenders.

**Effort:** ~30 min operator UI + ~1 hour audit pass.

**Revisit when:** Phase 1.4 security hardening session lands.

## ITS_Trusted_Contacts sheet replaces ITS_Config JSON allowlists [OPEN 2026-05-22]

Build ITS_Trusted_Contacts sheet in System workspace per Op Stds v11 §33 schema:
- Email (PRIMARY, exact-match), Display Name, Role (PICKLIST), Project Scope (multi-PICKLIST), Workstream Scope (multi-PICKLIST), Status (ACTIVE/DISABLED/PENDING_VERIFICATION), Added By, Added Date, Last Verified, Notes.

Refactor `safety_reports/intake.py` Stage 2 (Sender allowlist gate) to query trusted-contacts sheet with scope enforcement. Add header-forgery detection via `shared/graph_client.py` extensions: parse internetMessageHeaders for spf/dkim/dmarc results; compare Return-Path against From: domain; compare Received chain. Disposition: header-fail → ITS_Quarantine 'header_forgery_suspected'; soft-fail on trusted sender → ITS_Review_Queue; clean → proceed.

Retire `safety_reports.intake.allowed_senders` ITS_Config row at cutover. Per FM v8 Invariant 2 Layer 1.

**Effort:** ~half-day session (sheet build + intake refactor + header-forgery wiring + tests).

**Revisit when:** Phase 1.4 security hardening session lands; required before Phase 1.5 cutover.

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

## Smartsheet transient 404 on first-project sheet/folder create [OPEN 2026-05-22 — observed twice]

Two `weekly_generate` smoke runs on 2026-05-22 each surfaced exactly one transient 404 during per-project iteration:

- Smoke #1 (`--week-start 2030-01-07`): `SmartsheetNotFoundError('HTTP 404 (code 1006): Not Found')` on Bradley 2. Folder DID get created (cleanup confirmed it existed).
- Smoke #2 (`--week-start 2026-02-16`): same error on Rockford.

Different project each run; both error-and-continue per the weekly_generate per-project fence. Pattern: the FIRST project to need a fresh `ensure_current_week_folder` scaffold creation in a fresh process consistently 404s; subsequent projects in the same run succeed. Looks similar to the known `find_sheet_by_name_in_folder` SDK staleness pattern that PR #51 fixed via REST swap.

**Action:** if reproducible on a third smoke run, port the same SDK→REST swap pattern to whichever `safety_reports/week_folder.py` call is racing (likely the find-after-create on the daily/rollup template clone).

**Effort:** ~1 hour session if pattern reproduces; non-blocking otherwise (per-project fence absorbs it).

**Revisit when:** next weekly_generate smoke or live cycle surfaces a third occurrence.

## Intake stream extension for Weather + Labor + Mobilization metadata [OPEN 2026-05-22]

The WPR draft sections Weather Report, Construction Labor Report, Mobilization Date, and Location are currently `[REVIEWER TO FILL]` because the intake.py Daily Reports stream doesn't capture them — operator-side reviewers add the data during approval per Safety Reports Brief v6.1. Phase 1.4+ option: extend `safety_reports/intake.py` to capture weather (via a public weather API or `Summary of Events` extraction) and labor counts (via a new Daily Reports column or field PM submission convention), eliminating those `[REVIEWER TO FILL]` placeholders.

Mobilization Date is project-scoped not week-scoped — better captured as a project-level metadata sheet (a "Projects" master sheet keyed by `project_name`) rather than threaded through every Daily Reports row. Same for Location.

**Effort:** 1-2 sessions (intake-side weather + labor extension, projects-metadata-sheet schema + read-side wire-up).

**Revisit when:** Phase 1.4 security hardening cluster ships and operator feedback drives WPR template v0.2.0 calibration.
