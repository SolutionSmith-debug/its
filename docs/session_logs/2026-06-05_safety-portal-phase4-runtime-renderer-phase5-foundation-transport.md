---
type: session_log
date: 2026-06-05
status: closed
related_prs: [166, 167, 168, 169]
workstream: safety_portal
tags: [safety-portal, display-runtime, pdf-renderer, reportlab, hmac, transport, phase4, phase5, worker, d1]
---

# Session log — Safety Portal Phase 4 PR 2 → Phase 5 PR 2: display runtime, PDF renderer, WSR foundation, transport queue

Completed the two remaining Phase 4 PRs (definition-driven display runtime + Python
reportlab PDF renderer) and opened Phase 5 with two back-half foundation PRs (WSR human-review
sheet + Worker-side HMAC transport queue). All four PRs landed clean. The transport
architecture was ratified this session: Python PULL model (Worker stores; Mac-side
`portal_poll.py` drains), replacing the brief's email-shim approach. Phase 4 PR 1
(forms foundation) is already logged — see
[`2026-06-05_safety-portal-phase4-pr1-forms-foundation.md`](2026-06-05_safety-portal-phase4-pr1-forms-foundation.md).

## Commits / PRs landed

### PR #166 — feat(safety-portal): Phase 4 PR 2 — definition-driven display runtime

Squash `23af65f` on main. TypeScript display runtime consuming the meta-schema definitions
built in PR #164. New files and changes:

- **`safety_portal/src/forms/types.ts`** (NEW): TypeScript mirror of the meta-schema —
  discriminated union for all 7 section types, `FormDefinition` root type, `Submission`
  shape. Single source of type truth for the Worker and any future edge consumer.

- **`safety_portal/src/forms/registry.ts`** (NEW): Imports and bundles all 11 JSON
  definitions at build time; derives the parent/variant catalog used to populate the
  portal dropdowns. No runtime fetches — definitions are baked into the Worker bundle.

- **`safety_portal/src/forms/FormRenderer.tsx`** (NEW): Generic renderer for all 7
  section types (`header`, `repeating_table`, `signature_table`, `checklist`,
  `freeform`, `static_text`, `content_blocks`). Reuses `SignaturePad` for every
  `input:signature` field — SVG vector output. Skips the envelope keys `work_date` /
  `job` (runtime-bound, not in definitions). Renders tri-state (OK / NOT OK / N/A)
  and per-item scale-override checklist items correctly per the meta-schema.

- **`safety_portal/src/FormFillPage.tsx`** (MODIFIED): Job dropdown → form-type
  dropdown → conditional variant dropdown → `FormRenderer` render → submit. Amend
  prefill path. Replaces the hard-coded `JhaStubPage`.

- **`worker/index.ts`** (MODIFIED): Three session-gated endpoints added:
  `GET /api/jobs` (active job list for the job dropdown), `GET /api/recent`
  (worker's recent submissions for the amend prefill), `POST /api/submit`
  (type-checked, per-field length-bounded, `job_id` verified against D1). All three
  are send-free; no transmission capability.

- **`worker/migrations/0003_*.sql`** (NEW): D1 migration — jobs mirror table +
  submissions cache. Enables offline-capable job list and submission prefill.

- **`worker/migrations/0004_*.sql`** (NEW): Dev seed — populates jobs mirror with
  sandbox job data for `wrangler dev` smoke.

- **`worker/src/JhaStubPage.tsx`** (DELETED): Replaced entirely by the generic
  `FormFillPage.tsx` + `FormRenderer.tsx` pipeline.

Validated on `wrangler dev` + a Playwright smoke: Equipment → Skid Steer rendered
faithfully → submit → D1 captured the checklist responses.

---

### PR #167 — feat(safety-portal): Phase 4 PR 3 — Python render-parity PDF renderer

Squash `2946184` on main. Deterministic Python reportlab renderer with parity to the
TypeScript display runtime. New files and changes:

- **`safety_reports/form_pdf.py`** (NEW): `render_submission_pdf(submission, definition)`
  + `incomplete_checklist_items(submission, definition)` + `merge_pdfs(pdf_list)`.
  Constraints: NO AI / network / Smartsheet / Box calls — pure deterministic render.
  Mandatory and legal `static_text` sections rendered verbatim. SVG-path signatures
  embedded by parsing the `SignaturePad` M/L polyline into reportlab drawing commands.
  Checklist **N/A rendered visually distinct from blank** (not-inspected vs.
  deliberately-not-applicable). Malformed signature data → `logger.warning`, graceful
  skip. `merge_pdfs` concatenates Sat→Fri PDFs into a weekly packet via pypdf.

- **`tests/test_form_pdf.py`** (NEW): 18 parity tests — verifies the PDF output
  matches the display runtime's semantics across all 7 section types, N/A tri-state,
  SVG signature embed, legal text invariant, and `incomplete_checklist_items` logic.

- **`safety_portal/forms/equipment-skid-steer-v1.json`** (MODIFIED): Equipment
  tri-state amendment — Skid Steer checklist items changed from binary OK / NOT OK
  to tri-state **OK / NOT OK / N/A**, matching Telehandler's scale. Applied this
  session; definition, display runtime, and PDF renderer all updated atomically.

- **`pyproject.toml`** (MODIFIED): `pypdf` promoted from dev to runtime dependency
  (needed by `merge_pdfs` which runs in the main daemon path).

Validated by rendering a JHA and a Skid Steer PDF and viewing them: brand header,
tables, gold legal footer, vector signatures, N/A visually distinct from blank.

---

### PR #168 — feat(safety-portal): Phase 5 PR 1 — WSR human-review sheet + back-half foundation

Squash `ffad86b` on main. Deploy-independent foundation for the Phase 5 submission
pipeline. No live-daemon touch.

- **`scripts/migrations/build_wsr_human_review_sheet.py`** (NEW): Created the live
  **WSR_human_review** sheet (16 columns) in the standalone ITS — Safety Portal
  workspace's Safety Portal folder. Sheet supersedes `WPR_Pending_Review` for the
  portal submission flow. Editable Email Body column is the source of truth for the
  weekly summary email; human flips `Approve for Scheduled Send`; `MODIFIED_BY`
  auto-captures the approver identity; F22 (identity auto-capture) verified live.

- **`shared/sheet_ids.py`** (MODIFIED): Added `WORKSPACE_SAFETY_PORTAL`,
  `FOLDER_SAFETY_PORTAL` (with `FOLDER_OPERATIONS_SAFETY_PORTAL` alias for backward
  compatibility), `SHEET_WSR_HUMAN_REVIEW`. Fixed stale `ITS — Operations` comments
  on Safety Portal folder constants (folder was moved; IDs preserved — verified live).

- **`form_pdf.merge_pdfs()`**: `pypdf` promoted to runtime dependency so the merge
  path is available in the main daemon.

- **`docs/runbooks/safety_portal_submission.md`** (NEW): §43 successor runbook —
  procedures covering stuck submissions, the WSR review + approve + send cycle, and
  the escalate-to-Seth boundary for Worker or HMAC failures. Runbook Job ID format
  updated: 4-digit fill → 6-digit fill (amendment c).

1401 tests at merge.

---

### PR #169 — feat(safety-portal): Phase 5 PR 2 — Worker transport queue (HMAC + pull drain)

Squash `fc034eb` on main. Worker-side half of the Python PULL transport architecture.

- **`worker/index.ts`** (MODIFIED):
  - `POST /api/submit` now HMAC-SHA256-signs each submission payload
    (`crypto.subtle`) and stores the HMAC alongside the submission row.
  - `GET /api/internal/pending` (NEW): Bearer-gated queue drain endpoint — returns
    unacknowledged submissions with their HMACs for the Mac-side puller to verify and
    file. Digest-based, length-independent, constant-time bearer comparison
    (`timingSafeEqual`). Fail-closed (503) when the `HMAC_SECRET` environment
    variable is unset — the Worker cannot serve the queue without a signing key.
  - `POST /api/internal/mark-filed` (NEW): Receipt endpoint — flips `box_verified`
    and `filed_at` on a submission row after the Mac-side daemon confirms Box filing.
  - Worker remains send-free throughout — all transmission capability is on the Mac
    side. No `graph_client`, no `send_mail`, no Resend.

- **`shared/portal_hmac.py`** (NEW): Python HMAC contract for the Mac-side daemon.
  `canonical_payload()` / `sign()` / `verify()` — stdlib only (`hmac`, `hashlib`).
  `compare_digest` for constant-time comparison. Returns `False` on verification
  failure, never raises. Canonical payload serialization matches the Worker's
  signing order character-for-character.

- **`worker/migrations/0005_*.sql`** (NEW): Adds `hmac`, `box_verified`, `filed_at`,
  and `box_link` columns to the submissions table.

- **`worker/src/types.ts`** (MODIFIED): Updated submission and queue-drain response
  types for the new columns.

- **`.dev.vars.example`** (MODIFIED): Documents the two new required secrets
  (`HMAC_SECRET`, `INTERNAL_BEARER_TOKEN`). `.dev.vars` gitignored.

- **`tests/test_portal_hmac.py`** (NEW): 6 tests — round-trip sign/verify, canonical
  payload determinism, altered-payload rejection, compare_digest path.

**Cross-language validated on `wrangler dev`:** Python `portal_hmac.verify()` of the
Worker's `crypto.subtle` HMAC output = MATCH. The contract is proven end-to-end before
the Mac-side daemon (next PR) is built.

1407 tests at merge.

## CI runs / four-part verify

```
PR #166 — state MERGED · mergedAt 2026-06-05T16:29:58Z · mergeCommit 23af65f · main CI on merge commit SUCCESS (ci + Push on main)
PR #167 — state MERGED · mergedAt 2026-06-05T16:44:59Z · mergeCommit 2946184 · main CI on merge commit SUCCESS (ci + Push on main)
PR #168 — state MERGED · mergedAt 2026-06-05T17:18:51Z · mergeCommit ffad86b · main CI on merge commit SUCCESS (ci + Push on main + Graph Update pip)
PR #169 — state MERGED · mergedAt 2026-06-05T17:35:43Z · mergeCommit fc034eb · main CI on merge commit SUCCESS (ci + Push on main)
```

Per-session local validation gate before each merge:

- pytest: 1407 passed / 0 skipped (at final PR; stepwise increase through session)
- mypy: 0 errors / source files clean each PR
- ruff: clean
- main-branch CI on merge commit: SUCCESS (all four PRs)

`ops-stds-enforcer` pre-merge each PR: CLEAN — zero blocking findings. Worker remains
send-free; Python side Invariant 1 / Invariant 2 mechanics unchanged.

## Decisions made during session

1. **Transport architecture: Python PULL model (operator-ratified), not the brief's
   email-shim approach.**

   The brief proposed routing portal submissions through an HMAC-verified email shim
   (`portal-noreply@` → `safety@`) so the existing `intake_poll.py` daemon would
   consume them via the normal inbox. Rejected this session after a 6-auditor
   current-state audit mapped every Phase 5 seam.

   Adopted: the Worker stores submissions atomically in D1 on every POST
   (durable, always-on, decoupled from Mac-availability). A new `portal_poll.py`
   daemon (next PR) polls `GET /api/internal/pending` over HTTPS, verifies the HMAC,
   files via `intake.py` portal-marker branch, and POSTs the receipt to
   `POST /api/internal/mark-filed`.

   Rationale:
   - Reliable capture: D1 write is a local atomic Cloudflare operation; if the Mac
     is offline during a submission, the record is not lost — the puller drains it
     on the next cycle.
   - Worker stays send-free: no email/Resend/Graph calls on the edge; the
     attack surface at the Worker is smaller.
   - No email mailbox/domain/M365 dependency for the transport path itself.
   - Operator (verbatim): "that's how we've built everything" — capture is
     cloud-always-on; filing, rendering, and sending stays on the Mac. Write
     credentials, Box client, and the AI call remain on the Mac side, not the edge.
   - Human-in-loop (F22) preserved: `Approve for Scheduled Send` in WSR_human_review
     is still a manual human flip; `MODIFIED_BY` auto-captures approver identity;
     no auto-send path added.

   Alternative rejected: email shim. Failure mode: if the Mac is not polling when
   a submission arrives and the shim email ages out or is quarantined, the submission
   is silently lost. D1 + pull model has no silent-loss failure mode — submissions
   are durable until the receipt arrives.

2. **Equipment Skid Steer checklist: binary → tri-state OK / NOT OK / N/A.**

   Applied this session (amendment to PR #164's `equipment-skid-steer-v1.json`).
   Telehandler was already tri-state (OK / NO / N/A) in PR #164 — the inconsistency
   was identified during the display-runtime build when both forms were rendered
   side-by-side. Skid Steer was originally sourced from a PDF with binary (OK / NOT OK)
   checkboxes and no N/A column; the operator decision was to unify the scale to
   tri-state for operational consistency (some items are genuinely inapplicable
   depending on attachment configuration). The meta-schema, display runtime, and
   PDF renderer all updated atomically in PR #167.

   **N/A (deliberately not applicable, complete) remains distinct from blank
   (not inspected, incomplete) end-to-end:** in the meta-schema, in `FormRenderer.tsx`
   (renders N/A distinctly), in `form_pdf.py` (renders N/A distinctly), and in
   `incomplete_checklist_items()` (flags blanks, NOT N/A).

3. **WSR_human_review sheet in the standalone ITS — Safety Portal workspace
   (amendment b), not the Operations workspace.**

   The brief originally placed the WSR sheet in the Operations workspace alongside
   `WPR_Pending_Review`. Amendment b moved it to the Safety Portal workspace's
   Safety Portal folder — consistent with the Phase 3 ITS_Active_Jobs and
   ITS_Forms_Catalog placement; the Safety Portal has its own workspace for
   operator clarity. `FOLDER_OPERATIONS_SAFETY_PORTAL` alias preserved in
   `shared/sheet_ids.py` so any code referencing the old constant does not break.

4. **Pre-build 6-auditor current-state audit before Phase 5.**

   Before writing any Phase 5 code, a full current-state audit mapped every seam
   in the Phase 5 pipeline: Worker transport → Mac-side daemon → `intake.py`
   portal-marker branch → `safety_week.py` week bucket → `form_pdf.py` renderer →
   Box tree → receipt → weekly compile → human review → send. This surfaced the
   transport architecture decision above and identified `box_client` needing a
   `get_or_create_folder` primitive (currently stubbed as `canonical_job_path`).
   Cost: ~30 minutes. Benefit: all four Phase 5 PRs were scoped correctly with no
   mid-PR pivots.

5. **`MODIFIED_BY` column for approver identity (F22 preservation).**

   The WSR_human_review sheet includes a `MODIFIED_BY` column that Smartsheet
   auto-populates with the approver's identity when `Approve for Scheduled Send`
   is flipped. This was an explicit audit finding (F22) from the alignment audit
   (`2026-06-03_safety-portal-config-sheets-and-alignment-audit.md`). The
   `weekly_send.py` integration will read this column and include the approver
   identity in the send audit trail. No code change required in `weekly_send.py`
   to capture identity — it is platform-enforced.

## What was NOT touched

- Invariant 1 (External Send Gate) mechanics unchanged. `form_pdf.py` has zero send
  capability. `portal_hmac.py` has zero send capability. Worker PULL endpoints have
  zero transmission capability — the Worker cannot initiate outbound transmission.
- Invariant 2 (Adversarial Input Handling) mechanics unchanged. Form definitions are
  static JSON; the HMAC verification path does not process untrusted AI-call content.
- `intake_poll.py` — unchanged. The portal-marker branch in `intake.py` (PLANNED,
  not built) is Phase 5 PR scope.
- `weekly_generate.py` / `weekly_send.py` — unchanged. Phase 5 PRs 3–5 scope.
- No launchd plists added or modified this session. `portal_poll.py` daemon and its
  plist are the next PR.
- No doctrine or blueprint files touched. The §23 standalone-workspace doctrine bump
  (Op Stds v17) is flagged for the planning layer (not done here).
- `lint_doc_conventions.py` workstream set not updated to include `safety_portal`
  (pre-existing gap; carried forward to tech-debt).
- Cloudflare deploy not executed. The live Worker still runs the pre-Phase-4 code;
  the CLOUDFLARE_API_TOKEN provisioning session is still pending.

## Open items handed off

Phase 5 remaining work (all locally testable on `wrangler dev`; live-daemon blocked
on deploy session):

1. **`portal_poll.py` daemon** — Mac-side puller. Imports `shared/portal_hmac`;
   polls `GET /api/internal/pending`; for each submission verifies HMAC, routes to
   `intake.py` portal-marker branch (to be built in same PR); POSTs receipt to
   `POST /api/internal/mark-filed`. Modeled on `intake_poll.py`. Locally testable
   against `wrangler dev`.

2. **`intake.py` portal-marker branch** — HMAC→dedupe-on-UUID → `safety_week.py`
   week bucket → `week_folder.py` per-job/week Box tree → `form_pdf.render_submission_pdf`
   → Box upload → `form_pdf.merge_pdfs` (Sat→Fri) → receipt POST. Requires
   `box_client.get_or_create_folder` primitive (currently stubbed as
   `canonical_job_path`).

3. **`weekly_generate.py` narrative→PDF-merge + dual-write rollup** — week assembly
   sheet + `WSR_human_review` row. The compiled packet (per-submission PDFs merged
   via `form_pdf.merge_pdfs`) becomes the attachment.

4. **`weekly_send.py` attachment + WSR integration** — TO/CC from `ITS_Active_Jobs`
   active jobs + reads Email Body from `SHEET_WSR_HUMAN_REVIEW` + refuses-and-flags
   unapproved rows; Pacific-Monday send + watchdog catch-up.

5. **Deploy session** — Cloudflare API token provisioning, D1/R2 create, `database_id`
   placeholder in `wrangler.jsonc`, `HMAC_SECRET` + `INTERNAL_BEARER_TOKEN` (Worker
   secrets + Keychain mirror on Mac side).

6. **`box_client.get_or_create_folder` primitive** — needed by the portal-marker
   branch in `intake.py` for the per-job/week Box tree. `canonical_job_path` is
   currently a stub.

Known tech-debt items:

- **W2 Worker-side send-gate enforcement gap** — the Worker's `/api/internal/pending`
  and `/api/internal/mark-filed` endpoints do not enforce the capability-gating test
  (`test_capability_gating.py`). The Worker is TypeScript/Wrangler, outside the
  Python AST-scan. Documented in `docs/tech_debt.md`.
- **§23 standalone-workspace doctrine bump** — Op Stds v16 §23 does not yet account
  for the Safety Portal having its own workspace. Flagged for planning layer (Op Stds
  v17); not touched here.
- **ITS_Active_Jobs Address cells blank** — 6 cells; PM fills manually; carried
  from the 2026-06-04 session.
- **D1 dropdown sync (A.1.4)** — deferred to deploy session. Carried from PR #160.

## Cross-references

- Immediately prior safety_portal session log (Phase 4 PR 1 — forms foundation):
  [`2026-06-05_safety-portal-phase4-pr1-forms-foundation.md`](2026-06-05_safety-portal-phase4-pr1-forms-foundation.md)
- Prior safety_portal session log (Phase 3 contacts amendment):
  [`2026-06-05_safety-portal-phase3-contacts-amendment.md`](2026-06-05_safety-portal-phase3-contacts-amendment.md)
- Phase 3 job model session log:
  [`2026-06-05_safety-portal-phase3-job-model.md`](2026-06-05_safety-portal-phase3-job-model.md)
- Phase 2 Cloudflare scaffold session log:
  [`2026-06-04_safety-portal-phase2-cloudflare-scaffold.md`](2026-06-04_safety-portal-phase2-cloudflare-scaffold.md)
- Alignment audit (F22 origin):
  [`2026-06-03_safety-portal-config-sheets-and-alignment-audit.md`](2026-06-03_safety-portal-config-sheets-and-alignment-audit.md)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1
- Op Stds v16 §1 (External Send Gate — preserved; Worker is send-free)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD;
  `docs/runbooks/safety_portal_submission.md` ships as part of PR #168 definition-of-done)
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — W2 Worker-side send-gate gap; §23 doctrine bump; Address cells
- `safety_portal/forms/meta-schema.json` — the authoritative contract consumed by both
  renderers; N/A tri-state semantics defined here
- `shared/portal_hmac.py` — Python HMAC contract; cross-language validation against
  Worker's `crypto.subtle` confirmed on `wrangler dev`
- `scripts/migrations/build_wsr_human_review_sheet.py` — live migration; applied and
  verified against sandbox Safety Portal workspace
