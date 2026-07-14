---
type: session_log
date: 2026-07-13
status: closed
workstream: null
related_prs: [562, 563, 564, 566]
---

# Session — ITS_Errors row-cap incident (Check O storm-mode fix) + per-job tracking / PO document attachments / delivery-contact autofill (PRs #562, #563, #564, #566)

Same-day continuation of the 2026-07-13 thread that also landed the doctrine elevation (#551/#553/#555,
its own log `2026-07-13_doctrine-elevation-v21-exec.md`) and the PO/SC config + builder hardening batch
(#552/#554/#556/#557/#558/#559/#560, its own log `2026-07-13_po-sc-config-and-builder-hardening.md`). This
log covers a live production incident found and fixed mid-session (Check O / ITS_Errors row cap) plus the
three Feature A/B/C build handed off in `docs/cc-brief_per-job-sheets-and-po-enhancements.md`. All four PRs
merged same-day; all four independently re-verified four-part clean against the live merge commit (not just
`gh pr view`'s triad) as part of drafting this log.

## Commits landed

- **#562** `ec25f94` — `fix(watchdog): Check O storm-mode fallback — rotation can never be pinned by the 90d
  retention (2026-07-13 ITS_Errors cap incident)`. Root cause was two structural facts compounding: (1)
  `SHEET_ROW_ROTATION_RETENTION_DAYS = 90` exceeds the system's ~8-week life, so nothing was ever
  age-eligible for rotation; (2) 5 unseeded `ITS_Config` rows drove a per-cycle `config_row_missing` WARN
  storm (~1,400–4,500 `ITS_Errors` rows/day). `ITS_Errors` hit the Smartsheet 20,000-row hard cap
  (`errorCode 5634`) on every `add_rows`; Check O fired CRITICAL "nothing deletable" two days running and
  deleted nothing. Fix: `_rotate_one_sheet` now re-selects at a new `SHEET_ROW_STORM_FLOOR_DAYS = 2` storm
  floor (via a shared `_select_rotation_eligible` helper) when the 90d pass yields zero eligible rows —
  same terminal-row exclusion, oldest-first order, batching, per-run cap; storm-mode rotation is WARN with
  a loud "STORM-MODE" note, CRITICAL now fires only when even the storm floor is empty. Also corrected a
  latent bug found live: `SHEET_ROW_ROTATION_DELETE_BATCH = 450` (commented as "the Smartsheet per-call ID
  cap") failed with HTTP 400 the first time a rotation ever actually deleted — 450 sixteen-digit row IDs in
  the SDK's URL query string exceed the URL length limit. Corrected to 200 (live-verified clean on the
  day's 13,815-row operator-approved drain), `MAX_BATCHES_PER_RUN` 10 → 23 to preserve the ~4,500-row/run
  budget. Same PR also landed `scripts/migrations/seed_daemon_gate_config.py` (durable seed for the 5
  previously-missing config rows that caused the WARN storm) and the same-PR VC-03 registry reconciliation
  (3 previously-unenrolled seeded keys asserted `non_empty`, never forced `true`).
- **#563** `09ab217` — `feat(tracking): per-job Smartsheet folder+sheet for subcontracts + POs (Feature A)`.
  New shared `shared/job_sheet.py` (`ensure_job_sheet`) — dynamic find-or-create of a per-job folder (named
  identically to the per-job Box folder via `safety_naming.job_folder_name`) under new "Jobs" parent folders
  (`FOLDER_SC_JOBS`, `FOLDER_PO_JOBS`, created live 2026-07-13 via `scripts/migrations/build_job_folders.py`)
  containing a sheet structure-cloned from the flat `Subcontract_Log`/`PO_Log`. `append_filed_row` and
  `find_row_by_*_number` in both logs gained an optional `sheet_id` so the identical write path serves the
  flat ledger (SoR) and the per-job mirror. `subcontract_poll`/`po_poll` each gained a step-9b best-effort
  fenced mirror append (`subcontract_perjob_sheet_failed` / `po_perjob_sheet_failed`, WARN) right after the
  flat-Log append — a mirror failure never fails the filing.
- **#564** `7e96736` — `feat(po): draft-time document attachments — §34-screened pool→Box (Feature B)`. The
  first real DOC-attachment instantiation of Op Stds §34 (`photo_screen` is the image-only sibling). Worker
  (`worker/po_attachments.ts`, migration 0053) is send-free bounds-only: draft-scoped upload/list/delete,
  10 MB/file, 5/PO, MIME-allowlist + magic-sniff, new `po-att:v1` HMAC domain binding content (not just the
  row). `po_materials/po_attach_screen.py` runs the Mac-side trust boundary — magic/consistency → PDF
  active-content scan / OpenXML zip-bomb+macro walk / Pillow verify+bomb-cap → ClamAV on raw bytes
  (config-gated OFF). `po_poll`'s new pass ①b claims pending attachments, reassembles + re-verifies the
  HMAC and sha256, screens, and dispositions: CLEAN → original bytes to Box + PO_Log attach + `filed`
  (Worker deletes chunks same batch); SUSPICIOUS → Review-Queue + `refused`; MALICIOUS → CRITICAL naming the
  uploading account + security-flagged Review-Queue row + `refused`, never filed; HMAC/digest mismatch →
  CRITICAL + one-shot flag, no disposition (bytes retained for forensics). Delete-draft (#560) and the 90-day
  prune both cascade chunks/attachment rows. Three review-driven fixes landed in the same PR before merge
  (see Decisions below): a same-named-attachment collision that silently dropped a Smartsheet attachment, a
  non-int `chunk_index` misclassification, and a bidi-filename / truthful-screening-posture pass.
- **#566** `2e141ca` — `feat(po): config-editable delivery-contact list + builder datalist autofill
  (Feature C)`. New §50 json config artifact `po_materials/config/delivery_contacts.json` (seeded empty);
  `config_apply._apply_delivery_contacts_edit` (name required, phone/email optional + bounded, case-
  insensitive unique names, 200-entry cap, empty-list valid); a `PoConfigPage` editor card; and a
  `<datalist>` on the builder's existing delivery-contact name input — an exact match auto-fills phone/email
  (never overwriting a non-empty value), free text always still accepted. **Scope was corrected against the
  brief before building** (see Decisions below) — no new PO field, no migration; the existing
  `delivery_contact_name/phone/email` columns (migration 0043) and the PR #504 job-stakeholder ship-to
  autofill are reused untouched. Merged against `origin/main` after #564; one same-location test-file
  overlap resolved keep-both.

## CI runs

Four-part verify re-run directly against each merge commit's own check-runs (`gh api
repos/SolutionSmith-debug/its/commits/<sha>/check-runs`), not just `gh pr view`'s state/mergedAt/mergeCommit
triad:

| PR | mergeCommit | mergedAt | `test` | `portal` | `secrets` | CodeQL ×3 | Verdict |
|---|---|---|---|---|---|---|---|
| #562 | `ec25f942` | 2026-07-13T18:39:29Z | success | success | success | success | four-part verify clean |
| #563 | `09ab2172` | 2026-07-13T19:11:00Z | success | success | success | success | four-part verify clean |
| #564 | `7e96736d` | 2026-07-13T23:08:32Z | success | success | success | success | four-part verify clean |
| #566 | `2e141caa` | 2026-07-13T23:27:40Z | success | success | success | success | four-part verify clean |

Per-PR gate lines, quoted from each PR's own "Gates" section:

**PR #562**
```
- pytest: 3268 passed / 48 deselected
- mypy: 0 issues / 361 files
- ruff: clean
- main-branch CI on merge commit ec25f94: SUCCESS
```
(re-run after the same-PR VC-03 registry addendum; unchanged)

**PR #563**
```
- pytest: 3285 passed / 48 deselected
- mypy: clean
- ruff: clean
- main-branch CI on merge commit 09ab217: SUCCESS
```

**PR #564**
```
- pytest: 3304 passed / 48 deselected
- mypy: 0 errors / 362 source files
- ruff: clean
- main-branch CI on merge commit 7e96736: SUCCESS
```
(plus Worker vitest 1058 passed / 63 files, SPA vitest 652 passed / 51 files, `npm run typecheck` clean —
this PR's diff spans the send-free Worker boundary)

**PR #566**
```
- pytest: 3358 passed / 49 deselected
- mypy: 0 errors / 368 source files
- ruff: clean
- main-branch CI on merge commit 2e141ca: SUCCESS
```
(plus Worker vitest 1063 passed, SPA vitest 661 passed, tsc clean, vite build clean — the session's peak
gate numbers, all four PRs' work integrated)

## Decisions made during session

1. **Storm-mode floor (2 days) added rather than redesigning the 90-day retention window.** The retention
   period itself is still correct policy (matches report-cadence discipline); the bug was that a fixed
   90-day floor is structurally dead for a system's entire first 90 days of life. A secondary, tighter floor
   that only engages when the primary pass finds zero eligible rows preserves the intended 90-day behavior
   once the system is old enough, while guaranteeing Check O is never powerless in the interim. The terminal-
   row exclusion (open CRITICALs, un-drained queue rows) stays inside the shared selection helper so the
   storm floor cannot widen it.
2. **The 450→200 delete-batch fix was found live, not by inspection.** Nothing had ever actually rotated
   before today (nothing was ever age-eligible), so the URL-length-overflow bug in the SDK's row-ID query
   string was latent since the constant was set. Corrected to the live-verified 200 and the docstring's
   incorrect "the Smartsheet per-call ID cap" claim about 450 was fixed alongside it — the mocks-pass-live-
   fails class the standard now names explicitly.
3. **Prove-the-control-bites was run before merge on #562.** All 5 new watchdog tests were run against the
   unmodified `_rotate_one_sheet` first — 4 correctly red-lighted (storm-mode didn't exist yet, so the
   incident shape returned CRITICAL and deleted nothing) before the implementation turned them green with
   zero test changes.
4. **The Feature-B attachment-name collision was a BLOCKER caught by review, not by unit tests.** Two
   same-named uploads on one PO filed as a single Box version and silently replaced the first Smartsheet
   attachment — a real data-loss bug unit tests structurally could not surface. Fixed by embedding the D1
   attachment id in the filed filename (`po_attachment_filename`) so every upload gets a distinct name while
   staying idempotent on crash-retry. Caught alongside it in the same review pass: a non-int `chunk_index`
   now classifies as an INTEGRITY failure (CRITICAL + one-shot flag) instead of falling through to the
   generic exception fence, plus a bidi-filename rejection and a pass correcting the screening-posture
   language to be truthful about what the scanner does and does not catch (feeding directly into ATT-5/ATT-6
   below).
5. **PDF/OpenXML screening depth deliberately stopped at marker/structural scanning, not a full parser.**
   The PDF active-content scan is blind to `/ObjStm`-compressed object streams (the default output of modern
   PDF producers); the OpenXML walk inspects zip entries and known-dangerous parts but not in-content
   constructs like DDE field codes. The operator's accepted posture: PO attachments are a limited-blast-
   radius, limited-access workflow, and the real controls are that access boundary plus the optional ClamAV
   layer — not a deep parser. Recorded truthfully as accepted limitations (`docs/tech_debt.md` ATT-5, ATT-6)
   rather than silently shipping a scanner whose coverage claim overstated what it does, or spending the
   session building a full parser for a threat model the operator judged out of proportion to the access
   boundary.
6. **Feature C's brief was corrected before building, not built as specified.** The brief
   (`docs/cc-brief_per-job-sheets-and-po-enhancements.md`, item 3) called for a new `delivery_contact` PO
   field plus a D1 migration. A brief-validator pass confirmed the field already exists end-to-end —
   migration 0043 columns, `parseDraftBody` validation, `po_generate.py` render, and the builder section
   (PR #504's job-stakeholder autofill) — so #566 built only the genuinely new scope: the config artifact,
   the config-page editor, and the datalist autofill. No new field, no migration. Zero grep hits on the
   claimed gap was decisive over the brief's confident framing (HOUSE_REFLEXES §1).
7. **Two merge-conflict resolutions were both keep-both, and the tangled one was read carefully rather than
   auto-resolved.** #566 merged against `origin/main` post-#564; the one real overlap (a same-location new
   `describe` block appended to `PoBuilderPage.test.tsx`) resolved as both blocks back-to-back. Earlier in
   the day, a `po_poll.py` conflict between the Feature A per-job mirror and other in-flight work had a
   shared tail that, resolved naively, would have left an `error_log` call syntactically unterminated —
   flagged and hand-resolved correctly rather than accepting either side wholesale.
8. **The 20,000→6,185-row `ITS_Errors` drain was an operator-approved manual remediation step, distinct from
   the code fix.** 13,815 terminal (>48h old) rows were deleted live to restore write capacity immediately,
   rather than waiting for the fixed Check O to work through the backlog on its own rotation cadence — the
   code fix (storm-mode) prevents recurrence; the drain resolved the already-full sheet.

## Open items handed off

- **Deploy pending.** `wrangler d1 migrations apply` (0053, `po_attachments`/`po_attachment_chunks`) then
  `npm run deploy` from `safety_portal/` — carries #554–#560 plus Features B and C. Everything in this
  session ships **dark** (`po_materials.po_attach_screen.clamav_enabled=false`, per-job mirror passes and the
  attachment pass all gated on existing `polling_enabled` flags). The operator is running this post-session;
  not done as part of this log.
- **Combined live smoke, post-deploy** — the open verification: file a real attachment end-to-end (Worker
  upload → D1 pool → `po_poll` claim → `po_attach_screen` → Box + PO_Log), and confirm the delivery-contact
  datalist autofill round-trips a configured contact into a real PO draft.
- **`ITS_Review_Queue` nearing its own ~20,000-row Smartsheet cap** (`errorCode 5634`) — flagged in the prior
  same-day `2026-07-13_po-sc-config-and-builder-hardening.md` log, not investigated or fixed by this session.
  Watchdog Check O rotates `ITS_Errors`; whether an equivalent rotation check covers `ITS_Review_Queue` is
  the open question.
- **po_poll / subcontract_poll per-job mirror pass and the attachment pass remain dark** — activation is a
  separate future step once the operator has confirmed the post-deploy live smoke.
- **ATT-1 (VirusTotal, §34 Layer 4) and the ATT-5/ATT-6 accepted-limitation screening gaps** — deferred to
  the Phase-2 §34 hardening pass per `docs/tech_debt.md`; not a blocker for this ship.

## What was NOT touched

- **No live deploy performed by this session** — migration 0053 + `npm run deploy` are the operator's
  post-session step.
- **No ClamAV/VirusTotal wiring** for the new attachment screener (config-gated OFF; ATT-1 deferred).
- **No deep PDF/OpenXML content parser built** — the marker/structural-scan posture is a documented,
  operator-accepted limitation (ATT-5, ATT-6), not a gap this session tried to close.
- **`ITS_Review_Queue`'s own row-cap risk was not investigated** — noted as an open item, not this session's
  scope.
- **No activation of the per-job mirror or attachment polling passes** — both remain `polling_enabled=false`
  pending the post-deploy smoke.
- **PR #552 (Exhibit A PR-B2) is same-day but not part of this log** — it landed earlier in the day under a
  different thread; not re-narrated here.

## Lessons for memory capture (recommended, not applied by this pass)

Session-log-writer scope is the log itself; the following are flagged for the session-close-maintainer /
doc-reconciliation pass to fold in, not already written to memory by this agent:

- The Check O storm-mode class — "a fixed retention/staleness floor can be structurally dead for a system's
  entire early life; add a secondary floor that engages only when the primary pass finds nothing eligible"
  — is a reusable pattern for any other age-gated rotation/retention check, not just `ITS_Errors`.
  `docs/HOUSE_REFLEXES.md` §2 (mocks-pass-live-fails class) already covers the 450→200 batch-size finding;
  worth a one-line cross-reference from there to this incident as the concrete instance.
- `~/its-blueprint/references/memory-archive.md` — a new `§G` entry naming the incident (root cause, fix,
  drain) plus the Feature A/B/C build, so a fresh session doesn't have to reconstruct today's four PRs from
  transcripts.
- Auto-memory `project_aug7-delivery-program.md` and the per-job-sheets brief entry
  (`docs/cc-brief_per-job-sheets-and-po-enhancements.md`, already indexed in `MEMORY.md`) should be marked
  BUILT/landed now that all three features merged — the current index entry still frames it as "briefed,
  not built."

## Cross-references

- `docs/cc-brief_per-job-sheets-and-po-enhancements.md` — the originating brief for Features A/B/C.
- `docs/session_logs/2026-07-13_doctrine-elevation-v21-exec.md` — same-day, earlier thread (#551/#553/#555).
- `docs/session_logs/2026-07-13_po-sc-config-and-builder-hardening.md` — same-day, earlier thread
  (#552/#554/#556/#557/#558/#559/#560).
- `docs/tech_debt.md` — "PO attachments (Feature B) — conscious deferrals" section (ATT-1 through ATT-6, all
  new 2026-07-13).
- `docs/runbooks/po_poll.md` — Symptom 13 (per-job tracking sheet mirror failure, new this session).
- `docs/runbooks/config_actuator.md` — delivery-contacts config artifact, added this session.
- `docs/operations/pr_merge_discipline.md` — the four-part verify discipline applied above.
- `docs/HOUSE_REFLEXES.md` §2 — "mandatory live smoke" / "prove the control bites" reflexes, both exercised
  directly by #562's test-first storm-mode work and by #563's mandatory live mirror smoke before merge.
