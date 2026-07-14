# ITS — Tech Debt

Items deliberately deferred. Each carries the rationale for deferral and the trigger for revisiting. The repo-side companion to Master Checklist §6 (planning project) — this file holds execution-layer tech debt; the Master Checklist holds owner-decision tech debt.

When to add an entry: a session deliberately chooses preservation-over-refactor (per Op Stds v11 §14), discovers an external-API constraint that forced a workaround, or defers a non-trivial cleanup that's larger than the current session can absorb.

**Split 2026-07-12:** resolved/closed/delivered/superseded entries now live in [`docs/tech_debt_closed.md`](tech_debt_closed.md) (archive) so this file stays under the 256 KB cap — this file holds only OPEN items. When an entry closes, **move** it to the archive with resolution detail (don't delete — history is cheap, context is expensive).

**Cutover triage:** every open entry below is **post-delivery** unless its header is prefixed **`[CUTOVER-BLOCKING]`** (must resolve before the Aug-7 production cutover). The authoritative cutover gate is `docs/operations/cutover_checklist.md` (CL-01…CL-39) + `scripts/verify_cutover.py`, not these tags — the tags are prioritization only.

## WS2 operator dashboard — completion parked items [OPEN 2026-07-13]

From the dashboard-completion session (Blocks 1-5 landed #567/#570/#574/#576). None blocks the ship; each
is a deliberate scope line:

- **WS2-1 — RESOLVED 2026-07-14.** No Canva export was needed — the Safety Portal brand already had the
  vector + font. Pulled `evergreen-logo.svg` + `great-vibes.woff2` (+ OFL license) from `safety_portal/public/`
  into `operator_dashboard/static/` and wired the real lockup into the dashboard header: the Evergreen mark on
  a gold-bordered white plate + the "Integrated Technical System" gold-gradient Great Vibes script (the
  portal's exact treatment incl. the WebKit background-clip cap-loop padding fix).
- **WS2-2 (doc-sync) — CLAUDE.md "stubbed vs real" dashboard row is stale.** It still reads "No launchd plist
  yet (D1-3b)"; the plist + the six §44 verbs now exist. Parked (CLAUDE.md was a high-contention shared file the
  sibling session was also editing) — fold into the next doc-reconciliation pass. Same for the
  `scripts/verify_cutover.py` "no plist yet" comment.
- **WS2-3 (doc-sync) — the enablement guide predates Blocks 2-5.** `docs/enablement/operator_dashboard.md`
  (#572) documents D1-x only; it needs a delta for the launchd service, the interval-edit / daemon-control /
  breaker-clear verbs, the send-queue + audit panels, and the brand. Trigger: the next A8 enablement pass.
- **WS2-4 (Seth decision, by design) — no mutating send-lane verb.** The send-queue panel is read-only;
  bulk-approve / resend-FAILED / clear-HELD are deliberately NOT built (D13). Trigger: an explicit operator
  decision to expose a send-lane action (would need its own adversarial review).

## PO attachments (Feature B) — conscious deferrals [OPEN 2026-07-13]

From the Feature-B build (PO document attachments — the §34 doc-attachment pool → Mac screen →
Box pipeline). None blocks the ship; each is a deliberate scope line:

- **ATT-1 (doctrine-aligned) — VirusTotal (§34 Layer 4) not wired.** Op Stds §34 defers it to
  Phase 2+; `po_attach_screen` runs L1–L3 only (ClamAV config-gated OFF). Trigger: the Phase-2
  §34 hardening pass wires VT hash-lookup for BOTH photo_screen and po_attach_screen together.
- **ATT-2 (LOW) — encrypted OpenXML containers are not specifically classified.** A
  password-protected .docx/.xlsx either fails the zip walk (→ suspicious, refused-to-review) or
  walks by entry NAMES only (macro/executable name detection still holds) with content
  inspection impossible. Acceptable: the operator's own spec docs are not expected encrypted.
  Trigger: a real encrypted-attachment workflow.
- **ATT-3 (LOW) — attachments upload as ONE JSON request (≤10 MB decoded).** The Worker chunks
  into D1 rows server-side; there is no SPA-side chunked/resumable upload. Fine at the locked
  10 MB cap; a future cap raise needs an upload-session pattern (mirror filed_pdfs in reverse).
- **ATT-4 (BY DESIGN) — attachments on a PO canceled BEFORE filing are never screened/filed.**
  The internal pending route serves only FILED parents (pending_review+); a queued→canceled
  PO's attachment bytes sit in D1 until the prune's canceled-PO chunk hygiene (90d past
  updated_at) drops them. The byte-free rows remain as the forensic manifest. Revisit only if
  cancel volume makes 90d of latent bytes a real size concern (the prune's size tripwire now
  samples po_attachment_chunks).
- **ATT-5 (ACCEPTED LIMITATION, operator posture 2026-07-13) — the PDF active-content scan is
  blind to /ObjStm compressed object streams + compressed xref.** `po_attach_screen._scan_pdf`
  is a raw-byte marker scan (plus #xx name-escape normalization) — NOT a PDF parse. Markers
  inside flate-compressed object streams (the DEFAULT of modern PDF producers) are invisible
  to it, and we deliberately do NOT flag ObjStm-bearing PDFs (that is most legitimate modern
  PDFs — flooding the review queue would break the workflow) or build a deep parser. The
  operator's accepted posture: PO attachments are a limited-blast-radius, limited-access
  workflow — the real controls are that boundary + the optional ClamAV layer. The in-code
  honesty note lives on `PDF_ACTIVE_MARKERS`. Trigger: the Phase-2 §34 hardening pass (with
  ATT-1's VirusTotal), or a widening of who can upload.
- **ATT-6 (ACCEPTED LIMITATION, operator posture 2026-07-13) — OpenXML content-level vectors
  beyond macros/rels/OLE-parts are not inspected.** The zip walk now catches vbaProject.bin
  (malicious), nested executables (malicious), `TargetMode="External"` rels naming an
  attachedTemplate/oleObject (suspicious), and `embeddings/oleObject*.bin` parts (suspicious)
  — but DDE field codes inside document.xml (and other in-content constructs) are NOT parsed.
  Same limited-blast-radius rationale as ATT-5; the in-code note lives in the module docstring
  + `_scan_openxml`. Trigger: same as ATT-5.

## Subcontracts — SC-S3c adversarial-review follow-ups (non-blocking) [OPEN 2026-07-11]

From the SC-S3c verify phase (portal-worker-security-reviewer + ops-stds-enforcer + completeness critic;
all three verdicts CLEAN/WARN, no BLOCK). Deferred deliberately — none blocks the dark ship:

- **SC3c-1 (LOW, shared with PO) — supersede double-submit dup-guard is check-then-act, not atomic-in-WHERE.**
  `worker/subcontract.ts` `POST /:id/supersede` pre-`SELECT`s for an in-flight successor (`WHERE
  supersedes_sc_id=?1 AND status!='canceled'`) then acts — a tight double-click / replay by a
  `cap.subcontracts.manage` holder could mint two live successors for the same slot (each still passes
  its own SOV/HMAC/F22 gates, so it's a business-logic idempotency race, not an auth/money bypass; damage
  ceiling = a human cancels one draft). This is **verbatim inherited from `worker/po.ts:1113-1119`** — SC-S3c
  faithfully mirrors the reviewed PO pattern rather than diverging. **Fix belongs to BOTH** (fold the dup
  check into the clone `INSERT…SELECT`'s WHERE via `AND NOT EXISTS (SELECT 1 FROM <t> WHERE
  supersedes_*_id=?1 AND status!='canceled')`, then a post-insert SELECT only to disambiguate the 409
  message) — a shared po.ts+subcontract.ts change with its own PO re-review, out of SC-S3c's scope.
- **SC3c-2 (COSMETIC) — `migrations/0050_subcontracts.sql` header comment overstates a Worker gate.** It
  credits the Worker with asserting the §2.1 spelled-out price WORDS match the figure; that check is
  actually the Python render step (`subcontract_generate` via num2words), not a pre-queue Worker gate.
  Not a hole (the check exists in the pipeline), but a stale comment on a money/legal boundary. 0050 is a
  merged+applied migration — fix the comment only alongside a genuine 0050 touch (editing an applied
  migration file in isolation risks the migration-tracking / doc-currency sha).
- **SC3c-3 (LOW, forward-looking for SC-S4) — the SOV `.xlsx` Box file id is discarded.** `subcontract_poll`
  files both `.docx`+`.xlsx` but only tracks the `.docx` id as `box_file_id`; the `.xlsx` lives in Box under
  its deterministic `sc_xlsx_filename` with no ledger/D1 handle. Correct for S3c (the reviewer gets both via
  the inline attach); SC-S4's send — which will attach BOTH — must re-derive `sc_xlsx_filename` to locate it.
- **SC3c-4 (LOW, shared) — the daemon-scaffold subprocess-AST guard doesn't cover `subcontracts/`** (nor
  `po_materials/`; `tests/test_daemon_scaffold.py` `DAEMON_ROOT = safety_reports` only). Zero current
  exposure (`subcontract_poll` spawns no subprocess). Widen `DAEMON_ROOT` to a root LIST covering the
  daemon-bearing packages if/when that guard is generalized — a shared change, matches the existing PO gap.

## Subcontracts — PO/SC Configuration + builder follow-ups [OPEN 2026-07-12]

From the Office Operations nav / PO-SC Configuration session (PRs #541/#542/#546, plus the HELD PRs
#544/#548). None blocks the dark ship; PR-B2 below is the remaining operator-directed build item, not a
bug.

- **SC-CFG-1 (INFORMATIONAL, non-blocking) — `attach_reference.md` won't auto-flag as diverged if a
  future `standard_subcontract_v2` ever changes the preamble/§2.1 wording.** PR #544 (HELD for operator
  merge — touches ADR-0003 + the manifest description) fixed a real fence: an `attach`-kind terms profile
  (`negotiated_msa`) had no library text to load, so `render_body_text` raised and a valid negotiated-MSA
  subcontract could never file. Fix renders a one-page reference body from a new sha-pinned
  `subcontracts/terms/attach_reference.md` — PURE VERBATIM fragments lifted from the `standard_subcontract`
  body's preamble + §2.1 + signature block (an earlier draft with paraphrased/invented clauses was BLOCKED
  by ops-stds review and rewritten to pure-verbatim before re-review cleared it), so it correctly carries no
  independent legal-review gate of its own. **The residual:** `attach_reference.md` is pinned by its own
  `terms._ATTACH_REFERENCE_SHA256` module constant, frozen at v1-era wording. If `standard_subcontract` is
  ever bumped to a v2 with different preamble/§2.1 text, nothing re-checks `attach_reference.md` against the
  new wording — it just keeps rendering the frozen v1 fragments, consistent with the existing
  immutable-pin-per-version pattern elsewhere in the manifest, but silently so for this one file. **Trigger:**
  only relevant the day a `standard_subcontract_v2` is minted — worth an ADR-0003 note or a cross-check at
  that point, not before. **Tag:** `subcontracts`, `terms`, `legal-gate`, `informational`.
- **SC-CFG-2 (COSMETIC) — `worker/index.ts`'s `/api/internal/sync` address bound hardcodes the literal
  `512` instead of importing the shared `MAX_ADDRESS` constant.** PR #548 (HELD for operator deploy +
  live-smoke — touches the Worker) added `address` to the `ITS_Active_Jobs` down-sync payload and bounds it
  at `address.length > 512` inline in `index.ts`. The same `512` value is already defined as `MAX_ADDRESS`
  independently in three other Worker files (`po.ts`, `subcontract.ts`, `fieldops_job_write.ts`) —
  duplicated, not shared, across all four sites. Zero behavioral drift today (all four agree at 512), but a
  future bump to any one site without the others is a latent inconsistency. **Fix:** hoist `MAX_ADDRESS` into
  a shared Worker constants module and import it at all four call sites (a small, contained refactor — no
  functional change). **Tag:** `subcontracts`, `worker`, `cosmetic`, `low-severity`.
- **PR-B2 (the remaining Exhibit-A + payment-terms build, operator-directed, NOT started) — Exhibit-A
  versioned+gated editing + subcontract payment-terms editing + a `config.ts` comment fix.** Mapped this
  session (Explore agent) as one LARGE, atomic Python+worker+SPA change, deliberately left for the operator's
  presence because it needs a worker deploy AND a Layer-A legal-attestation seed:
  1. Restructure `subcontracts/exhibit/manifest.json` `trade_templates` from flat `{file,sha256}` to
     versioned `{current_version, versions:{vN:{file,sha256,legal_review}}}` — requires seeding the 7
     existing trade templates `legal_review=cleared` (an operator Layer-A attestation, the same pattern used
     for `standard_subcontract` v1, `95a01cb`).
  2. `exhibit.py`'s loader + the `subcontract_docx` renderer's pin-resolution add a legal gate to the LIVE
     render path (currently exhibit.py has no such gate — a known WARN from PR #538's review, intentional at
     the time since Exhibit A is operator-authored per-trade Article II, not independently-drafted legal
     text like the standard body).
  3. `config_apply.py` gains `_apply_exhibit_*` handlers reusing the existing `add_version`/`set_current`/
     `create_profile` op shapes — no new D1 migration needed.
  4. `config_actuator.py`'s `_MANAGED_PATHS` + `_MANAGED_TERMS_DIRS` add `subcontracts/exhibit`.
  5. Worker `config.ts` gains an `exhibit` artifact kind + registry entry + a kind→op branch rework (new
     `EXHIBIT_OPS`); `worker/subcontract.ts` gains new serve routes (list template keys + get text by
     key/version) — **atomic with the manifest schema change**, because the worker build-imports
     `exhibitManifest` directly (the same "Worker bundles config at build time" constraint noted throughout
     this doc).
  6. `subcontracts.ts` SPA fetchers + a NEW exhibit-editor block in `PoConfigPage` — NOT the shared
     `TermsProfilesEditor` (exhibit is keyed per-trade, not per-profile).
  7. Payment-terms editing (CE-7 above) folds in here too, once the served `/api/subcontracts/config` route
     exposes the day-fields.
  **Trigger:** next dedicated subcontracts-config session, operator present for the deploy + the
  legal-attestation seed. **Tag:** `subcontracts`, `config-editor`, `exhibit-a`, `deploy-gated`,
  `legal-gate`, `not-started`.

## Config editor (§50) — deferred follow-ups [OPEN 2026-07-10]

From the slice-2 (`config_actuator`) build + adversarial review (PR #509):

- **CE-1 (LOW, defense-in-depth) — §54 redact parity on the daemon `_fail` stamp legs. RESOLVED 2026-07-13.**
  `safety_reports/publish_daemon._fail` now applies the byte-identical `redact(reason[:1800])` that
  `config_actuator._fail` already had, so the `stamp_publish(..., failure_reason=reason)` leg — which lands on
  the portal Status Monitor, a sink that BYPASSES error_log's redact choke — no longer egresses an accidental
  token/PII from an `_exc_reason` subprocess-stderr tail. Proven by `tests/test_publish_daemon.py::
  test_fail_redacts_a_secret_bearing_reason_before_egress` (RED on the pre-fix unredacted line). The stale
  "tracked follow-up" comment in `config_actuator._fail` was updated to note parity. (The broader "redact at
  the `portal_client.stamp_*` call sites so every future daemon inherits it" idea is left as a future
  refactor, not this fix.) Sweep to `tech_debt_closed.md` in the follow-up doc-hygiene pass.
- **CE-2 (legal-gate depth) — render-side `legal_review` refusal (Layer A). RESOLVED 2026-07-10 (slice T2).**
  `terms._version_entry` now REFUSES a library version whose `legal_review != "cleared"` — the single choke
  point shared by `load_terms_text` + `required_tokens`, firing on an explicit pin OR the `current_version`
  default. A mis-bumped `current_version` can no longer render an un-reviewed version; it raises + fences the PO
  (`po_poll` → Review Queue). Shipped with the two required predecessors, in lockstep: (1) the two shipped
  versions (`standard_17_v1`, `chint_vendor_v1`) were backfilled to `legal_review:"cleared"` in the same change
  (operator-confirmed clearance), so no live PO fences; (2) the `set_current` make-current op + a confirmable
  portal control ("I've reviewed this — make it live") is the activation path (clears legal_review + advances
  `current_version` through the config actuator). The **legal judgment** it encodes stays a §44 high-class call
  (Seth / legal), training-enforced per the §43 runbook — the control is the mechanism, not a re-delegation.
- **CE-3 (was HIGH, blocked the editor entirely) — self-defeating CI test class recurs: CI hard-pins the
  live editable config content, so a legitimate purchaser/tax edit cannot merge. RESOLVED 2026-07-10
  (session 2) — PR #514 (`ca9c776`).** Discovered 2026-07-10 during the first live activation smoke: the
  operator edited the purchaser's `invoice_routing.to`, the actuator queued → committed → opened
  **PR #511** (`chore(po-config): purchaser: Evergreen Renewables LLC -> config_version 2 (req 1)`) — and CI
  red-lit at the `tested` stage, so the daemon's `_wait_for_ci` never advanced it past `validated`/`tested`
  and the edit was permanently stuck. Root cause: `safety_portal/test/po.test.ts:222-223` asserted the exact
  live-bundled purchaser entity + `invoice_routing.to` value, and `tests/test_config_apply.py` asserted an
  absolute `config_version == 2` plus a pinned preserved field — both coupled to the CURRENT content of the
  file being edited rather than its shape. This was the **identical class** already named in
  `claude-code-info-gap.md` §5 "Self-defeating CI test class" (2026-06-09, PR #222/#228, form-publish
  catalog counts) recurring on a second §50-actuator instantiation. **Fix landed (PR #514):** rewrote
  `tests/test_config_apply.py` (fixture seeded at a non-1 sentinel `SEED_CONFIG_VERSION=5` + relative
  `new == seed+1` asserts), `tests/test_po_terms.py` (shape asserts — non-empty, email-shaped, integer-bp in
  range, key parity — a second blocker the initial brief missed), and `safety_portal/test/po.test.ts`
  (imports the same bundled config the worker uses; derives `EXPECTED` tax math from `taxConfig.rates_bp.IL`;
  asserts served-config == imported source; terms wiring derives from the manifest) — all now assert
  shape/round-trip instead of pinning live content. A guard against a THIRD instantiation hitting this blind
  is now in `docs/HOUSE_REFLEXES.md` §5 ("never pin editable-config content; assert shape/round-trip/
  served-equals-source"). **PR #511 itself did NOT merge — it is `state: CLOSED`, not `MERGED`** (verified
  via `gh pr view 511`); the next purchaser/tax edit retests clean against the fixed suites, but #511's
  specific `invoice_routing.to` edit needed resubmission through the SPA, it did not auto-resume. **Tag:**
  `po_materials`, `config-editor`, `ci`, `self-defeating-test`, `resolved`. See CE-4 below for a residual the
  same PR flagged, not fixed.
- **CE-4 (LOW, out of scope of CE-3's fix) — `po.test.ts`'s `draftBody` hard-codes `ship_to_state:"IL"`.**
  Flagged by PR #514 as a known residual: CE-3's fix makes the test track a tax-RATE edit to IL (or an
  additional state) correctly, but a tax edit that **removes or renames the IL entry entirely** would still
  break `po.test.ts`, because `draftBody` assumes an IL ship-to unconditionally. Pre-existing, not introduced
  by PR #514. Low real-world risk while IL is the only active job state. **Trigger:** revisit if/when a
  second ship-to state goes live, or the next time `po.test.ts` is touched for an unrelated reason. **Tag:**
  `po_materials`, `config-editor`, `ci`, `low-severity`.
- **CE-5 (MEDIUM, pre-activation decision) — terms "Make a version current" attests legal clearance; the
  attesting population isn't yet decided.** Terms editing shipped in two slices this session (T1 #518 —
  edit-text pre-fill; T2 #520 — make-current + the Layer-A `legal_review != "cleared"` render-side refusal,
  **CE-2 RESOLVED**). The portal's confirmable "Make a version current" control (`cap.po.manage`) both clears
  `legal_review` and advances `current_version` in one action — i.e. checking that box IS the legal
  attestation ("I've reviewed this version's legal text"). `docs/runbooks/config_actuator.md` and this
  session's memory keep that judgment a FIXED §44 high-class call (Seth/legal, training-enforced, never a
  Tier-2 flip) — but the control itself only checks `cap.po.manage`, not a narrower "is this person actually
  Seth or legal" capability. **Decide before activation:** whether any `cap.po.manage` holder may attest, or
  whether the control needs a narrower capability / a second confirmation step. **Trigger:** before flipping
  `po_materials.config_actuator.polling_enabled` live for terms editing (the editor as a whole is already
  gated on this flag; this is a use-of-capability question, not a code gap). **Tag:** `po_materials`,
  `config-editor`, `terms`, `authorization`, `pre-activation`.
- **CE-6 (LOW, doc-currency) — `docs/enablement/purchase_orders.md:148-149` still says PO config is
  "read-only … not a portal edit," which has been false since the config editor's 3-slice vertical (#508–
  #510/#512) shipped and terms editing (T1/T2, #518/#520) completed it. RESOLVED 2026-07-13 (#506/#566):**
  the "Configuration" section was rewritten to describe the actual editable surfaces; a grep for the old
  "read-only"/"not a portal edit" phrasing returns empty and the manifest sha is current (CI green on HEAD).
  Original context — deferred at write
  time: editing an enablement doc trips the `docs/enablement/manifest.yaml` sha256 recompute (see auto-memory
  `reference_enablement-doc-sha-manifest-coupling.md`) and the doc-currency CI gate (`test_docs_pdf --check`)
  goes RED until the new hash is hand-recorded — not a blocker to skip, just a two-step edit rather than a
  one-liner. **Trigger:** next time `docs/enablement/purchase_orders.md` is touched for any reason, or as a
  dedicated small PR; update the "Configuration" section to describe the actual editable surfaces (purchaser/
  tax/terms via the portal, fully-automatic actuation) and re-run `scripts/generate_config_dictionary.py` /
  re-record the sha in the manifest per the existing coupling pattern. **Tag:** `po_materials`,
  `config-editor`, `docs`, `enablement`, `low-severity`.
- **CE-7 (LOW, blocks a SPA feature not a live edit) — subcontract payment-terms editing deferred to
  PR-B2: the actuator needs day-fields the served config doesn't expose yet.** PR #546 ("PO/SC
  Configuration — subcontract Contractor + terms editors (v1)") built the Contractor identity editor + the
  extracted shared `TermsProfilesEditor` for subcontracts, but deliberately left payment-terms editing
  unbuilt: `po_materials/config_apply._apply_payment_terms_edit` (the actuator handler — it is workstream-
  generic, not subcontracts-specific, despite living under `po_materials/`) validates+writes
  `application_for_payment_day` / `progress_payment_day` (`_bp(..., 1, 31)`), but the served
  `/api/subcontracts/config` route does not yet expose those fields to the SPA. Building the editor now
  would let the operator POST a payload the actuator can validate but the SPA can't pre-fill/round-trip
  correctly (no source of truth for the current values). **Fix:** extend the subcontracts-config Worker
  route to serve the two day-fields (small, deploy-gated — the same "Worker bundles config at build time"
  pattern as purchaser/tax/terms), then build the SPA editor. Folds into **PR-B2** alongside Exhibit-A
  versioned+gated editing (see the Subcontracts — PO/SC Configuration section below for the full PR-B2
  scope). **Tag:** `subcontracts`, `config-editor`, `deferred`, `low-severity`.

## [CUTOVER-BLOCKING] Aug-7 cutover readiness — deferred code follow-ups [OPEN 2026-07-10]

Surfaced during the cutover-readiness drive (PR #525); each is a real code follow-up not blocking
the merged work, tracked so it isn't lost. The operator-gated cutover items live in
`docs/operations/cutover_operator_punchlist.md`, not here.

- **CO-1 (LOW, belt-and-suspenders) — `po_send_poll.py:77 DEFAULT_POLLING_ENABLED = True` diverges from
  HOUSE_REFLEXES §5 (dark-ship default-False).** PO send ships dark via a seeded
  `po_materials.po_send.polling_enabled=false` row, so the seeded row is load-bearing (a MISSING row would
  default the SEND poller ENABLED). Flip the code default to `False` so a lost/absent row fails safe (a
  send-gate should never fail-open to sending). **Deliberately NOT landed autonomously** — it touches a
  `SEND_SCRIPTS`-enrolled send daemon, and the External Send Gate is a FIXED high-capability class; even a
  fail-safe tightening on that surface is Seth's call. **Trigger:** any PO send-path session, or a §5 sweep.
  **Tag:** `po_materials`, `po_send`, `external-send-gate`, `low-severity`.
- **CO-2 (MEDIUM, prove-the-control-bites) — no live-clamd EICAR end-to-end smoke for portal-upload
  ClamAV.** `safety_reports/photo_screen._clamav_scan` is wired into every portal upload path and ships
  default-OFF (`safety_reports.photo_screen.clamav_enabled`); the EICAR test (`test_photo_screen.py`) PATCHES
  `_clamav_scan`, so no live clamd ever runs. Per HOUSE_REFLEXES §2, add a live EICAR-through-clamd smoke
  (construct the EICAR string at runtime — do NOT commit a malicious file — feed it through
  `screen_photo(..., clamav_enabled=True)`, assert disposition=malicious; skip-if-no-clamd). CI cannot run
  clamd, so it's an operator-run Phase-C smoke (clamd installs in host-migration Phase A2). **Trigger:**
  Phase-C hardening gate, when the operator enables `clamav_enabled`. **Tag:** `security`, `safety_reports`,
  `clamav`, `prove-the-control-bites`.
- **CO-3 (LOW, mechanical coverage) — VC-03 does not sandbox-scan every mirror-bearing config row. RESOLVED
  2026-07-13 (as-designed):** the actionable sub-item is done — `system.operator_email` is enrolled in VC-03
  with `sandbox_scan=True` (`scripts/verify_cutover.py:264`, comment "CO-3"). The 2 Box
  `portal_root_folder_id` rows stay intentionally unenrolled (numeric IDs with no `evergreenmirror` marker to
  scan; CL-14 grep + CL-12 sweep are their backstop). Original context — PR #525
  enrolled the 2 extra `worker_base_url` copies + `po_send.from_mailbox`, but `system.operator_email` (global,
  mirror-domain fallback) and the 2 Box `portal_root_folder_id` rows remain outside VC-03 — the manual CL-14
  grep + the CL-12 sweep are their backstop. Enrolling `operator_email` (sandbox-scanned) would close another
  gap. Deferred because it changes gate behaviour and the Box roots are numeric IDs (no `evergreenmirror`
  marker to scan). **Trigger:** the next verify_cutover hardening pass. **Tag:** `cutover`, `verify_cutover`,
  `low-severity`.

## Doc-conventions workstream taxonomy is missing `po_materials`/`purchase_orders` [RESOLVED 2026-07-13]

**RESOLVED 2026-07-13** (verified across all three surfaces): `po_materials` + `subcontracts` are now present in `scripts/lint_doc_conventions.py` `CANONICAL_WORKSTREAMS`, the `docs/operations/doc_conventions.md` §"Workstream taxonomy" table, AND `docs/doctrine_manifest.yaml` `workstream_tags` (the 2026-07-12 WP1 reconciliation closed the three-copy set — HOUSE_REFLEXES §1). `purchase_orders` is intentionally NOT a doc-tag workstream (the exec package/tag is `po_materials`; `purchase_orders` lives only in the manifest planning-`slugs` vocabulary). Original context below.

The blueprint workstream `workstreams/purchase-orders/` has been fully built out in this repo since
`S1` (PR #492, 2026-07-09) through this session's #504–#512 — 20+ PRs, live daemons (`po_poll`, `po_send`,
`config_actuator`), and an `ITS_Config` workstream tag (`po_materials.*`) in real production use — but
`scripts/lint_doc_conventions.py`'s `CANONICAL_WORKSTREAMS` closed set (and its companion table in
`docs/operations/doc_conventions.md` §"Workstream taxonomy") was never updated to add it. Concretely:
`docs/runbooks/po_poll.md` and `docs/runbooks/po_send.md` (PR #501) both had to set `workstream: null` in
frontmatter and stash `purchase_orders`/`po_poll`/`po_send` into the free-text `tags` list instead — the
canonical-workstream lint would reject `workstream: po_materials` or `workstream: purchase_orders` today.
Low-severity (the lint is warn-only in CI per its own doc, and the workaround is harmless), but it's the
exact "zero taxonomy acknowledgment" gap the session-close cross-repo supersession check watches for — code
massively acknowledges the workstream, the doc-conventions closed set doesn't. **Fix:** add `po_materials`
(matching the runtime `ITS_Config` tag, mirroring `field_ops`/`progress_reports`'s pattern of naming the
code-level tag, not the blueprint folder name) to both `CANONICAL_WORKSTREAMS` in
`scripts/lint_doc_conventions.py` and the table in `docs/operations/doc_conventions.md`, then re-point the
two PO runbooks' `workstream:` field from `null` to `po_materials`. **Tag:** `po_materials`, `docs`,
`doc-conventions`, `low-severity`. **Revisit when:** next touching either PO runbook, or doing a
doc-conventions taxonomy sweep.

## Smartsheet-wiring audit findings — daemon-health + capacity hygiene [OPEN 2026-07-04]

From `docs/audits/2026-07-04_smartsheet-wiring-audit.md` (Task B — the SoR is wired correctly; these are hygiene/observability items, **no correctness breaks**):
- **M-1 (MEDIUM) — RESOLVED 2026-07-06:** `smartsheet.sheet_count_ceiling`=1500 / `_margin`=50 seeded as **explicit** `ITS_Config` rows (Workstream `global`), closing the silent-hardcoded-default gap (forensic class #7). Operator confirmed **Business plan — not limit-constrained** (upgrade if approached), so the values stay at the conservative advisory default (the guard WARNs but never blocks a create; won't fire until ~240 jobs); the true per-workspace cap isn't Smartsheet-API-exposed. Each row carries a tuning note in its Description. Raise if it ever false-WARNs.
- **M-2 (MEDIUM) — RESOLVED 2026-07-06 (stale claim):** inspected the LIVE `ITS_Daemon_Health` sheet before any delete — it holds **exactly the 6 healthy self-provisioning daemon rows** (fieldops_sync, portal_poll, publish_daemon, compile_now_poll, weekly_send_poll, progress_send_poll), all reporting `OK`. The 5 stale placeholder rows this entry described are **already gone** — nothing to delete. (Good instance of "trust the live state, never the claim": name-guarded inspection found the cleanup was already done, avoiding a delete against a live daemon's row.) The `watchdog`/`shared.picklist_sync` self-report-vs-external-monitor question (S-2) is moot — neither has a stale row.
- **M-3 (LOW) — CLOSED 2026-07-05 (PR #473, `86bfab0a`, four-part verify CLEAN: state=MERGED, mergedAt non-null, mergeCommit present, main-branch CI on the merge commit = SUCCESS):** `fieldops_sync` heartbeat interval mismatch — `SYNC_INTERVAL_SECONDS` set 300→90 to match launchd `StartInterval=90` (`install.sh:79`); feeds the daemon-health cadence.
- **S-1 (systemic) — MECHANISM DONE 2026-07-06 (#481 `c04f4cd`, four-part verify CLEAN):** the tracked `REQUIRED_CONFIG` startup-logging pass (#336) is BUILT — `shared/required_config.py` + `resolve_and_log` wired into ALL daemons; each declares a module-level `REQUIRED_CONFIG`; a missing declared row now WARNs `config_row_missing` **distinctly** (no longer silent) and each resolved setting logs its source; the §52 `narrated_controls` ledger entry `required_config_observable_resolution` flipped `dated_exception`→`enforced`. Residual (OPEN): the two named cross-workstream footgun rows still must be SEEDED correctly (unchanged); the shared `sheet_capacity` global keys are a documented carve-out (a bounded follow-up — see the `required_config.py` docstring).

**Tag:** `smartsheet`, `daemon-health`, `config`, `capacity`, `audit`, `field_ops`.

## [CUTOVER-BLOCKING] its#460 — create `progress@evergreenmirror.com` mailbox + Entra Application Access Policy (Mail.Send) [OPEN 2026-07-04, operator action]

**Tracked as a GitHub issue (`its#460`), cross-referenced here per convention.** `progress_reports.progress_send.from_mailbox` is already set to `progress@evergreenmirror.com` in `ITS_Config` (live) and matches the code default (`progress_send.DEFAULT_FROM_MAILBOX`) — but the mailbox itself does not exist yet in the `evergreenmirror.com` M365 sandbox tenant. **Operator action:** (1) create the mailbox; (2) add it to the Entra app registration's Application Access Policy with `Mail.Send` on the resource (mirrors the existing `safety@evergreenmirror.com` setup). Until then, progress weekly-report sends are **HELD at approval** (Invariant 1 human-in-loop) — nothing sends silently; this blocks only the final external send of progress packets, not compile/review. Flip to the production mailbox at the Phase 1.5 tenant cutover. Everything else in the progress go-live (routing, config, picklist, compile, WSR/WPR review) has been live since PR #459.

**Tag:** `progress-reports`, `mailbox`, `operator-action`, `m365`, `its#460`.

## `/pending-jobs` transport flakiness — deeper cause untraced, only blast-radius mitigated [OPEN 2026-07-05]

**PR #469 (`466e1e8`) fixed the SYMPTOM, not the root cause.** The live bug ("logged time not
showing" in the Hours Log) traced to `fieldops_sync._sync_inside_lock` returning early whenever
`GET /api/internal/fieldops/pending-jobs` raised a `PortalTransportError` — starving the independent
hours/equipment/material-list mirror passes on any cycle where the job-queue fetch happened to fail.
#469 **decouples** the passes (a transient job-fetch failure no longer blocks the others) and adds a
Check-Q-style sustained-outage escalation, but it never diagnosed **why `/pending-jobs` fails
intermittently in the first place**. No live failure has been captured with its actual HTTP status
code or response body — the daemon logs only that a `PortalTransportError` was raised, not what the
Worker actually returned.

**Suspected causes (unconfirmed):** Cloudflare bot-fight-mode / WAF challenging the daemon's
server-to-server bearer-token request (no browser fingerprint, no cookie jar — a classic false-positive
shape for bot mitigation), or a transient Worker-side D1 query error/timeout unrelated to Cloudflare's
edge. Both are plausible; neither has evidence yet.

**Fix:** on the next observed transient failure, log the actual status code + a truncated response
body at WARN (currently swallowed into a generic `PortalTransportError`); cross-check the Cloudflare
dashboard's bot-fight/WAF event log for the `/api/internal/fieldops/pending-jobs` route during the
failure window. If confirmed Cloudflare-side, the fix is a WAF allowlist rule scoped to the daemon's
bearer-token header pattern (never widen the allowlist to all traffic). If Worker-side, escalate as a
D1 query-shape issue.

**Tag:** `field_ops`, `fieldops_sync`, `transport`, `cloudflare`, `diagnose`.

## Remove the progress-% estimate system-wide [OPEN 2026-07-06 — SPA+route done; code-cleanup + column-drop DEFERRED as operator-reviewed]

**Ready spec (verified against live main 2026-07-06; all refs = the `jobs.progress` %-estimate, NOT the sync-mirror `progress`/`progress_report`/`progress_contact`):** the SPA slider/bar + the `POST /:job_id/progress` route/handler are already gone (#403, 2026-07-03); the client create call no longer sends `progress`. **Remaining:** (1) `worker/fieldops_job_write.ts` — stop honoring `body.progress`: delete `const progress` (~L171) + the `clampPct` helper (~L46), bind `0` in the INSERT (~L238, keeps the column/shape → no positional renumber); (2) dead read surfaces (zero consumers): `worker/wire-types.ts` `JobRow.progress` (~L50) + `JobDetail.progress` (~L133), `worker/fieldops_jobtracker.ts` the two `SELECT j.progress` (~L48/L162) + row types (~L64/L173) + response maps (~L136/L338); (3) `src/lib/fieldops_jobtracker.ts` `progress?: number` in the createJob body type (~L107); (4) `src/lib/errorCopy.ts` dead `invalid_progress` (~L95); (5) `test/fieldops-job-write.test.ts` drop the `progress: 40` create + change the assert to `toBe(0)`. **DEFERRED (2026-07-06):** touches the worker CREATE-route INSERT (a trust boundary — `portal-worker-security-reviewer` DoD) + the destructive `ALTER TABLE jobs DROP COLUMN progress` (`0014`) migration is deploy-coupled; dead-code removal on a `NOT NULL DEFAULT 0` dormant column is low-value / moderate-risk, so it's parked for a supervised worker-reviewed PR rather than an autonomous one (the column is harmless left in place). Original note below.

**Operator-locked 2026-07-01: the `jobs.progress` %-complete estimate is a misleading single-value guess and should be removed EVERYWHERE, not just omitted from the P6 rollup** (P6 already excludes it). A **multi-surface** removal — enumerate ALL consumers first (the multi-surface fan-out discipline):
- ~~SPA: the progress bar / slider control in the Job Tracker~~ — DONE (#403 removed the UI; the `setJobProgress` client fn deleted R4-F5).
- ~~Worker: `POST /api/fieldops/job/:job_id/progress` route (`fieldops_job_write.ts`)~~ — DONE 2026-07-03 (deleted with the B3 dead-route approval; tombstone in `fieldops_job_write.ts`). Still remaining: `progress` in the create body (accepted, default 0).
- D1: the `jobs.progress` column (`0014`) — leave the column vs. drop via migration (decide; a drop needs care).
- Any read route/response surfacing `progress`.
Grep `progress` across worker + SPA and distinguish `jobs.progress` (the %-estimate to remove) from the unrelated `sync_state` mirror progress. **Tag:** `field_ops`, `job-tracker`, `cleanup`, `multi-surface`.

## P2.5 Slice 6 — portal-owned canonical number: residual redundancy [OPEN 2026-06-30]

**Slice 6 (P2.5 revision).** The portal now ASSIGNS the canonical `JOB-######` (worker `job_counter`, migration 0022) and writes it as BOTH `job_id` and `canonical_job_id` from birth; `active_jobs_writer` writes it into the Smartsheet "Job ID" column (retyped AUTO_NUMBER → TEXT at cutover). Two deliberate §14-preservation leftovers — both harmless, both candidates for a later cleanup:

1. **`Portal Job Key` column == `Job ID`.** Both Active-Jobs columns now carry the identical `JOB-######`. The daemon's find-or-create still keys on Portal Job Key (unchanged, tested), so the column is redundant-but-load-bearing. A future simplification could drop Portal Job Key and key find-or-create on Job ID directly (and drop the `active_jobs.get_job` second-loop fallback) — deferred to avoid churn on a working, reviewed path.
2. **`canonical_job_id` mirror machinery is now always-set.** The down-sync canonical-aware pre-pass (`index.ts`) and the `jobs-mark-mirrored` `COALESCE(?4, canonical_job_id)` were built for the old NULL-until-read-back model; with canonical set at birth they are idempotent no-ops, not removed (they still correctly fence portal jobs off the smartsheet down-sweep).

**Revisit when:** a later slice consolidates the identity columns, or the canonical machinery is otherwise touched. **Tag:** `field_ops`, `job-tracker`, `smartsheet-upsync`, `preservation`.

## P2.5 job-tracker up-sync — fast-follows [OPEN 2026-06-30, updated 2026-07-01]

**P2.5 (PRs #383–#387).** The job-tracker → Smartsheet up-sync (`field_ops/fieldops_sync.py` + `shared/active_jobs_writer.py`) landed with six tracked, non-blocking follow-ups. **P2.5 cut over LIVE 2026-07-01** (`sync_enabled=true`; JOB-000017 confirmed mirrored to both Active-Jobs sheets); three of the six items closed same-day (#397, #400):

1. **`_ENROLLMENT_SUFFIXES += "_sync.py"` — still OPEN, re-evaluated and deliberately deferred again by FF5 (PR #400, 2026-07-01).** Adding the `_sync.py` suffix to the capability-gating enrollment list cascades and flags the pre-existing `shared/picklist_sync.py` as unenrolled (breaking the meta-test). Correct fix order: enroll `picklist_sync.py` in the appropriate gating list FIRST (separate PR), then add the `_sync.py` suffix. `tests/test_capability_gating.py` carries the revert note.
2. **Watchdog Check-C `fieldops_sync` slug not wired — RESOLVED by PR #397 (2026-07-01).** `fieldops_sync` now writes its freshness marker into `scripts/watchdog.py` `TRACKED_JOBS` (8-min staleness window, mirroring the `safety_compile_now_poll` 90s→8-min pattern). Verified FRESH against the live daemon post-cutover.
3. **`_route_to_review` partial-commit context — RESOLVED by PR #400 FF-B (2026-07-01).** A per-job fence now records `mirrored_safety` + `failed_sheet` in the Review-Queue payload, so the operator can tell from the row alone whether the failure was pre- or post-safety-write.
4. **Re-find-after-create race-dup hardening — still OPEN, re-evaluated and deliberately deferred again by FF5 (PR #400, 2026-07-01).** `active_jobs_writer.upsert_job`'s find-or-create has the same find-after-create race as `week_folder` (two near-simultaneous cycles could create two rows for one Portal Job Key). FF5 judged it hard-to-hit (single-host, serialized daemon) and idempotent (a duplicate row is a nuisance, not a correctness break) — skipped again in favor of the higher-value 401-severity + partial-commit fixes in the same PR. Tracked for symmetry with the `week_folder` entry.
5. **401-on-mark-mirrored severity — RESOLVED by PR #400 FF-A (2026-07-01).** A 401 on `mark_fieldops_jobs_mirrored` now raises `PortalAuthError` (a `PortalTransportError` subclass) through an earlier explicit `except` clause → CRITICAL `fieldops_mark_mirrored_unauthorized`, instead of falling into the generic transient-retry clause. Matches the pending-jobs 401 posture already used elsewhere.
6. **JOB-1042 placeholder UX nit — RESOLVED by Slice 6.** The Job-ID input was removed entirely (the portal now assigns the number on create), so the placeholder no longer exists.

**Revisit when:** items 1 and 4 (both OPEN) — item 1 when `picklist_sync.py`'s capability-gating enrollment is separately addressed; item 4 opportunistically, or if a live near-simultaneous-cycle duplicate is ever observed. **Tag:** `field_ops`, `job-tracker`, `smartsheet-upsync`, `watchdog`, `capability-gating`.

## install.sh interval-help-text stale — lists only 3 of 5 interval daemons [RESOLVED 2026-07-13]

**RESOLVED 2026-07-13** (in-code verify): `scripts/launchd/install.sh`'s `usage()` heredoc + header comment + the `poll_interval_config_key()`/`poll_interval_default()` logic now ALL enumerate the SAME **8** interval daemons with matching defaults (weekly-send 900 / portal-poll 60 / compile-now-poll 90 / progress-send 900 / fieldops-sync 90 / po-poll 90 / po-send 900 / subcontract-poll 120) — help and logic are in sync. The "3 of 5" framing below is superseded.

**Surfaced 2026-07-01** during the FF4/FF5/P2.6 session. `scripts/launchd/install.sh`'s `usage()` function and its header comment (top-of-file) both enumerate only 3 interval daemons — `weekly-send` (default 900), `portal-poll` (default 60), `compile-now-poll` (default 90) — and describe the `[interval]` CLI arg as overriding "the poll-interval daemons (weekly-send / portal-poll / compile-now-poll)".

The actual per-daemon resolution logic (`poll_interval_config_key()` + `poll_interval_default()`, both further down the same file) has since grown to **5** daemons: the original 3 plus `progress-send` (`progress_reports.progress_send.poll_interval_seconds`, default 900) and `fieldops-sync` (`field_ops.fieldops_sync.poll_interval_seconds`, default 90). The help text and header comment were never updated when those two were added — an operator reading only `usage()` (or the header) would not know `progress-send`/`fieldops-sync` accept an `[interval]` override or what their defaults are.

**Fix (trivial, docs-only):** update the `usage()` heredoc and the header comment block to list all 5 daemons + their defaults, matching `poll_interval_config_key()`/`poll_interval_default()`. No behavior change — purely a stale-doc-in-code fix, same class as the `docs/session_logs/README.md` index gap above.

**Tag:** `field_ops`, `progress_reports`, `launchd`, `docs`. **Revisit when:** next `install.sh` touch, or opportunistically.

## Progress (and safety) no-recipient HELD surfaces a record, not an operator page [OPEN 2026-06-30]

**P5 (PR #380).** `shared/recipient_health.report_unhealthy_recipient` files an `ITS_Review_Queue` RECORD on a no-recipient HELD (visible in the operator review queue; watchdog Check A WARNs if it sits past 2× SLA; watchdog Check T WARNs on a HELD older than 24h). It deliberately does **not** fire an operator PAGE — per Op Stds §3.1 the only §3.1-compliant push leg `alert_dedupe` may gate is a `Severity.CRITICAL`, and a missing-contact config issue was judged not CRITICAL-class (consistent with `_mark_held`'s existing WARN treatment of HELDs).

**Revisit when:** the operator decides a blocked customer-facing weekly send warrants an active page rather than a queue item — at which point add a dedicated CRITICAL push leg (a Send-Gate severity-posture decision, Seth-owned). **Tag:** `progress_reports`, `safety_reports`, `external-send-gate`.

## `hours_log.find_entry_row` does a full client-side scan of the sheet on every upsert/supersede call [OPEN 2026-07-04]

**P7 Slice 1 (exec PR #461).** `progress_reports/hours_log.find_entry_row(sheet_id, entry_uuid)` calls `smartsheet_client.get_rows(sheet_id)` (fetches every row in the sheet) and then scans client-side for the matching `Entry UUID`. It is the dedupe/amend-resolution authority for both `upsert_entry_row` (idempotent re-mirror safety) and `supersede_entry_row` (amend chains), so it runs at least once per pending time entry, every `fieldops_sync` cycle. Per Op Stds §51 design, this is a **standing, append-only, never-deleted** sheet — the exact accumulating shape the A5 row-cap watchdog exists to bound at ~20k rows. A full-sheet fetch-and-scan per entry is O(sheet size) per call, meaning per-cycle cost grows linearly with the sheet's lifetime total, not with the cycle's actual workload — the daemon accumulates a heavier cycle every day the job stays open, well before the row-cap watchdog itself would fire.

Not urgent today (a new job's Hours Log starts empty and low-volume by design — a handful of entries/day), but it is the first §51 accumulating-log write path built this way; the same shape will recur in the P7 Equipment/Materials mirror passes. Two independent fixes available when it bites: (a) cache the sheet's UUID→row-id map in daemon-local state between cycles (invalidate on a miss, re-fetch full); (b) if Smartsheet's `get_rows` gains column-value filtering in a future SDK, filter server-side instead of client-side. Neither is built.

**Tag:** `progress_reports`, `field_ops`, `smartsheet-upsync`, `p7`, `scaling`, `§51`. **Revisit when:** a live Hours Log sheet is observed taking a materially longer `fieldops_sync` cycle, or before onboarding a job with a crew large enough to make per-cycle entry volume nontrivial (a 20-job cutover is the named scale point in the 2026-06-28 20×20 eval).

## build_wsr_human_review_sheet.py would fail on a fresh create (ABSTRACT_DATETIME not API-creatable) [RESOLVED 2026-07-13]

**P2 (PR #362).** Building the progress twin `WPR_human_review` surfaced that `scripts/migrations/build_wsr_human_review_sheet.py` declares `Approved At` / `Sent At` as `type: ABSTRACT_DATETIME`, which the Smartsheet API **rejects on create** (`errorCode 1142`, "reserved for project sheets and may not be manually set on a column"). The build only succeeds today because it is idempotent and the live WSR sheet already exists — masking the bug. The **live** WSR `Approved At`/`Sent At` columns are in fact `type=DATE` (verified 2026-06-29); the ABSTRACT_DATETIME schema in the builder + the detailed ABSTRACT_DATETIME rationale comment in `safety_reports/wsr_review.py` are **doc-vs-live drift** (the intended retype-to-ABSTRACT_DATETIME via `update_column` was never applied to the live WSR sheet). `build_wpr_human_review_sheet.py` was therefore created with `DATE` columns, matching the working live WSR exactly (live WPR-vs-WSR parity verified 2026-06-29).

**Fix (low-class):** change `build_wsr_human_review_sheet.py`'s two columns to `DATE` (matching live) — OR, if Date/Time (time-of-day) display is actually wanted, add a create-as-DATE-then-`update_column`-retype step to BOTH builders + a retype migration for the live WSR + WPR sheets, and correct the `wsr_review.py` comment. Today's behavior is correct (DATE accepts `to_wsr_datetime`'s naive string end-to-end); this is cleanup + a comment-accuracy fix.

**RESOLVED 2026-07-13.** The live WSR `Approved At`/`Sent At` columns were re-confirmed `type=DATE` (live `get_columns` read, 2026-07-14). `build_wsr_human_review_sheet.py`'s two columns are now `DATE` (mirroring `build_wpr_human_review_sheet.py`); the stale ABSTRACT_DATETIME rationale in `safety_reports/wsr_review.py` (the module comment + `to_wsr_datetime` docstring) and in `tests/test_wsr_review.py` (section comment + assert message) were corrected to DATE. Regression-pinned by `tests/test_wsr_review.py::test_build_wsr_datetime_columns_are_creatable_date_not_abstract_datetime` (RED on the pre-fix ABSTRACT_DATETIME schema). Fresh-create-only change — the live sheet (idempotent skip) is untouched. Sweep to `tech_debt_closed.md` in the follow-up doc-hygiene pass.

**Tag:** `safety_reports`, `progress_reports`, `smartsheet`, `migration`. **Revisit when:** the safety build migrations are next touched, or if time-of-day display is desired on the approval/sent stamps.

## Orphan per-job Smartsheet folder from the JOB-000013 50-char-cap incident [OPEN 2026-06-13]

**PR #283 (2026-06-13).** A field PM submitted a portal form for JOB-000013 ("I don't know project name Montgomery", 36 chars). `week_sheet.py` creates the per-job Smartsheet folder BEFORE the week-of sheet; the folder creation succeeded, but the sheet creation 400'd (`errorCode 1041` — name exceeded 50 chars). This left an **empty per-job folder** named "I don't know project name Montgomery" in the `ITS — Safety Portal` workspace (ITS — Safety Portal workspace), beside the now-populated truncated-name week sheet that succeeded after the fix was deployed and the stuck submission was re-drained.

**Operator-manual cleanup:** delete the orphan folder "I don't know project name Montgomery" from the ITS — Safety Portal workspace via the Smartsheet UI. It is empty; nothing reads or writes it. Harmless but stray.

**Not a code gap** — the fix (PR #283) adds `SHEET_NAME_MAX = 50` to `week_sheet.py`; `week_sheet_name` now truncates the project prefix so the composed name always fits. Future submissions with long project names will land in a truncated-name week sheet within the same per-job folder, without creating the orphan. The per-job folder name (from `safety_naming.job_folder_name`) is NOT subject to the 50-char sheet-name cap — it is a folder, not a sheet — so the folder always creates successfully regardless of project-name length.

**Tag:** `safety-portal`, `smartsheet`, `operator-manual`. **Revisit when:** next ITS — Safety Portal workspace tidy pass.

## weekly_send upload-session threshold = 2.5 MB (heuristic, not measured) [OPEN 2026-06-12]

**PR-3 (photo workstream tail).** `weekly_send` now switches transport by compiled-packet size: `≤ UPLOAD_SESSION_THRESHOLD_BYTES` (2.5 MB) sends **inline** via `graph_client.send_mail` (one request, base64-inline); `>` it sends via the Graph **upload-session** (`graph_client.send_mail_large_attachment` — draft → chunked PUT honoring `nextExpectedRanges` → send). The threshold is a **heuristic**: Graph's inline `/sendMail` ceiling is ~3 MB, and base64 inflates the payload ~33% plus message-envelope overhead, so 2.5 MB raw leaves headroom below the wire limit. It was **not** empirically measured against the live Graph tenant — the exact inline-reject boundary (and whether it counts raw or base64 bytes) is unverified. Low risk because the upload-session path is correct for ANY size 3–150 MB, so a too-low threshold just sends some sendable-inline packets the (slightly slower) chunked way; a too-high threshold is the only real failure (an inline send that Graph rejects ~3 MB → FAILED + retry, never a silent drop).

**Tag:** `safety-reports`, `graph`, `send-gate`, `threshold-heuristic`. **Revisit when:** the first live photo-bearing packet crosses ~2.5 MB (confirm the inline/upload boundary against the real tenant and tune the constant), or a `weekly_send.graph_error` retry cluster appears on packets near 3 MB.

## R2 upgrade path for portal photo transport (deferred) [OPEN 2026-06-12]

**PR-3 / cross-ref [ADR-0001](adr/0001-portal-photo-transport-d1-vs-r2.md).** Site photos ride **D1-inline base64** today (owner decision 2026-06-12) — simplest transport within the current ≤8 × 400 KB per-submission budget, and it keeps the Worker a send-free queue holding no documents. The recorded **upgrade path is Cloudflare R2** (object storage; D1 carries only the object key, the Mac fetches bytes at screen time), to be adopted when **field crews need > 4 full-res photos per field** (or the per-submission photo budget is raised past what D1-inline base64 carries within the Worker body bound). Deferred because R2 means provisioning a second storage plane, an object-key scheme, lifecycle/expiry, and a Mac access path — non-trivial and unneeded at the current budget.

**Tag:** `safety-portal`, `photo`, `r2`, `transport`, `adr`. **Revisit when:** the > 4-full-res-photos-per-field trigger fires, or the Worker body bound blocks a needed photo-budget increase. See ADR-0001 for the full decision + consequences.

## weekly_send upload-session chunk-retry hardening (deferred) [OPEN 2026-06-12]

**PR-3.** `graph_client._put_upload_chunk` mirrors `_request`'s retry shape (429/503 back off + retry; a hang fails fast as `GraphTimeoutError` without consuming the budget) and the chunk loop **honors `nextExpectedRanges`** so an interrupted transfer *can* resume to a server-reported offset within a single call. What is **deferred**: (a) no **session-resume across `send_one_row` calls** — a chunk failure that escapes the retry budget aborts the whole upload (the draft is left UNSENT in Drafts, fail-toward-not-sending), and the next poll cycle re-creates a fresh draft from byte 0 rather than resuming the prior `uploadUrl`; (b) no **explicit upload-session cancel** (`DELETE uploadUrl`) on abort — the abandoned draft + session simply expire (Graph TTL); (c) the anti-stall guard forces linear progress if a 200 body reports a non-advancing range rather than retrying the same range. Acceptable because a 3–150 MB packet uploads in a handful of chunks, restart-from-zero is cheap at that size, and the External Send Gate is unaffected (a failed upload never sends a partial packet).

**Tag:** `safety-reports`, `graph`, `upload-session`, `retry`. **Revisit when:** live telemetry shows recurring mid-upload failures on large packets (then add cross-cycle session resume + an explicit cancel), or packet sizes grow toward the 150 MB ceiling where restart-from-zero becomes expensive.

## Smartsheet API constraint: column FORMAT must be set via model attribute, not dict constructor [OPEN 2026-06-07]

**Verified live (PR #187, 2026-06-07).** When using the Smartsheet Python SDK to create or update a column, the column **format string** (font, size, bold, color, etc.) must be assigned via the model **attribute** (`column.format = "..."`) — passing `format` as a key in the dict constructor (`smartsheet.models.Column({"format": "..."})`) silently drops the value. Column **width** works via either path (dict or attribute). The same per-cell format DOES work via the `Cell` dict constructor (`_resolve_cells` attaches it via the `_formats` meta-key extension).

**Palette index source:** `GET /2.0/serverinfo` → `.formats.color` (array, index → hex). Verified live: 38 = `#237F2E` (dark green), 7 = `#E7F5E9` (light green), 18 = `#E5E5E5` (gray). `dateFormat` enum at `.formats.dateFormat`. Format-descriptor positions: 2=bold, 8=textColor, 9=backgroundColor, 16=dateFormat.

**Impact:** code that sets a column format via the dict constructor silently succeeds (200) but the column stays unformatted. Always use the attribute path for column format.

**Tag:** `smartsheet`, `sdk-vs-live`, `styling`. **Revisit when:** any new column-format code; `smartsheet_client.apply_column_styles` already uses the attribute path.

## Safety Portal — `scheduled_send_local` not seeded + silent fail-open on malformed value [OPEN 2026-06-08]

`safety_reports.weekly_send.scheduled_send_local` (ITS_Config; e.g. `"MON 07:00"` — the Pacific weekday/time window in which `Approve for Scheduled Send` rows dispatch) is read live each cycle by `weekly_send_poll._read_str_setting` → `_parse_scheduled_spec` → `_is_scheduled_window`. Two minor gaps: (1) it is **not** in `scripts/seed_its_config.py` (added manually to the mirror) — a fresh tenant build would lack the row and fall back to the `DEFAULT_SCHEDULED_SEND_LOCAL = "MON 07:00"` constant (functionally safe, but undocumented in the seeder). (2) `_parse_scheduled_spec` **silently** coerces any malformed value (bad weekday, bad time, empty) to `(MON, 07:00)` with **no log** — an operator typo'd window would quietly send Monday 07:00 instead of erroring. The fallback is intentional + tested (`test_parse_scheduled_spec_defaults_on_malformed`), but it's a quiet-failure footgun for an operator-tuned schedule.

**Proposed fix:** (a) add the row to `seed_its_config.py`; (b) WARN-log to ITS_Errors when `_parse_scheduled_spec` hits the `except` branch (still fall back, but surface the bad value). ~30 min. **Revisit when:** next seeder pass or weekly_send hardening. Surfaced 2026-06-08 (operator asked to confirm the config-driven schedule during mirror activation).

## `smartsheet-python-sdk` upper-bound pin (CI-break stopgap) [OPEN 2026-06-08]

`pyproject.toml` now pins `smartsheet-python-sdk>=3.0.0,<3.10.0`. A release >3.9.0 (2026-06-08) dropped/moved `smartsheet.exceptions`, which `shared/smartsheet_client.py:46` imports (`import smartsheet.exceptions as sdk_exc`) — the previously-unpinned `>=3.0.0` let CI fresh-install the broken version and **all 48 test modules failed at collection** (`ModuleNotFoundError: No module named 'smartsheet.exceptions'`). main was last green at `d393ee6` (2026-06-07 19:35); the breaking SDK release landed after. Local + every prior green CI run used 3.9.0 (which has `smartsheet.exceptions`).

**Stopgap (PR #192):** upper-bound `<3.10.0` keeps CI on a working SDK. Caps below 3.10 (the lowest possible breaker) rather than `<4.0.0`, since a minor *or* major could be the one that dropped the module.

**Proper fix (deferred):** verify the newer SDK's exception surface, then either (a) update `shared/smartsheet_client.py`'s import to the new location and loosen the bound, or (b) make the import resilient (try/except across the old/new path). ~1 hr. **Revisit when:** next dependency-maintenance pass, or when a smartsheet SDK feature/security update is wanted.

## Pre-mirror-tree portal Box filings are sandbox orphans [OPEN 2026-06-07]

**Mirror root activated 2026-06-08** — `safety_reports.box.portal_root_folder_id = 388017263015` (`ITS_Safety_Portal`) seeded in ITS_Config; new submissions now file to `ROOT → per-job → per-week`. The 3 submissions filed BEFORE activation (to the legacy tree) are confirmed orphans; left as-is (sandbox), per below.

PR-K mirrors the Smartsheet schema in Box (`ROOT → per-job → per-week → PDFs`),
replacing the legacy `project_routing` → category-subfolder layout for the portal
path. Submissions filed BEFORE the operator activates the mirror tree (sets
`safety_naming.CFG_BOX_PORTAL_ROOT`) live under the old category subfolders (e.g.
`Bradley 1 ▸ … ▸ 05. Tool Box Talks`). These are **pre-launch sandbox orphans** — no
migration is provided (validation-tenant data, pre-customer-1). Box keeps both; the
mirror tree simply files NEW submissions into the new tree once activated.

**Repair:** none required (sandbox). At a real cutover, decide per-customer whether
to leave or hand-move the handful of pre-activation PDFs. **Revisit when:** the Box
root is activated for a live customer tenant.

## Watchdog/launchd hang-killer: hard-kill a daemon exceeding N× expected cycle duration [OPEN 2026-06-02]

Fix part (c) carved out of the now-closed graph_client-timeout entry. The graph + (future) box timeouts convert *known* network surfaces' hangs into finite errors, but a hang from any *other* cause (a future un-timed call, a CPU spin, a deadlock) still defeats the launchd one-shot-per-interval model: the hung process holds the fcntl lock and every later interval no-ops on `poll_lock_held`. Check C's marker-staleness floor only **detects** this (after the staleness window); it does not **recover** it (the 2026-06-02 incident needed a manual `launchctl kickstart -k`).

**Proposed fix:** a watchdog (or a launchd `ExitTimeOut` / wrapper) that hard-kills a daemon process whose elapsed wall time exceeds N× its expected cycle duration, so the next interval can re-acquire the lock and self-heal. Larger design decision (where the kill lives, how to size N per daemon, interaction with legitimately-long cycles) — its own item.

**Phase target:** 1.4/1.5 reliability — the recovery complement to Check C's detection.

Surfaced: 2026-06-02 A2 graph_client timeout work (the indefinite-hang incident motivated detection→recovery, not just per-call timeouts).

## Conftest mock surface coverage [OPEN 2026-05-23]

`tests/conftest.py` (PR #74) autouse-mocks `shared.keychain.get_secret` and `shared.kill_switch.check_system_state`. The keychain mock at the source attribute covers all 7 credentialed surfaces transitively (smartsheet_client / graph_client / box_client / resend_client / sentry_client / anthropic_client / alert_dedupe). Two opt-out lists guard test files that exercise these surfaces directly (`test_keychain.py` + `test_helpers.py` for keychain; `test_kill_switch.py` for kill_switch).

Latent risk: future credentialed surfaces (a new client wrapper for a new external service) might need parallel opt-outs if a corresponding `tests/test_<service>_client.py` lands. Action trigger: any new Linux-CI failure with a `*Error: macOS-only` signature, OR a CI-fix follow-on PR that adds a fixture beyond the keychain + kill_switch pair, OR a new credentialed client module added to `shared/`.

**Revisit when:** next CI-hygiene pass, or any of the above triggers.

## Structural fix: lazy keychain loading + DI-injected kill_switch [OPEN 2026-05-23]

The conftest fix (PR #74) closes the immediate CI hole. A durable structural fix would:

- `shared/smartsheet_client.py::_get_client` — defer the `keychain.get_secret("ITS_SMARTSHEET_TOKEN")` call from build time to first-API-call time, so a test that never makes a real network call never hits the keychain.
- `shared/kill_switch.py` — accept a `get_setting` callable via dependency injection (with the module-level `smartsheet_client.get_setting` as default), so tests can inject without monkeypatching the source module.

Both are non-trivial refactors with cross-call-site impact. Deferred from PR #74 to keep scope focused on the CI fix. Trigger: next session that touches either module for an unrelated reason, fold the refactor in.

**Revisit when:** smartsheet_client or kill_switch refactor session lands.

## Smartsheet API constraint: DATETIME columns require system column type [OPEN]

Discovered 2026-05-17 evening while provisioning `ITS_Errors`, `ITS_Quarantine`, and other sheets. The Smartsheet "Create Sheet" endpoint accepts `DATETIME` columns only when paired with `systemColumnType: MODIFIED_DATE | CREATED_DATE`. User-defined DATETIME columns (e.g., "Timestamp", "Surfaced At", "Resolved At", "Received At", "Reviewed At") are rejected with a generic HTTP 500 / error code 4000 and no descriptive message.

**Workaround:** Use `DATE` for all user-defined date columns. Time-of-day precision is lost from the in-sheet representation.

**Mitigation:** Smartsheet's intrinsic row-level `created_at` (and `modified_at`) attributes are full datetimes and are queryable via the API. Code-side ordering and time-of-day inspection use those fields rather than the in-sheet DATE columns. The in-sheet DATE columns serve human readability; the intrinsic timestamps serve programmatic precision.

**Revisit when:** Smartsheet API surfaces user-editable DATETIME columns, or a workstream finds DATE-only resolution genuinely insufficient and the `created_at` fallback isn't viable for the use case.

_Update 2026-06-09 (PR #245 WSR Approved At / Sent At sweep):_ `ABSTRACT_DATETIME` (the "Date/Time" user type in the Smartsheet UI) **CAN** be created/retyped to via `update_column` and accepts a **naive** `YYYY-MM-DDTHH:MM:SS` value (stored/displayed literally). A plain `DATETIME` column is still rejected with errorCode 4000 — that restriction stands. `ABSTRACT_DATETIME` rejects any offset or 'Z' suffix (errorCode 5536). Existing DATE-only cells coerce to midnight on retype to ABSTRACT_DATETIME. The `WSR_human_review` sheet (id `5035670127988612`) columns "Approved At" (col `7944658226548612`) and "Sent At" (col `5129908459442052`) were live-retyped DATE → ABSTRACT_DATETIME, confirming the above. Write naive Pacific wall-clock (operator preference).

## Smartsheet API constraint: AUTO_NUMBER columns rejected at sheet creation [OPEN]

Discovered same session. `systemColumnType: AUTO_NUMBER` is rejected at the "Create Sheet" endpoint, whether or not the column is primary, with or without an `autoNumberFormat` config. Other system column types (`MODIFIED_DATE`, `MODIFIED_BY`) are accepted in the same payload — so the rejection is specific to AUTO_NUMBER, not a generic system-column-at-create issue.

**Workaround:** Each system sheet's primary column is a plain `TEXT_NUMBER` that code populates with a descriptive label ("Error", "Quarantined Message", "Entry"). Smartsheet's intrinsic row IDs serve as the unique identity for any code-side references.

**Mitigation:** Code-side row references use the Smartsheet row ID (returned in every API response). The human-readable primary column gives operators a meaningful label in the UI without needing auto-numbering.

**Revisit when:** A workstream requires user-visible auto-IDs (e.g., a customer-facing ticket number) and the code-populated label pattern is insufficient. Likely never — the intrinsic row IDs cover the technical need and labels cover the human need.

## PowerShell macOS Gatekeeper deprecation 2026-09-01 [OPEN]

The powershell@preview cask path used for EXO ServicePrincipal management (Connect-ExchangeOnline; New-ServicePrincipal) is scheduled for macOS Gatekeeper deprecation on 2026-09-01. Without intervention, post-deprecation runs will fail Gatekeeper signature verification on the cutover MacBook.

Plan B: Azure Cloud Shell. Same Connect-ExchangeOnline + New-ServicePrincipal commands run in a browser shell instead of local PowerShell. No code change required; runbook change only.

Cutover impact: Handover Plan v6 Step 4 verification currently assumes local PowerShell. If Phase 1.5 cutover lands after 2026-09-01, runbook needs the Azure Cloud Shell variant.

Resolves when: 2026-08-15 calendar check confirms status (still scheduled / postponed / cask alternative emerged). Runbook updated based on findings.

## anomaly_logger: SUSPICIOUS_FIELD_PATTERNS will false-positive on legitimate system_* fields [OPEN 2026-05-20]

`shared/anomaly_logger.py` flags any extraction field name matching `^system_` as a security anomaly (Phase 1 starter sentinel list for prompt-injection detection). The pattern is correct against the threat model — a legitimate workstream extraction schema shouldn't include `system_*` field names, so their presence suggests the AI invented them under injection.

**The risk:** this is a forward-dated FP source. As workstream extraction schemas mature, any legitimate field with a `system_` prefix (e.g., `system_version`, `system_id`, `system_serial_number` on machine pre-inspections) will fire `security_flag=True` on every extraction, polluting `ITS_Review_Queue` with noise and training operators to dismiss the flag.

Tuning belongs to the first 30 days of sandbox operation against real extraction outputs (per Safety Reports Brief v6 — "Phase 1 sentinel list, extend as patterns emerge"). The sentinel list should be re-audited once `safety_reports/weekly_generate.py` has run against the migrated closed-project corpus and produced a representative extraction sample.

**Specific suggested follow-ups when tuning lands:**
- Narrow `^system_` to specific known-bad names (`system_prompt`, `system_role`, `system_instruction`) rather than the prefix glob.
- Same audit for `^role_` and `^ignore_` — both have similar FP-on-legitimate-naming risk.
- Add a `tests/test_anomaly_logger.py` case for any legitimate field name that ends up in a real extraction schema, so the sentinel list and the schemas can't drift apart.

Surfaced 2026-05-20 in a senior-dev audit pass; not yet triggered in practice because no workstream extraction has shipped.

## R2 Watchdog Check E (Anthropic spend trend) deferred to Phase 1.5 [OPEN 2026-05-20]

Check E of R2 Watchdog (Anthropic API spend trend analysis) deferred to a follow-on PR (the Check E shipping PR) at Phase 1.5 production cutover. **Architectural choice, not capability gap.** Individual Anthropic orgs DO expose Admin keys once a formal Organization is created (Settings → Organization with business address; verified 2026-05-20). Deferral rationale: sandbox spend signal-to-noise is too low at $5-credit scale for trend analysis to produce meaningful alerts. Re-evaluate at production cutover when spend is real and recurring. Implementation will add `shared/anthropic_billing.py` + `_check_spend_trend` in `scripts/watchdog.py`, seed the 4 `spend.*` `ITS_Config` rows + the `system.anthropic_admin_api_keychain_key` row, and convert the existing smoke runner's Phase E from a SKIPPED placeholder into a real exerciser.

Originally surfaced 2026-05-20 in R2 Session 2 pre-flight (the Keychain `ITS_ANTHROPIC_ADMIN_API_KEY` held a workspace key, `sk-ant-api03-…` prefix, not an Admin key). Session 2 shipped Checks A/B/C/D/F via PR #36; Check E is the only outstanding piece of the R2 Watchdog spec.

## PowerShell `Get-ApplicationAccessPolicy -Identity <friendly-name>` directory lookup fails [OPEN 2026-05-20]

`Get-ApplicationAccessPolicy -Identity <friendly-name>` fails with a directory-object-not-found error in Exchange Online PowerShell, even when the policy exists and is valid.

**Workaround:** call the bare cmdlet (no `-Identity`) and filter the result set client-side. Pattern: `Get-ApplicationAccessPolicy | Where-Object { $_.Description -match '<keyword>' }` or pipe to `Select` and pattern-match the returned rows.

Captured 2026-05-20 during M365 sandbox re-verification while validating the `ITS Scoped Mailboxes` policy for R2 Watchdog Check F. The bare-cmdlet form returned a valid record with `IsValid: True` despite the friendly-name lookup failing seconds earlier on the same policy.

## voice@ mailbox AppAccessPolicy scope addition pending [OPEN 2026-05-20]

`voice@evergreenmirror.com` is one of 5 ITS-intake mailboxes (per the mailbox roster) but is NOT currently in the `ITS Scoped Mailboxes` ApplicationAccessPolicy scope. Confirmed by `Get-ApplicationAccessPolicy` on 2026-05-20 — current scope covers `safety / procurement / subcontracts / its`, no `voice@`.

**Resolves when:** an ITS workstream activates the `voice@` mailbox as an intake source. At that point: add `voice@evergreenmirror.com` to the AppAccessPolicy scope via Exchange Online PowerShell, and register the corresponding `mail_intake.voice.max_idle_hours` row in `ITS_Config` so R2 Watchdog Check F starts monitoring it. No code change required for the policy update; the watchdog already iterates `mail_intake.*` rows via `smartsheet_client.get_settings_with_prefix` (PR #36).

## Stale Anthropic Service Account `svac_…SR7vDMJ` for archival [OPEN 2026-05-20]

Stale Anthropic Service Account `svac_…SR7vDMJ` (created during R2 Watchdog Check E investigation 2026-05-20) flagged for archival. The associated workspace API key has already been deleted from macOS Keychain. No urgency; clean up when next in the Anthropic Console (Settings → Service Accounts → Archive). Captured here so the cleanup isn't forgotten at the next Anthropic-Console visit.

## Eventually migrate from legacy boxsdk to `box_sdk_gen` (Gen API) [OPEN 2026-05-20]

The `boxsdk` PyPI package jumped to a renamed Gen API at 10.x (imports as `box_sdk_gen`, with a substantially different surface). PR #39 pins to `<4.0.0` to use the legacy 3.x API. The Gen API is the future direction per Box; legacy 3.x will eventually be deprecated.

**Action:** re-evaluate when (a) Box announces a deprecation timeline for 3.x, (b) the legacy API lacks something the Gen API offers, or (c) annual dependency-hygiene sweep.

**Migration scope:** `shared/box_client.py`, `tests/test_box_client.py`, `scripts/setup_box_oauth.py`, `scripts/smoke_test_box.py`. Probably non-trivial (~half day of work).

**Urgency:** low. Pin holds until Box deprecation pressure or capability gap.

Surfaced: PR #39 review, 2026-05-20.

## Add Box refresh-token age check to R2 Watchdog [OPEN 2026-05-20]

`ITS_BOX_REFRESH_TOKEN` rotates on every Box API call and stays valid as long as ITS makes at least one Box call every 60 days. If ITS goes dark for >60 days (extended outage, post-handover period without activity), the refresh token expires and re-running `scripts/setup_box_oauth.py` is required.

A watchdog check would warn the operator before the token expires:
- **Warn** at 50 days since last rotation
- **Critical** at 58 days

**Mechanism:** track last-rotation timestamp via either
- (a) a sidecar Keychain entry `ITS_BOX_REFRESH_TOKEN_LAST_ROTATED` updated by the `store_tokens` callback in `shared/box_client.py`, or
- (b) a row in `ITS_Config` (`system.box_refresh_token_last_rotated`).

**Implementation venue:** R2 Watchdog Session 2 (planning pass needed first) or later. Not blocking; absence of this check is documented in the handover runbook as a known operator-touch requirement.

**Urgency:** medium. Real risk if ITS goes dark for an extended period post-handover. Pre-handover is fine because ITS runs daily.

Surfaced: PR #39 brief, 2026-05-20.

## Phase 1.5 — provision dedicated ITS Box user account, re-auth [OPEN 2026-05-20]

ITS currently authenticates to Box as `seths@evergreenmirror.com` (operator account). All API actions attribute to that user in Box audit trails, and all ITS-created files are owned by that user.

At Phase 1.5 cutover, provision a dedicated ITS Box user account (e.g., `its@evergreenrenewables.com` once the production tenant is live) and re-authenticate ITS as that user. No code changes needed — just re-run `scripts/setup_box_oauth.py` while logged into Box as the new user.

**Concerns to handle at migration time:**
- File ownership of anything ITS created under the operator account may need to be transferred to the new user.
- Collaborator permissions on existing folders must be granted to the new user before re-auth.
- Old refresh token under the operator account should be revoked in the Box account settings.

**Urgency:** Phase 1.5 cutover task. Not before.

Surfaced: PR #39 brief, 2026-05-20.

## Confirm `canonical_job_path()` format with owner [OPEN 2026-05-20]

`shared/box_client.py` exposes `canonical_job_path(customer, job_number, job_name, year)` which returns `"/Customer/JobNum — JobName/YYYY/"`. This is the WRITE-path format for new ITS-created content.

Owner confirmation has not happened yet — the format is the legacy-stub placeholder, never validated against owner preference. `box_migration/parse_job_v3.py` handles read-side recognition of the 4 active Box schemas, so this only affects what ITS creates going forward, not what it can recognize.

**Action:** surface to owner at next opportunity, confirm or adjust format, update `shared/box_client.py` + tests if needed.

**Urgency:** low until the first workstream consumes `canonical_job_path`. At that point the decision becomes blocking and locks the format for all future ITS-created content.

Surfaced: PR #39 brief, Open Question Q2, 2026-05-20.

## Seed `system.box_smoke_folder_id` in ITS_Config [OPEN 2026-05-20]

`scripts/smoke_test_box.py` supports a `--write-test` opt-in flag that does a write-read-delete loop against a known sandbox folder. The folder ID comes from an `ITS_Config` row at `system.box_smoke_folder_id`.

The row is not yet seeded. The read-only smoke (default invocation) works without it; only the opt-in write-test path requires it.

**Action:** create a dedicated "ITS Smoke" folder in Box, copy its folder ID, seed the `ITS_Config` row. After seeding, run `python3 scripts/smoke_test_box.py --write-test` once to confirm.

**Urgency:** low. Read-only smoke is sufficient for most operator checks. Write-test is useful only when diagnosing suspected scope or permission issues.

Surfaced: PR #39 brief, Open Question Q4, 2026-05-20.

## Alert-routing dedupe key granularity [OPEN 2026-05-20]

(Naming gloss for this entry and several below: "PR α" = PR #42 — alert-dedupe core; "PR β" = PR #44 — watchdog Check G summary sweep. Greek-letter aliases predate the actual PR numbers landing.)

`shared/alert_dedupe.py` keys dedupe windows on `(script, error_code)` (built at the `_fire_resend_leg` call site). Today's only call path uses `error_code="uncaught_exception"`, so all decorator-driven CRITICALs from a given script collapse into one window. If production shows distinct underlying exception classes inside one script collapsing within a window — and the operator misses the second bug because the first one suppressed its alert — upgrade the key to `(script, error_code, exc_class)`.

**Action:** one-line change at the `dedupe_key = f"{script}::{error_code}"` site in `shared/error_log._fire_resend_leg`. Thread `exc_class` from the decorator's `except Exception as e:` path via `type(e).__name__`.

**Urgency:** low until production surfaces the collapse-different-bugs failure mode. Bounded blast radius — Smartsheet ITS_Errors + Sentry still record each bug separately, so the operator sees the second bug eventually; only the wake-up email is delayed.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Cross-leg dedupe activation [OPEN 2026-05-20]

PR α suppresses only the Resend leg. Sentry events and Smartsheet ITS_Errors rows always write (per Op Stds v11 §3.1 — dedupe applies only to push, never to records). Today this is the right choice: Sentry's own alert rules and Smartsheet's sheet-level notifications are NOT configured.

**Resolves when:** the operator configures Sentry alert rules (or Smartsheet notifications) that themselves wake the operator on every event. At that point, those legs become "push" surfaces too and need their own dedupe layer. The shared `correlation_id` is already wired through all three legs, so a future cross-leg dedupe (or alert-aggregator) has the join key it needs.

**Urgency:** activates only when external alert rules are configured. No risk while Sentry/Smartsheet stay record-only.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Alert-dedupe state is per-machine [OPEN 2026-05-20]

`~/its/state/alert_dedupe.json` lives on the local MacBook. The dedupe window is per-host. If ITS ever runs on multiple hosts (Phase 4+ blueprint generalization, or a hot-spare during MacBook RMA), each host would dedupe independently — and an operator-facing flapping CRITICAL on two hosts would produce one email per host instead of one total.

**Resolves when:** ITS gains multi-host execution. The state needs to move into a centralized store. Smartsheet itself can't host it (Smartsheet IS a triple-fire leg; circular dependency). Likely candidates: a dedicated S3 prefix, a Redis sidecar, or a per-customer SQLite that lives on whichever host happens to be authoritative.

**Urgency:** low. Phase 1 through Phase 3 is single-host on a designated MacBook. Multi-host is a Phase 4+ blueprint-generalization decision.

Surfaced: PR α (alert-dedupe-core) brief, 2026-05-20.

## Smoke harness pattern divergence between dedupe smoke and Resend/Sentry smokes [OPEN 2026-05-20]

`scripts/smoke_test_alert_dedupe.py` uses the full `@its_error_log` decorator path so all three triple-fire legs fire (Smartsheet `log()` write + Resend + Sentry). `scripts/smoke_test_sentry.py` and `scripts/smoke_test_resend.py` call `shared.error_log._alert_critical` directly, which deliberately bypasses `log()` and therefore does NOT write to ITS_Errors.

The divergence is acceptable because the older two scripts validate narrower scopes (the Sentry leg, the Resend leg), and the alert-dedupe smoke validates the cross-leg integration. The trap is that the `_alert_critical`-direct pattern silently skips the Smartsheet leg — if a future smoke claims to exercise full triple-fire but uses that pattern, the ITS_Errors assertion will pass vacuously (zero rows match, zero rows expected by the harness).

**Action:** any new smoke that intends to verify all three legs MUST go through the `@its_error_log` decorator. Smoke that targets a single leg can keep the `_alert_critical`-direct pattern.

**Urgency:** low. No active failure; this entry is forward-protection for the next time someone writes a triple-fire smoke. Discovered post-PR-#42 merge when the operator's live run produced 0 ITS_Errors rows.

Surfaced: PR α (alert-dedupe-core) live verification, 2026-05-20.

## Alert-dedupe state-file growth in pathological flap-with-new-error-code scenarios [OPEN 2026-05-20]

PR β's two-phase deletion bounds state-file growth at ≤1 day per `(script, error_code)` key pair across the sweep cadence: an entry is fired-and-marked on sweep N, deleted on sweep N+1. Worst-case file growth across the ITS lifetime is one entry per distinct dedupe key.

The pathological scenario the bound assumes against: a script that flaps repeatedly with a NEW `error_code` each window, producing unbounded distinct keys per day. `_alert_critical` today always uses `error_code="uncaught_exception"`, so the bound holds. If `_fire_resend_leg` is ever upgraded to a richer key (e.g., `(script, error_code, exc_class)` per the existing tech-debt entry on key granularity), AND the underlying script raises a wide variety of exception classes within short windows, growth could accelerate.

**Action:** monitor state-file row count. If it grows past ~100 persistent entries between sweeps, investigate before tuning sweep cadence or compacting the state schema.

**Urgency:** none today. Bounded blast radius; sweep cadence is the lever if the file ever balloons.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Watchdog sweep cadence vs dedupe window length [OPEN 2026-05-20]

Default `alerting.dedupe_window_minutes = 60`. Watchdog runs once daily at 7:00 AM ET. Worst-case operator-visible summary delay = ~24 hours from window close (a window that closes at 7:01 AM waits until the next morning's sweep).

This is intentional: operators on the daily-rhythm cadence don't need real-time summary push, and the 24h delay only applies to the close-the-loop notification — the original CRITICAL email + the suppressed-marker log lines fire in real time.

**Resolves if:** operator wants tighter feedback. Lever 1 — increase watchdog cadence to hourly via launchd. Lever 2 — separate the summary sweep into its own scheduled script with its own cadence. No code change to dedupe core in either case.

**Urgency:** none. Re-evaluate if operator triage workflow shows ≥24h-delayed summaries causing problems.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Summary email content depth (filter-criteria vs inline correlation IDs) [OPEN 2026-05-20]

PR β summary email body lists aggregate counts + window timestamps + filter criteria pointing at ITS_Errors (Script + Surfaced At range). It does NOT enumerate per-suppressed-event correlation IDs inline, because the state file stores only aggregates per dedupe key — individual UUIDs live in ITS_Errors rows.

If operator triage workflow shows excessive Smartsheet lookups when triaging a summary, the upgrade path is: grow the state schema to retain a list of correlation IDs per window (capped at N most recent to bound file size), and inline those in the summary body. State migration would be needed; existing entries lack the field.

**Action:** track operator triage patterns. If "open the summary → open ITS_Errors → copy filter → run filter" becomes a frequent friction point, upgrade the schema.

**Urgency:** none today. Pull-from-source-of-truth pattern is cleaner if operator only triages a handful of summaries per week.

Surfaced: PR β (watchdog summary sweep) brief, 2026-05-20.

## Picklist_Sync_Config mixes config and runtime state [OPEN 2026-05-20]

`Picklist_Sync_Config` holds both configuration (mapping_id, source/target sheet+column, enabled, notes) and runtime state (last_run_at, last_run_hash) on the same sheet. Architecturally a small smell — runtime state evolving on a "config" sheet means operators editing the sheet can accidentally clear hash/timestamp, forcing a full re-sync.

**Why kept as-is:** §14 preservation-over-refactor. Phase 1.5 doesn't need the split. The convenience of "one sheet per concern" outweighs the purity cost while there's only one consumer.

**Resolves if:** picklist_sync grows complex enough to need migration/versioning (multi-customer fork edge cases, schema evolution of per-mapping state, etc.). At that point: move `last_run_at` + `last_run_hash` to a separate `Picklist_Sync_State` sheet keyed on `mapping_id`, leave `Picklist_Sync_Config` purely declarative.

**Urgency:** none. Watch for operator-edit accidents that wipe hash/timestamp — first such incident is the resolution trigger.

Surfaced: Picklist sync hardening review, 2026-05-20.

## SDK-vs-live body-shape mismatches need integration coverage [OPEN 2026-05-20]

PRs #47/#48/#49 each surfaced one body-shape mismatch the Smartsheet SDK accepted silently but the live API rejected, in successive iterations:

- **PR #47**: `id` in body — errorCode 1032 ("attribute(s) column.id are not allowed for this operation").
- **PR #48**: `type` missing from body — errorCode 1090 ("Column.type is required when changing options").
- **PR #49**: `type` present but wrapped as `EnumeratedValue`, SDK silently strips it — wire body becomes `{"options": [...]}` with no `type`, API rejects same as #48.

Class of bug: `SimpleNamespace`-based mocks at the SDK boundary don't enforce the live API's contract on body shape, required fields, or value wrapping. Mock tests passed; live calls failed.

**Mitigation landed in this PR (2026-05-21):** `tests/test_smartsheet_client_integration.py` runs create → list → update → delete round-trips against live sandbox sheets. Registered as `@pytest.mark.integration`; default `pytest` skips them (pyproject.toml `addopts = -m 'not integration'`). Operator runs `pytest -m integration` pre-deployment after any `shared/smartsheet_client.py` or `shared/picklist_sync.py` change.

**Pattern to extend:** any future `shared/*` SDK wrapper that exercises a non-trivial verb (update/create/delete) on typed columns or rows should gain a parallel integration test. The pattern: create the minimum live state required, exercise the verb, assert post-state, tear down in `finally`.

**Urgency:** addressed. Note kept open for visibility — any new wrapper that lands without parallel integration coverage re-introduces the class of bug.

Surfaced: PR #46 → #47 → #48 → #49 iteration, 2026-05-20/21.

## Smartsheet MULTI_PICKLIST type doesn't survive sheet-creation round-trip [RESOLVED 2026-07-14]

**RESOLVED 2026-07-14 — it was NOT a Smartsheet quirk; `list_columns_with_options` read columns without `level=2`.** The Smartsheet API downgrades a `MULTI_PICKLIST` (and `MULTI_CONTACT_LIST`) column to its base type in a `GET …?include=columns` response UNLESS `level=2` is requested — so the round-trip "showed TEXT_NUMBER" purely because the read omitted `level=2`. Fixed by adding `level=2` to the single `get_sheet` in `list_columns_with_options`; a live create→read integration assertion (`test_list_columns_with_options_unwraps_picklist_type`, now with a MULTI_PICKLIST column) proves it. This ALSO unblocked `ensure_picklist_options` (it can now manage live multi-select columns) and cleared the `audit_picklist_drift` false positives on the two live columns. **The "no production mapping uses it" note below is SUPERSEDED** — `ITS_Subcontractors.Trades` + `ITS_Vendors.Supply Categories` are live production MULTI_PICKLIST columns. Original entry (kept for the diagnosis trail):

Creating a sheet with `{"type": "MULTI_PICKLIST", "options": [...]}` via `Folders.create_sheet_in_folder` (or the equivalent REST POST `/folders/{id}/sheets`) returns 200 OK, but a subsequent `GET /sheets/{id}?include=columns` shows the column's type as `TEXT_NUMBER`, not `MULTI_PICKLIST`. The column doesn't behave as MULTI_PICKLIST either.

Probed live during the PR #51 integration-test run. Adding the column via a separate `POST /sheets/{id}/columns` after the sheet exists DOES return `"type": "MULTI_PICKLIST"` in the immediate response — but the subsequent GET still shows TEXT_NUMBER. The discrepancy is consistent enough that "sheet creation with MULTI_PICKLIST" appears to be a Smartsheet API behavior, not a transient race.

**Impact on `shared/picklist_sync.py`:** none today. The picklist sync's only target columns are PICKLIST (master DBs → downstream forms). MULTI_PICKLIST is a defensive code path in `update_column_options` (accepts the type, unit-tested via `test_update_column_options_accepts_multi_picklist`) but no production mapping uses it.

**Action if MULTI_PICKLIST becomes a real use case:** investigate whether the column needs to be created with additional flags (`validation`, `width`, …) or via a different REST endpoint. May require a Smartsheet support ticket — their column-type matrix isn't fully self-documenting.

**Urgency:** none. Tracked for visibility so a future operator looking at the integration test's missing MULTI_PICKLIST coverage understands why.

Surfaced: PR #51 integration test run, 2026-05-21.

## Smartsheet UI-only constraints (Forms, CF, Filter Views, Restrict-to-dropdown) [OPEN]

Several Smartsheet features are exposed only through the Smartsheet web UI and have NO REST/SDK surface — meaning Claude Code can NOT provision, audit, or sync these per-customer settings during deployment. Operator must configure each manually at deployment time and document the choices.

The known UI-only surfaces (as of 2026-05):

- **Form creation + configuration** — `Smartsheet → Forms` panel. Forms are the primary intake surface for several workstreams; no API equivalent. Form rules (required fields, conditional logic, custom thank-you page, branding) are all UI-only.
- **Conditional Formatting** (cell-color rules based on cell values or row state) — UI-only.
- **Filter Views** (saved per-user filter definitions over a sheet) — UI-only.
- **Restrict to dropdown values only** (PICKLIST column validation toggle) — UI-only. Critical for `shared/picklist_sync.py` activation: the sync writes the option list, but the "reject free-text entries" enforcement toggle must be set manually per column. Without it, picklist sync still works but users can type values that aren't in the master DB (canonical-name drift).

**Impact on `shared/picklist_sync.py`:** the `Restrict to dropdown values only` toggle must be manually set on each downstream PICKLIST column at deployment time. Without it, the sync still works (options stay in sync) but the strict-mode validation that prevents users from typing vendor-name drift is absent. Documented in `docs/references/picklist_sync.md` activation checklist step 5.

**Impact on form-and-clone cascade:** every form requires manual UI setup. The cascade flow assumes operator builds forms in the UI as the final cutover step.

**Resolves if:** Smartsheet exposes any of these surfaces via API. Worth re-checking annually — Smartsheet's API surface expands slowly. No action item today; this entry exists so future operators / new customer forks know the manual-deployment-step list without rediscovering it.

**Urgency:** none. Operationally accepted; manual deployment steps documented per-customer.

Surfaced: Phase-0 architecture review 2026-05; referenced from `docs/references/picklist_sync.md` activation checklist.

## safety_reports week-folder create-find race condition [OPEN 2026-05-21]

`safety_reports/week_folder.ensure_current_week_folder` performs a find-or-create on the per-week folder under each project's Field Reports subtree. Two concurrent callers (e.g., a same-week intake.py and a Friday weekly_generate.py firing within the same minute) could both pass the initial `find_folder_by_name_in_folder` step and both create the folder; Smartsheet does not enforce folder-name uniqueness, so both creates succeed.

The helper detects the duplicate on a post-create find: if the post-create lookup returns a different folder ID than the just-created one, it logs a WARN to ITS_Errors with `error_code="week_folder_race_duplicate"` and proceeds with the first match (the survivor). The orphan folder ID appears in the WARN message for operator triage.

**Workaround:** operator manually deletes orphan folders via short-lived sandbox token + curl per Op Stds v11 §25 MCP-gap REST fallback (`curl -X DELETE https://api.smartsheet.com/2.0/folders/<orphan_id> -H "Authorization: Bearer <token>"`). No automatic cleanup — race is rare at single-machine cadence, and the safer move is operator visibility (WARN → review) over an automated delete that could race against legitimate concurrent writes.

**Why not auto-clean:** the orphan folder is initially empty (the losing-race caller hasn't created its sheets yet at the moment of duplicate detection). But a subsequent run on the orphan side WOULD create sheets, and an auto-delete couldn't safely distinguish "empty orphan" from "filled-by-another-thread orphan." Operator visibility wins.

**Resolves if:** observed in practice (no incident expected at single-machine cadence; multi-machine ops would trigger this).

Surfaced: R3 foundation PR brief, 2026-05-21.

## Fallback path removal after ITS_Config cutover [OPEN 2026-05-23]

Per the ITS_Trusted_Contacts delivery above, the legacy ITS_Config allowed_senders fallback stays in `safety_reports/intake.py` (`_check_legacy_allowlist` + the `sheet_contacts` branch in `_run_pipeline`) until the operator confirms one full Friday cycle clean post-cutover. Then:

- Remove `_check_legacy_allowlist`.
- Remove the `sheet_contacts = trusted_contacts._load_contacts()` / `if sheet_contacts:` branch in `_run_pipeline`; replace with direct `check_trusted_sender(...)` call.
- Delete `_fallback_logged` + the once-per-process INFO log.
- Drop the `CFG_ALLOWED_SENDERS` constant + `_read_allowed_senders` helper.
- Update `test_intake_stage2_refactor.py::test_empty_sheet_falls_back_to_its_config_allowlist` + `test_sheet_with_rows_is_authoritative_skips_legacy_allowlist` accordingly.

**Effort:** ~30-min session.

**Revisit when:** operator confirms one Friday cycle clean post-cutover.

## Native multi-PICKLIST graduation for Trusted Contacts scope columns [OPEN 2026-05-23]

`Project Scope` and `Workstream Scope` columns on `ITS_Trusted_Contacts` are TEXT_NUMBER JSON-lists, not native multi-PICKLIST. Rationale (per the Phase 1.4 brief): the Smartsheet SDK returns inconsistent shapes for multi-PICKLIST (sometimes comma-string, sometimes list) and the cross-sheet picklist sync from PR #45-51 doesn't cover multi-select reliably. Once the Phase 1.4 picklist-hardening deliverable lands:

- Convert column types to MULTI_PICKLIST.
- Update `shared/trusted_contacts.py::_parse_scope` to accept either form during the transition.
- Add reference-checked sync to the picklist_sync.py registry.

**Effort:** ~1 hour session.

**Revisit when:** Picklist Hardening #1 deliverable lands.

## DKIM in-process re-validation [OPEN 2026-05-23]

`shared/header_forgery.py` trusts the inbound MTA's `Authentication-Results` DKIM verdict — no local DNS TXT lookup + RSA verify. Acceptable for Phase 1: the only path delivering messages is via the verified inbound MTA chain. If a future threat-model session demands cryptographic re-validation:

- Add `dkimpy` (or `python-dkim`) to requirements.
- Replace the `dkim=tokens.get(...)` path with a re-validation step (parse `DKIM-Signature` → DNS TXT lookup → RSA verify).
- Cache DNS TXT records per (selector, domain) for the poll cycle.

**Effort:** ~half-day session.

**Revisit when:** security review or threat-model session flags the in-MTA-trust assumption.

## Operator-UI Shortcuts for trusted-contacts workflows [OPEN 2026-05-23]

`ITS_Trusted_Contacts` operator edits today require direct Smartsheet UI. A Shortcuts-track addition could wrap common flows:

- "Approve pending sender" — picks PENDING_VERIFICATION rows, prompts operator, flips to ACTIVE + sets Last Verified=today.
- "Disable sender" — by Email or row pick, flips Status to DISABLED + notes the reason.
- "Verify identity" — re-stamps Last Verified=today for ACTIVE rows.

**Effort:** ~half-day session.

**Revisit when:** Tooling-track session has bandwidth.

## Attachment screening pipeline Layers 1-3 [OPEN 2026-05-22]

Implement 4-layer attachment screening per Op Stds v11 §34 + FM v8 Invariant 2 Layer 6 (Layers 1-3 for Phase 1.5; Layer 4 VirusTotal deferred Phase 2+):
- Layer 1 (static): magic-number verification, size sanity, filename pattern matching.
- Layer 2 (structural): PyMuPDF or pypdf for PDF JS/embedded-file detection; python-docx/openpyxl for Office macro/OLE detection; EXIF anomalies; embedded URL extraction.
- Layer 3 (ClamAV): pyclamd + clamd daemon + freshclam auto-update. Homebrew install on operator Mac.
- Layer 4 (VirusTotal): defer.

EICAR test signature fixtures verify pipeline health without real malware. Integration test against corpus of legitimate DFR samples.

Disposition: malicious → ITS_Quarantine + CRITICAL triple-fire + sender DISABLED in ITS_Trusted_Contacts; suspicious → ITS_Review_Queue; clean → proceed.

**Effort:** ~half-day to one-day session (operator-side ClamAV install + code + tests).

**Revisit when:** Phase 1.4 security hardening session lands; required before Phase 1.5 cutover.

## 5-duplicate ITS_Errors sheets in System/02-Logs [OPEN 2026-05-22 — operator UI delete required]

Bootstrap drift from 2026-05-18 sheet creation: 5 ITS_Errors sheets created within ~75 seconds. Canonical sheet is 27291433258884 per Op Stds v11 §23. The four duplicates are dead and require operator UI delete:
- 2704945844277124
- 470411799121796
- 4505679602601860
- 4195780532326276

Smartsheet MCP has no delete-sheet primitive; operator UI is the only path.

**Revisit when:** next operator Smartsheet UI session; not blocking any code or workflow.

## audit_picklist_drift.py marker writer is not wired to a launchd plist [OPEN 2026-06-01]

Surfaced during the Check I (weekly_generate catch-up) build. `scripts/watchdog.py` Check C tracks `safety_picklist_audit` (8-day window), and the **only** writer of the `safety_picklist_audit.last_run` marker is `scripts/audit_picklist_drift.py`. But the picklist launchd plist (`scripts/launchd/org.solutionsmith.its.picklist-sync.plist`) invokes `scripts/run_picklist_sync.py` (the hourly option-SYNC job), **not** `audit_picklist_drift.py` (the drift-AUDIT job) — and `run_picklist_sync.py` writes no watchdog marker. So either (a) the operator schedules `audit_picklist_drift.py` via a plist outside `scripts/launchd/`, or (b) the `safety_picklist_audit` marker is never written → a permanent stale Check C WARN. Separately, `run_picklist_sync.py` (the actually-scheduled hourly job) is not in TRACKED_JOBS at all, so its silent death is invisible to Check C.

**Out of scope** for the Check I PR (no behavior changed here — recording the finding only). Per Op Stds "silent fail-open hazards must become watchdog-detectable signals," this should be reconciled: confirm where `audit_picklist_drift.py` is scheduled (or wire it), and consider tracking `run_picklist_sync.py`.

**Revisit when:** the picklist scheduling/Tranche-0 work is next touched, or the first time a `safety_picklist_audit` stale WARN fires with no underlying cause.

## Smartsheet transient 404 on first-project sheet/folder create [PARTIALLY MITIGATED 2026-05-22]

Two `weekly_generate` smoke runs on 2026-05-22 each surfaced exactly one transient 404 during per-project iteration:

- Smoke #1 (`--week-start 2030-01-07`): `SmartsheetNotFoundError('HTTP 404 (code 1006): Not Found')` on Bradley 2. Folder DID get created (cleanup confirmed it existed).
- Smoke #2 (`--week-start 2026-02-16`): same error on Rockford.

Different project each run; both error-and-continue per the weekly_generate per-project fence. Pattern: the FIRST project to need a fresh `ensure_current_week_folder` scaffold creation in a fresh process consistently 404s; subsequent projects in the same run succeed. Same class as PR #51's `find_sheet_by_name_in_folder` SDK staleness — both look like SDK in-process caching missing a just-created object.

**Mitigation shipped (2026-05-22 follow-on PR):** single-shot retry on `SmartsheetNotFoundError` inside the per-project fence (`_process_with_retry` wrapper in `safety_reports/weekly_generate.py`, 500 ms sleep + one retry, bumps `summary.retries_attempted`). When retry exhausts (or any non-404 error fires), the fence writes a `GENERATION_FAILED` placeholder row to `WPR_Pending_Review` so the operator's queue surfaces the failed project instead of leaving a silent gap. The placeholder respects the existing-row contract: approved rows are left untouched, unapproved rows have a `[GENERATION_FAILED: <ErrorClass>]` tag appended to Notes (Draft Body preserved), and missing rows get a fresh placeholder with the manual-rerun command embedded in Draft Body. Op Stds v11 §30 SDK-vs-Live discipline.

**Durable fix still deferred:** SDK→REST swap on the `ensure_current_week_folder` / `get_rows` paths to eliminate the staleness window entirely. Trigger condition: 3+ observed `weekly_generate.transient_404_retry` events in production cycles (meaning the retry IS firing in real runs, not just smoke). The `summary.retries_attempted` counter is the canonical signal — watchdog Check C or a follow-on metric scrape can surface the count without operator log-grep.

**Effort to swap:** ~1-2 hour session (mirror PR #51's pattern; ~6 unit tests around the find-after-create REST flow).

**Revisit when:** retries_attempted >= 3 in any consecutive 4-week window, OR a real Friday cycle surfaces a `GENERATION_FAILED` placeholder (the user-visible signal).

## Intake stream extension for Weather + Labor + Mobilization metadata [OPEN 2026-05-22]

The WPR draft sections Weather Report, Construction Labor Report, Mobilization Date, and Location are currently `[REVIEWER TO FILL]` because the intake.py Daily Reports stream doesn't capture them — operator-side reviewers add the data during approval per Safety Reports Brief v6.1. Phase 1.4+ option: extend `safety_reports/intake.py` to capture weather (via a public weather API or `Summary of Events` extraction) and labor counts (via a new Daily Reports column or field PM submission convention), eliminating those `[REVIEWER TO FILL]` placeholders.

Mobilization Date is project-scoped not week-scoped — better captured as a project-level metadata sheet (a "Projects" master sheet keyed by `project_name`) rather than threaded through every Daily Reports row. Same for Location.

**Effort:** 1-2 sessions (intake-side weather + labor extension, projects-metadata-sheet schema + read-side wire-up).

**Revisit when:** Phase 1.4 security hardening cluster ships and operator feedback drives WPR template v0.2.0 calibration.

## HTML email rendering for weekly_send [OPEN 2026-05-23]

`weekly_send.py` v0.1.0 sends `Draft Body` as inline text via `content_type="Text"`. Sponsors may prefer HTML formatting (paragraph breaks, bullet lists, the WPR layout's table structure rendered properly). Calibrate with Teala after the first 30 days of real Friday cycles — same 30-day window as the `safety_weekly_generate` prompt v0.1.0 calibration entry.

Implementation: render `Draft Body` (currently plain text with `[REVIEWER TO FILL]` placeholders) into minimal HTML via a small template, pass `content_type="HTML"` to `graph_client.send_mail`. Same recipient flow.

**Effort:** ~half-day session including +2-4 unit tests for the rendering function + a smoke run.

**Revisit when:** Teala provides feedback on the v0.1.0 inline-text format (after first 30 days of real cycles).

## Doc-conventions lint strict-mode flip after retrofit window closes [OPEN 2026-05-24]

`scripts/lint_doc_conventions.py` ships warn-only. Two follow-on items track the retrofit window's close:

1. **Bulk-retrofit sweep** of grandfathered docs (~36 session logs + a handful of pre-existing audits / references) — add YAML frontmatter to each. Target window: ~60 days (2026-07-24). Lazy retrofit per `docs/operations/doc_conventions.md` is the interim policy; this sweep is the optional bulk-migration option.
2. **Flip lint to `--strict`** in CI after the sweep completes. `.github/workflows/ci.yml` currently invokes the lint without `--strict`; one-line change to add the flag once the sweep lands and all violations clear.

Trigger conditions:
- Auto-trigger #1: 2026-07-24 reached (default sweep target).
- Manual-trigger #1: operator decides to skip the bulk sweep and accept indefinite grandfather state. In that case strict-mode flip is also skipped; the conventions doc's "Retrofit policy" section should be updated to mark the policy as permanent.

**Effort:** ~2 hours for bulk sweep (mostly automatable — frontmatter generation from filename/git-log); ~5 min for the strict-mode flip.

**Revisit when:** 2026-07-24, or sooner if operator opens a doc-retrofit session.

## Nightly auto-index regen wiring [DEFERRED 2026-05-24]

`docs/operations/doc_conventions.md` mentions a "nightly regeneration" path for `scripts/regen_doc_indexes.py` via `scripts/watchdog.py::TRACKED_JOBS`. Not wired in the initial ship: regen runs in CI (`--check` mode) on every PR, which is the load-bearing enforcement. A nightly launchd job would add freshness for un-merged branches sitting on the operator's MacBook, but the CI gate is sufficient for `main`.

**Action when triggered:**
1. Add launchd plist `org.solutionsmith.its.doc-index-regen.plist` (StartCalendarInterval, daily 03:00 local).
2. Have the script write a watchdog marker on successful regen.
3. Append `doc_index_regen` to `scripts/watchdog.py::TRACKED_JOBS` with 36-hour freshness window.

**Effort:** ~30 min.

**Revisit when:** operator notes drift between local doc state and CI's view, OR a third polling daemon ships and the watchdog wiring patterns are being touched anyway.

## Hardcoded BOX_SUBPATH_BY_CATEGORY in safety_reports/intake.py [OPEN 2026-05-24]

`safety_reports/intake.py:172` defines `BOX_SUBPATH_BY_CATEGORY: dict[str, tuple[str, ...] | None]` — hardcoded mapping from inbound email category to Box subfolder path. `VALID_CATEGORIES` (line 195) is derived from this dict's keys. Adding a new safety-reports category requires code change.

**Failure mode:** same shape as `BOX_PROJECT_FOLDERS` (config-migration sibling): operator can't add a category without a PR. Lower change cadence than projects (categories churn slowly — the safety-reports taxonomy is more stable than the project set), but same redeploy-for-ops-task problem.

**Proposed fix:** migrate to either (a) `ITS_Config` rows with key prefix `BOX_SUBPATH_<category>` and tuple values JSON-encoded, or (b) a dedicated `ITS_Category_Routing` sheet alongside the project-routing sheet from the A2 entry. Same caching pattern. Same Box-resolution validation. Coupled enough with A2 that landing both in one PR pair makes sense (a `shared/routing.py` module covering both lookups).

**Effort:** ~2 hours, lower than A2 because category set is smaller and the schema is simpler (no `Active` bool needed if categories are append-only).

**Phase target:** 1.6 — lower priority than A2 because category set is stable. Bundle with A2 only if the routing-module shape benefits from co-design.

**Tag:** `config-migration`.

**Revisit when:** A2 lands (do A3 right after, sharing the routing-module pattern), OR a new safety category needs adding before A2 lands (force the move at that point).

Surfaced: 2026-05-24 hardcoded-values audit brief, §A3.

## Hardcoded default fallbacks for ITS_Config-sourced timing constants [OPEN 2026-05-24]

`safety_reports/weekly_send_poll.py:97-98` defines `DEFAULT_POLLING_ENABLED = True` and `DEFAULT_POLL_INTERVAL = 900` (15 minutes). The authoritative runtime values come from ITS_Config rows `safety_reports.weekly_send.polling_enabled` and `safety_reports.weekly_send.poll_interval_seconds` — the hardcoded constants are fallback defaults when those rows are missing or malformed. Other timing-bearing files (intake_poll, watchdog) follow the same pattern.

This is partially good (already ITS_Config-sourced) and partially fragile: silent fallback to a hardcoded default when an operator typos an ITS_Config row means the daemon "works" but on the wrong schedule, with no operator-visible signal that the override didn't take.

**Failure mode:** operator edits ITS_Config to change poll interval from 900 to 1800. Typos the key name. Daemon silently uses the hardcoded 900 default. Operator believes the new value is in effect; isn't. Costs and responsiveness are both off the operator's mental model.

**Proposed fix (two layers):**

1. **Startup log line** in every daemon: log the *resolved* values at startup (`[startup] poll_interval_seconds = 900 (source: default fallback)` vs `(source: ITS_Config)`). Cheap; makes the silent-fallback observable in launchd stdout/stderr logs.
2. **Optional but stronger:** convert silent fallback to WARN-loud fallback when the ITS_Config row is unexpectedly missing for keys the daemon documented as "should be configured." A dedicated registry of "expected ITS_Config keys" per daemon, checked at startup, surfaced via Sentry WARN if missing. Same shape as the validation-at-startup proposal in C1.

**Effort:** ~1 hour for layer 1 (startup-log only) across the 2-3 polling daemons. Layer 2 folds into C1's startup-validation module.

**Phase target:** 1.6 alongside C1 (config validation cluster).

**Tag:** `config-migration`.

**Revisit when:** C1 startup-validation work begins, OR an operator hits the silent-fallback-after-typo failure mode in real ops.

Surfaced: 2026-05-24 hardcoded-values audit brief, §A5. Note: the brief's framing assumed full hardcoding of timing constants; actual state is ITS_Config-sourced with hardcoded defaults as fallback. The fragility is the silent fallback, not the constants themselves.

## Severity-tiered + multi-recipient alert routing [OPEN 2026-05-24]

Current state: `shared/resend_client.send_alert()` sends to a single recipient resolved from `system.operator_email` in ITS_Config at runtime (per `shared/resend_client.py:164`). No multi-recipient distribution. No severity gating — every CRITICAL via `_alert_critical` fires the same Resend leg to the same single recipient regardless of severity.

Adequate for the solo-operator stage. Becomes a gap when:

- Team composition expands (on-call rotation, multiple operators in different timezones).
- Severity stratification matters (CRITICAL to phone-via-Resend, WARN to a digest sheet only).
- Customer 2+ onboarding lands and per-customer recipient lists need separation.

**Proposed fix:** new `ITS_Alert_Routing` sheet with columns `Email` (TEXT_NUMBER, primary), `Severity Threshold` (PICKLIST: CRITICAL/WARN/INFO), `Workstream Filter` (TEXT_NUMBER, JSON list — `["*"]` for all), `Active` (bool), `Notes`. `send_alert()` reads the sheet, filters rows by severity ≥ threshold AND workstream match, fans out to each matching recipient. Email validation at sheet load (basic `^[^@]+@[^@]+\.[^@]+$`). Keep `system.operator_email` as the single-recipient fallback when the sheet is empty or unreachable.

**Effort:** ~half-day session including schema migration script (mirror the trusted-contacts pattern) + `shared/alert_routing.py` reader + `send_alert()` rewiring + tests.

**Phase target:** 2 (post-Customer-1 cutover). Single-recipient is sufficient for the solo + Customer-0 stage and shouldn't preempt Phase 1.4/1.5 critical-path work.

**Tag:** `config-migration`.

**Revisit when:** team expansion is concrete, OR Customer 2 onboarding begins.

Surfaced: 2026-05-24 hardcoded-values audit brief, §A4. Note: the brief's premise (hardcoded recipients in `shared/alert.py`) was inaccurate — that file doesn't exist; recipient is already ITS_Config-sourced. This entry reframes the spirit of the concern: future multi-recipient + severity-tiered routing, not present-day hardcoding.

## Allowlist drift detection — typo'd trusted-contacts entry silently quarantines [OPEN 2026-05-24]

`ITS_Trusted_Contacts` entries with a typo in the Email field silently route legitimate senders to quarantine. Operator has no signal that the list itself is wrong vs. the sender being legitimately untrusted. Same shape applies to the legacy `safety_reports.intake.allowed_senders` JSON list still alive as the dead-fallback path (per the existing "Fallback path removal after ITS_Config cutover [OPEN 2026-05-23]" entry — that fallback should be removed soon, narrowing this surface).

**Failure mode:** field PM emails a JHA from `joe.smith@evergreenrenewables.com`. Trusted-contacts row was seeded with `joe.smtih@evergreenrenewables.com` (transposed). Message routes to ITS_Quarantine instead of intake. Operator assumes everything is fine until a missed safety report surfaces downstream.

**Proposed fix (two-layer):**

1. **Validation at sheet read:** `shared/trusted_contacts._load_contacts()` adds basic email regex validation when materializing rows from `ITS_Trusted_Contacts`. Rows with malformed emails get logged to `ITS_Errors` with `error_code='trusted_contacts_row_malformed'` and skipped. Cheap; surfaces typos in the email format itself.
2. **Reconciliation sweep:** weekly job that lists distinct senders in `ITS_Quarantine` over the last 7 days. For each, compute Levenshtein distance against every active `ITS_Trusted_Contacts` Email. Distance ≤ 2 surfaces as a `near_miss_quarantine` row in `ITS_Review_Queue` with the two emails side-by-side. Low-urgency review-queue item, not an alert. Catches typos that pass basic regex (`joe.smtih@...` is a valid email format).

**Effort:** ~3 hours for layer 1 (regex validation + 5-6 unit tests). ~half-day for layer 2 (sheet read + Levenshtein + review-queue integration + tests + watchdog cadence wiring).

**Phase target:** 1.6 (lands cleanly post-Customer-0-cutover; layer 1 can ship immediately once `_load_contacts` is being touched anyway).

**Revisit when:** layer 1 — next touch of `shared/trusted_contacts.py`. Layer 2 — Phase 1.6 hardening, or operator first encounters a near-miss-typo incident.

Surfaced: 2026-05-24 hardcoded-values audit brief, §B1.

## Box folder delete-and-recreate breaks folder ID resolution [OPEN 2026-05-24]

Box folder IDs are stable across renames but NOT across delete-and-recreate. If someone deletes a project folder in Box and recreates it with the same name, uploads to the stale ID will land in the wrong place (or fail, depending on SDK behavior against trashed folders — needs verification: the boxsdk 3.x trashed-folder upload path returns success or error?).

**Failure mode (silent variant — needs SDK verification):** if Box returns 2xx on upload-to-trashed-folder, ITS-generated files land in trash invisibly. Operator sees no upload error; thinks files are filed correctly. Real-world impact: documents lost until someone notices missing files in the active folder.

**Failure mode (loud variant):** Box returns error; intake daemon surfaces via triple-fire CRITICAL alert. Operator gets the alert but the failure cause ("404 folder not found" against a folder that "exists" in Box UI under a new ID) is opaque without tribal knowledge of the delete-recreate gotcha.

**Proposed fix (depends on A2 landing first):**

1. **Startup validation** in the new `shared/project_routing.py` (or whatever lands from A2): every active row's `Box Folder ID` must resolve via Box API to a non-trashed folder. Validation runs at daemon startup AND in a weekly reconciliation watchdog check. Log WARN + skip routing to invalid folders rather than crash.
2. **Operator runbook entry**: "If a Box folder is recreated, update the routing sheet with the new ID. The old ID will WARN in watchdog within 24 hours regardless."
3. **SDK trashed-folder behavior verification:** one-off smoke test against a deliberately-trashed sandbox folder to confirm whether boxsdk 3.x upload returns error or silently lands in trash. Document the answer in `docs/references/box_sdk_gotchas.md` (or similar).

**Effort:** ~2 hours for validation logic + watchdog wiring (mostly straightforward once A2's routing sheet exists). ~30 min for the SDK behavior smoke test.

**Phase target:** Phase 2 — depends on A2 landing first, since this is the validation layer for that routing config.

**Revisit when:** A2 lands; bundle this immediately after as the second PR in the config-migration cluster.

Surfaced: 2026-05-24 hardcoded-values audit brief, §B2.

## Future PDF/JHA field extraction needs found-flag pattern [OPEN 2026-05-24]

Phase 1.5 work introduces PDF-form-field extraction (and possibly free-text regex extraction) for JHA documents inbound from field PMs. Different field PMs format dates, names, and other fields inconsistently — one types `5/24/26`, another types `2026-05-24`, another writes `May 24`. Naive regex or PDF-form-field-by-name lookup silently extracts blank when the format doesn't match.

(Note: this is NOT an extension of `box_migration/parse_job_v3.py`, despite the audit brief's framing. `parse_job_v3` parses Box folder *names* against the 4 active project-folder taxonomies — see `tests/test_parse_*.py` for its scope. JHA field extraction is a distinct future workstream that hasn't been built yet.)

**Failure mode:** blank field in Smartsheet row. Downstream consumers (`safety_reports.weekly_generate`, reports, rollups) silently skip the row or compute wrong totals. No alert fires because "blank field" is not an error from the parser's perspective — it just didn't match. Worst case: a weekly safety report omits a critical incident because the date field was blank.

**Proposed fix:**

1. **Each extracted field returns a `(value, found: bool, confidence: float)` triple, not a bare value.** Existing anomaly_logger + review_queue + confidence-threshold convention (Op Stds §35) already covers the routing — if a *required* field comes back `found=False`, the row routes to `ITS_Review_Queue` with a flag instead of silently writing blank.
2. **Build a corpus of real JHA samples** at the Phase 1.5 PDF-extraction workstream's design phase. Run extraction across the corpus, measure miss rate per field. Iterate format detection (multi-pattern regex, fuzzy date parser like `dateutil.parser`, etc.) until miss rate is acceptable for required fields.
3. **Customer-facing JHA template** — produce a fillable form template that constrains the format at submission time, so future fields are pre-canonicalized. Reduces extraction burden for everyone.

**Effort:** large — this is part of the Phase 1.5 PDF-extraction workstream design itself, not a separable cleanup. Multi-session work. The found-flag pattern alone is small (a few hours) but the corpus + iteration + customer-template + downstream-consumer wiring all add up to ~2-3 sessions.

**Phase target:** 1.5 — directly part of PDF extraction workstream design. Solve found-flag + corpus + template together; don't ship PDF extraction without them.

**Revisit when:** Phase 1.5 PDF-extraction workstream brief gets drafted (the regex-side concerns belong in that brief).

Surfaced: 2026-05-24 hardcoded-values audit brief, §B3. Cross-ref Op Stds v11 §35 (confidence-scored extraction → review queue routing pattern).

## Configuration validation at daemon startup [OPEN 2026-05-24]

Once items A2 / A3 / A5 (and the existing trusted-contacts work) migrate config into Smartsheet, daemons fetch config at startup with no formal validation step. A malformed row, missing key, or unresolvable folder ID can let the daemon enter its main loop with broken config — it'll fail per-cycle at unpredictable points instead of failing loud at startup.

**Failure mode:** operator typos an ITS_Config row. Daemon starts. First poll cycle runs. Per-cell-write fails in some downstream call. ITS_Errors fills with cryptic errors. Operator's mental model: "ITS broke, why is the watchdog quiet?" — because the watchdog can't distinguish "broken config" from "broken external API."

**Proposed fix:** new `shared/config_validation.py` with a single `validate_all()` entry point called from every daemon's `main()` before the loop starts. Per-daemon manifest of required keys + validators:

- All required ITS_Config keys present (per a per-daemon registry — `intake_poll.REQUIRED_CONFIG`, etc.).
- All email addresses pass `^[^@]+@[^@]+\.[^@]+$`.
- All Box folder IDs resolve via Box API to non-trashed folders (depends on A2 landing).
- All referenced Smartsheet sheet IDs exist (cheap `get_sheet_summary`-style probe).

On failure: log full report to Sentry + ITS_Errors, exit non-zero. **Do not enter the loop with broken config — fail loud.**

**Effort:** ~half-day session including the validation module + per-daemon registries + tests + integration smoke + runbook update ("if a daemon fails to start, check the Sentry / ITS_Errors entry for the validation report").

**Phase target:** 1.6 — lands after A2/A3/A5 migrate config into Smartsheet. Sequence: config-migration cluster → validation layer.

**Tag:** `config-migration` (the consumer side).

**Revisit when:** A2 lands, AND a third polling daemon is queued, OR operator hits the silent-fallback-into-bad-config failure mode in real ops.

Surfaced: 2026-05-24 hardcoded-values audit brief, §C1.

## Config-change audit trail [OPEN 2026-05-24]

Once configuration lives in Smartsheet (ITS_Config rows + future `ITS_Trusted_Contacts` / `ITS_Project_Routing` / `ITS_Alert_Routing` sheets), changes happen without a git commit. For security-relevant config — `ITS_Trusted_Contacts` especially — this is an audit gap. Smartsheet has cell-history natively, but that history is bounded to the Smartsheet tenant; if a customer ever needs an external audit copy independent of Smartsheet (compliance requirement, post-incident forensics, vendor risk), there's no out-of-band record.

**Failure mode (low-frequency):** post-incident, operator wants to know "who added `acme@external-domain.com` to trusted contacts on 2026-XX-XX." Smartsheet cell history covers it. But if the question is "show me the entire trusted-contacts state on 2026-XX-XX" — Smartsheet's history surface is per-cell, not point-in-time-snapshot; reconstructing requires manual scrubbing.

**Proposed fix (layered):**

1. **Runbook entry:** document Smartsheet's built-in cell-history view as the canonical audit trail. Train operator on the per-cell-history surface. Low-cost, covers the common case.
2. **Weekly diff-export job** for high-stakes sheets (`ITS_Trusted_Contacts`, future `ITS_Alert_Routing`): snapshot to a versioned file in Box on a weekly cadence. Filename `<sheet_name>_<YYYY-MM-DD>.json`. Gives a point-in-time snapshot independent of Smartsheet. Watchdog Check writes a marker; missing snapshots WARN.
3. **Higher-stakes-yet option (deferred):** route trusted-contacts edits through a PR-style approval flow in a separate sheet (`ITS_Trusted_Contacts_Proposed` → operator-approval column → applied to canonical sheet). Likely overkill for solo-operator stage.

**Effort:** ~1 hour for layer 1 (runbook). ~half-day for layer 2 (snapshot script + Box upload + watchdog wiring + tests). Layer 3 is a separate workstream if it ever lands.

**Phase target:** 2 (post-Customer-1 cutover, when audit-as-deliverable becomes a customer-facing concern). Not a launch blocker for Customer 0.

**Revisit when:** first customer raises compliance / audit requirements explicitly, OR a security review session formally surfaces the gap.

Surfaced: 2026-05-24 hardcoded-values audit brief, §C2.

## Single-token blast radius for Smartsheet [OPEN 2026-05-29]

One PAT (`ITS_SMARTSHEET_TOKEN`) does ALL Smartsheet read + write across the whole system. A scope mistake on rotation (e.g. accidentally minting a read-only or viewer-scoped token) breaks every daemon at once, and — per the entry above — does so silently at first write. There is no blast-radius reduction (no separate read vs write tokens, no per-workstream tokens).

**Proposed consideration (not necessarily implement):** evaluate splitting tokens by capability or workstream at a future hardening pass, weighed against the added secret-management complexity for a solo-operator stage. Likely overkill before Customer 2+ multi-customer secret management (already deferred to 1Password CLI per the observability-stack roadmap).

**Phase target:** 2+ (revisit alongside multi-customer secrets).

**Revisit when:** a rotation incident actually causes a system-wide outage, OR multi-customer secret management lands.

Surfaced: 2026-05-29 integration-keychain-stub fix session.

## Optional `fail_closed_until` kill-switch hardening (deferred) [DEFERRED 2026-05-29]

The kill switch is **fail-OPEN by design** (Op Stds v14 §1, audit F07): if ITS_Config is unreachable, the `system.state` row is missing, or its value is invalid, `check_system_state()` resolves to ACTIVE-with-WARN so scheduled work proceeds — it is an operator-convenience pause, NOT a security control. (See the `shared/kill_switch.py` Phase 3 no-op / preserved-fail-open paragraph in the "Picklist-hardening pre-Customer-1" `[CODE DELIVERED 2026-05-23]` entry above, and the `shared/kill_switch.py` capability-table row + the `@require_active` bullet in CLAUDE.md.)

The F07 reframe (blueprint PR #23, Q8) deferred an **optional** `fail_closed_until` mechanism: a timestamp in ITS_Config (e.g. `system.fail_closed_until`) that would let the operator make the kill switch fail **CLOSED** (block / exit cleanly) until a specified time — a time-bounded hard halt for a known-bad window (e.g. "halt all scheduled work until 2026-XX-XX 09:00 while I investigate") — as defense-in-depth over the current always-fail-open behavior.

**Why deferred (not built):** the External Send Gate (Foundation Mission Invariant 1) is the real security boundary — no external transmission happens without explicit human approval regardless of kill-switch state — so a fail-CLOSED kill switch is belt-and-suspenders, not a gap. Adding it now would also complicate the deliberately-simple fail-open contract that the preserved Phase 3 decision settled on.

**Proposed shape (if built):** read an optional `system.fail_closed_until` ISO-8601 timestamp in `check_system_state()`; if present AND `now < fail_closed_until` AND the state row is unreachable/missing/invalid, return PAUSED (block) instead of the fail-open ACTIVE. Absent or past → current fail-open behavior unchanged. Keep it strictly opt-in so the default stays fail-open.

**Effort:** ~half-day (config read + one branch in `check_system_state` + tests covering present-future / present-past / absent).

**Phase target:** 2+ defense-in-depth hardening; not a launch blocker (Invariant 1 already covers the security case).

**Revisit when:** an operator ever needs a time-bounded hard halt of scheduled work (a known-bad maintenance/incident window) that the simple operator-set PAUSED state + fail-open default doesn't cover.

Surfaced: 2026-05-29 exec-ledger-cleanup session (F07 reframe Q8 ledger item). Related: the kill-switch fail-open note in the Picklist-hardening DELIVERED entry above; Op Stds v14 §1; FM Invariant 1 (External Send Gate).

## Inline doctrine-pin normalization across shared/* + safety_reports/* [DEFERRED 2026-06-01]

Tranche 0 (PR #132 — FM v11 / Op Stds v16 citation reconciliation) reconciled the *current-doctrine prose* surfaces (CLAUDE.md, README.md, the manifest) but deliberately did NOT touch the **inline doctrine-version pins in `shared/*` + `safety_reports/*` module docstrings/comments** — a sweep of **~50 sites across 17 files** (the Tranche-0 brief §7 set a "stop and report if >15 sites" guardrail; this is far past it). The pins cite a mix of **FM v8 / Op Stds v11 / v13 / v14**, each recording the doctrine version current *when that module was written* — i.e. historical provenance. Per Op Stds §14 (preservation-over-refactor) + §42 (self-documentation), and because `check_doctrine_drift.py` deliberately scopes `.py` files OUT of the M1 version-drift tier, these are correctly left as-is for now: they are not current-doctrine prose.

Two things a future normalization pass should resolve:
1. **Decide the convention (operator call).** Either (a) leave each pin as build-time provenance (cheapest; the version dates the decision), or (b) normalize to an "as-of v16 / FM v11" convention with the build-time version noted. Stylistic/provenance choice, not a correctness fix.
2. **One real correctness fix to fold in:** `safety_reports/weekly_send.py:72` cites `Op Stds v11 §23.3` for the "sheet-level columns added via UI, not API" constraint. **§23.3 resolves nowhere** in any blueprint version (§23 is the Workspace-Topology stub). Tranche 0 corrected the *matching* CLAUDE.md citation to **§19 (Smartsheet UI-only constraint)** — the canonical home, confirmed by the doc-reconciliation-auditor across 5 commits. Retarget `weekly_send.py:72` §23.3→§19 here so code + doc agree. (`shared/picklist_sync.py:23` similarly cites `Op Stds v11 §25` for "MCP-gap REST fallback" while §25 in live v16 is "per-workstream sheets" — verify and retarget during the sweep.)

**Effort:** ~1–2 hours (mechanical, but each of ~50 pins wants a per-site judgment: bump-version vs leave-as-provenance vs retarget-section). **Phase target:** not a launch blocker — provenance pins don't affect behavior.

**Revisit when:** an operator wants a uniform doctrine-pin convention across the code, or the next session that touches `weekly_send.py` / `picklist_sync.py` for another reason (fix the §23.3→§19 / §25 mis-cites opportunistically per §14 retrofit-when-touched).

Surfaced: 2026-06-01 Tranche 0 doctrine-citation reconciliation (PR #132). Related: PR #132 body "Flags & operator decisions" §2; CLAUDE.md §23.3→§19 correction.

## ITS_Active_Jobs Address cells blank — office PM fill required [OPEN 2026-06-03]

The 6 rows seeded into ITS_Active_Jobs (PR #155) have blank Address values. Real addresses were not invented (§4 — adversarial input / data fidelity; no structured live source exists). The Safety Portal's Work Location auto-fill path will return empty strings until these cells are populated.

**Required action:** office PM opens ITS_Active_Jobs in Smartsheet (Operations workspace → Safety Portal folder) and fills the Address column for all 6 rows (bradley-1, bradley-2, evergreen-hq, poa, rockford-s1, rockford-s2) with the correct street addresses before the Safety Portal goes live.

**No code change required.** The column exists and is schema-correct; the data gap is operational.

**Tag:** `safety-portal`, `data-gap`.

**Revisit when:** Safety Portal goes live (before activating Work Location auto-fill).

Surfaced: 2026-06-03 Safety Portal config sheets session (PR #155). Related: `docs/runbooks/safety_portal_config_sheets.md`.

## Safety Portal — deploy + provisioning deferred [OPEN 2026-06-04]

Cloudflare D1/R2/Pages-or-Workers resource creation, `wrangler secret put SESSION_SIGNING_SECRET`, `wrangler deploy`, and custom domain `safety.evergreenmirror.com` binding are all deferred. Blocked on operator obtaining a `CLOUDFLARE_API_TOKEN` with the required scopes (Workers / D1 / R2 / Pages, or Workers Static Assets depending on topology decision below). The Safety Portal Phase 2 code (PR #158) was locally validated end-to-end via `wrangler dev --local` + Playwright before deferral.

**Required operator steps (at deploy time):**
1. `wrangler login` (or set `CLOUDFLARE_API_TOKEN`).
2. `wrangler d1 create its-safety-portal-db` → copy the returned `database_id` into `wrangler.toml`.
3. `wrangler d1 migrations apply its-safety-portal-db` (remote).
4. `wrangler secret put SESSION_SIGNING_SECRET` (≥32-byte random value).
5. `wrangler deploy` (or Pages upload if Pages topology wins).
6. Bind custom domain `safety.evergreenmirror.com` → Worker/Pages route.

**Tag:** `safety-portal`, `deploy`, `cloudflare`.

**Revisit when:** operator has CLOUDFLARE_API_TOKEN. Anticipated pre-Phase-3 portal go-live.

Surfaced: 2026-06-04 Safety Portal Phase 2 session (PR #158). Session log: `docs/session_logs/2026-06-04_safety-portal-phase2-cloudflare-scaffold.md`.

## Safety Portal — Pages-vs-Workers Static Assets topology TBD [OPEN 2026-06-04]

Blueprint `workstreams/safety-portal/mission.md` §11 and any DNS/route assumptions were written against a Cloudflare Pages (`*.pages.dev`) topology. Cloudflare's current guidance (confirmed via cloudflare-docs MCP, 2026-06) recommends **Workers Static Assets** as the standard model for serving SPAs from a Worker. The Phase 2 code (`safety_portal/worker/`) is deploy-agnostic (Vite builds to `dist/`; `wrangler.toml` can target either). The decision must be made at deploy time.

**Decision required:** Workers Static Assets (current best-practice; better D1/binding integration) vs Cloudflare Pages (`*.pages.dev` + Pages-native CI). Update blueprint `workstreams/safety-portal/mission.md` §11 and DNS config to match.

**Tag:** `safety-portal`, `cloudflare`, `architecture`.

**Revisit when:** Safety Portal deploy step (above entry). One decision, made once.

Surfaced: 2026-06-04 Safety Portal Phase 2 session (PR #158). Related: `docs/tech_debt.md` "Safety Portal deploy + provisioning deferred" entry above.

## Safety Portal — Worker-side capability-gate for TS not covered by Python AST gate [OPEN 2026-06-04]

`tests/test_capability_gating.py` enforces Invariant 1 at the Python AST level. It does not reach the TypeScript Worker at `safety_portal/worker/`. Phase 2 Worker is send-free by inspection (no email, no Graph, no Anthropic). When the Phase 5 HMAC email shim lands (the Worker emits a verified email to `safety@` → `intake.py`), this gap becomes load-bearing.

**Proposed fix (at Phase 5):** add a TS-equivalent capability-gate step — either a `tsc --noEmit` + `grep`-based AST scan of Worker entrypoints for forbidden imports, or extend `test_capability_gating.py` to scan `.ts` entrypoints with the same pattern. Phase 2 does not require this yet.

Note: the Phase 2 brief referenced "Decision 4" for this item, but no named blueprint decision with that ID exists. The decision is tracked here instead.

**Tag:** `safety-portal`, `capability-gate`, `invariant-1`.

**Revisit when:** Phase 5 email-shim work begins.

Surfaced: 2026-06-04 Safety Portal Phase 2 session (PR #158). Related: `tests/test_capability_gating.py`.

## Safety Portal — bcryptjs cost-10 may exceed Workers Free 10ms CPU cap [OPEN 2026-06-04]

`safety_portal/worker/src/worker/auth.ts` uses bcryptjs with cost factor 10. On the Cloudflare Workers **Free plan**, CPU time is capped at 10ms per request (Error 1102). A bcrypt compare at cost 10 can take 50–100ms in V8, reliably triggering the cap on login.

**Options at deploy:**
1. Deploy on Cloudflare Workers **Paid plan** (5ms CPU wall removed; 30s+ allowed) — simplest.
2. Swap `auth.ts` to `Web Crypto PBKDF2-SHA-256` at 100k iterations — CPU-comparable security, runs within Free limits, requires `nodejs_compat` flag and minor code change.

**Tag:** `safety-portal`, `cloudflare`, `performance`.

**Revisit when:** Safety Portal deploy. Decision is Paid-plan vs PBKDF2 swap. Decide before `wrangler deploy`.

Surfaced: 2026-06-04 Safety Portal Phase 2 session (PR #158).

## Safety Portal — no server-side session revocation [OPEN 2026-06-04]

`safety_portal/worker/src/worker/middleware/requireSession.ts` validates a HMAC-signed session cookie (iat + 90-day expiry) but does NOT check a server-side session table. A deprovisioned user's cookie remains valid until `iat + 90d`. A stolen cookie cannot be individually invalidated before expiry.

**Proposed fix (Phase 7):** add a D1 `sessions` table (session_id, user_id, created_at, revoked_at); `requireSession` queries it; admin route provides revoke-session capability.

**Tag:** `safety-portal`, `auth`, `security`.

**Revisit when:** Phase 7 admin route build, or earlier if a user is deprovisioned while a live session exists.

Surfaced: 2026-06-04 Safety Portal Phase 2 session (PR #158).

## ITS_Active_Jobs AUTO_NUMBER `Job ID` column — manual operator UI step pending [OPEN 2026-06-05]

The Smartsheet REST API cannot create `AUTO_NUMBER` columns (verified: bare `type:AUTO_NUMBER` → `errorCode 1008`; UI-only type). The Phase 3 migration (PR #160) did the API-doable parts (4 contact columns + rename `Job ID`→`Job Slug`, freeing the title) and detects-or-instructs if the `Job ID` AUTO_NUMBER column is missing. Operator must add the `Job ID` AUTO_NUMBER column in the Smartsheet UI to complete the schema: prefix `JOB-`, 4-digit fill, start 1. `shared/active_jobs.py` reads it the moment it exists.

**Required operator steps (Smartsheet UI):**
1. Open ITS_Active_Jobs in the Smartsheet UI.
2. Insert a new column named `Job ID`, type AUTO_NUMBER (System column).
3. Set prefix `JOB-`, fill width 4, start 1.
4. Confirm `shared/active_jobs.py::get_job_by_id()` resolves correctly on the next lookup.

**Tag:** `safety-portal`, `smartsheet-api-constraint`, `data-gap`.

**Revisit when:** operator has Smartsheet UI access at deploy time. Required before Job-ID-keyed portal queries work end-to-end.

Surfaced: 2026-06-05 Safety Portal Phase 3 session (PR #160). Session log: `docs/session_logs/2026-06-05_safety-portal-phase3-job-model.md`.

## "New Job" Smartsheet form on ITS_Active_Jobs — operator-UI creation pending [OPEN 2026-06-05]

Smartsheet forms are UI-configured (not API-creatable). A "New Job" form on ITS_Active_Jobs is needed so office PM can add jobs without opening the sheet directly. Required fields: Project Name, Address, Stakeholder Name / Email / Phone (email required), Safety Reports Contact Email (required), Active. Job ID auto-fills from the AUTO_NUMBER column (off the form).

**Required operator steps (Smartsheet UI):**
1. Open ITS_Active_Jobs → Forms → Create New Form.
2. Add and mark required fields per above.
3. Set form title "New Job".
4. Share form URL with office PM.

**Tag:** `safety-portal`, `smartsheet-ui`, `data-gap`.

**Effort:** ~15 minutes (UI-only).

**Revisit when:** deploy session, after the AUTO_NUMBER column entry above is complete.

Surfaced: 2026-06-05 Safety Portal Phase 3 session (PR #160). Related: `docs/tech_debt.md` AUTO_NUMBER entry above.

## Phase 5 manual week-sheet additions [OPEN 2026-06-05]

Operator-decided edge case (2026-06-05): if a PM submits a safety doc directly (outside the portal) for a specific job-week, the operator adds a row + the safety doc directly to the per-job week sheet, fills the relevant cells; `intake.py` ignores the manually-added row and `weekly_generate.py` rolls it into the compiled packet like any other doc. This is by design — no automation needed for an occasional manual correction.

**Tag:** `safety-portal`, `operator-workflow`.

**Revisit when:** Phase 5 build. Low-urgency; operator-decided.

Surfaced: 2026-06-05 Safety Portal Phase 3 session (PR #160).

## Worktree discipline for safety_reports edits [OPEN 2026-06-05]

Phase 3 (PR #160) was built in `~/its` directly (not a git worktree) because the `resolve_project()` legacy was retired and nothing was incoming to the sandbox during development. However, any live `safety_reports/` edit in `~/its` goes live in the launchd daemon tree on the next 60s poll cycle. Future `safety_reports/` feature edits should follow `docs/operations/worktree_discipline.md` and use a dedicated worktree to avoid hot-path exposure of WIP code.

**Tag:** `worktree-discipline`, `safety-reports`.

**Revisit when:** next `safety_reports/` edit session.

Surfaced: 2026-06-05 Safety Portal Phase 3 session (PR #160). Related: `docs/operations/worktree_discipline.md`.

## ITS_Active_Jobs column order cosmetically scrambled [OPEN 2026-06-05, low]

The 4 contact columns (Stakeholder Name, Stakeholder Email, Stakeholder Phone, Safety Reports Contact Email) were added one-at-a-time to ITS_Active_Jobs after the initial schema, causing them to interleave with Active/Notes and the system columns in the Smartsheet UI. Column order is not load-bearing — `shared/active_jobs.py` looks up columns by title, not position. Reorder in the Smartsheet UI if desired for operator readability.

**Tag:** `safety-portal`, `cosmetic`, `smartsheet-ui`.

**Effort:** ~5 minutes (UI drag-to-reorder).

**Revisit when:** convenience; not a blocker.

Surfaced: 2026-06-05 Safety Portal Phase 3 session (PR #160).

## ITS_Active_Jobs CC recipients are operator-entered, not allowlist-validated [OPEN 2026-06-05, accepted-risk]

`shared/active_jobs.py` `cc_emails` (and the TO `safety_reports_contact_email`) come from operator-typed TEXT cells on ITS_Active_Jobs. They are email-shape-validated + de-duped, but NOT checked against `ITS_Trusted_Contacts` or any allowlist. When Phase 5 `weekly_send` wires up `cc_emails`, a PM socially-engineered into entering an attacker address would CC the compiled packet to an unintended party. **Accepted risk** (trusted-operator-input model; the External Send Gate still requires explicit `Approved for Send` before any send). Phase 5 `weekly_send` must document that CC/TO recipients are unverified operator-entered addresses, and log the full resolved TO+CC list at send (already in the Phase 5 brief).

**Tag:** `safety-portal`, `safety-reports`, `phase-5`, `accepted-risk`.

**Revisit when:** building Phase 5 `weekly_send` recipient resolution.

Surfaced: 2026-06-05 Safety Portal Phase 3 contacts amendment (ops-stds-enforcer W1).

## Safety Portal — toolbox talk header context missing from form definitions [OPEN 2026-06-05, low]

The source Toolbox Talk PDFs have no operator header fields (the digital record gets job and work-date from the submission envelope; the sign-in section's first row serves as the instructor record). The 5 `toolbox-talk-*.json` definitions are faithful to the source PDFs and therefore contain no Presenter or Date-on-page field. If a Presenter/Date-on-page header field is wanted beyond what the envelope provides, it must be added explicitly to those definitions.

**Tag:** `safety-portal`, `form-definitions`, `low`.

**Effort:** trivial (add a field to the definition + update the catalog row).

**Revisit when:** PM confirms whether a header field is wanted on the rendered PDF.

Surfaced: 2026-06-05 Safety Portal Phase 4 PR 1 session (PR #164). Related: `safety_portal/forms/toolbox-talk-*.json`.

## Safety Portal — job-specific JHA variant content deferred [OPEN 2026-06-05]

The parent/variant mechanism is built (ITS_Forms_Catalog `Parent Form` + `Variant Tag` columns; meta-schema `variantOf` field in form definitions). Specific job-site JHA variants (e.g., `jha-bradley`) are added later as: (1) a new row in ITS_Forms_Catalog with `Parent Form = jha` + a `Variant Tag`; (2) a new `safety_portal/forms/jha-<variant>.json` definition inheriting/overriding the parent. No code change to the renderer — variant resolution is data-driven.

**Tag:** `safety-portal`, `form-definitions`, `phase-4+`.

**Revisit when:** PM identifies a job with site-specific JHA requirements.

Surfaced: 2026-06-05 Safety Portal Phase 4 PR 1 session (PR #164). Related: `safety_portal/forms/meta-schema.json` `variantOf`, ITS_Forms_Catalog `Parent Form`/`Variant Tag` columns.

## [OPEN] Worker-side send-gate enforcement (the TS Worker is outside the Python AST capability-gate)

**What:** `tests/test_capability_gating.py` enforces Invariant 1 (no send capability on
generation scripts; no AI on send scripts) by AST-scanning Python under `shared/` +
`safety_reports/`. It does NOT reach the TypeScript Cloudflare Worker
(`safety_portal/worker/`). As of Phase 5 PR 2 the Worker holds the HMAC signing secret +
the internal bearer token, so it is no longer trivially "send-free by binding-absence" —
its send-free posture rests on code review + the module docstring only. The **pull model**
keeps the Worker send-free by design (it serves a queue + accepts a receipt; it never
initiates outbound), but nothing structurally PREVENTS a future Worker edit from acquiring
an outbound `fetch()` to an external host.

**Fix (when the Worker surface grows):** add a CI grep / ESLint rule forbidding `fetch(` in
`safety_portal/worker/` except to an allowlist (the ASSETS binding), as the TS-side
equivalent of `test_capability_gating.py`. Surfaced by `ops-stds-enforcer` (W2).

**Tag:** `safety-portal`, `security`, `invariant-1`, `phase-5`, `medium`.

**Revisit when:** the Worker gains any new outbound-capable code path, or at the deploy hardening pass.

Surfaced: 2026-06-05 Safety Portal Phase 5 PR 2 (transport queue).

## [CUTOVER-BLOCKING] Safety Portal Phase 5 — deploy prerequisites (Cloudflare secrets + D1 + wrangler.jsonc IDs) [OPEN 2026-06-05]

Additional prerequisites surfaced by Phase 5 PR 2 (transport queue, PR #169) beyond the base deploy entry above:

1. `CLOUDFLARE_API_TOKEN` — operator obtains (Workers + D1 + R2 scopes); `wrangler login` or env var.
2. `wrangler d1 create its-safety-portal-db` → copy `database_id` into `wrangler.jsonc` (placeholder present).
3. `wrangler d1 migrations apply` (remote, migrations 0001–0005).
4. Worker secrets (two new Phase 5 secrets, in addition to `SESSION_SIGNING_SECRET`):
   - `wrangler secret put HMAC_PAYLOAD_SECRET` (≥32-byte random; used by `shared/portal_hmac.py` verify contract; cross-language HMAC validated in PR #169 tests).
   - `wrangler secret put PORTAL_INTERNAL_API_TOKEN` (bearer token for `/api/internal/*`; mirrored to Keychain as `ITS_PORTAL_INTERNAL_TOKEN` on the Mac side).
5. Keychain entries on the Mac: `ITS_PORTAL_HMAC_SECRET` (same value as `HMAC_PAYLOAD_SECRET`) + `ITS_PORTAL_INTERNAL_TOKEN`.
6. `wrangler deploy` → custom domain binding.

**Tag:** `safety-portal`, `phase-5`, `deploy`, `cloudflare`.

**Revisit when:** Safety Portal deploy session. This entry extends the earlier "deploy + provisioning deferred" entry; that entry covers the base steps; this one covers Phase 5-specific secrets and the D1 migration count update.

Surfaced: 2026-06-05 Safety Portal Phase 5 PR 2 session (PR #169).

## [OPEN] Safety email-intake retire — operator-manual + future-PR follow-ups [2026-06-05]

The 2026-06-05 retire of the safety email-intake path (PR: chore/retire-safety-email-intake)
left these:

1. **Operator-manual: unload the launchd job** `org.solutionsmith.its.safety-intake` on the
   production Mac — `scripts/uninstall_safety_intake_daemon.sh`. `intake_poll.py` is a retired
   tombstone (quiet WARNING no-op on `poll_once`); until unloaded it runs every 60s doing
   nothing. Never done from code.
2. **Operator-manual: delete the `Job Slug` Smartsheet COLUMN** (if/when wanted) — by hand in
   the UI after confirming nothing reads it. Never from a migration. (Runbook: safety_portal_job_management.md Task B.)
3. **Future PR: delete WPR_Pending_Review** (sheet 3096105695793028 + `SHEET_WPR_PENDING_REVIEW`)
   — GATED on the `weekly_generate`/`weekly_send` rewire to `WSR_human_review`. WPR is
   DECOMMISSIONED-by-doc but still read/written by the live weekly daemons; deleting the
   constant/sheet now breaks them. Pairs with the existing Phase-5 weekly-rewire tech-debt entry.
4. **Future: cleanup the tombstone + its assets** — delete `safety_reports/intake_poll.py`,
   `scripts/launchd/org.solutionsmith.its.safety-intake.plist`, and `install/uninstall_safety_intake_daemon.sh`
   once no orphan plist remains and `portal_poll.py` has landed.
5. **Preserved (do NOT touch):** `shared/graph_client.py` (incl. `fetch_latest_inbound_timestamp`,
   whose docstring still says "Used by watchdog Check F" — stale, fix in a future shared/-touching
   PR) and all other `shared/` primitives — Email Triage reuses them.

**Tag:** `safety-portal`, `email-triage`, `cleanup`, `phase-5`, `medium`.

Surfaced: 2026-06-05 safety email-intake retire.

## WPR_Pending_Review final removal (decommission-by-doc → delete)

After the Phase-5 WSR rewire (PRs portal-rewire-pr1..pr4, 2026-06-05), **no live
runtime code references `WPR_Pending_Review`**: `weekly_generate` (compile→WSR),
`weekly_send` + `weekly_send_poll` (send←WSR), and `watchdog` Check I (row-exist←WSR)
are all repointed. The constant `shared.sheet_ids.SHEET_WPR_PENDING_REVIEW` + the
`shared.picklist_validation` WPR registry entry are kept (decommission-by-doc) only
because a few non-runtime refs remain:

  - `scripts/smoke_test_watchdog_catchup.py` — still seeds/clears WPR rows to simulate
    a populated week; needs a WSR rewrite (the catch-up now checks WSR via the Saturday
    `Week Of`).
  - `tests/test_picklist_validation.py` — asserts the WPR Send Status registry entry.
  - the constant + picklist entry themselves.

**Follow-up (trivial, after the operator deletes the WPR sheet):** rewrite the catch-up
smoke to WSR, drop the picklist WPR entry + its test assertion, then delete
`SHEET_WPR_PENDING_REVIEW`. The WPR Smartsheet sheet itself is operator-deleted.

**Tag:** `safety-portal`, `cleanup`, `phase-5`, `low`.

Surfaced: 2026-06-05 WSR rewire (PR4).

## [OPEN 2026-06-09] Publish daemon: rollback UI picker missing

The backend rollback path is fully built: `apply_publish` supports a `rollback` op, the daemon handles it, and `PublishOp` carries the rollback target. The **editor's retired-version-history PICKER UI** is the only missing piece — there is no way to select a rollback target in the admin form without direct API calls. The rollback op is functional today via API.

**Fix:** add a dropdown in `FormEditor.tsx` that populates from the retired form definitions (versions with `status: "retired"` in the catalog) and issues a `rollback` publish-request.

**Tag:** `safety-portal`, `phase-2`, `form-editor`, `low`.

**Revisit when:** a rollback is operationally needed, or at the start of Phase-3 form-editor polish.

Surfaced: 2026-06-09 Phase-2 Form Manager build (PRs #203–#218).

## [OPEN 2026-06-09] Publish daemon: privileged subprocess chain is operator-validated-live only

`safety_reports/publish_daemon.py` orchestrates a chain of git/gh/wrangler subprocess calls (commit, create PR, wait for CI, merge, deploy). Unit tests mock at the subprocess boundary per Op Stds §30. PR #218's `_wait_for_ci` + `_reset_to_main` ran live for the first time during the operator's recovery session. No dedicated integration test harness for the full commit→merge→deploy chain exists.

**Fix:** build a dry-run harness (flag `--dry-run`) that exercises the subprocess chain against a throwaway branch without merging or deploying, so CI can catch subprocess-interface regressions. Until then, every daemon code change to the privileged subprocess chain requires operator live-smoke before merge.

**Tag:** `safety-portal`, `phase-2`, `publish-daemon`, `medium`.

**Revisit when:** the publish daemon code is modified, or at the Phase-3 hardening pass.

Surfaced: 2026-06-09 Phase-2 Form Manager build (PR #218).

## [OPEN 2026-06-09] Form editor: S1 per-item scale/comment authoring from scratch

The `hsse` form uses `scale` and `comment` item-level attributes. These survive an **edit** operation today (existing values are preserved in the round-trip through `apply_publish`). However, there is **no UI in the form editor** to set `scale` or `comment` values when creating a new item from scratch. A new `hsse`-type form authored through the editor would produce items without these attributes.

**Fix:** add `scale` / `comment` optional fields to the item-creation widget in `editorModel.ts` / `FormEditor.tsx`. Scope: narrow UI change, no backend changes needed.

**Tag:** `safety-portal`, `phase-2`, `form-editor`, `low`.

**Revisit when:** a new HSSE-type form is authored via the editor.

Surfaced: 2026-06-09 Phase-2 Form Manager build (PRs #203–#218).

## [CUTOVER-BLOCKING] [OPEN 2026-06-09] Safety Portal — no rate limiting on `/api/login` or `/api/*` (Part-A A2)

Nothing throttles the portal Worker: `/api/login` runs `bcrypt.compare` at cost 10 per attempt (brute-force + a CPU-cost amplification vector), and `/api/submit` + all routes are unbounded.

**Fix (operator, cutover):** add Cloudflare **rate-limiting rules** (dashboard → Security → WAF → Rate limiting rules) — tight on `/api/login` (~5 req / 10 s / IP → ~10 min block), looser blanket on `/api/*`. Documented as a cutover step in `safety_portal/README.md` ("Production hardening — operator cutover steps"). In-code alternative: the Workers **`ratelimit` binding** (in-repo + testable) — adopt if GA for the account at deploy time. **Operator-gated** (Cloudflare account/dashboard), so NOT implemented in code this session per the operator's call.

**Tag:** `safety-portal`, `security`, `operator-action`, `cutover`.

**Revisit when:** Evergreen production cutover, or when the `ratelimit` binding is confirmed GA.

Surfaced: 2026-06-09 Part-A production-hardening session (A2).

## [OPEN 2026-06-09, low] Orphaned Reports sheet — column styling not applied (Part-C C1 cosmetic)

`scripts/migrations/build_orphaned_reports_sheet.py` creates the Orphaned Reports sheet (built live 2026-06-09, `SHEET_ORPHANED_REPORTS=2577084374273924`) with the correct columns + types, but does NOT apply the cosmetic column WIDTHS/formats the brief C1 "styled" item mentioned (it mirrors `build_its_active_jobs_sheet.py`, which also doesn't style in-script). The sheet is fully functional with default widths.

**Fix:** add a `_apply_styles_best_effort`-style pass (per-column width/format) to the migration AND a one-shot `update_column` styling run against the existing live sheet (find-or-create skips a re-create, so the existing sheet needs the columns updated directly), OR fold it into `scripts/style_safety_portal_sheets.py`.

**Tag:** `safety-portal`, `orphaned-reports`, `cosmetic`.

**Revisit when:** the operator finds the default widths inconvenient, or a styling pass is run across the Safety Portal sheets.

Surfaced: 2026-06-09 Part-C session (functional done; cosmetic styling deferred).

## [OPEN 2026-06-09, low] Draft cache stores one draft per account — starting a new form replaces it

`src/lib/draftCache.ts` (PR #250) stores exactly ONE draft per admin account (localStorage key `its-portal-draft:v1:<username>`). Opening the editor for a second form (or creating a brand-new form while a WIP edit exists) silently overwrites the cached draft for that account.

This is accepted behavior — the operator builds one form at a time, and the confirm-discard dialog before starting a fresh form guards against accidental loss. However, the limitation is worth tracking: if concurrent multi-form editing is ever needed, the key scheme would need to include the form identity (e.g., `its-portal-draft:v1:<username>:<formId>`).

**Fix (if multi-form editing is ever desired):** change the localStorage key to include the form identity; expose a "clear draft" call per form; update the editor mount logic to auto-restore the per-form draft.

**Tag:** `safety-portal`, `form-editor`, `draft-cache`, `low`.

**Revisit when:** operator requests concurrent multi-form edit capability, or a WIP draft-loss incident is reported.

Surfaced: 2026-06-09 Form Editor UX + draft-caching session (PR #250; deliberate single-slot design).

## [OPEN 2026-06-09, low] Worker publish-reject paths return bare error codes — no `reason` field for server-side parity with `explainPublish`

The Worker's `POST /api/admin/publish` endpoint returns HTTP 400/401 with a bare JSON `{ error: "..." }` body for validation failures. `FormsPage.explainPublish` (PR #249) maps these codes on the client side, but the server never writes a human-readable `reason` alongside the code. If a new reject path is added on the Worker (or a Hono middleware fires before the handler), `explainPublish` may encounter an unmapped code and fall back to the "code + HTTP status" catch-all.

The current fallback is explicit and non-silent (shows "code + HTTP status"), so this is low-severity. It is deferred because the client-side fix (PR #249) is self-contained and the Worker paths are stable.

**Fix (optional):** add a `reason` field to the Worker's reject bodies so the client can display the server-authored message directly, removing the client-side mapping table entirely.

**Tag:** `safety-portal`, `form-editor`, `error-messaging`, `low`.

**Revisit when:** a new Worker reject path surfaces an unmapped code in production, or a UI polish pass is done on the publish flow.

Surfaced: 2026-06-09 Form Editor UX + draft-caching session (PR #249; client fix is self-contained).

---

## 2026-06-09 Evening Forensic Audit — Deferred Findings

The following entries were surfaced by a read-only 12-dimension forensic audit of the Safety Portal this session. H2, M3, M8, and the SENDING-picklist regression were fixed in PRs #247/#252/#253 respectively. The findings below are explicitly deferred.

## [OPEN 2026-06-09] Safety Portal M1 — authenticated submitter can overwrite a peer's PENDING submission

`worker/index.ts` `/api/submit` accepts a client-controlled `submission_uuid` and executes `INSERT OR REPLACE` — this resets `box_verified=0` on an existing row. `/api/recent` leaks any job's latest UUID+payload (not scoped to the authenticated user). The intake dedup only guards already-filed UUIDs; a plain overwrite writes no `audit_log` row. An authenticated submitter can therefore silently replace a peer's un-filed submission with attacker-controlled content, leaving no audit trail.

Not currently exploitable remotely (requires an authenticated session), but a defense-in-depth gap before multi-user production rollout.

**Fix:** server-generate `submission_uuid` (remove client control) OR reject a UUID collision from a different actor. Stop `/api/recent` from leaking arbitrary-job UUIDs not owned by the caller. Add an `audit_log` row for every overwrite attempt.

**Collision risk:** active SPA work shares `worker/index.ts`. Coordinate with any in-flight Worker edits before touching `/api/submit`.

**Tag:** `safety-portal`, `security`, `adversarial-input`, `medium`.

**Revisit when:** next Worker security hardening pass, or before real PM users are provisioned on a live tenant.

Surfaced: 2026-06-09 12-dimension forensic audit (M1).

## [OPEN 2026-06-09] Safety Portal M2 — capability gate is static-AST-import-only; transitive and dynamic paths are unchecked

`tests/test_capability_gating.py::_imports_in` is static AST-import-only — blind to `importlib.__import__` dynamic imports, has no transitive closure over `shared/` + `safety_reports/`, and `WALKED_ROOTS` excludes `scripts/`. The docstring ("fails at CI before it can ship") overstates the gate's reach.

**Fix:** add `importlib` / `__import__` needles to the banned-pattern scanner; build a transitive-closure walk over `shared/` + `safety_reports/` (not just the top-level file); add a `scripts/`-scoped check for the no-AI-and-send combination.

**Tag:** `security`, `capability-gate`, `testing`.

**Revisit when:** next `tests/test_capability_gating.py` hardening pass, or before Customer-1 launch.

Surfaced: 2026-06-09 12-dimension forensic audit (M2).

## [OPEN 2026-06-09] Safety Portal M6 — publish daemon has zero watchdog/health coverage

`safety_reports/publish_daemon.py` (the sole privileged actuator) has no `write_last_run_marker` call, no `ITS_Daemon_Health` row, and is absent from `scripts/watchdog.py::TRACKED_JOBS`. A silent daemon death pages nothing. The SPA `PublishMonitor` gives only a partial "stuck queued" signal (stale after a network loss or operator-gated pause), not a dead-daemon signal.

**Fix:** add `write_last_run_marker` at the end of `publish_once`; register `safety_publish_daemon` in `TRACKED_JOBS` with an appropriate freshness window; self-provision an `ITS_Daemon_Health` row (mirror `weekly_send_poll`'s pattern).

**Tag:** `safety-portal`, `publish-daemon`, `observability`, `medium`.

**Revisit when:** next publish-daemon or watchdog hardening pass. Before Evergreen production cutover.

Surfaced: 2026-06-09 12-dimension forensic audit (M6).

## [OPEN 2026-06-09] Safety Portal M7 — publish daemon runs destructive git on the live `~/its` tree without a lock or worktree

`publish_daemon.py` runs `git clean -fd` / `git checkout` on the live `~/its` working tree with no exclusive lock and no guard against the `.claude` `PreToolUse` hook (which has zero reach into `subprocess.run`). `_reset_to_main` scopes the clean to `safety_portal/forms` only, but the tree was stranded in production earlier this session before `_unstrand_if_needed` was added. This violates the repo's own documented worktree discipline and could discard an operator's uncommitted work.

**Fix:** run the daemon from a dedicated worktree + venv (the repo's canonical discipline for processes that write Python source); add a refuse-with-WARN on dirty managed paths instead of silently discarding.

**Tag:** `safety-portal`, `publish-daemon`, `git-discipline`, `medium`.

**Revisit when:** next publish-daemon hardening pass. Before Evergreen production cutover.

Surfaced: 2026-06-09 12-dimension forensic audit (M7).

## [OPEN 2026-06-09] ITS_Daemon_Health sheet observability drift

The operator-visibility surface has drifted significantly from the live daemon topology:
- The RETIRED `safety_reports.intake_poll` row is still present (frozen 2026-06-05, status "OK") — PENDING DELETE (row `7461022174478212`, operator-gated).
- `weekly_generate`, `weekly_send`, `picklist_sync`, and `watchdog` rows read `NEVER_RAN` with pre-pivot WPR descriptions.
- `publish_daemon`, `compile_now_poll`, and `picklist_audit` have NO rows.
- `portal_poll`'s "Last Error Summary" column is not cleared on a successful cycle (stale-error display persists).

A Tier-2 successor-operator reading this sheet would be misled about which daemons are live and healthy.

**Fix (in priority order):** (1) operator deletes the `intake_poll` row via UI; (2) publish daemon gains `ITS_Daemon_Health` self-provision (M6 above); (3) compile_now_poll gains a health row (tracked in the Part-B entry at line ~1858 above); (4) portal_poll clears Last Error Summary on a clean cycle; (5) remaining unloaded daemons' descriptions updated when they are loaded.

**Tag:** `observability`, `daemon-health`, `tier-2-successor`, `medium`.

**Revisit when:** next daemon-health hardening pass. Before Evergreen production cutover.

Surfaced: 2026-06-09 12-dimension forensic audit (live ITS_Daemon_Health inspection).

## [OPEN 2026-06-12] PR-4 Part A — PDF download cache: deferred optimizations + PR-5 supersession

PR-4 Part A shipped the request-driven canonical PDF download (D1-chunked `filed_pdfs` cache, `pdf_requested`/`box_file_id`/`pdf_ready_at` columns, the `portal_poll._service_pdf_requests` pass, the submitted-page receipt). Four deliberate deferrals:

- **Timing-A post-back deferred.** The brief's "if `pdf_requested` is set when intake files, upload the just-rendered PDF" optimization was NOT built — it would force `intake.py` to acquire portal creds + call `portal_client` (breaking the intake/portal_poll separation, since intake holds the rendered bytes but not the creds, and portal_poll holds the creds but not the bytes). Instead the `portal_poll` `_service_pdf_requests` pass re-downloads the filed PDF from Box via `box_file_id` (one extra Box GET + up to one ~60s cycle of latency) for ALL requests, before or after filing. Within the "under 2 min" UI. **Revisit if** the request-before-filing case becomes latency-sensitive at scale.
- **D1 size telemetry uses the `SUM(LENGTH(...))` fallback.** `PRAGMA page_count`/`page_size` throws `D1_ERROR: not authorized: SQLITE_AUTH` under Miniflare (verified in `prune.test.ts`); the Worker keeps a PRAGMA-first `try/catch` for real Cloudflare D1 (where it may be authorized) and falls back to summing `chunk_b64` + `payload_json` byte lengths. **Revisit if** Cloudflare authorizes `PRAGMA` through the D1 binding (then the byte sum, which under-counts indexes/overhead, can be dropped).
- **Recent-submissions list affordance deferred to PR-5.** The brief's "recent-submissions list gains the same per-row affordance" has no surface today (the SPA has only the single-row amend-prefill notice). PR-5 builds the `FormRequestPage` browse list; Part A delivers the **submitted-page** receipt/download only. **Revisit:** PR-5.
- **PR-5 supersession (forward note).** PR-5 refactors the single `submissions.pdf_requested`/`pdf_ready_at` columns into a `pdf_requests(submission_uuid, account, requested_at, ready_at)` table (downloads become **requester-bound, 24h**, not owner-set). Part A's submitter-request flow becomes the first row in that table — Part A behavior is preserved exactly. Do NOT change Part A's contract mid-flight; PR-5 supersedes it as its own reviewed change.

**Tag:** `safety-portal`, `pdf-download`, `deferred-optimization`, `pr-5-supersession`.

**Revisit when:** PR-5 (form-request browse) lands; or a latency/scale review of the download path.

Surfaced: 2026-06-12 PR-4 Part A implementation.

## weekly_send upload-session — live-Graph integration smoke (deferred to pre-Customer-1) [OPEN 2026-06-12]

**PR-3 review (§30 SDK-vs-Live).** `graph_client.send_mail_large_attachment` (draft → createUploadSession → chunked PUT honoring `nextExpectedRanges` → send) is covered ONLY by mocked unit tests (`tests/test_graph_client_upload_session.py`); there is no live-Graph integration smoke. The four-step Graph REST sequence + the pre-authed `uploadUrl` on a different domain (outlook.office.com, which rejects an `Authorization` header) + the 320 KiB-aligned chunk ranges are exactly the mocks-pass-but-live-fails surface §30 guards. Pre-Customer-1 (and as part of confirming the 2.5 MB threshold), run a live sandbox smoke with a throwaway 3–4 MB PDF fixture: create draft → createUploadSession → single-chunk PUT → send → assert the message lands in **Sent**, then clean it up. Add as `tests/test_graph_client_upload_session_integration.py` (skipif no live token, mirroring the integration-marker gating used elsewhere).

**Tag:** `safety-reports`, `graph`, `integration-smoke`, `pre-customer-1`.

**Revisit when:** the pre-Customer-1 live-tenant validation pass, or the first real photo-bearing weekly packet.

Surfaced: 2026-06-12 PR-3 adversarial review.

## [CUTOVER-BLOCKING] [OPEN 2026-06-12] PR-5 Worker + migration 0012 NOT yet deployed to live mirror

PR-5 (#276, merge `213d076`) introduced the `pdf_requests` table (migration 0012, schema `(submission_uuid TEXT, account TEXT, requested_at REAL, ready_at REAL, PRIMARY KEY (submission_uuid, account))`) and the new Worker routes (`GET /api/filed`, `POST /api/request-pdfs`, updated `/status`+`/pdf` re-gated on a live request row, updated `/api/internal/pdf-requests` filtered to live rows). As of session close, the **live mirror Worker does not have these changes**. The README activation step (added in-PR) documents the required ordering: apply migration 0012 to live D1 BEFORE redeploying the Worker — if the Worker is deployed first, the new routes fail-closed (referencing a non-existent table). Until deployed, the Form Request browse page and requester-bound PDF download are not available on `safety.evergreenmirror.com`.

**Fix (Developer-Operator):** `wrangler d1 migrations apply --remote` (operator-run, CC is classifier-blocked on live D1 migrations) → `npm run deploy`.

**Tag:** `safety-portal`, `deployment-pending`, `operator-step`, `pr-5`.

**Revisit when:** the next operator deploy session (pre-Customer-1 activation).

Surfaced: 2026-06-12 PR-5 implementation (session close).

## [OPEN 2026-06-20] Safety Portal browser-tab `<title>` + favicon still say "ITS Portal" after banner rebrand

The 2026-06-20 banner rebrand (PRs #297–#300) dropped the ITS-crest PNG and replaced the "Portal" header text with "Integrated Technical System" (Great Vibes gold-script wordmark). However, the browser-tab `<title>` (`<title>ITS Portal</title>` in `safety_portal/worker/src/index.html` or the React root) and the ITS-crest favicon (`public/favicon.ico` / `<link rel="icon">`) were deliberately left unchanged — out of banner scope, operator's call.

**Impact:** minor cosmetic inconsistency — the wordmark now says "Integrated Technical System" but the browser tab still shows "ITS Portal." Functionally inert.

**Fix when:** next frontend cosmetic pass. Update `<title>` to "ITS — Safety Portal" (or "Integrated Technical System") and replace the favicon with an Evergreen-aligned icon.

**Tag:** `safety-portal`, `frontend`, `cosmetic`, `low`.

**Surfaced:** 2026-06-20 banner rebrand session (PRs #297–#300). Session log: `docs/session_logs/2026-06-20_safety-portal-banner-wordmark.md`.

## [BLOCKED 2026-06-28] Field-ops Smartsheet/Box source-of-truth integration (P2.4+ downstream)

> **⛔ BLOCKED — PARKED 2026-06-28 (operator decision).** The P2.4 mirror daemon is blocked on **no access to the canonical/main Evergreen Smartsheet account**: Seth cannot currently see the real **schema** or the **source-of-record** for materials / deliverables / etc. A daemon whose whole job is to write D1 → the canonical Smartsheet, built against an *unseen* target schema, would encode **guesses** that will be wrong — worse than absent. **Do not build P2.4 until the SoR is visible.** This blocks ONLY the up-sync/filing layer; every D1-local phase (P3 materials admin-editable catalog, etc.) is unaffected. **Unblock condition:** access to the main Evergreen Smartsheet (real schema + SoR). See `decision_p2.4-parked-no-smartsheet-access` + `feedback_dont-build-against-unseen-sot` memories. The §50 doctrine bump (below) is a *separate* gate that also still needs Seth's sign-off.

The P2.2 field-ops READ views (Personnel #308 / Equipment #309 / Job Tracker #310) read **D1 live** (the local primary) and are send-free — deliberately decoupled from the source-of-truth sync/filing layer (Invariant 1). Wiring Smartsheet (operator-SoR, structured) + Box (document-SoR, filing) in as canonical stores is downstream work the read/write layer does NOT block but does NOT yet implement. Three concrete pieces:

1. **P2.4 mirror daemon** (`field_ops/fieldops_sync.py`) — **PARTIALLY SUPERSEDED 2026-06-30.** The **JOB up-sync half is BUILT** (P2.5 Slice 5: `field_ops/fieldops_sync.py` + `shared/active_jobs_writer.py` dual-sheet mirror into the ITS-owned `ITS_Active_Jobs` + `ITS_Active_Jobs_Progress` sheets; §50/§51-blessed; ships `sync_enabled` OFF). The **origin-flip inversion described here was a BUG and is RETIRED** — the corrected identity model keeps `origin='portal'` FOREVER (the typed `job_id` is the permanent key; a `Portal Job Key` bridge + `canonical_job_id` write-back replace the flip; the Worker down-sync gained a canonical-aware pre-pass instead). What REMAINS parked: the **field-ops-tables up-sync** (personnel / equipment / task_assignments / time_entries / inspections → P7) and the **canonical/main Evergreen Smartsheet integration** (still ⛔ BLOCKED on SoR visibility — that integration writes the *unseen* canonical account, not the ITS-owned sheets P2.5 mirrors). So P2.5 unblocked the JOB mirror against ITS-owned sheets; P7/M2 + canonical-Evergreen stay parked.
2. **Box document linkage** — add a `box_file_id` (or folder ref) column to the document-bearing field-ops records (inspections; later job docs) and surface it on the read routes. Mirrors how safety-report submissions carry `box_file_id`. Not yet on the field-ops tables/schema.
3. **Op Stds §50 "D1-as-writer" doctrine blessing** — making D1 the primary that mirrors to Smartsheet is a doctrine decision; v18→v19 bump to FLAG to Seth. Plus the §43 successor-remediation runbook for the P2.4 daemon. (The read routes themselves are read-only Worker code → a break is high-capability-class category-4 code-fix-only → no Tier-2-reachable failure mode → **no §43 entry required for the read views**; planning layer to confirm.)

**Optional cheap read-layer hook (deferred, NOT built):** surface jobs `origin`/`sync_state` in the Job-Tracker list/detail response so the portal shows provenance ("from Smartsheet" vs "created in portal") the moment the mirror daemon lands. Small response-shape extension to `fieldops_jobtracker.ts` + lib + page + tests.

**Tag:** `field-ops`, `smartsheet`, `box`, `source-of-truth`, `doctrine`, `planning-layer`, `blocked`. **Revisit when:** Seth gains access to the main Evergreen Smartsheet (real schema + SoR visible) — the hard prerequisite — AND/OR the §50 doctrine bump reaches Seth.

Surfaced: 2026-06-27 (operator forward-compatibility concern, P2.2 read-views session); **moved to BLOCKED 2026-06-28** (operator parked P2.4 — no canonical Smartsheet access). See `project_fieldops-portal-program` + `decision_p2.4-parked-no-smartsheet-access` memories + `docs/session_logs/2026-06-27_field-ops-p2.2-read-views.md`.

## [OPEN 2026-06-27] Field-ops P2.3 write-layer follow-ups (deferred sub-features + governance)

The P2.3 write routes landed complete (PRs #312–#317; `docs/session_logs/2026-06-27_field-ops-p2.3-write-routes.md`). Five tracked follow-ups deferred out of the write slices (item #4 write-UI **RESOLVED 2026-06-28**; four remain):

1. **Inspection quick-log** (the design's Slice 5 also). A lightweight equipment pre-use inspection write (`POST /api/fieldops/equipment/:id/inspection` → `inspections`, version-pinned) was NOT built: there is **no equipment-pre-inspection forms catalog** in the system to validate `form_code` against (the form-editor's published forms are the safety/progress ones, `identity-v<version>`-validated, not equipment inspections). **Blocked on an operator/domain input:** define the equipment pre-inspection forms + their `form_code`s (e.g. `skid-daily`, `telehandler-preuse`). Then it's a quick add — same integrity-bar pattern as the maintenance log + a `form_code` allow-list + server-side version-pin.

2. **H1 — orphaned `cap.admin.equipment` capability key** (security-governance, from the Slice-6 review). Migration 0016 seeds `cap.admin.equipment` + grants it to admin, but **no worker route enforces it** — the roster routes gate on `cap.equipment.manage` (0013), per the design's F2 choice. Current access control is correct (fail-closed, submitter→403), so it was NOT a merge blocker. BUT the live `role_capabilities` table shows admin holding a key that doesn't control any access: an operator on the capability-management surface who grants/revokes `cap.admin.equipment` will silently affect nothing. **Fix before the cap-management UI becomes operator-reachable:** a cleanup migration (e.g. `0019`) `DELETE`ing `cap.admin.equipment` from `capabilities` + `role_capabilities` (touches the capability vocabulary → confirm with Seth). **Tag:** `field-ops`, `capabilities`, `governance`, `migration`.

3. **`cap.tasks.own` 0013 label tidy.** The description says "View + complete OWN assigned + daily-checklist tasks" but the task-status route enforces a **broad** policy (any holder advances any task — field-PM-manages-the-board). Operator CONFIRMED broad (2026-06-27). Update the 0013 description string to match the enforced behavior (cosmetic; a migration-comment / description tidy, not a behavior change).

4. ~~**Write-UI phase.**~~ **RESOLVED 2026-06-28** (PRs #319–#322, all four-part-verified). The forms that drive the P2.3 routes shipped as 4 pure-SPA slices: equipment status+machine-log #319, equipment move+roster admin #320, Job-Tracker create/close/progress/add-task/task-status #321, time-logging #322. Canonical write-UI pattern: `useAuth()` capability-gate (convenience — Worker re-gates) + `postJson` + `crypto.randomUUID` for integrity-bar uuids + reload-after + `vi.mock("../../lib/auth")` (default read-only) test pattern. See `project_fieldops-portal-program` memory.

5. **§50 D1-as-writer doctrine bump** (planning layer / Seth). P2.3 makes D1 an authoritative writer for payroll-grade field-ops data without per-entry human approval (send-free, audit-trailed). Built under the operator's "proceed" go-ahead; the formal Op Stds v18→v19 §50 blessing is the standing P0-ceremony item (see the SoR-integration entry above).

**Tag:** `field-ops`, `p2.3`, `write-routes`. **Revisit when:** the cap-management UI is scheduled (H1), or the equipment-inspection forms are defined (#1). _(Item #4 write-UI RESOLVED 2026-06-28.)_

Surfaced: 2026-06-27 (P2.3 write-routes session); item #4 resolved 2026-06-28 (write-UI phase session).

## [OPEN 2026-06-28] Field-ops portal UI polish follow-ups (post write-UI restyle)

PR #328 (`9ef3d5b`) shipped the shared `PageShell` and a unified restyle of the four tracker pages. Three polish items deferred:

1. **Route the form pages through `PageShell`.** The write-UI form pages (personnel create/edit, equipment roster admin, job create, time-entry) are not yet wrapped in `PageShell`. They use ad-hoc layout. Wrap them in a follow-up PR once the form page shape is stable (personnel creation task #22 will establish the canonical form-page pattern).

2. **Tracker action messages → `.banner` class.** In-page action feedback (e.g., "Equipment status updated", "Time entry saved") is currently displayed via inline `ok`/`error` divs. These should use the `.banner` CSS class (defined in the design system) for visual consistency with the portal's other feedback surfaces.

3. **`--danger` button variant for destructive actions.** "Close job", "Retire unit", "Retire personnel" actions use the default button style. Add a `--danger` modifier variant (red background or border) to visually distinguish destructive from constructive actions. Matches the UX standard for the admin panel's destructive ops.

**Tag:** `field-ops`, `frontend`, `polish`, `low`. **Revisit when:** personnel creation (task #22) PR is in progress — wrap the new form page in `PageShell` at that point and batch the banner + danger-variant work in the same PR.

Surfaced: 2026-06-28 Progress-Reporting program session (PR #328 restyle).

## [OPEN 2026-06-28] `.dash-section` CSS class duplicates `.card`

The `safety_portal/worker/src/styles/` tree contains a `.dash-section` utility class that is substantially identical to `.card` — same border, padding, border-radius, and box-shadow rules. The duplication is minor (2 classes, ~8 lines) and has no functional impact, but it is a maintenance surface: a future design-system change to `.card` must also update `.dash-section` or the two surfaces drift.

**Fix:** alias `.dash-section` as `@apply .card` or consolidate at the next design-system pass. Not worth a standalone PR.

**Tag:** `field-ops`, `frontend`, `css`, `minor`. **Revisit when:** next design-system consolidation pass.

Surfaced: 2026-06-28 Progress-Reporting program session.

## [PARTIALLY_MITIGATED 2026-07-09] §6a enablement-doc DoD owed per Progress-Reporting slice

**Update 2026-07-09 (WS3 / D2-1, `feat/docs-pdf-pipeline`):** the §6a manifest artifact NOW EXISTS — `docs/enablement/manifest.yaml`, loaded by `docs_pdf/manifest.py`, rendered to branded PDF manuals by `scripts/build_docs_pdfs.py` (the md→PDF pipeline in the new `docs_pdf/` package). It is seeded with all seven enablement guides that exist on main today (`fieldops_checklists`, `manager_tier`, `subcontractor_tier`, `portal_job_creation`, `progress_rollup_numbers`, `crew_time_corrections`, `purchase_orders`). "Registration" is now a concrete action: add an entry (key/title/version/source/sha256) to that YAML. Doc-currency is enforced by `build_docs_pdfs.py --check` (SHA-256 drift; warn-only-friendly, mirrors `regen_doc_indexes --check`). Residual work keeping this open: (a) the in-doc `TODO(operator): register this doc in the §6a manifest` comments in each enablement guide are now actionable and can be retired when those docs are next touched (deferred — editing them triggers a frontmatter retrofit; `crew_time_corrections.md` also lacks conforming `type`/`date` frontmatter); (b) `material_catalog` (M1) still has no capability-guide entry (no guide authored yet); (c) the D2-2 content (ITS Owner's Manual, generated ITS_Config data dictionary) + the D2-3 Box publish leg are not built. See `docs/2026-07-09_aug7_delivery_program.md` WS3.

Per the approved plan (`~/.claude/plans/let-s-go-with-option-greedy-fiddle.md`), every progress-workstream slice that creates a sheet, compiles, or adds a daemon ships a **§43 successor-remediation runbook skeleton + §6a manifest registration in the same PR** (definition-of-done, not a follow-up). The polished distributable PDF (A8 documentation program) is a pre-20-job-cutover requirement.

Currently: M1 (material_catalog, migration 0019 + Worker CRUD + admin SPA) was the first Track M slice and **did not ship a §6a manifest registration** — M1 is D1-local (no Smartsheet sheet, no daemon, no external send), so the §43/§6a DoD obligation is reduced, but the §6a capability manifest should still record the `material_catalog` capability. Track M slices that add daemon paths (M2 bidirectional sync, M3 incidents + photos) have a full §43/§6a obligation.

**Rule going forward:** every slice brief for the Progress-Reporting program must explicitly call out the §6a registration step and the §43 runbook scope (often "None for this slice — read-only/D1-local" is the correct answer, but it must be stated, not omitted).

**Tag:** `progress-reports`, `doctrine`, `§43`, `pre-cutover`. **Revisit when:** each Progress-Reporting slice brief is written.

Surfaced: 2026-06-28 Progress-Reporting program session (approved plan §6/A8 clause).

## [OPEN 2026-06-28] Exec session log gap — 2026-06-17 to 2026-06-18 arc still missing

The 2026-06-17→18 session arc (#292 D1 job cleanup + #294 tech-debt easy-wins code/test fixes + #295 live-cleanup closes + the D1 clean-slate execution) has **no exec session log**. This gap was first noted in `project_safety_portal_state.md` memory ("No exec session log yet for the 2026-06-17→18 arc") and has not been filled.

The arc is non-trivial: two PRs landed, a clean-slate was executed on live D1 + Smartsheet + Box, and CodeQL caught two real issues in PR #292. The decisions (purge-job endpoint design, CodeQL fixes, test-artifact scope decisions) are not reconstructable from git history alone without the session log narrative.

**Fix:** operator invokes `session-log-writer` for this arc, using PR #292 (`22ab1db`) + PR #294 (`79c96b2`) + PR #295 (`974b111`) and the `project_safety_portal_state.md` memory as context.

**Tag:** `housekeeping`, `session-log`, `documentation`. **Revisit when:** operator has bandwidth for a retroactive log write.

Surfaced: 2026-06-28 session close (still missing after the 2026-06-17→18 arc + the 2026-06-20 banner session + the 2026-06-28 write-UI session all added their logs).

## [OPEN 2026-06-29] Portal permission-model stale plumbing — vestigial + orphaned capabilities, coarse gate, missing crew→job link

**Surfaced 2026-06-29** during a forensic investigation of the portal permission model (operator asked "what happened to my 3-tier permission model that broke my login and got reverted?"). Resolution: the capability system (migration `0013`, PR #302, `8bd9995`) is **live and was never reverted**; the 2026-06-28 login breakage was the deploy-order lockout, fixed operationally. The 5-agent read-only sweep + direct verification surfaced stale/half-wired permission plumbing to address later — **documented, not fixed** (preservation-over-refactor, §14). Relevant to the queued **P2.6 — Manager tier** slice and any future capability-management UI.

1. **Granted-but-never-enforced capabilities** (defined in `0013`, granted to a role, but no route gates on them — routes use `requireSession` or `requireRole('admin')` instead, so the cap is not a security boundary). Originally 4 named: `cap.form.submit`, `cap.form.request`, `cap.inspection.job`, `cap.checklist.manage` (plus `cap.tasks.assign`, tracked as a 5th in the same sweep). Two are now RESOLVED: **`cap.tasks.assign`** by the S1 Assigned-Tasks build (migration `0025`) — task create/reassign routes gate on `cap.jobtracker.manage` OR `cap.tasks.assign` (with a subcontractor-target guard); **`cap.checklist.manage`** by the S2 checklist-engine build (PR #407), carried through R1/R4/R5 (PRs #416/#417/#420) — every checklist CRUD/assign/cancel route in `fieldops_checklist.ts` (`gates.requireCapability(CAP_CHECKLIST)`, ~19 call sites) now gates on it. **Still ungated (1 remains, deliberately):** `cap.inspection.job` — NO surface exists to gate (nothing writes the `inspections` table; job-level inspection forms ride `/api/submit` under `cap.form.submit`). `cap.form.submit` + `cap.form.request` are now ENFORCED (PR #440, 2026-07-03 — intended as a held PR, merged via a disclosed staging error; the deep security review's lockout analysis proved all three roles hold both caps, so no ability was lost): `/api/submit` + the six form-request/download surfaces. Decide enforce-or-remove on `cap.inspection.job` when a job-level inspection surface ships.
2. **3 orphaned capability references** appearing ONLY in `migration 0016_equipment_management.sql` comments (lines 54-55), never defined in `0013`: `cap.inspection.fill`, `cap.dashboard.equipment`, `cap.machine.log` — URS-Marine port leftovers; granting any would fail the `role_capabilities` FK. Clean the comments. (Companion to the already-tracked `cap.admin.equipment` orphan-key cleanup in the "Field-ops P2.3 write-layer follow-ups" entry above.)
3. **Coarse `cap.jobtracker.manage` — RESOLVED by P2.6 (PR #398, 2026-07-01).** `cap.crew.assign` (the 19th capability) + `POST /api/fieldops/personnel/:id/assign` shipped, letting a Manager assign/move crew without granting `cap.jobtracker.manage` (job/task creation stays admin-only). Time entries confirmed orthogonal as designed — a person placed on Job A can log time against Job B without reassignment.
4. **No `personnel.current_job` column / standalone crew→job assignment route — RESOLVED by P2.6 (PR #398, 2026-07-01).** `personnel.current_job TEXT` (migration `0023`) + the assign route above are live. **New finding surfaced scoping the next slice (unified job-create flow):** the job-list and job-detail crew queries in `fieldops_jobtracker.ts` still compute crew from `task_assignments`, NOT from the new `current_job` column — a person placed via the P2.6 route would not show up as crew until that's converged. Tracked as its own slice: see the "Unified job-creation flow" entry above (spec at `~/.claude/plans/spec_unified-job-create-flow.md`, Slice 1) and `memory-archive.md` §G49.6.

**Tag:** `safety-portal`, `capabilities`, `auth`, `field-ops`, `P2.6`. **Revisit when:** item 1 is 2-of-5 RESOLVED 2026-07-01/07-02 (`cap.tasks.assign` by S1, `cap.checklist.manage` by S2/R1/R4/R5) — 3 caps still cheap-open (no trigger yet); item 2 still-open cheap cleanup (no trigger yet); items 3-4 RESOLVED 2026-07-01 (crew-query convergence spun out as its own tracked follow-up, see item 4 note).

Surfaced: 2026-06-29 permission-model forensic investigation; full spec at `~/.claude/plans/what-happened-to-my-floating-porcupine.md`; reusable inventory in the `reference_portal-capability-enforcement-gaps` memory.

---

## [OPEN 2026-07-01] Manager tier over-permissioned on personnel — can retire/delete, should only create + assign

**Operator-reported 2026-07-01.** The `manager` role holds `cap.personnel.manage`, which currently bundles **create / edit / link / unlink / retire** of personnel. The operator wants a manager to be able to **create** a person and **assign** them to a job (`cap.crew.assign`, already correct), but **NOT retire/delete** personnel — retire stays admin-only. Today the retire route (`POST /api/fieldops/personnel/:id/retire` in `fieldops_personnel_write.ts`, gated `cap.personnel.manage`) is reachable by a manager, and the SPA renders the Retire button for anyone with `cap.personnel.manage` (`FieldOpsPersonnel.tsx`).

**Fix options (decide at build):** (a) split `cap.personnel.manage` → keep create/edit/link for manager, move **retire** behind a new `cap.personnel.retire` granted admin-only (a migration + a route re-gate + SPA gate); or (b) a lighter `role==='admin'` hard-check on the retire route + SPA button (mirrors the login-account-mint self-gate pattern) without a new cap. Option (a) is the cleaner capability-model fit; (b) is faster. Either way: re-gate the Worker route (the real boundary) AND the SPA button (convenience). Add a gate-bites test (a manager gets 403 on retire).

**Operator direction:** park OR **fold into the next big website update** (the Assigned-Tasks tab work — thematically a manager-facing permission change). **Tag:** `field_ops`, `capabilities`, `auth`, `manager`, `p2.6`, `personnel`. **Revisit when:** building the Assigned-Tasks tab / next manager-facing update. **RESOLVED 2026-07-01 by S1 Assigned-Tasks build** (retire route + SPA button gated `role==='admin'`, option (b); +regression test).

---

## R-series spec Deferred #5–#10 — named follow-ups, not in this program [OPEN 2026-07-02]

**Surfaced 2026-07-02**, `~/.claude/plans/refinement-spec-r-series.md` §3 "Deferred / won't-do." Six items were explicitly scoped OUT of the R-series refinement program (R1–R5, R7) as named follow-ups, not silent gaps:

5. **Mid-day template re-sync into open instances.** Admin edits to a checklist template take effect "tomorrow," not on today's already-generated instances (R4 ships copy-only — "changes take effect tomorrow" — snapshot semantics kept as-is).
6. **Mid-day job-reassignment orphan-instance surfacing/auto-cancel.** If a person is reassigned off a job mid-day, their already-generated daily-checklist instance for that job is not auto-cancelled or flagged orphaned; R2's day-rollover refetch narrows the confusion window but doesn't close the gap.
7. **Scoped crew edit/retire for subcontractor-created crew + time amend/void UI** via the `amends_uuid` chain — a data-correction follow-up epic. R2 ships the crew list + duplicate warning; R1/R7 stop new junk rows, but no amend/void UI exists yet.
8. **Server-side completed-history cutoff/deletion.** R2 ships client-side collapse only; history stays queryable/unbounded server-side.
9. **Full URL router.** R3 ships minimal hash/history integration only (push-per-view-change, popstate restore, `beforeunload` dirty-form guard) — not a real router.
10. **`task_assignments.due_date` column.** Considered and deferred per audit; `created_at`/`assigned_by` rendering (R2) covers the urgency-signal gap for now, but there is no due-date field on task assignments.

None of these are regressions — they were locked-decision scope cuts made explicitly, with the reasoning captured in the spec. Listed here so they don't get silently rediscovered as "bugs" in a future session.

**Tag:** `field_ops`, `checklist`, `tasks`, `r-series`, `deferred`. **Revisit when:** planning the next field-ops UX pass — check this list before re-scoping any of the six from scratch.

---

## Checklist template identity is title-keyed (0026 design) — a same-title admin template collides on re-seed [OPEN 2026-07-02]

**Flagged during the #414 review** (migration `0028_sop_checklist_content.sql`, R-seed). Checklist template find-or-create is keyed on `(kind, title)` — every seed `INSERT` is guarded `WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = '<exact title>')`, and the `daily_default` re-seed logic is sentinel-guarded on an exact item **label** match. This is a deliberate 0026 design choice (no template "code"/slug column), and it works cleanly for migration idempotency (a re-apply is a no-op).

The edge case: if an **admin authors a template through the UI** with a title that happens to exactly match a future seed migration's title (e.g., re-creates "Excavation / Trench Daily Inspection" by hand), a later migration re-apply — or a future seed migration reusing that exact title — will treat the admin's template as "already exists" and silently **merge items into it** (via the per-item `NOT EXISTS (template, label)` guard) rather than creating a separate template. Blast radius is low today: templates are seeded once (0026 placeholder → 0028 real content) and there's no evidence of an admin having hand-authored a colliding title.

**Fix (if it becomes live):** add a stable template `code`/slug column distinct from the human-editable `title`, and key find-or-create on `code`. Only worth doing if the inspection/checklist template library grows past the current seeded set and admin-authored templates become common — preservation-over-refactor (§14) says don't build this speculatively.

**Tag:** `field_ops`, `checklist`, `templates`, `data-model`, `r-series`. **Revisit when:** the checklist/inspection template library grows beyond the seeded set, or an admin reports items merging into the wrong template.

---

## Optimization-plan doctrine-adjacent decisions awaiting operator green-light [OPEN 2026-07-03 — item 2 (B3) RESOLVED 2026-07-03]

**Item 2 (B3) RESOLVED 2026-07-03 — operator approved ("go ahead with your recommendations") and the dead-route deletion was executed.** Four Worker routes deleted with per-site tombstones naming the approval + date: `GET /api/fieldops/checklist/mine` (the deprecated daily generation read — it still WROTE daily instances + snapshots when called, the junk-data footgun), `GET /checklist/mine/rollup-draft` (S5 draft assembler, superseded by the SOP form's own prefill), `POST /api/fieldops/job/:job_id/close` (thin back-compat alias; `/lifecycle` is the live close path), and `POST /api/fieldops/job/:job_id/progress` (nothing displayed the value since #403; no Python reader). Daily-exclusive machinery removed with them (`generateDailyInstance`, `pacificToday` (worker copy), `reconcileFormLinked`, `AUTO_CHECK_SQL_DAILY`, `ITEM_STATES_SQL_DAILY`, `MergedItem`, `DailyEmptyReason`, `ROLLUP_LEG_CAP`); the inspection engine (assign/assigned/instances/cancel/item-state), the S2 default/job-override **editor routes**, and the 0028 `daily_default` seed rows were **NOT removed** (narrower scope than option (a) — the approval covered the four dead routes only). Tests: the 3 daily suites deleted (36 tests), 6 daily-path tests removed from `fieldops-r1-contracts`, 5 route tests removed from `fieldops-job-write`, 3 item-state contracts re-pinned via the assigned-inspections path (worker suite 668 → 624). Item 1 below remains OPEN.

Original entry (item 1 still awaiting green-light):

**`~/.claude/plans/optimization-plan.md` "Needs-operator" #2 and #3** — two propose-only options CC is explicitly barred from executing unilaterally:

1. **[RESOLVED 2026-07-03 — the D5 registry split PR]** Operator-APPROVED ("absolutely need to split the registry — that would very quickly become a problem and crash our website") and BUILT: active current+previous versions eager, historical lazy (`getDefinitionFor`), the sliding window keeps the main chunk ~constant. The C1 brief carries a dated amendment; the approval is quoted in `src/forms/registry.ts`.

2. ~~**Deprecated daily-checklist Worker surfaces + dormant 0028 `daily_default` rows**~~ — RESOLVED above (route deletion executed; 0028 rows + editor routes deliberately kept).

Item 1 blocks nothing; it is a dead-weight-vs-preservation-over-refactor call that only Seth should greenlight. **Tag:** `field_ops`, `optimization`, `doctrine-adjacent`, `preservation-over-refactor`. **Revisit when:** Seth reviews the optimization-plan's Needs-operator section.

---

## D1-primary tables have no ITS-side backup — Cloudflare D1 Time Travel is the restore path (accepted) [OPEN 2026-07-03]

**R3-F7 (resiliency audit), decision: don't build a backup job — document the restore path.** Two tables are **D1-primary** (no Smartsheet/Box mirror; ITS holds no other copy): `job_daily_requirements` (per-job daily-form requirement overlay, migration `0030`/`0032`) and `job_expected_materials` (per-job expected-receipts list, migration `0031`). Everything else in D1 is either a queue drained to the Mac (submissions → filed PDFs), a mirror of Smartsheet (`ITS_Active_Jobs` sync), or re-derivable. Receipt EVIDENCE already survives outside D1 — a confirmed receipt appends a `deliveries_received` row into the filed daily PDF, and an incident files its own material-incident submission — so a D1 loss cannot silently erase what was received.

**Restore path (operator, Tier-3/Seth):** Cloudflare **D1 Time Travel** — every D1 database keeps 30 days of point-in-time restore (`npx wrangler d1 time-travel info its-safety-portal-db`, then `… time-travel restore its-safety-portal-db --timestamp=<unix|ISO>`). Restore rolls back the WHOLE database, not one table — expect to replay any submissions queued after the restore point (the Worker re-serves unfiled rows; already-filed PDFs are safe on Box/Smartsheet).

**Blast radius if lost outright (>30 days / Time Travel unavailable):** re-enterable admin data — the office re-keys each job's requirement items and expected-materials rows from the client's punch list. Bounded, annoying, not evidence-destroying. That bound is WHY no ITS-side backup job is built (§14; the audit explicitly rejected one).

**Tag:** `field_ops`, `d1`, `resilience`, `runbook`, `accepted`. **Revisit when:** a third D1-primary table lands (re-evaluate the no-backup call), or Cloudflare changes the Time Travel retention window.

- **[OPEN 2026-07-03] `_write_heartbeat()` liveness-touch called bare across all 6 daemon consumers** — a
  local-disk `OSError` from `HeartbeatReporter.write_liveness()` (`state_io.atomic_write_text` raises
  natively) would propagate out of the poll/publish loop and skip that cycle's health-row +
  watchdog-marker writes. Pre-existing live pattern (PR #344) replicated verbatim by the CS3 consumers
  per review; the right fix is ONE shared-level catch inside `shared/heartbeat.py::write_liveness`
  (never-blocks-primary-work applied to the liveness half too), not six call-site wraps. (CS3 ops-stds
  review WARN, 2026-07-03.)

- **[OPEN 2026-07-03] G1 item-photo queue: no explicit queue-AGE signal + refusal-spam window** — the
  stuck-pending >7d prune WARN + the portal_poll heartbeat notes are the only backlog signals (the
  brief's req-5 wanted an age signal; deferred as minimal-viable). A hostile account spamming refused
  photos pages once per dedupe window (Sentry+Resend deduped post-#449; ITS_Errors records per
  occurrence, bounded by Check O rotation) — accepted posture, revisit if it fires in practice.
  (G1 regression review WARNs, 2026-07-03.)

- **[OPEN 2026-07-03] Daily-form date-flip discards attached photos (second in-session loss path)** —
  `onDateChange` applies drafts without the photo overlay: flip-away wipes live photos and flip-back
  can't restore them (drafts are photo-stripped by quota design). Defensible (photos belong to their
  date) but the in-code honest-regression comment frames unmount as the only loss path — this is a
  second. Fix = the same functional-overlay pattern if it bites in practice. (Photo-disappear fix
  review NIT, 2026-07-03.)

## ITS scaling hardening — 20-job/20-user Tier-A roadmap [OPEN 2026-06-28]

> **Status (recovered from PR #324 on 2026-07-04; kept for provenance):** most Tier-A items have
> since **shipped** — A1 `verify_sheet_cap` (#326), A2 single-host resilience (#327), A3 Box/Keychain
> lock + watchdog Check P (#345), A4 backlog alerts + Checks Q/R (#349), A6 `weekly_generate`
> hardening (#346), A7 photo-413 (CS2 #437), plus the broader forensic-hardening cluster (#342–#351).
> The live marching order is **`docs/ROADMAP.md` Track 3**; the remaining open items live there. This
> section is preserved verbatim below for the original analysis; the full report is
> `docs/reports/2026-06-28_forensic-scaling-eval-20x20.md`.

2026-06-28 forensic scaling evaluation (read-only; `improve`-skill + multi-agent Workflows; full report at `docs/reports/2026-06-28_forensic-scaling-eval-20x20.md`). Audited the system for a planned ramp to 20+ active jobs / 20+ daily **photo-heavy** portal users this quarter. 98 findings (7 CRITICAL / 33 HIGH; **39 silent-failure**). No code changed — diagnosis + logged executable specs only.

**Tier-A (must-fix-before-cutover) — full self-contained specs in the report's Part II; all 7 code specs are first-draft `needs-revision`:**
- **A1 (gating, do first):** verify the real Smartsheet per-workspace sheet-count cap + design a week-sheet archival/rollup strategy. ~1,040 new sheets/yr (20×52) is the #1 dollar cost (plan-tier upgrade $600 Pro / $2,400 Business) and a possible hard cap. `smartsheet_client` has no list/count-sheets method yet. Gates the cost + cutover timing.
- **A2:** single-host resilience — daemon auto-start after reboot (LaunchAgent-at-login gap), SDK network timeouts (boxsdk has none → indefinite daemon hang), Keychain-locked-after-reboot handling.
- **A3:** Box OAuth refresh-token cross-process lock + `keychain.set_secret` lock + 50-day idle warning (silent 60-day auth-death risk).
- **A4:** unfiled-submission backlog/age alert + portal_poll outage escalation (`box_verified=0` rows never pruned → silent loss if the host dies).
- **A5:** ITS_Review_Queue + ITS_Errors 5,000-row cap rotation (silent drop at cap).
- **A6:** weekly_generate hardening — per-job timeout + streamed merge + partial-write resumability. **CORRECTION:** the original "launchd kills it at >1h" CRITICAL is FALSE — the plist sets no `ExitTimeOut`; real risk is wall-clock + memory.
- **A7:** photo/payload 413 reconciliation (raise PAYLOAD_MAX envelope, keep the four-way §34 photo mirror synced — `worker/index.ts` + `photo_screen.py` + `PhotoField.tsx` + `publishValidation.ts`) + amend-prefill empty-payload guard. Doctrine flag RESOLVED — see report Part III.
- **A8 (P1, parallel):** Operator & User Enablement Documentation program — a PDF guide / user manual / comprehensive troubleshooting tree for every ITS function (Portal, ~17 Smartsheet surfaces incl. an `ITS_Config` data-dictionary PDF, daemons/CLIs, future workstreams). Enabling precondition for the distributed-Evergreen-operator model; needs a doc-currency discipline.

Cost at 20×20 ≈ **$610–$2,410/mo hard ≈ the Smartsheet tier decision** + ~$8 Cloudflare; Anthropic ~$0 (portal deterministic); labor distributed across existing Evergreen staff (not a bottleneck).

**Revisit when:** the 20-job ramp is scheduled (start with A1's read-only cap verification), or any Tier-A item is picked up for implementation.

