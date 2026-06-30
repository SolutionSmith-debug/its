---
type: operations
date: 2026-06-29
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, forms, publish, workflow, tier-2]
---

# Runbook — Form workflow selector + the `recategorize` publish op (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator**. The §42 code-reader
rationale lives in `safety_reports/publish_manifest.py` (the `create`/`recategorize` branches
of `apply_publish`), `safety_portal/worker/publishValidation.ts` (`validateCategory`),
`safety_portal/workflows.json` (the workflow registry), and `shared/form_category.py`
(the Python registry reader).

## What this controls

Every published form belongs to a **workflow** — today `safety` or `progress`. The workflow
is stored on the form's **catalog parent** (`safety_portal/catalog.json`, parent-level
`category`) and decides which downstream pipeline the form's submissions feed (see the
companion runbook `progress_intake_routing.md`). Two admin actions set it, both through the
**same §50 publish actuator** (Worker queues a request → the Mac `publish_daemon.py` is the
sole actuator: claim → validate-against-HEAD → commit → deploy):

- **On a new form** — the Form Editor's **Workflow** dropdown (selectable only while
  *creating*; locked once the form exists). A category-less/legacy create defaults to
  `safety`.
- **On an existing form** — the **Change workflow** control in the form's view pane issues a
  `recategorize` publish op. It flips the catalog parent's `category` and moves **every form
  and variant under that parent** to the new workflow at once. It writes **no form-definition
  files** — only `catalog.json` changes.

The registry of valid workflows is `safety_portal/workflows.json` (read by both the
TypeScript Worker and Python). **Adding a new workflow is a code/doctrine change — out of
scope for Tier-2; escalate to Seth** (high-class category #4, code changes).

## Procedure

### Fault A — A "Change workflow" (or new-form workflow) request is stuck

**Symptom.** An admin used **Change workflow** (or published a new form with a workflow set),
the Publish monitor shows the request **PENDING / claimed but not applied**, and the form's
workflow hasn't changed after a few minutes.

This is the **ordinary publish-pipeline stall** — `recategorize` rides the exact same actuator
as every other publish op. **Repair it with the standard publish runbook** —
`safety_portal_forms.md`, "publish request stuck" — which covers checking the publish daemon's
health row and re-running it. No recategorize-specific step is needed.

**Escalate to Seth if** the publish daemon log shows a validation rejection you don't
understand, or the request is marked `failed` with a `publish_*` error code — that is the
actuator/validation code, a high-class (code-change) surface.

### Fault B — A form shows the wrong workflow

**Symptom.** A form's view pane (or its downstream routing) shows `safety` when it should be
`progress`, or vice-versa — e.g. an admin picked the wrong workflow, or a `recategorize`
targeted the wrong form.

**Repair (low-class, admin-UI only).** Open the form in the admin Forms tab, use **Change
workflow**, select the correct workflow, and publish. This is a normal `recategorize` — the
same in-app action, no code, no secrets. Confirm the Publish monitor lands it, then re-check
the view pane.

**Note — this moves the WHOLE parent.** `recategorize` re-files **every form and variant**
sharing that catalog parent, not just one variant. If only one variant looks wrong but the
others are correct, that is **not** a recategorize fault — **stop and escalate to Seth**; the
form may need a definition change (a code surface).

### Fault C — "unknown workflow category" rejection

**Symptom.** A publish (create or recategorize) is rejected with `invalid_category` /
`unknown workflow category`.

**Cause.** The chosen workflow isn't in `safety_portal/workflows.json`. In the live admin UI
the dropdown only offers registered workflows, so this normally means a stale browser or a
hand-edited request. **Repair (low-class):** hard-refresh the admin page (Cmd-Shift-R) and
re-issue the action picking a workflow from the dropdown.

**Escalate to Seth if** the workflow you need genuinely isn't offered — **adding a workflow to
the registry is a code change** (it edits `workflows.json` + ships with tests), a high-class
category. Do not hand-edit `workflows.json`.

## Escalate-to-Seth boundary (observable terms)

Escalate — do **not** attempt — whenever:

- the publish daemon log shows a **validation rejection / `publish_*` failure** you can't
  resolve by re-running the daemon (actuator/validation code — high-class);
- a **single variant** under a parent is mis-categorized while its siblings are right
  (definition-level, not a workflow flip);
- a **new workflow** is needed that the dropdown doesn't offer (registry/code change);
- anything asks you to **edit `workflows.json`, `catalog.json`, or any `safety_portal/**`
  code by hand** (code changes are always high-class, regardless of documentation).

Tier-2 here is exactly: **re-run the stalled publish daemon, or re-issue a Change-workflow /
new-form-workflow selection through the admin UI.** Everything else escalates.
