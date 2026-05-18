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
