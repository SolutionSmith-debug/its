# ITS — Tech Debt

Items deliberately deferred. Each carries the rationale for deferral and the trigger for revisiting. The repo-side companion to Master Checklist §6 (planning project) — this file holds execution-layer tech debt; the Master Checklist holds owner-decision tech debt.

When to add an entry: a session deliberately chooses preservation-over-refactor (per Op Stds v7 §14), discovers an external-API constraint that forced a workaround, or defers a non-trivial cleanup that's larger than the current session can absorb. When to mark CLOSED: the underlying item is resolved in a commit; preserve the entry with resolution detail rather than deleting (history is cheap, context is expensive).

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

## parse_job_v3: V/S vendor-sub enumeration unclaimed [OPEN]

Surfaced 2026-05-18 during the box_migration reconcile sanity check after `parse_subsubject` landed. See `docs/session_logs/2026-05-18_box_migration_reconcile.md` "Sanity check findings" for context.

**Pattern:** `^(V|S)\d{1,2}\.\s+.+`. `V` = Vendor, `S` = Sub. Two-digit enumeration is the unmatched case; the existing `SUBJOB_LETTER_UC` (`[A-Z]\d?\.`) caps at one digit, so `V1.` matches but `V12.` falls through.

**Examples observed:** `V12. EPEC`, `V17. CAB`, `V22. Chint`, `V31. Cable Markers`, `S10. Well Demo`, `S11. Erosion Control Consulting INC`, `S12. Helm`, `S13. Peerless`.

**Concentration / volume:** ~30 unique names, 2–3 portfolios (Forefront-heavy by appearance based on the equipment-vendor name mix). Not universal across the 10.

**Suggested entry point:** new `parse_vendor_sub(raw) -> Optional[VendorSubParse]` in `box_migration/parse_job_v3.py`. Fields: `raw`, `kind` ('vendor' | 'sub'), `index` (str), `name` (str). Mirror the shape of `parse_subsubject`. Insert in the reconcile harness's claim chain between `subsubject` and `canonical_non_job`.

**Test corpus snippets** (drop directly into a new `tests/test_parse_vendor_sub.py`):

```python
# Positive
("V12. EPEC",                                 "vendor", "12", "EPEC"),
("V31. Cable Markers",                        "vendor", "31", "Cable Markers"),
("S11. Erosion Control Consulting INC",       "sub",    "11", "Erosion Control Consulting INC"),
("S12. Helm",                                 "sub",    "12", "Helm"),
# Negative — must not steal from SUBJOB_LETTER_UC's domain
("V1. Single Digit",                          None),    # SUBJOB_LETTER_UC owns this
("A1. Kiwi",                                  None),    # not V or S
("V100. Three Digit",                         None),    # cap at 2 digits
```

**Expected coverage delta:** small — single-digit name count across the full corpus. Roughly 30 unique × 2–3 occurrences each = ~60–90 unclaimed-occurrences moved into a new `vendor_sub` claim. Trivially measurable by re-running `box_migration/reconcile_box_listings.py` before and after.

**Status:** scheduled for a focused follow-up PR; no date promised; revisit when other `box_migration/` work is in the same area.

## parse_job_v3: ISO date prefix (YYYY-MM-DD) unclaimed [OPEN]

Surfaced 2026-05-18 in the same sanity check.

**Pattern:** `^\d{4}-\d{2}-\d{2}\s+.+`. ISO 8601 date prefix followed by a descriptive name.

**Examples observed:** `2024-12-04 Brimfield 1 IFC CAD`, `2024-12-13 Brimfield 1 IFC CAD - V2`, `2025-09-15 BBCHS PBASE`, `2024-08-13 - Bonacci Solar - Base Map - Standard`, `2025-08-26 Roxbury IFC CAD Files`.

**Concentration / volume:** ~13 unique names, low volume, consistent shape. Likely concentrated in CAD-versioning workflows (most observed names end in `IFC CAD` or `CAD Files`).

**Why existing parser misses it:** `parse_date_prefix` handles only `R. M.D.YY <topic>` and `S. M.D.YY <topic>` (Received/Sent-tagged American-format dates). ISO has no direction prefix, uses dashes instead of dots, and uses 4-digit years.

**Suggested entry point:** extend `parse_date_prefix` **in-place** rather than creating a new function. Add an `ISO_DATE_PREFIX` regex; on match, return a `DatePrefixParse` with a new `direction='ISO'` discriminator (or refactor the field to `Optional[str]` and leave it None for ISO matches — designer's choice). Do not create a new function — same domain.

**Test corpus snippets** (drop into existing `tests/` location, or `tests/test_parse_date_prefix.py` if a dedicated file becomes warranted):

```python
# Positive
("2024-12-04 Brimfield 1 IFC CAD",       "ISO", "2024-12-04", "Brimfield 1 IFC CAD"),
("2025-09-15 BBCHS PBASE",                "ISO", "2025-09-15", "BBCHS PBASE"),
# Existing R./S. forms must continue to match (regression check)
("R. 5.6.25 Permit response",             "R",   "5.6.25",     "Permit response"),
("S. 11.22.24 to Luminace",               "S",   "11.22.24",   "to Luminace"),
# Negative
("2025 Some Project",                     None),  # no MM-DD
("2024-12 Partial Date",                  None),  # no DD
("2024-12-04Brimfield",                   None),  # no space separator
```

**Expected coverage delta:** very small — ~13 unique × 1 occurrence each = ~13 unclaimed-occurrences moved. The ISO pattern is rare enough that the value is in correctness of the parser surface, not volume of reclassified names.

**Status:** scheduled for a focused follow-up PR; no date promised; revisit when other `box_migration/` work is in the same area. Could naturally bundle with the V/S vendor-sub follow-up since both are parser-extension work of similar scope.

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

**Suggested first step (before any regex change):** spot-check 20 flagged names from the live reconcile output. For each, decide:
- True positive — actual person name encoded as a folder tag.
- False positive — capitalized trailing word that is a customer name, vendor name, location name, or other non-person reference.

Categorize the 20. If false-positive rate is >50%, the third alternation needs to be narrowed or removed. If it's <20%, the pattern is mostly working and only edge cases need attention. Reconcile output to mine: `docs/reports/2026-05-18_reconcile_full.md`, search for `person_tag_in_subject` in the per-portfolio chaos sections.

**Expected coverage delta:** unknown until the corpus inspection runs. Could be substantial (138 names is a lot) or modest (some portion is genuinely person-tagged content), depending on the false-positive ratio.

**Status:** scheduled for a focused follow-up PR; no date promised; revisit before any workstream depends on `person_tag_in_subject` as a high-signal hygiene indicator. Until then, treat the flag as noisy and don't surface it to operators as actionable. Pairs naturally with a broader "chaos pattern false-positive audit" if other patterns turn out to over-match too.

## smartsheet_migration: import-time side effects in three scripts [OPEN]

Surfaced 2026-05-18 in the sanity-check audit (finding M4). See `docs/session_logs/2026-05-18_sanity_check_sweep.md`.

**Affected files:**
- `smartsheet_migration/inspect_closeout.py`
- `smartsheet_migration/inspect_source_schedule.py`
- `smartsheet_migration/migrate_schedule_dryrun.py`

**Pattern:** module-level code that requires runtime state (the `SMARTSHEET_TOKEN` env var). Specifically, top-level statements call `ss_api._token()` or fetch sheets via `ss_api.get_sheet(...)`. `migrate_schedule_dryrun.py` even prints `"Fetching source + destination sheets..."` and makes live API calls during `import`. Importing any of the three from a context without the env var set raises `RuntimeError`.

**Why existing parser misses it:** not a parser issue — these are one-off migration scripts that work when run with `python script.py SMARTSHEET_TOKEN=...` from the operator's shell. The anti-pattern doesn't bite at runtime; it bites at any future programmatic use (introspection, test collection, batch tooling, IDE static analysis).

**Concentration / volume:** 3 files, all in `smartsheet_migration/`. Same `_token()` call site (`smartsheet_migration/ss_api.py:34`).

**Suggested fix:** wrap each script's top-level work in `if __name__ == "__main__":` and move any module-level constants that don't depend on `_token()` to remain at module scope. ~5-line change per file. No behavior change when invoked from the shell.

**Test snippets:** add a tests/test_migration_import_hygiene.py that does `importlib.import_module(name)` against each of the three modules with the env var unset, asserting no exception. Drop-in:

```python
import importlib
import sys
import os
from pathlib import Path
import pytest

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "smartsheet_migration"
if str(MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATION_DIR))

@pytest.mark.parametrize("name", ["inspect_closeout", "inspect_source_schedule", "migrate_schedule_dryrun"])
def test_module_imports_without_smartsheet_token(name, monkeypatch):
    monkeypatch.delenv("SMARTSHEET_TOKEN", raising=False)
    importlib.import_module(name)
```

**Expected coverage delta:** none for the structural-claim chain (these aren't user-facing structures). The benefit is import safety for any future tooling.

**Status:** scheduled for a focused follow-up PR; no date promised; low priority since the scripts are one-off migration tools. Worth bundling with any other `smartsheet_migration/` touch.

## mypy: import-untyped noise from vendor SDKs without stubs [OPEN]

Surfaced 2026-05-18 in the mypy baseline reconciliation. See `docs/reports/2026-05-18_mypy_baseline.md` for the full inventory.

**Pattern:** four `mypy .` errors of the form `Skipping analyzing "X": module is installed, but missing library stubs or py.typed marker [import-untyped]` or `Library stubs not installed for "X"`.

**Affected imports:**
- `msal` in `scripts/smoke_test_graph.py:32` — MSAL Python SDK lacks py.typed marker.
- `requests` in `scripts/smoke_test_graph.py:33` — installable as `types-requests`.
- `smartsheet` and `smartsheet.exceptions` in `tests/test_smartsheet_client.py:16` — smartsheet-python-sdk lacks py.typed marker.

**Why existing code misses it:** not a code bug. The vendor SDKs simply don't ship type information. Mypy is correctly flagging the gap.

**Concentration / volume:** 4 errors, 2 files. Constant across all commits in 2026-05-18 — these have been in the baseline since well before today.

**Suggested fix:** add a `[[tool.mypy.overrides]]` block in `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ["msal", "msal.*", "smartsheet", "smartsheet.*"]
ignore_missing_imports = true
```

For `requests`, install the stubs: add `types-requests` to the dev dependencies in `pyproject.toml`, then `uv sync` or `pip install`. Stubs maintained by the typeshed project.

**Test snippets:** N/A — this is mypy config, not tested code.

**Expected coverage delta:** 4 errors drop from `mypy .` baseline. Brings remaining `mypy .` count from 8 → 4 (post-PR-#15 baseline) if applied. The remaining 4 are all real type issues in `box_migration/` + `smartsheet_migration/`.

**Status:** scheduled for a focused follow-up PR; should land BEFORE any mypy-in-CI integration so the signal-to-noise ratio is acceptable. Otherwise persistent vendor-SDK warnings will train operators to ignore mypy output.

## parse_job_v3.py: matched needs type annotation [CLOSED 2026-05-18]

Resolved by adding the explicit annotation `matched: dict[Schema, list[str]] = {...}` in `classify_schema()`. Inferred type from `_V3_SIGNATURES` keys (Schema enum members) and the `.append(name)` call site where `name` is a `str`. One-line annotation change; zero behavior change. Preservation-over-refactor §14 honored — only the annotation line was modified.

Resolution: see commit on the `fix/parse-job-v3-matched-annotation` branch (squash-merged), and `docs/session_logs/2026-05-18_alert_critical_and_mypy_closure.md`.

Originally surfaced 2026-05-18 in the mypy baseline reconciliation; see `docs/reports/2026-05-18_mypy_baseline.md` for the lifecycle context.

## smartsheet_migration/ss_api.py: api body arg type mismatch [OPEN]

Surfaced 2026-05-18 in the mypy baseline reconciliation. See `docs/reports/2026-05-18_mypy_baseline.md`.

**Pattern:** `smartsheet_migration/ss_api.py:79: error: Argument "body" to "api" has incompatible type "list[dict[Any, Any]]"; expected "dict[Any, Any] | None" [arg-type]`. The `api()` function's `body` parameter is annotated as `dict | None` but a caller passes a `list[dict]` — common for Smartsheet bulk-add endpoints that accept arrays.

**Why existing code misses it:** the `api()` helper was written when Smartsheet calls were dict-only; bulk endpoints were added without widening the annotation.

**Concentration / volume:** 1 error, 1 location. Other call sites in `smartsheet_migration/` likely pass through correctly.

**Suggested fix:** widen the `body` parameter annotation in `api()` to `dict | list | None` and verify all call sites still type-check. Roughly 3 lines.

**Test snippets:** N/A — `smartsheet_migration/` has no test files; would need to add a minimal `tests/test_ss_api_type.py` if testing is desired.

**Expected coverage delta:** 1 error drops from `mypy .` baseline.

**Status:** scheduled for a focused follow-up PR. Worth bundling with the import-time-side-effects entry above since both touch `smartsheet_migration/`. Preservation-over-refactor applies but this is a real type bug, not a stylistic issue — refactor permitted under §14's "real bug" carve-out.

## smartsheet_migration/migrate_fl.py: warnings list type annotation [OPEN]

Surfaced 2026-05-18 in the mypy baseline reconciliation. See `docs/reports/2026-05-18_mypy_baseline.md`.

**Pattern:** `smartsheet_migration/migrate_fl.py:176: error: Need type annotation for "warnings" (hint: "warnings: list[<type>] = ...") [var-annotated]`. The `warnings` variable is initialized as `[]` without an element type annotation.

**Why existing code misses it:** mypy can't infer the element type from `warnings = []` followed by conditional `warnings.append(...)` calls. Annotation needed.

**Concentration / volume:** 1 error, 1 location.

**Suggested fix:** add the explicit annotation `warnings: list[str] = []` (or whatever element type the appends produce — likely `str`). Inspect with `git blame smartsheet_migration/migrate_fl.py | sed -n '174,180p'`.

**Test snippets:** N/A — annotation fix.

**Expected coverage delta:** 1 error drops from `mypy .` baseline.

**Status:** scheduled for a focused follow-up PR; preservation-over-refactor holds. Same bundling rationale as the `ss_api` entry above — touch the migration directory once when convenient.
