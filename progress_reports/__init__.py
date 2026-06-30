"""Progress Reporting workstream — the report-and-send twin of safety_reports.

A NEW sibling workstream (Op Stds v19 §51 — ITS-owned structured-SoR write-back; mission
its-blueprint/workstreams/progress-reporting/mission.md). It produces a weekly Progress
Report from the field-ops capture surface, human-reviewed in WPR_human_review and sent
externally to the job stakeholder after explicit approval — the structural twin of the
Safety Portal weekly pipeline.

Per the locked "parameterize, not clone" decision (§14), the security-critical machinery
(week_sheet, weekly_send, send_poll_core, compile_core) is REUSED from safety_reports via
its required no-default config objects (WeekSheetConfig / SendConfig / DaemonConfig); this
package holds only the progress-specific bindings + thin modules (P2: wpr_review).
"""
