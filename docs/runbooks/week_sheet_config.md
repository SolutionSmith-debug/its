---
type: operations
date: 2026-06-29
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, week-sheet, parameterize, tier-2, tier-3]
---

# Runbook — week_sheet config binding (submissions mis-file / week sheet names change) (Successor-Remediation, Op Stds §43)

Written for the **Successor-Operator** (runs Claude Code, reads Smartsheet rows
and alert emails, does **not** read code). The §42 code-reader rationale for the
same capability lives in the `safety_reports/week_sheet.py` `WeekSheetConfig`
block comment — the two are complements.

## Background (what changed)

`week_sheet.ensure_week_sheet` was parameterized (P1a) so a future progress
workstream can reuse it. The two workstream-specific knobs — the **workspace** the
per-job folders live under, and the **week-sheet name builder** — now come from a
required `WeekSheetConfig`. Safety always passes `SAFETY_WEEK_SHEET_CONFIG`, which
is byte-identical to the old behavior: same workspace pin, same `"<project> —
week of <Saturday>"` names. **Nothing about safety's behavior changed.** This entry
exists because a *future* mis-binding would surface here.

## Symptom → check → action → escalate

### Symptom A — a daemon won't start; `ITS_Errors` shows `TypeError` mentioning `WeekSheetConfig` or `ensure_week_sheet` missing an argument
- **What it means:** a code change left a `week_sheet` caller without a (valid) config. This is a fail-LOUD build/deploy error — the daemon refuses to run rather than file into the wrong place.
- **Successor-Operator check:** confirm in `ITS_Daemon_Health` which daemon is down (portal-poll / weekly-generate / compile-now-poll) and that the error text names `WeekSheetConfig` / `ensure_week_sheet`.
- **Action:** **none at the operator tier.** This is a code defect.
- **Escalate to Seth (Developer-Operator) immediately** — category #4 (code change). Do **not** hand-edit any sheet.

### Symptom B — safety portal submissions stop filing, or week sheets appear under an unexpected workspace
- **What it means:** a config was mis-bound to the wrong `workspace_id`.
- **Successor-Operator check:** in the Safety Portal workspace, confirm new submissions are NOT creating per-job folders/sheets; check `ITS_Review_Queue` / `ITS_Errors` for filing failures.
- **Action:** **none at the operator tier** — the fix is a code change (re-point the safety binding to the correct workspace).
- **Escalate to Seth** — category #4. Provide the workspace where sheets are (wrongly) appearing.

### Symptom C — week sheet **names** changed, or a duplicate empty week sheet appeared for an existing week
- **What it means:** the name/key builder was altered, so find-or-create no longer matches the existing sheet and creates a new one.
- **Successor-Operator check:** compare a newly created week sheet's name against the expected `"<project> — week of <Saturday>"` pattern (the Saturday that opens the work-date's week).
- **Action:** **none at the operator tier.** The extra empty sheet is harmless (bounded blast radius); leave it for cleanup.
- **Escalate to Seth** — category #4. Name the affected job + week.

## Escalate-to-Seth condition (summary)

**Every** repair here is a code change → **always escalate to the Developer-Operator
(Seth).** The Successor-Operator's only in-tier role is gathering the observable
evidence above (which daemon, which workspace, which sheet name) so Seth can fix it
fast. There is no Tier-2 low-capability-class repair for this capability.
