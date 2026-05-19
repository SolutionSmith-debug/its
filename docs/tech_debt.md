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

## parse_job_v3: person_tag_in_subject chaos over-match [OPEN]

Surfaced 2026-05-18 in the box_migration reconcile. See `docs/session_logs/2026-05-18_box_migration_reconcile.md` "Chaos detection" section for the raw count.

**Pattern (existing regex):** `PERSON_TAG_IN_SUBJECT` in `box_migration/parse_job_v3.py`:

```
r'(\bfor\s+[A-Z]{3,}\b|'                                  # "for ZACK"
r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b|'       # "Teala Organize folder"
r'-\s*[A-Z][a-z]+\s*$)'                                    # "Budget- Jason"
```

**Why existing parser misses it:** It doesn't miss — it over-matches. The third alternation (`-\s*[A-Z][a-z]+\s*$`) flags any `<something>-<Capitalized Word>` ending, which catches legitimate dash-customer-paren naming conventions where the trailing capitalized word is a customer label, not a person tag. Example false positive shape: `14130.1 Dooley (Mortenson) Field` — `Mortenson` here is the customer (Invenergy operating company), not a person.

**Concentration / volume:** **138 unique names flagged across the 10-portfolio reconcile.** Highest count of any chaos pattern by 4x (next is `pre_canonical_zero` at 35). Concentration not yet measured per-portfolio.

**Suggested entry point:** no new entry point. Refinement happens in-place on the existing `PERSON_TAG_IN_SUBJECT` regex in v3 — narrow the third alternation to require a stronger person-name signal than "trailing capitalized word." Candidates: known-first-name allowlist, two-word person form (`First Last`), or contextual position requirement (only flag when not preceded by a customer-name pattern). Decision belongs in the follow-up, after the corpus inspection step below.

**Test snippets:** intentionally not provided yet. The work starts with corpus inspection, not a code change. Adding test snippets now would prescribe the fix shape before we know what shape is right.

**Audit complete 2026-05-19 — see `docs/person_tag_audit_2026-05-19.md`.** 20-sample categorization across all 10 portfolios produced FP rate of **60–70%** (depending on how ambiguous cases are counted). All confirmed FPs hit the third alternation; the first two alternations correctly catch real TPs. Audit recommends **Direction (A): remove the third alternation entirely** — TP loss is low (2–4 catches across the entire corpus), FP cost is high (138 occurrences flagged, ~95% noise).

**Pending operator decision** between three directions:
- (A) Remove third alternation — recommended.
- (B) Allowlist-based refinement — more powerful, higher maintenance.
- (C) Lower severity to INFO — treats symptom not cause.

A follow-up PR implements the chosen direction and closes this entry. Tests to add are spelled out in the audit doc (regression coverage for alternations 1+2 + explicit negative cases for the 12 confirmed FPs).

**Status:** scheduled for a focused follow-up PR; no date promised; revisit before any workstream depends on `person_tag_in_subject` as a high-signal hygiene indicator. Until then, treat the flag as noisy and don't surface it to operators as actionable. Pairs naturally with a broader "chaos pattern false-positive audit" if other patterns turn out to over-match too.

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

## Mail.app rule silent disable on macOS updates [OPEN]

macOS updates have a known pattern of silently disabling Mail.app rules without warning. Affects any workstream whose intake depends on Mail.app rules routing messages to the Claude Code script.

Workaround / mitigation: Watchdog (Op Stds v9 §2) must include an inbound-mail-processed-in-24h check. If watchdog observes no recent intake activity from a given workstream, surface as WARN to operator. Originally captured in Foundation Scaffold v4 "Outstanding Gotchas"; carried forward through v5; re-surfaced via Cascade Audit Errata 2026-05-19.

Resolves when: shared/watchdog.py implements inbound-mail-activity check across all intake-bearing workstreams (Excellence Roadmap v2.1 Track 1 R2).

## PowerShell macOS Gatekeeper deprecation 2026-09-01 [OPEN]

The powershell@preview cask path used for EXO ServicePrincipal management (Connect-ExchangeOnline; New-ServicePrincipal) is scheduled for macOS Gatekeeper deprecation on 2026-09-01. Without intervention, post-deprecation runs will fail Gatekeeper signature verification on the cutover MacBook.

Plan B: Azure Cloud Shell. Same Connect-ExchangeOnline + New-ServicePrincipal commands run in a browser shell instead of local PowerShell. No code change required; runbook change only.

Cutover impact: Handover Plan v6 Step 4 verification currently assumes local PowerShell. If Phase 1.5 cutover lands after 2026-09-01, runbook needs the Azure Cloud Shell variant.

Resolves when: 2026-08-15 calendar check confirms status (still scheduled / postponed / cask alternative emerged). Runbook updated based on findings.
