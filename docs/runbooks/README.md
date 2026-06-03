# Runbooks

Successor-Remediation runbook entries (Op Stds v16 §43). Each entry is
plain-language Markdown shipped **with** a capability, written for the
**Successor-Operator** (a trained operator who runs Claude Code and reads
Smartsheet rows + alert emails, but not code). Claude loads the relevant
entry to drive a Tier-2 repair; the operator sees the evidence and approves.

These are the operator-facing counterpart to the code-reader `§42`
docstrings/comments in the modules themselves — same capability, different
audience (see [`../operations/doc_conventions.md`](../operations/doc_conventions.md)
and Op Stds §43 vs §42). Each entry follows the §43 four-part shape
(Symptom → What the Successor-Operator checks → The Claude prompt or UI
action → Escalate-to-Seth condition); they use `type: operations`
frontmatter (the conforming type for runbook/procedure docs — the
convention has no separate `runbook` type).

<!-- BEGIN AUTO-INDEX -->
| Date | Type | Status | Workstream | Title | PRs |
|------|------|--------|------------|-------|-----|
| 2026-06-03 | operations | active | safety_portal | [Runbook — Safety Portal config sheets (ITS_Active_Jobs + ITS_Forms_Catalog) (Successor-Remediation, Op Stds §43)](safety_portal_config_sheets.md) | _–_ |
| 2026-06-02 | operations | active | safety_reports | [Runbook — ITS_Daemon_Health row self-provision (Successor-Remediation, Op Stds §43)](daemon_health_self_provision.md) | _–_ |
| 2026-06-02 | operations | active | infrastructure | [Runbook — Weekly picklist audit reports drift (Successor-Remediation, Op Stds §43)](picklist_drift_reconcile.md) | _–_ |
| 2026-06-02 | operations | active | safety_reports | [Runbook — Project not routed to a Box folder (Successor-Remediation, Op Stds §43)](project_routing_onboarding.md) | _–_ |
| 2026-06-02 | operations | active | infrastructure | [Runbook — Smartsheet token cannot write (Successor-Remediation, Op Stds §43)](token_write_capability.md) | _–_ |
| 2026-06-01 | operations | active | infrastructure | [Runbook — Smartsheet circuit breaker + alerts-per-hour cap (Successor-Remediation, Op Stds §43)](circuit_breaker.md) | _–_ |
| 2026-06-01 | operations | active | safety_reports | [Runbook — weekly_generate catch-up (Successor-Remediation, Op Stds §43)](safety_weekly_generate.md) | _–_ |
<!-- END AUTO-INDEX -->
