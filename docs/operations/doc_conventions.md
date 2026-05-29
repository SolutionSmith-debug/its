---
type: operations
date: 2026-05-24
status: active
related_prs: []
workstream: docs
tags: [conventions, lint, retrofit]
---

# ITS Doc Conventions

Canonical structure for every Markdown file in this repo. **Lazy
retrofit:** existing docs are grandfathered; new docs MUST conform;
existing docs upgrade when touched for unrelated reasons (see
[Retrofit policy](#retrofit-policy) below).

The spec codifies what cc + chat have been doing organically since the
session-log convention landed 2026-05-17. The shape changes that
matter — YAML frontmatter + per-type section templates — make the
docs **programmatically queryable**: a script can answer "find all
session logs for `safety_reports`" or "list all `OPEN` tech-debt
entries" without grepping prose.

## Frontmatter

**REQUIRED on every doc under `docs/`, `prompts/`, and `prompts/samples/`,
EXCEPT** the [exempt list](#exempt-from-frontmatter) below.

```yaml
---
type: session_log | brief | audit | report | operations | reference | sample | readme
date: YYYY-MM-DD
status: draft | active | superseded | archived | closed
related_prs: [123, 124]       # optional, list of PR numbers
workstream: safety_reports | safety_portal | box | ci | security | docs | infrastructure | null
supersedes: docs/path/to/old-doc.md  # optional; omit when first version
tags: [phase_1.4, security]   # optional
---
```

### Field semantics

| Field           | Required | Notes |
|-----------------|----------|-------|
| `type`          | yes      | Discriminator — lint branches on this for section validation. Must be one of the listed values. |
| `date`          | yes (time-bound types) | YYYY-MM-DD format. For session_log / brief / report / audit-snapshot. Evergreen types (operations, reference, sample, readme) MAY omit if the doc isn't dated. |
| `status`        | yes      | Tracks the doc's lifecycle. Operations/reference docs default to `active`; session_logs to `closed` (they're snapshots of completed work). |
| `related_prs`   | no       | List of PR numbers this doc relates to. Enables reverse lookup ("what docs mention PR #75?"). |
| `workstream`    | yes      | Closed-set tag (see [taxonomy](#workstream-taxonomy)). `null` is valid for cross-cutting docs. |
| `supersedes`    | no       | When a new doc replaces an old one, link forward via `supersedes:`. Old doc's `status` flips to `superseded`. |
| `tags`          | no       | Free-form supplementary tags. Don't reinvent `workstream` here. |

### Exempt from frontmatter

- `CLAUDE.md` (repo entry point cc reads on every session)
- `README.md` (top-level + per-workstream evergreen entry points)
- `docs/tech_debt.md` (accumulator; each entry has its own `[STATUS DATE]` header)
- All `docs/**/README.md` index files (their structure is auto-generated)
- `prompts/<name>.md` (DIRECT children of `prompts/` only — NOT `prompts/samples/`).
  These use the prompt-specific frontmatter convention
  (`name / version / model / notes`) documented in `prompts/README.md`,
  not the canonical doc-conventions schema. Samples in `prompts/samples/`
  DO follow the canonical schema (`type: sample`).
- `docs/agents/*.md` (the mattpocock/skills agent-OS config — `issue-tracker.md`,
  `triage-labels.md`, `domain.md`, consumed by the installed skills per
  CLAUDE.md "## Agent skills"). These follow the upstream skills convention,
  not the canonical doc-conventions schema — same rationale as the
  `prompts/` direct-children carve-out.

These have implicit `status: active` and don't need a date. The lint
script's exempt list mirrors this.

## Section conventions per doc type

The lint script validates the presence of these section headers (level-2
`##` by default). Order is conventional, not enforced — but stick to it.

### `type: session_log`

The full pattern that has been in practice since 2026-05-17 (see
`docs/session_logs/README.md` for the why-this-exists explanation):

1. **Purpose** — 1-2 sentences on what the session set out to do.
2. **Pre-flight findings** — anything surprising discovered before changes.
3. **Code changes** — file-by-file or phase-by-phase summary.
4. **Verification** — 4-part: `pytest` / `mypy` / `ruff` / main-branch CI on merge SHA (per `docs/operations/pr_merge_discipline.md`).
5. **Live smoke** — if applicable (operator-driven runs against the live mirror tenant).
6. **Out-of-scope notes** — deliberately deferred items.
7. **Sequencing context** — what this unblocks; what was prereq.
8. **Operator-side actions remaining** — post-merge follow-ups.
9. **Merge verification quartet output** — verbatim block from the four-part discipline.

### `type: brief`

The structure cc + chat have settled on for PR-shaped briefs (rarely
committed; if a brief is captured in-repo, it follows this shape):

1. Objective
2. Pre-implementation: verify baseline
3. Context
4. Decisions already resolved
5. Foundation invariants (where applicable)
6. Substance / Implementation tasks (numbered phases)
7. Tests
8. Out of scope
9. Verification gates
10. Done when
11. Operator-side actions remaining
12. Anti-patterns to avoid (optional)

### `type: audit`

Structured findings against a closed scope, e.g.
`docs/audits/picklist_hardening_audit.md`:

1. Purpose / what's being audited
2. Status legend (emoji conventions, if used)
3. Findings tables (one per scope; status emoji or text per row)
4. Owner / next action
5. `date` in frontmatter doubles as "last reviewed"

### `type: report`

One-shot quantitative or qualitative snapshot, e.g.
`docs/reports/2026-05-18_mypy_baseline.md`:

1. Summary — 1 paragraph
2. Methodology — what was measured / how
3. Findings — data tables, numbered observations
4. Recommendations
5. Appendix — raw data / commands run / artifacts referenced

### `type: operations`

Runbook / how-to-do-a-procedure docs, e.g. `docs/operations/pr_merge_discipline.md`:

1. Purpose
2. Procedure — numbered steps with copy-paste commands
3. Examples
4. Validation / how to verify the procedure was followed
5. Owner

### `type: reference`

Evergreen explanatory docs, e.g. `docs/references/picklist_sync.md`:

1. Purpose
2. Background / context
3. Behavior / how it works
4. Edge cases / known limitations
5. Related docs / cross-references

### `type: sample`

Few-shot anchors in `prompts/samples/`, e.g.
`prompts/samples/legacy_wpr_gates_solar_2016-03-12.md`:

1. Provenance — source file + project + date in frontmatter
2. Verbatim content — the anchor itself; no editorial overlay inside the
   sample body (the surrounding prose introduces it once at the top)

### `type: readme`

Index READMEs (one per subdirectory):

1. Purpose of the directory — short prose paragraph
2. Auto-generated index block bounded by

   ```text
   <!-- BEGIN AUTO-INDEX -->
   …regenerated by scripts/regen_doc_indexes.py…
   <!-- END AUTO-INDEX -->
   ```

   Operator-edited prose lives **outside** the sentinels and is preserved
   across regeneration runs.

### Tech-debt entries

`docs/tech_debt.md` is the accumulator; the file itself takes no
frontmatter. Each entry follows:

```markdown
## <Title> [STATUS YYYY-MM-DD]

<Description paragraph>

<Context / why this was deferred / what closed it>

**Revisit when:** <trigger condition>
```

`STATUS` ∈ `OPEN | PARTIALLY_MITIGATED | CLOSED | DEFERRED | DELIVERED`.
The convention has been in practice since 2026-05-22; the lint script
will eventually validate this entry shape too (deferred to a follow-on).

## Filename convention

Slugs are lowercase with underscores between words (matches existing
convention). No spaces, no caps, no `__double__`.

- **Time-bound docs** (session_log, brief, report, audit-snapshot):
  `YYYY-MM-DD_topic-slug.md`. Example:
  `docs/session_logs/2026-05-23_r3_session_3_weekly_send.md`.
- **Evergreen docs** (operations, reference, sample, readme):
  `topic-slug.md`. Example:
  `docs/operations/pr_merge_discipline.md`.
- **Index READMEs**: `README.md` in each subdirectory.

The lint script flags filename mismatches per type.

## Workstream taxonomy

Closed set, defined here. New workstreams require editing this list and
opening a small PR before the value is used elsewhere.

| Value             | Scope |
|-------------------|-------|
| `safety_reports`  | intake / weekly_generate / weekly_send + trusted_contacts |
| `safety_portal`   | Cloudflare-hosted portal, sync Worker, email shim, form schemas, prune Worker, intake.py portal-marker branch |
| `box`             | 1111A/1111B, migrations, parse_job_v3 |
| `ci`              | CI workflows, verification discipline, conftest |
| `security`        | picklist hardening, header forgery, attachment screening |
| `docs`            | conventions, indexes, retrofits |
| `infrastructure`  | kill_switch, watchdog, heartbeat, alerts |
| `null`            | cross-cutting; the doc spans multiple workstreams |

## Cross-repo supersession drift

The blueprint (`its-blueprint`) holds doctrine; this repo holds code. The main
drift risk is the two diverging — most dangerously when the blueprint supersedes
a model this repo still asserts. (Concrete case: the 2026-05-28 Safety Portal
pivot made attachment screening N/A for safety reports, superseding the model
this repo had asserted in audit HIGH-2 — see `docs/tech_debt.md`.) There is **no
automated cross-repo divergence check** — it would have to read both repos and
is deliberately not built. The guard is the existing mechanisms, used together:

- **Blueprint frontmatter** `last_verified` / `last_verified_against` records the
  execution-repo SHA each canonical doctrine doc was validated against; the
  blueprint's `lint_frontmatter.py` warns when it ages past the stale-day
  threshold.
- **Audit docs** (`docs/audits/` here, `audits/` in the blueprint) capture
  point-in-time cross-repo verification snapshots citing the affected doc(s) and
  the SHA where drift was observed.
- **`session-close-maintainer`** runs a recurring manual "Cross-repo
  supersession check" at every session close, in both directions (a blueprint
  workstream with no exec acknowledgment; this repo asserting a superseded
  model). See `.claude/agents/session-close-maintainer.md`.
- **`doc-reconciliation-auditor`** is the heavy / on-demand counterpart to that
  manual check: a propose-only agent (opus) that runs
  `scripts/check_doctrine_drift.py` against `docs/doctrine_manifest.yaml` (the
  canonical-facts manifest) plus a semantic judgment tier, and emits a dated
  findings report to `docs/audits/`. Invoke it after a doctrine version bump or a
  doctrine-touching PR; it writes nothing (a `PreToolUse` hook enforces it). See
  `.claude/agents/doc-reconciliation-auditor.md`.

When you supersede a model in one repo, reconcile the other in the same session —
or file a dated audit / tech-debt entry naming the lag. Don't leave the stale
assertion standing.

## Retrofit policy

Existing docs as of this PR's merge are **grandfathered**. They satisfy
the convention by virtue of being pre-existing, even when they lack
frontmatter or have non-standard sections.

When ANY of these triggers fires on a grandfathered doc, retrofit it to
current convention as part of the change:

1. The doc is being edited for any reason.
2. The doc is moved between directories.
3. A cross-reference to the doc breaks and needs fixing.
4. The doc's status changes (active → superseded, etc.).

After ~60 days (target 2026-07-24), a separate "retrofit sweep" PR may
bulk-migrate any remaining grandfathered docs. Until then, no big-bang
migration.

The lint script `scripts/lint_doc_conventions.py` runs in **warn-only**
mode during the retrofit window — violations report to stdout but the
exit code stays 0. Flip to `--strict` only after the sweep completes
(tracked in `docs/tech_debt.md`).

## Tooling

Two scripts enforce + automate the convention:

- **`scripts/regen_doc_indexes.py`** — walks `docs/`, parses YAML
  frontmatter, regenerates the AUTO-INDEX section in each subdirectory's
  README between sentinel markers. Idempotent. `--check` mode for CI
  (exits non-zero if any README would change). Wired into
  `scripts/watchdog.py::TRACKED_JOBS` as `doc_index_regen` for nightly
  freshness.
- **`scripts/lint_doc_conventions.py`** — validates frontmatter +
  section headers + filename per type. Warn-only default. CI integration
  in `.github/workflows/ci.yml`. Strict mode is the post-retrofit
  follow-on.

## Owner

`@solutionsmith`. Changes to this convention require a small PR; the
lint script + index generator are codified against the spec above so
extending one extends the other.
