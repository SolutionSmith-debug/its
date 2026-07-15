---
type: reference
date: 2026-07-15
status: active
workstream: docs
tags: [documentation-corpus, troubleshooting-tree, schema]
---

# Troubleshooting Tree — Schema

## Purpose

`docs/troubleshooting/tree.yaml` is the **single source of truth** for the operator
troubleshooting tree. One file drives BOTH the interactive dashboard `/troubleshoot` view and
a generated printable guide (`troubleshooting_guide.md` → branded PDF). This doc is the
authored spec for that file; the machine-checked spec is the loader
(`troubleshooting/loader.py`, which raises `TreeError` on any shape violation) plus the
coverage tests (`tests/test_troubleshooting_tree.py`), which are the completion meter.

## Shape

```yaml
workflows:                       # the top level: a list of end-to-end workflows
  - id: <slug>                   # stable, unique
    title: <human title>
    summary: <one-line what-this-is>
    steps:                       # the pipeline, in real execution order
      - id: <slug>               # unique within the workflow
        title: <human title>
        what_happens:            # optional facts about this step
          daemon: <plist-label>          # e.g. portal-poll, weekly-send, watchdog
          worker_route: <path>           # e.g. GET /api/internal/pending
          sheets: [<sheet name>, ...]
          config_gates: [<ITS_Config key>, ...]
        healthy_signals:         # how you know this step is OK (panel / heartbeat / log)
          - <str>
        failure_modes:           # ≥1 unless no_failure_modes is set
          - id: <slug>
            symptom: <what the operator observes>
            signals: [<held_* word / error code / watchdog check / status>, ...]
            checks: [<ordered, concrete: which panel / sheet / log / command>, ...]
            resolutions: [<ordered, concrete>, ...]
            class: daniel_solo | seth_coresolve
            runbook: docs/runbooks/<file>.md[#anchor]   # optional
            watchdog_check: _check_<name>               # optional; the watchdog fn that catches this
        # a step that genuinely has no failure mode must instead carry:
        no_failure_modes: <reason>
runbook_exemptions:              # runbooks intentionally NOT linked by a node (with reason in a comment)
  - <runbook filename>
```

## The class-assignment rule

Every failure mode carries a resolution `class`. The enum values are `daniel_solo` and
`seth_coresolve` (the brief-specified identifiers); the **display labels are role-based** —
`daniel_solo` renders as *"Operator-resolvable"* (the trained Successor-Operator, solo) and
`seth_coresolve` as *"Escalate to Seth"* (co-resolve with the Developer-Operator). The mapping
lives in [escalation_matrix.md](../references/escalation_matrix.md).

Assign the class by this rule (bake it into every node):

- **`seth_coresolve`** if the node touches ANY of the four **fixed high-capability-class**
  categories — (1) the External Send Gate, (2) secrets / auth, (3) doctrine, or (4) a code
  change / migration / deploy — **or** if the fix is novel / not in a runbook.
- **`daniel_solo`** only if the fix is **documented** (has a `runbook:`) **AND** low blast
  radius (re-run a daemon, toggle a documented `ITS_Config` value, re-send an approved row,
  re-seed a row, clear a stuck lock).
- **When unsure → `seth_coresolve`.**

## Designed-dark vs broken

A capability whose `polling_enabled` (or `sync_enabled`) gate is `false` is **dark by design**,
not broken. Every "it's not running / not sending" node must distinguish the two: first check
the gate (the dashboard shows the live value), and only treat it as a fault if the gate is on.
The tree must never present a designed-dark gate as a failure.

## Coverage floors (enforced by `tests/test_troubleshooting_tree.py`)

The tree is "complete" when these pass:

1. `tree.yaml` schema-validates (loader).
2. Every launchd daemon (a `scripts/launchd/org.solutionsmith.its.*.plist`) appears as a
   `what_happens.daemon` in ≥1 step.
3. Every HELD send-status word appears in ≥1 failure node.
4. Every live watchdog check (`scripts.watchdog.CHECKS`) is referenced by a node's
   `watchdog_check`.
5. Every `runbook:` link resolves to a real file; every `#anchor` is a real heading.
6. Every runbook file is referenced by ≥1 node OR listed in `runbook_exemptions`.
7. Every step has ≥1 failure mode OR a `no_failure_modes` reason.

These tests extract the floor from live code, so a new daemon / check / runbook RED-lights the
tree until it is covered — the tree cannot silently fall behind the system.

## Related docs

- [tree.yaml](tree.yaml) — the content this schema describes
- [escalation_matrix.md](../references/escalation_matrix.md) — the class → role mapping
- [daemon_reference.md](../references/daemon_reference.md) — the daemons the steps reference
- [glossary.md](../references/glossary.md) — HELD states, watchdog-check, gate/breaker vocab
