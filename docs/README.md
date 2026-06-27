# Documentation

Top-level entry point for the `docs/` subtree. See
[`docs/operations/doc_conventions.md`](operations/doc_conventions.md) for
filename, frontmatter, and section conventions across every doc type.

## Subdirectories

- [`session_logs/`](session_logs/) — durable narrative records of cc
  sessions. See `session_logs/README.md` for the why-this-exists
  explanation + when-to-write criteria.
- [`operations/`](operations/) — runbook / how-to-do-a-procedure docs
  (PR merge discipline, doc conventions, etc.).
- [`reports/`](reports/) — one-shot quantitative / qualitative snapshots
  (mypy baselines, audit summaries, etc.).
- [`audits/`](audits/) — structured findings against closed scopes
  (picklist hardening, person_tag false positives, etc.).
- [`references/`](references/) — evergreen explanatory docs (picklist
  sync runbook, etc.).

## Top-level files

- [`tech_debt.md`](tech_debt.md) — accumulator of deferred items. See
  the file header for entry conventions.

<!-- BEGIN AUTO-INDEX -->
| Date | Type | Status | Workstream | Title | PRs |
|------|------|--------|------------|-------|-----|
| 2026-06-12 | design | draft | safety_portal | [Safety Portal — Form Request month-year + form-type filter (PR-6) · CC brief](cc-brief_form-request-month-filter.md) | _–_ |
| 2026-06-08 | design | draft | safety_portal | [Safety Portal — Phase 2 Form Editor + Session Hardening · design brief (feeder)](phase2_form_editor_and_session_hardening_brief.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [CC kickoff brief — Field-Ops portal program, resume at P2.2 Briefs A/B/C](cc-brief_p2.2-next-session.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [P2.2 Field-Ops READ-views — design + build briefs (workflow-authored, scalability-first)](cc-brief_p2.2-readviews.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [ITS — Tech Debt](tech_debt.md) | _–_ |
<!-- END AUTO-INDEX -->
