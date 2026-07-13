---
title: "CC Brief — Per-job Smartsheet tracking + PO attachment field + PO delivery-contact autofill"
status: brief
audience: next-session Claude Code (Developer-Operator)
created: 2026-07-13
supersedes: none
---

# Next-session brief — three features (per-job sheets · PO attachment · PO delivery contact)

**Author context:** written 2026-07-13 at the end of a long build session (see "What landed this session"
below). Every file:line / sheet-ID / workspace-ID below was **verified against live HEAD this session** —
but per HOUSE_REFLEXES §1, re-`grep`/`Read` before editing (claims drift). Run `brief-validator` if unsure.

**Operator intent (verbatim, 2026-07-13):** "I do want each job that we generate them for to have them
tracked in the job specific folder and Smartsheet. This behavior should also extend to the purchase order
workflow." + "adding an Attachment field to the end of the purchase order workflow so we can attach documents
such as technical specifications or drawings, etc." + "a delivery contact auto fill function that can be
edited in the configuration page and allows for pre-selecting a contact or still the same free text."

**Ordering / independence:** the three features are independent — build in any order, each its own PR(s).
Feature A is the largest (live-Smartsheet SoR writes + a new shared scaffolding module). Feature B is a
trust-boundary (Invariant 2 / §34) build. Feature C is a config-editor (§50) extension. **Confirm the
open decisions (end of each section) with the operator before building the live-SoR / trust-boundary parts.**

---

## What landed this session (context — do NOT rebuild)

All four-part-verified clean (`state=MERGED` · `mergedAt` · `mergeCommit` · main-branch CI on the merge commit):

- **#554** PO/SC Config **tabs** (Purchase Order / Subcontract).
- **#556** **config-driven subcontract trade list** (`GET /api/subcontracts/trades` = manifest `trade_map` keys).
- **#557** **"New article template"** = exhibit `create_profile` (new trade + template, full-wired; `_SUBCONTRACTOR_TRADE_VALUES` now manifest-derived).
- **#558** raised the Exhibit A "Work" cap **8000 → 100_000** (electrical template is ~20k chars, was unsaveable).
- **#559** **required-field gates**: a blank `owner_entity`/`project_name`/`trade` (subcontract) or `terms_profile_id` (PO) is now refused at **Generate** (422 `missing_*`) + flagged in the builder `validate()`, instead of silently fencing at render. Guard is on the `/generate` route, NOT `parseDraftBody` (a partial draft still saves).
- **#560** **Delete-draft** action (hard, draft-only, no orphaned lines) + **prune** of stale `draft`/`canceled` rows at 90d — BUT only never-generated rows (`sc_number/po_number IS NULL`) so an allocated number is never freed for reuse (a worker-security BLOCKER caught + fixed).

**Deploy status:** all of the above need `cd ~/its/safety_portal && npm run deploy` (Worker + SPA). No migrations pending. The operator had NOT yet deployed as of session end — confirm before assuming the live portal reflects the above.

**Two diagnostics surfaced this session (operator-actionable, not code):**
1. The operator's test subcontract `sc_id=2` **fenced** (`subcontract_render_failed: missing owner_entity`) — a permanent one-shot flag; it never filed. #559 prevents the recurrence; the stuck row itself needs a new subcontract (or a manual D1 fence-flag clear).
2. **`ITS_Review_Queue` is at Smartsheet's ~20,000-row cap** (`errorCode 5634`) → fences/reviews can't be recorded system-wide. Watchdog **Check O** (row-cap rotation for ITS_Errors + ITS_Review_Queue) is evidently not clearing it — investigate separately (it blocks ALL workstreams' review recording).

---

## FEATURE A — Per-job Smartsheet folder + sheet (subcontracts + POs)

### Current state (verified)
- **Box: per-job folder ALREADY exists.** `subcontracts/subcontract_poll.py:975 _resolve_subcontract_box_folder(job_name)` and `po_materials/po_poll.py:891 _resolve_po_box_folder(job_name)` §45 find-or-create ROOT → per-job folder (by `safety_naming.job_folder_name`) → "Subcontracts"/PO subfolder. **Nothing to do on the Box side.**
- **Smartsheet: FLAT.** Both workstreams write one ledger row to a single flat sheet:
  - `subcontracts/subcontract_log.py` — `SHEET_ID = sheet_ids.SHEET_SUBCONTRACT_LOG` (= `1195034345951108`), `append_filed_row(...)` at line 123, called from `subcontract_poll.py:777`. Columns (`subcontract_log.py:42-56`): SC Number, Job / Project, Job ID, Subcontractor, Sub Key, Status, Total, Subcontract PDF, Supersedes, Superseded By, Terms Profile, Created By, Created At, Sent At, Notes.
  - `po_materials/po_log.py` — `SHEET_ID = sheet_ids.SHEET_PO_LOG` (= `3152487031721860`), `append_filed_row(...)` at line 119, called from `po_poll.py:710`.
  - There is **no per-job Smartsheet folder/sheet** — this is the gap.

### The mechanism to mirror (safety)
- `safety_reports/week_folder.py` — `find_or_create_folder` + `create_sheet_in_folder_from_template(folder_id, name, template_sheet_id, include=[])` (structure-only clone). `ensure_current_week_folder` is the reference (find-or-create folder + sheet, race-safe with a WARN-on-duplicate-folder post-create check).
- **KEY INSIGHT — no separate template sheet needed:** clone the **flat Log's own structure** as the per-job sheet template (`template_sheet_id = SHEET_SUBCONTRACT_LOG` / `SHEET_PO_LOG`, `include=[]`). The per-job sheet then has byte-identical columns automatically.
- **DIFFERENCE from safety:** safety pre-creates a folder per *known* project (`sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT` — a hardcoded dict of 6 projects). Subcontract/PO jobs are **dynamic** (from `ITS_Active_Jobs`), so the scaffolding must **find-or-create the per-job folder by job name** under a parent — exactly like the per-job Box folder already does. Reuse `safety_naming.job_folder_name(job_name)` for the folder name so Box + Smartsheet folders match.

### Workspace topology (verified)
- `WORKSPACE_SUBCONTRACTS = 6073264716965764`; `FOLDER_SC_CONTROL = 5896629078255492` (holds the flat Log + Pending_Review + ITS_Subcontractors).
- `WORKSPACE_PURCHASE_ORDERS = 6191118619568004`; `FOLDER_PO_CONTROL = 6619259473291140` (holds PO_Log + Pending_Review + vendors).
- **Neither workspace has a per-job area.** Add a **"Jobs" parent folder** to each workspace to hold the per-job folders.

### Design (additive — the flat Logs STAY; they mirror D1 and feed Pending_Review/send)
1. **New "Jobs" parent folder** in each workspace + `sheet_ids` constants `FOLDER_SC_JOBS` / `FOLDER_PO_JOBS`. Create via a new `scripts/migrations/build_job_folders.py` (operator-run against live Smartsheet; record the two IDs into `sheet_ids.py` — same pattern as every other `build_*` script).
2. **New shared scaffolding module** (parameterized; mirror `week_folder.py`) — e.g. `shared/job_sheet.py`:
   ```
   ensure_job_sheet(parent_folder_id, template_sheet_id, job_folder_name, sheet_name) -> int
   ```
   find-or-create the per-job folder (by `job_folder_name`) under `parent_folder_id`, then find-or-create a
   sheet `sheet_name` inside it cloned from `template_sheet_id` (`include=[]`); return the sheet ID.
   Idempotent + race-safe (WARN + return first match on duplicate folder, like `week_folder`). This is **new
   shared infrastructure → mandatory live smoke before merge** (feedback: mandatory-live-smoke).
3. **Parameterize the log-append to accept a sheet_id.** `subcontract_log.append_filed_row` and
   `po_log.append_filed_row` hardcode the module-level `SHEET_ID`. Add a `sheet_id: int | None = None`
   param (default = the flat Log) so the SAME builder can write to the per-job sheet. (Or extract a
   row-cells builder and call `add_rows` on both sheet IDs — cleaner if the append does more than build cells.)
4. **Wire into both daemons' filing paths** (idempotent, best-effort like the Box attach — a per-job-sheet
   failure must NOT fail the filing; Box + the flat Log are the SoR):
   - `subcontract_poll.py` after the flat-Log append (~line 777): resolve `job_folder_name` (same value the
     Box folder used), `sid = job_sheet.ensure_job_sheet(FOLDER_SC_JOBS, SHEET_SUBCONTRACT_LOG, job_folder_name, "Subcontracts")`, then append the same row to `sid` (guard on find-by-sc_number in the per-job sheet for idempotency, mirroring the flat-Log guard).
   - `po_poll.py` after the flat PO_Log append (~line 710): the mirror with `FOLDER_PO_JOBS`, `SHEET_PO_LOG`, `"Purchase Orders"`.

### Build order / discipline
- Python-source worktree with its OWN fresh venv (worktree_discipline). The daemons run the live `~/its`
  tree — do NOT edit Python source there.
- Registry reconciliation (HOUSE_REFLEXES §1): new `sheet_ids` constants; a new shared module → the
  "What's stubbed vs real" CLAUDE.md table row + `generate_config_dictionary._SCAN_ROOTS` if it reads config.
- **Mandatory live mirror smoke:** run `build_job_folders.py` on the mirror → generate + file a real
  subcontract AND a real PO → verify each creates its per-job folder + sheet (by job name) and appends the
  row there, AND the flat Log still gets its row, AND a second file of the SAME job REUSES the folder/sheet
  (idempotent, no duplicate). `sdk-integration-test-scaffold` for the new SDK-touching module.
- No new daemon → no watchdog/plist change. No D1 migration.

### Open decisions (confirm with operator BEFORE the live-SoR build)
- **Parent folder name** ("Jobs" assumed) + **per-job sheet name** ("Subcontracts" / "Purchase Orders" assumed).
- **Per-job folder name** = the job name (same as the Box per-job folder) — assumed, so Box + Smartsheet line up.
- Confirm **additive** (keep the flat Logs) — assumed yes (they mirror D1 + feed Pending_Review/send).

---

## FEATURE B — PO Attachment field (attach specs / drawings / docs to a PO)

**Operator intent:** "adding an Attachment field to the end of the purchase order workflow so we can attach
documents such as technical specifications or drawings, etc."

### This is a TRUST-BOUNDARY build (Invariant 2 / Op Stds §34) — treat accordingly
Any inbound file from the portal is **untrusted data**. It MUST be §34-screened on the Mac (never the Worker)
before it is filed to Box or attached to a Smartsheet row. **Adversarial review is definition-of-done**
(`portal-worker-security-reviewer` for the Worker upload route; `ops-stds-enforcer` for the §34 Python path).

### What exists to reuse — and the gap
- `safety_reports/photo_screen.py` is the canonical §34 instantiation, but it is **IMAGE-only** (JPEG/PNG
  magic, Pillow verify, decompression-bomb cap, forced re-encode, ClamAV-on-raw gated
  `safety_reports.photo_screen.clamav_enabled`). Its own header (`photo_screen.py:24`) notes **"§34 Layer 2
  was authored for PDF/Office attachments; it does NOT enumerate"** them. So PO doc attachments (PDF, Office,
  CAD/DWG, images) need the **arbitrary-file** §34 path, which per CLAUDE.md Invariant 2 Layer 6 is
  **"planned Phase 1.4, Email-Triage-bound" and NOT yet built**. This feature is the first real doc-attachment
  screener — scope it as such (do NOT hand-wave the screening).
- The **§34 Option-D screened-photo pool** pattern (`reference_section34-option-d-photo-pool` in memory; the
  safety submission `photo` header input, `worker/index.ts` bounds gate → D1 pool → Mac screen → Box) is the
  architectural template: **Worker bounds-gates + queues bytes in D1 (send-free); the Mac daemon screens then
  files.** Mirror that, generalized to documents.

### Design sketch (resolve the decisions first)
1. **Worker (`safety_portal/worker/po.ts` + a D1 migration):** a bounded upload route (size cap, count cap,
   allowed-MIME allowlist, magic re-check) that stores the attachment bytes in a D1 table keyed to the PO
   (draft or generated — decide), send-free. Bound SQL, mutation+audit atomic (W4).
2. **SPA (`PoBuilderPage.tsx`):** an Attachment field "at the end" of the builder — a file input (multi?),
   showing attached filenames, with client-side type/size hints (the Worker is the real gate).
3. **Mac screener (new module, `po_materials/po_attach_screen.py` or a generalized `shared/attach_screen.py`):**
   §34 sub-layers for docs — (a) magic-number + size + filename; (b) format-aware structural inspection
   (PDF `/JS`/`/JavaScript`/`/EmbeddedFile`/`/OpenAction`, Office macro/OLE); (c) ClamAV via pyclamd
   (config-gated, default OFF like photo_screen); (d) VirusTotal deferred. MALICIOUS → refuse before filing +
   CRITICAL + a `security_flag=True` Review-Queue row (mirror photo_screen's malicious disposition).
4. **`po_poll.py`:** screen each attachment → on clean, upload to the PO's per-job Box folder (reuse
   `_resolve_po_box_folder`) + attach to the PO Smartsheet row (mirror `subcontract_poll._attach_files_best_effort`,
   but FIX the MIME mislabel — see that fn's caveat: it hardcodes `application/pdf`; a `content_type` param is
   the deferrable follow-up that this feature should just do).

### Open decisions (confirm with operator)
- **Allowed file types** (PDF + images always; CAD/DWG? arbitrary Office?) — drives the §34 structural layer +
  the ClamAV posture. DWG/CAD has no cheap structural inspection → ClamAV + magic + size only, documented.
- **Attach at draft time or at generate/file time?** (draft = the attachment rides the draft row and files
  with the PO; simpler UX, but the D1 storage + prune interaction (#560) must be considered — a deleted draft
  must delete its attachments too).
- **Where stored:** D1 blob (chunked, like `filed_pdfs`) vs. direct-to-Box. D1-pool-then-screen-then-Box
  matches the §34 pattern (screen on the Mac before Box). Prefer that.
- Size/count caps.

---

## FEATURE C — PO delivery-contact auto-fill (config-editable; pre-select OR free text)

**Operator intent:** "a delivery contact auto fill function that can be edited in the configuration page and
allows for pre-selecting a contact or still the same free text."

### This is a config-editor (§50) extension + a new PO field — the smallest of the three
### What exists to reuse
- **§50 config editor rail** (this session's `project_config-editor-build` memory): `CONFIG_REGISTRY`
  (`safety_portal/worker/config.ts:58`) — `po_materials` artifacts are `purchaser`/`tax`/`terms`. New json
  artifacts slot in with the generic queue→actuator machinery. `po_materials/config/` holds `purchaser.json`,
  `tax.json`.
- The **§50 actuator** (`po_materials/config_apply.py` + `config_actuator.py`) validates + git-commits +
  deploys a config edit. The SPA config page is `safety_portal/src/pages/PoConfigPage.tsx` (now tabbed —
  #554; the PO tab).
- The **"pre-select OR free text"** UX = an HTML `<datalist>` combobox (a text input backed by a suggestion
  list) — the delivery-contact input suggests the configured contacts but accepts a free-text value.

### Design sketch
1. **New config artifact `delivery_contacts`** (json): `po_materials/config/delivery_contacts.json` — a list
   of `{ name, phone?, email?, address? }` (decide the shape). Add `delivery_contacts: { kind: "json" }` to
   `CONFIG_REGISTRY.po_materials` (config.ts) + a `_apply_delivery_contacts` validator in `config_apply.py`
   (mirror `_apply_purchaser_edit` — required/bounded fields). The Worker BUNDLES config at build time, so an
   edit is live after `npm run deploy` (see the config-editor memory KEY FINDING).
2. **Config page editor** (`PoConfigPage.tsx`, PO tab): an editor for the delivery-contact list (add/edit/
   remove entries) → POST `/api/config/requests` (the generic rail). **Do NOT hard-pin the list content in a
   test** (HOUSE_REFLEXES §5 — the self-defeating config-content-pin class; assert shape/round-trip, seed
   fixed fixtures).
3. **New PO field `delivery_contact`** on the PO record: a D1 column (migration) + `parseDraftBody`
   validation (bounded; likely optional or required — decide) in `worker/po.ts` + include it in the PO
   render (`po_materials/po_generate.py` + the PO template/`form_pdf`), so it prints on the PO.
4. **PO builder field** (`PoBuilderPage.tsx`): a "Delivery contact" input as a `<datalist>` combobox seeded
   from the served `delivery_contacts` config (a `GET` that serves the bundled list, like the served tax/
   terms) — auto-fills/suggests a configured contact, still accepts free text.

### Open decisions (confirm with operator)
- **Contact shape:** just a name string, or name + phone + email + address? (Is "delivery contact" the
  ship-to recipient printed on the PO, or an internal coordination contact?)
- **Required or optional** on the PO (drives whether it joins the #559 required-field gate).
- Where it prints on the PO document.

---

## Cross-cutting discipline (all three features)
- **Worktree + fresh venv** for any Python-source edit; SPA/Worker worktrees symlink `node_modules`
  (worktree_discipline). The live daemons run `~/its` from disk.
- **Four-part PR-landing verify**; autonomous-merge is authorized for slice PRs (feedback: autonomous-merge-authorized)
  but NOT doctrine bumps / preconditioned gate flips.
- **Adversarial review is DoD** on every trust-boundary surface — Feature B especially (`portal-worker-security-reviewer`
  + `ops-stds-enforcer`). Feature A's live-SoR writes + Feature C's new write-route also warrant a review pass.
- **Mandatory live mirror smoke** before merge for Features A + B (new shared infra / trust boundary):
  mocks-pass-but-live-API-rejects is a recurring class; the Smartsheet/Box/ClamAV live rejects are what a mock misses.
- **Registry reconciliation in the SAME PR** for every new package/sheet-id/config-artifact/D1-column/migration
  (HOUSE_REFLEXES §1) — `grep` the datum across every surface before claiming done.
- **Config gate rows** for any new dark-shipped capability must be SEEDED (even `=false`) so activation is a
  visible cell-flip (HOUSE_REFLEXES §5).

## Gate summary at session end (for the four-part on this session's PRs)
- pytest / mypy / ruff / worker vitest / spa vitest / build: all green on each merged PR (worker peaked at 1034, spa at 648).
- Op Stds is now **v21** live (blueprint); CLAUDE.md still cites v20 — a doc-reconciliation pass for Seth (not blocking these features).
