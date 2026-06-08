---
type: design
date: 2026-06-08
status: draft
related_prs: []
workstream: safety_portal
tags: [phase-2, form-editor, session-hardening, catalog, publish-pipeline, design-brief, feeder]
---

# Safety Portal — Phase 2 Form Editor + Session Hardening · design brief (feeder)

**Status: DRAFT design brief — the feeder for the canonical Phase-2 mission.** Per repo
doctrine (CLAUDE.md), the *canonical* workstream mission/brief is authored in the planning
(blueprint) layer / Session B, not from an execution session. This document captures every
design decision resolved in the 2026-06-08 grill so that the canonical mission and the
execution PRs can be written directly from it. **Items marked `⚙ CONFIRM` are recommended
defaults awaiting operator sign-off** before the corresponding slice is built.

It is written **CC-/ultracode-optimized**: each build slice is a tracer-bullet vertical PR
with file anchors, invariants, and acceptance criteria, and the parallelizable work is
called out so a future CC session can fan it out with the Workflow tool.

---

## 0. Orientation

The Safety Portal is LIVE on the mirror (`safety.evergreenmirror.com`): a Cloudflare Worker
(`safety_portal/worker/index.ts`, Hono) + D1 (`its-safety-portal-db`) + a React SPA
(`safety_portal/src/`), fed by a Mac-side pull daemon (`safety_reports/portal_poll.py`). Forms
are **schema-driven JSON** (`safety_portal/forms/*.json`) validated against
`forms/meta-schema.json` and consumed by THREE renderers that share one contract
(`src/forms/types.ts`):
1. the SPA `FormRenderer` (on-screen fill),
2. `form_pdf.render_submission_pdf` (the filed submission PDF, rendered by `intake.py`),
3. `form_pdf.render_blank_fillable` (the blank archive PDF — PR-L).

Today forms are **build-time bundled** (`src/forms/registry.ts` eager-globs `forms/*.json`) and
the Python side reads disk via `form_pdf.load_definition(form_code)`. There is **no `/api/forms`**
and the intended `ITS_Forms_Catalog` (Smartsheet) → SPA sync was **never built** (the SPA shows
all bundled forms). Phase 2 changes how forms are *authored and published*, NOT the renderers.

This brief has **two parts**:
- **Part A — Portal security hardening** (post-audit): being executed now as a standalone PR.
- **Part B — Phase-2 Form Editor + Session Hardening**: the design resolved in the grill.

---

# PART A — Portal security hardening (post-audit)

Closes the 11 code/config findings from the 2026-06-08 adversarial audit (the core posture
held: injection 0/4, no auth bypass, the atomic last-admin guard survived the TOCTOU race). The
full per-finding remediation spec is the separate hardening brief; summary:

- **A1 (#1)** null/non-object JSON body → 500: per-handler body-shape guard
  (`typeof body!=="object" || body===null || Array.isArray(body)` → 400) on every route + a
  global `app.onError` (clean 400/500, no stack leak, no Sentry-page on unauth malformed input).
- **A2 (#4)** `values:[]` slips the object check → add `|| Array.isArray(values)`.
- **B (#2/#3/#8–11)** security headers: CSP + X-Frame-Options on the SPA **document** (via a
  `_headers` file emitted to `dist/client`, OR the `run_worker_first:true` middleware fallback if
  Static Assets doesn't honor `_headers`); nosniff / Referrer-Policy / HSTS on document + `/api/*`;
  `Cache-Control: no-store` on `/api/*` only. CSP starts strict, loosened for the signature pad's
  `data:` image URIs + Vite inline styles. **CSP enforce flip needs a human smoke** (signature
  capture) → ship Report-Only first or hold enforce for the operator.
- **C (#5/#6)** concurrency error codes: map the D1 UNIQUE-violation on create/rename to **409**
  (not 500); disambiguate the atomic-guard `changes()==0` into **404** (gone) vs **409 last_admin**.
  The atomic guard itself is unchanged (audit-confirmed TOCTOU-safe).
- **DEFERRED #7** session-epoch revocation → folded into Part B Session Hardening (needs a migration).
- **Rider:** the **AccountsPage edit-login close-on-submit fix** (no-change Submit closes the
  editor instead of erroring) ships in this PR so it rides the same deploy.

**Invariants:** Worker stays SEND-FREE (headers + guards + error handling only — no send path);
no D1 migration. **Activation is operator-gated** (live deploy + the CSP/signature smoke).

---

# PART B — Phase-2 Form Editor + Session Hardening

## B1. Goals & scope

**Goal.** A role-gated admin form-editor (the deferred "Tab 3") that lets the two non-technical
admins **create, edit, version, and delete** safety forms by composing the existing field/section
vocabulary — with **automatic, reliability/safety/replicability-first publishing** and a clean
**disaster fallback**.

**In scope:** the editor UI; the four authoring operations; a git-committed form catalog; the
fully-automatic Mac-gated publish pipeline + status monitor; the historical Box blank archive; and
the role-aware Session Hardening bundle (incl. deferred #7).

**Out of scope / non-goals:** novel field/section *types* (the vocabulary is closed — see B3);
form↔job binding (job-specific forms are naming-only — B4.3); multi-tenant; any change to the
three renderers' contract.

## B2. Resolved architecture

- **Git is the source of truth.** Forms stay `<identity>-vN.json` files; a **git-committed catalog
  manifest** holds the active-set + current-version-per-form + parent/variant grouping + display
  order. This buys **replicability** (every form + the catalog are version-controlled, diffable,
  revertible, forkable for the blueprint), **safety** (CI validates before live), **reliability**
  (the tested pipeline). The renderers stay **byte-unchanged**.
- **Catalog = git manifest (Q6=A).** Canonical; **replaces the never-built `ITS_Forms_Catalog`
  (Smartsheet) sync** intent. `⚙ CONFIRM` whether `ITS_Forms_Catalog` is retired or derived from
  the manifest.
- **Closed vocabulary; the editor is the safety boundary.** The editor composes ONLY existing
  field inputs (`text|textarea|date|time|number|select|signature`), item kinds
  (`rated|numeric|circle_one|text`), and section types
  (`header|static_text|repeating_table|signature_table|checklist|freeform|content_blocks`). Because
  there is **no human merge gate** (B5), the editor must enforce the *semantic* guards the
  meta-schema doesn't: `form_code` uniqueness, no duplicate field `key`s, non-empty form/section,
  valid `archetype`, parent/variant consistency — with CI as the backstop.
- **Publish feels automatic; a few-minute pipeline is acceptable** in exchange for the above.

## B3. The closed vocabulary (reference)

Per `forms/meta-schema.json` / `src/forms/types.ts`. The editor surfaces exactly these; adding a
new type is a renderer change (a separate, larger project), explicitly out of scope.

## B4. The four authoring operations

All four ride the **same** publish pipeline + status monitor (B5). They differ only in identity
and catalog effect:

| Op | Identity | Catalog effect on publish-complete |
|----|----------|-----------------------------------|
| **Create-from-blank** | new identity, v1 | add a new active form |
| **Edit** | same identity, **version-bumped** (`jha-v1`→`jha-v2`) | **auto-swap** PM-facing to the new version, **auto-retire** the prior (REPLACE) |
| **Add-version** (clone-as-template → rename) | **new** identity (e.g. `jha-bradley1`) | add a parallel active form (COEXIST) |
| **Delete** | — | retire (soft, reversible; file + history kept) |

### B4.1 Edit (improve the standard form) — REPLACE
- Opens the current version pre-populated as a template. Any change produces a **full new version**
  of the SAME identity. The `-vN` version number is **admin-dashboard-only** — PMs never see it
  (they see the unchanged form name). On publish-complete the pipeline **auto-swaps** the PM-facing
  form to the new version and **auto-retires** the superseded one. Old submissions keep resolving
  against the version they were filed under (retired files stay in git/history; `intake` renders at
  submit time so filed PDFs never re-render).
- Distinct from Add-version. `⚙ CONFIRM` concurrency rule when two admins edit the same identity
  (recommended: last-publish-wins on the version counter; the monitor shows the lineage).

### B4.2 Add-version (client/job-specific variant) — COEXIST
- Clone an existing form as an editable template → **rename to a new identity** → modify fields →
  publish **in parallel**; the base is untouched. This is the path to client/job-specific forms.

### B4.3 Job-specific = naming-only (Q3=A)
- A job/client form (e.g. `jha-bradley1`) is a **manually-named variant** under its parent (`jha`),
  appearing in the existing **parent → variant** dropdown alongside the standard variant — the same
  mechanism equipment inspections / toolbox talks already use. **No form↔job binding, no
  hardcoding.** (`jha-bradley1` was an illustration; Bradley is an archived job.)

### B4.4 Delete = retire (soft)
- Drops from the picker; keeps the file, git history, and any submissions that referenced it. Never
  a hard delete. Reversible (un-retire).

## B5. Publish pipeline (fully automatic, Mac-gated) + status monitor

**No human merge gate** (the closed vocabulary makes admin output structurally valid by
construction; the editor + CI are the gates). **The Mac is the sole privileged actuator** — mirrors
the External Send Gate philosophy: the cloud Worker stays send-free and can only *enqueue*; the
trusted Mac holds the GitHub token + deploy auth and performs the privileged commit/deploy.

Flow:
1. **Queue** — the admin clicks Publish → the Worker writes a **publish-request** row to D1
   (send-free), carrying the composed definition + op type (create/edit/add-version/delete) + the
   chosen identity/name.
2. **Actuate (Mac daemon, sibling to `portal_poll`)** — pulls the request → writes the form file +
   **updates the catalog manifest** (swap/coexist/retire per op) → commits → opens a PR →
   **auto-merges on CI-green** (`gh pr merge --auto`; branch-protection-respecting).
3. **Validate / Test (CI)** — meta-schema conformance + a **render smoke** (the new form renders
   without error in all three renderers). Note: editor-authored forms have **no reference PDF**, so
   render-parity-vs-PDF is replaced by a render-without-error smoke. `⚙ CONFIRM`.
4. **Live (auto-deploy)** — a **new deploy-on-merge GitHub Action** for the portal builds + deploys
   the Worker/SPA (the catalog manifest propagates) and the active-set swap/coexist/retire takes
   effect for PMs.
5. **Archived** — render the blank fillable → upload to Box (B6).

**Publish Status Monitor** (admin dashboard): a stepper / progress ring per in-flight publish —
**Queued → Validated → Tested → Live → Archived** (red + reason on any failure). Source of truth =
the D1 publish-request row, stamped by each stage (the daemon, the CI checks API, the deploy, the
archive job). `GET /api/admin/publish-status` polls it.

`⚙ CONFIRM` — the new **auto-deploy-on-merge Action** is a new repo capability (any merge to the
portal auto-deploys). Acceptable given the Mac-gated, CI-validated, closed-vocabulary pipeline, but
it widens the blast radius of a bad merge; the operator should sign off.

## B6. Box historical archive (storage of record / disaster fallback)

The Box `00_Form_Archive` becomes a **historical storage of record** of rendered blank fillables,
extending PR-L's one-shot `generate_form_archive.py` into a **pipeline step** (the "Archived" stage,
**load-bearing for DR**):
- **Current-active set** — one blank per active form (what someone grabs day-to-day).
- **Retained history** — every version/variant ever published is kept (e.g. a `_history/` subfolder
  or versioned filenames), so a retired/old version's blank is recoverable. `⚙ CONFIRM` layout
  (recommended: current set in `00_Form_Archive/`, full history in `00_Form_Archive/_history/`).
- **Purpose:** if the entire system is down (portal + Mac), the office opens Box, prints/fills the
  blanks by hand, and emails them — a clean revert to the paper system (the PR-L cover sheet already
  documents this). **Two complementary records: git = definitions; Box = human-usable blanks.**

## B7. Session Hardening (bundled; includes deferred audit #7)

Role-aware session lifetime + real revocation:
- **Submitters (field PMs):** keep the **90-day** persistent session (field convenience, unchanged).
- **Admins:** **5-minute idle timeout** — any activity (mouse move / click / keypress) resets it; 5
  minutes with none → logged out, must re-login. Needs **both** halves: SPA client-side activity
  detection (logout + redirect at 5 min idle, pings server on activity) **and** server-side
  enforcement (a short sliding cookie window so a captured admin cookie dies at 5 min regardless).
  `⚙ CONFIRM` — 5 min is aggressive (an admin pausing to read for 5 min is bounced); pair with a
  "still there?" nudge ~4:30.
- **#7 — real revocation:** a per-user **session epoch** (monotonic counter, D1 column → **needs a
  migration**), embedded in the cookie at issue, checked in `requireSession` (reject if
  `cookie.epoch < user.epoch`); **logout AND password-change bump it.** This is the proper fix for
  the audit's "logout is client-side only / a captured cookie stays valid to iat+90d" finding.

## B8. Editor UX (recommended default — `⚙ CONFIRM`)

A sectioned form-builder in the admin dashboard (a new "Forms" tab):
- A form-level panel (form name, parent form-type, variant label, archetype) + an ordered list of
  sections; each section has its type + its fields/items/columns.
- Add / remove / reorder sections; within a section, add / remove / reorder fields (from the closed
  input set) with a per-field property panel (key, label, required, options for select, etc.).
- A live preview using the actual SPA `FormRenderer` (render-parity confidence in-editor).
- The four operations as top-level actions: **New form** / **Edit** (loads current version) /
  **Add version** (clone → rename) / **Delete (retire)**, plus the Publish Status Monitor.
- Inline validation (the semantic guards from B2) before Publish is enabled.

## B9. Naming, identity & schema rules (`⚙ CONFIRM`)

- `form_code` is the stable unique key. **Edit** auto-manages the `-vN` suffix on a fixed identity;
  **Add-version / Create-blank** take a manual name → editor enforces **uniqueness + slug format**
  (lowercase, `[a-z0-9-]`, the `<parent>-<suffix>` convention) and parent/variant consistency.
- **Schema fields for editor-authored forms:** make `source_pdf` **optional** (editor-authored
  forms have no reference PDF) — relax the meta-schema accordingly; keep `archetype` a **required
  pick** in the builder (it drives rendering hints). Cloned forms inherit the parent's values.

## B10. Foundation invariants compliance

- **External Send Gate (Invariant 1):** the editor/pipeline adds **no send path**. The Worker stays
  send-free (enqueue only); the privileged actuator is the Mac daemon (commit/deploy), not the cloud
  — the same trust posture as the existing pull model.
- **Adversarial Input Handling (Invariant 2):** the composed definition is operator-influenceable →
  server-side validated (meta-schema + the editor's semantic guards + CI), bound D1 params, type/length
  bounds; the `role`-gated `/api/admin/*` surface enforces admin-only authoring server-side.
- **§43 successor-remediation:** every Tier-2-reachable failure mode (a stuck publish, a failed
  deploy, a bad form live) ships a runbook entry (symptom → low-class repair → escalate-to-Seth).
  Note: publishing forms is **high-capability** (it deploys code) → likely a Tier-3 / co-resolution
  capability; the runbook documents the boundary.

## B11. Execution plan — tracer-bullet PR slices (CC/ultracode-optimized)

Ordered; each is an independently four-part-verifiable PR. Parallelizable groups noted.

1. **Catalog manifest + runtime wiring** — introduce the git-committed manifest; make the SPA
   picker + the Python renderers read the active-set/current-version/order from it (replacing the
   all-bundled derivation). *Foundational; everything else depends on it.*
2. **Read-only Forms tab + live preview** — admins view the catalog + preview any form via
   `FormRenderer`. (Parallel with #3.)
3. **Publish-request queue + Mac publish daemon + auto-deploy Action** — the pipeline backbone
   (D1 publish-request, the daemon sibling to `portal_poll`, the deploy-on-merge Action) + the
   **Publish Status Monitor** read view. *Foundational for ops.*
4. **Create-from-blank + Add-version (clone)** — the COEXIST operations + manual naming/uniqueness
   guards. (Depends on #1, #3.)
5. **Edit (version-bump + auto-swap + retire)** — the REPLACE operation. (Depends on #1, #3.)
6. **Delete = retire.** (Small; depends on #1.)
7. **Box historical archive as a pipeline step** — extend `generate_form_archive.py`; wire the
   "Archived" stage; the `_history/` retention. (Parallel-ish; depends on #3.)
8. **Session Hardening** — migration (users.session_epoch) + role-aware lifetime + 5-min admin idle
   (client + server) + #7 epoch revocation (logout/password-change bump). *Independent of the editor;
   can run in parallel with #1–#7.*

## B12. Acceptance criteria (high level)

- Admin composes a form from the closed vocabulary; invalid compositions are blocked pre-publish.
- Edit → a new version goes live to PMs automatically (few min), old auto-retired, version visible
  only to admins; old submissions still render.
- Add-version → a parallel variant coexists in the dropdown; base untouched.
- Delete → retired from the picker, recoverable, history intact.
- Every publish stamps the monitor through Queued→…→Archived; a failure shows red + reason and does
  NOT go live.
- The blank archive in Box reflects every active form + retains history (DR fallback verified).
- Admin sessions expire after 5 min idle; logout + password-change invalidate immediately;
  submitter 90-day sessions unchanged.
- The three renderers remain byte-unchanged; CI render-smoke passes for every published form.

## B13. Open decisions to confirm (the `⚙ CONFIRM` set)

1. `ITS_Forms_Catalog` (Smartsheet) — retire, or derive from the git manifest?
2. The auto-deploy-on-merge Action (widened blast radius) — approve?
3. Box archive layout (`_history/` subfolder vs versioned filenames).
4. Concurrent-edit rule (last-publish-wins vs lock).
5. CI validation for no-reference-PDF forms (render-without-error smoke) — sufficient?
6. 5-min admin idle — keep, and the "still there?" nudge.
7. Editor UX shape (the B8 builder) — sign off before building slice #2/#4/#5.
8. `source_pdf` optional + `archetype` required pick.

---

## Doctrine note

The **canonical Phase-2 mission** lands in the planning/blueprint layer (Session B) per CLAUDE.md;
this exec-repo brief is the **feeder**. The deferred audit **#7** carries here (B7). When the
mission is authored, it inherits B13's confirmed decisions.

---

# PART C — Red-team revisions (2026-06-08 adversarial Workflow critique)

A 5-lens adversarial Workflow red-teamed Part B (46 findings). This section corrects the
stale-context false-positives and folds in the real architectural fixes. **The canonical
mission must incorporate Part C, not just Part B.**

## C0. Stale-context corrections (findings that read the un-pulled `~/its` tree)
Several "blocker" findings assumed a pre-Phase-1 codebase. These are **already built** — the
editor builds ON them, no new substrate:
- **Role model EXISTS** — `users.role` + `requireRole("admin")` + the session+role-gated
  `/api/admin/*` surface (PR #195). The Forms tab lives behind `requireRole("admin")`.
- **Worker CI EXISTS** — the `portal` job (`npm ci` → `tsc` ×3 → `vitest` against real
  workerd + Miniflare D1, PR #195). Phase 2 EXTENDS it (C5), it doesn't create it.
- **`render_blank_fillable` + `generate_form_archive` EXIST** (PR-L #194). The Box "Archived"
  stage extends them.
- **An admin browser session EXISTS** (PR-2). Session hardening (B7) builds on it.

## C1. Files are append-only on disk; the manifest is the ONLY active-set gate
EDIT/retire/DELETE **never delete or rename** a form's `.json` — they flip the manifest's
active-set/current-version. `load_definition` resolves **every historical `form_code` forever**
(filed AND in-flight submissions). Both renderers read the manifest for the active set (SPA
`registry.ts` at build, Python `load_definition`). *Resolves: renderer skew window;
retire-breaks-in-flight; version-swap "atomicity".*

## C2. The Mac actuator daemon owns the WHOLE privileged sequence — NOT a GitHub Action
After it commits (+ merges), the **Mac daemon** itself: deploys via the **operator's local
wrangler auth** (the Cloudflare credential never goes on GitHub — consistent with the Mac-gated,
Send-Gate-mirroring model), **fast-forwards the live `~/its` tree** so `load_definition` sees the
new file, regenerates the Box archive, and stamps the monitor **"Live" only after a post-deploy
health check** (GET the live manifest/form). *Resolves: the broken "deploy lands the file on the
Mac" chain AND "no `CLOUDFLARE_API_TOKEN` in CI" — in one move. The auto-deploy is a daemon step,
not a CI Action.*

## C3. The SERVER + daemon are the safety boundary — not the editor
The Worker validates the composed definition (meta-schema + renderer-contract rules +
a **reserved-key denylist** incl. the envelope keys `job`/`work_date` + **cross-section-unique
keys**) at the `/api/admin/publish` enqueue, REJECTING before the D1 row is written. The Mac
daemon **re-validates against the live git tree + manifest** at commit (authoritative uniqueness
/ parent-variant check). The editor's client validation is **UX only**. Add hard bounds to the
meta-schema (max sections/fields/nesting). *Resolves: client-side-trust; validity≠renderability;
key collisions.*

## C4. Amend continuity across version bumps
`/api/recent` + the `submissions` cache key on the **version-independent identity**
(`parent_form_code`, stored as a column on `submissions`), NOT the versioned `form_code`. The
amend prefill **reconciles** prior values against the current definition's keys (drop removed,
default added) instead of a naive spread. *Resolves: EDIT bump silently severs amend continuity
+ stale-key leakage.*

## C5. CI gains a 3-renderer render-smoke + forms-validation
Every published form is validated (meta-schema + manifest invariants) AND rendered through **all
three renderers with realistic non-empty fixtures**, asserting **non-degraded** output (expected
section/field counts), not just "no exception." *Resolves: auto-merge ships an un-eyeballed form
that the under-covering smoke misses.*

## C6. Publish-request state machine + failure/recovery (the monitor is not happy-path-only)
Full terminal states (Validated / Tested / Merged / Live / Archived + **Failed@stage with reason
+ recovering actor**). Each stage **idempotent-resumable**; a per-row heartbeat/lease; on daemon
startup scan + resume/fail stuck rows; a **watchdog Check** for the new daemon's marker (mirror
Check C). CI-red / Box-upload-failure / merge-blocked → terminal **Failed + the operator CRITICAL
triple-fire** (never a silent red dot an idle-logged-out admin can't see). *Resolves: happy-path
monitor; silent Box-archive failure; daemon-crash-stuck-row.*

## C7. Rollback operation
Re-promote a prior version to the active set (**manifest-only**; files retained) — rides the same
pipeline + monitor; §43-documented as the response to "a just-published form is wrong."

## C8. Concurrency — serialize publishing per `parent_form_code`
Reject a 2nd Publish for a parent with a non-terminal request ("a publish for this form is in
progress"). Compute the version bump on the **Mac from git HEAD**, not a client counter.

## C9. In-flight submission policy
**Accept stale-version submissions** — `/api/submit` files against the `form_code` the client
submitted regardless of the current active set (enabled by C1's append-only files). Matches "old
submissions resolve against their filed version."

## C10. Session hardening mechanics (B7 detail)
`session_epoch` folds into the **existing `disabled` SELECT** (one query: `disabled + epoch +
role`). Migration default `0`; a cookie with **no epoch claim → treated as 0** (pre-#7 sessions
survive — don't mass-logout submitters). Migration applied **BEFORE deploy** (fail-closed order,
the 0006/0007 precedent). The 5-min admin idle is **decoupled from the publish operation's
lifecycle** — the publish is durable in D1, not session-bound; status-monitor polling is a passive
keepalive and a publish resumes after re-login. *Resolves: idle-logout-mid-publish; epoch
migration 401-trap.*

## C11. Box category mapping into the manifest
Move the parent→Box-category map into the manifest so create-from-blank assigns a category at
author time; `intake.py` reads it from the manifest, not the hardcoded dict.

## C12. RESOLVED (2026-06-08) = A — fully automatic, with a HIGH-guard-rails + detect-and-alert mandate
**Decision: A (fully automatic publish, NO human merge gate).** Rationale the operator gave: he will
NOT hand-review every form publish, so the safety lives in the AUTOMATED guard rails — which must be
built **very high, defense-in-depth**. **MANDATE (load-bearing — this is what makes A safe, not
optional polish):** (1) a bad/breaking publish MUST be caught + STOPPED by the gates — CI's 3-renderer
non-degraded render smoke (C5) + server/daemon-side schema validation (C3) + the repo's existing guard
rails (branch protection, the PreToolUse hooks, doctrine-drift + secret-scan CI); (2) the portal MUST
**detect publish progress + ALERT** (operator CRITICAL triple-fire + the status monitor going red) the
instant anything STOPS or fails the deployment — **no silent stall** (C6). Build the pipeline so
flipping to (B) one-click-approval later stays a one-line change.

Original framing (for the record): You chose **fully-automatic** (no human merge gate), reasoning the closed vocabulary makes output
structurally valid. The red-team + CLAUDE.md flag that form-publish **commits + deploys CODE**,
which doctrine classifies as the **highest capability class (Tier-3 — escalate to the
Developer-Operator)**. Two reconciliations, designed so it's a one-line switch:
- **(A — your stated choice)** Fully-automatic, made safe by server+daemon+CI validation (C3/C5),
  the Mac-gated actuator (C2 — deploy credential never on the cloud), append-only files (C1, no
  destructive deploy), and rollback (C7). Documented as a deliberate **per-category clearance by
  you** (the Developer-Operator).
- **(B — doctrine-aligned middle)** The Mac daemon opens the PR + the **deploy waits for your
  one-click approval** on the PR (mirrors the human-in-loop Send-Gate philosophy). Still
  "automatic-feeling" — one click, CI already green.
**RESOLVED = A (see the heading mandate above).** The guard rails + the detect-and-alert are the
load-bearing conditions; (B) one-click-approval remains a one-line switch if ever wanted.

## C13. Revised PR slicing (supersedes B11)
- ~~slice 0 (admin shell)~~ — already built (PR-2).
- **1a (smallest first PR, ~hours):** additive catalog manifest matching the current bundled set
  + a CI consistency check (form_code uniqueness, parent/variant, every active form_code resolves
  to a file). Pure data + test; de-risks the contract the editor writes.
- **1b:** flip `registry.ts` + `load_definition` to read the manifest for active-set/version/order
  (renderers byte-unchanged); fix the stale `registry.ts` `/api/forms` comment.
- **2:** read-only Forms tab + live preview (`FormRenderer`).
- **3a:** publish-request D1 table + Worker `/api/admin/publish` enqueue (server-side validation,
  C3) + the status-monitor read view (Worker-only, send-free).
- **3b:** the Mac publish daemon (commit + the configurable merge gate (C12) + deploy-via-local-
  wrangler + land-file-on-Mac + post-deploy health check, C2) — the privileged actuator.
- **3c:** the 3-renderer CI smoke + forms-validation (C5) — the auto-publish safety net.
- **3d (≡7):** Box historical archive as the "Archived" stage (extends `generate_form_archive`;
  `_history/` retention; failure = CRITICAL, C6/C11).
- **4:** create-from-blank + add-version (clone-as-template).  **5:** edit (version-bump + manifest
  swap + auto-retire).  **6:** delete = retire.  **rollback:** re-promote prior version (C7).
- **8a:** `session_epoch` (parallel anytime).  **8b:** admin 5-min idle timeout (C10).

The full 46-finding red-team output is at the workflow result path in the session transcript dir.
