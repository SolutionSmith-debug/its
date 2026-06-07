---
type: session_log
date: 2026-06-07
status: closed
related_prs: [185, 186, 187, 188, 189]
workstream: safety_portal
tags: [safety-portal, box, smartsheet, styling, naming, config-gate, codeql, custom-domain, phase7]
---

# Session log — Safety Portal Phase 7: styling, Box schema, custom domain (PRs #186–#189 + #185 open)

Safety Portal Phase 7 batch: four PRs merged + four-part verified (Box-409 fix, sheet
styling, custom domain declaration, Box mirror tree); one PR (#185 admin route) built +
CI-green but OPEN pending two CodeQL false-positive dismissals the operator must apply
manually. Live actions this session: one-time styling pass applied to 3 static sheets + 7
week sheets; ZZ Portal Proof test job (JOB-000008) deactivated. Earlier the same calendar
day, PRs #181–#184 (week-sheet filing, D1 job sync, F22 workspace-share authority, PDF
inline attachment) were also merged and live-proven — captured here for completeness, as
they form the same Phase 7 batch.

## PRs landed

### PR #186 — Compile-Now Box-409 fix (`34e271de`)

`box_client.upload_bytes_or_new_version` + `_find_child_file`: when a same-name upload
returns 409 (file already exists), the client now uploads a new Box **version**
(`update_contents_with_stream`) instead of stranding. The compiled weekly-packet path in
`weekly_generate.py` uses this helper. Box=SoR is preserved (stable file ID, version
history retained). Live SDK-vs-Live validated. Surfaced via the Compile-Now flow on real
Bradley 1 rows.

- pytest: count not recoverable from `-q` + coverage log (ci workflow conclusion = success)
- mypy: 0 errors / 181 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #186 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-07T17:17:09Z
- mergeCommit: 34e271de0bf6fca411a12a7388744fc264d12bff
- main CI on merge commit: SUCCESS (run 27099398648, workflow: ci; run 27099398415, workflow: CodeQL)
- pytest: count not recoverable from `-q` + coverage log (ci workflow conclusion = success)
- mypy: 0 errors / 181 source files
- ruff: clean

---

### PR #185 — Phase 7 admin route + session revocation (OPEN — NOT merged)

Worker `types.ts` `PORTAL_ADMIN_API_TOKEN` (separate from the poller token);
`worker/auth.ts` `hashPassword` + `normalizeUsername`; `worker/index.ts`
`requireAdminToken` + admin user CRUD (`provision`/`reset`/`disable`/`enable`/`list`,
bcrypt cost-10, never logs plaintext) + `requireSession` per-request D1 revocation
(fail-closed); migration 0006 `users.disabled`; `shared/portal_client.admin_request`;
`safety_reports/portal_admin.py` CLI.

**ALL CI GREEN EXCEPT 2 CodeQL `py/clear-text-logging` FALSE POSITIVES** (alert #11
`portal_admin.py:52`, alert #13 `portal_admin.py:148`). These are CodeQL interprocedural
imprecision: the bearer token (sensitive source) taints `admin_request`'s return value,
which causes CodeQL to flag any print of that return — including the list-users and
`_fail` paths that are printing non-sensitive admin data. One of three original FPs was
cleared by refactor (stopped echoing the raw response dict). The two remaining FPs are
unfixable without contorting correct code. `ops-stds-enforcer`: security CLEAN.

**Blocked on operator action:** use `codeql-fp-triager` agent + GitHub UI to dismiss
alerts #11 and #13, then merge PR #185. Migration 0006 **must** be applied to live D1
**before** redeploying the Worker.

---

### PR #187 — Safety Portal sheet styling (`53c27ac2`)

`smartsheet_client.apply_column_styles` (post-create width + format; column format MUST
be set via the SDK model ATTRIBUTE `m.format = "..."`, NOT the dict constructor — the dict
constructor silently drops format, a live-verified gotcha);
`smartsheet_client._resolve_cells` extended with additive `_formats` meta-key for
per-cell format (byte-identical when absent; enables per-cell Status coloring at write
time); `week_sheet.WEEK_SHEET_STYLES` (widths + bold dark-green primary column + Status
cell coloring Active=green/Superseded=gray); `scripts/style_safety_portal_sheets.py`
one-time pass. Palette indices from `GET /2.0/serverinfo` `.formats.color` (38=#237F2E
dark green, 7=#E7F5E9 light green, 18=#E5E5E5 gray). LIVE: one-time pass styled 3 static
sheets + 7 week sheets.

- pytest: all passed / 3 skipped (dot-matrix 100%, no F/E; -q mode suppressed count line)
- mypy: 0 errors / 182 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #187 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-07T17:43:03Z
- mergeCommit: 53c27ac2e4a59d4b422fb0633f7a07a3133aa8b6
- main CI on merge commit: SUCCESS (run 27100005065, workflow: ci; run 27100004840, workflow: CodeQL)
- pytest: all passed / 3 skipped (dot-matrix 100%, no F/E; -q mode suppressed count line)
- mypy: 0 errors / 182 source files
- ruff: clean

---

### PR #188 — Custom domain declaration (`6c1993d0`)

`wrangler.jsonc` routes `custom_domain: true` for `safety.evergreenmirror.com`. NOT
deployed — the custom domain becomes active when the operator runs `npm run deploy` (or
adds the custom domain via the Cloudflare dashboard). Inert until activation.

- pytest: log tail shows coverage table only — passed count not recoverable from log stream (no failures observed)
- mypy: 0 errors / 181 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #188 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-07T17:40:51Z
- mergeCommit: 6c1993d01d4bcc6e45307ca6a885fe24f8d4054a
- main CI on merge commit: SUCCESS (run 27099953752, workflow: ci; run 27099953649, workflow: CodeQL)
- pytest: log tail shows coverage table only — passed count not recoverable from log stream (no failures observed)
- mypy: 0 errors / 181 source files
- ruff: clean

---

### PR #189 — Box schema mirrors Smartsheet schema (`ecb06d9c`)

NEW `safety_reports/safety_naming.py` — single source of truth for job folder names,
week labels, and `CFG_BOX_PORTAL_ROOT` shared by Box + Smartsheet. `week_sheet`
`_folder_name`/`week_sheet_name` delegate to it (byte-identical). `intake.py`
`_resolve_portal_box_folder` + `weekly_generate.py` `_ensure_its_week_folder` gain a
**config-gated mirror-tree branch**: `ROOT → per-job → per-week` via
`box_client.get_or_create_folder`, gated on ITS_Config key
`safety_reports.box.portal_root_folder_id` (unset → legacy path, INERT). Fixes the
new-job Box strand. Legacy project_routing/category/`_its_week_folder_name` preserved for
the dormant email path. Live SDK-vs-Live nesting round-trip validated.

**Config-gate:** merging + pulling is inert until the operator creates the "ITS Safety
Portal" root Box folder and sets the ITS_Config key. This was a deliberate design choice
so the live SoR filing path is not switched without operator intent.

- pytest: 1471 passed / 2 skipped
- mypy: 0 errors / 184 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #189 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-07T18:01:45Z
- mergeCommit: ecb06d9c291135ff5873596feb038e09d37c064d
- main CI on merge commit: SUCCESS (run 27100440886, workflow: ci)
- pytest: 1471 passed / 2 skipped
- mypy: 0 errors / 184 source files
- ruff: clean

---

## Key decisions

1. **Box version-on-conflict: upload a new version rather than fail or strand.**

   The Compile-Now path in `weekly_generate.py` uploads the compiled PDF packet to Box.
   When a same-name file already exists (e.g., operator re-runs Compile-Now), Box returns
   409. The prior behavior (raised; caller stranded) was surfaced on the real Bradley 1
   rows. Decision: `upload_bytes_or_new_version` checks for a 409, locates the existing
   file by name via `_find_child_file`, and calls `file.update_contents_with_stream(BytesIO(...))`.
   This uploads a new Box version — stable file ID, history preserved (Box=SoR).
   The per-submission intake path keeps the suffix strategy (`amend-N`) as distinct docs.

   Alternative considered: delete-then-upload (destroys history). Rejected — Box is SoR
   and version history is part of the audit trail.

2. **Smartsheet column FORMAT must be set via model attribute, not the dict constructor.**

   Discovered live during PR #187: `Column(width=200, format="...")` silently drops the
   `format` argument; only `col.format = "..."` (attribute assignment on the SDK model
   object) applies it. Width is safe either way. Cell format via the Cell dict works.
   Palette indices come from `GET /2.0/serverinfo` `.formats.color` (not documented
   in the SDK). The `_formats` meta-key extension to `_resolve_cells` is additive and
   byte-identical when absent — it does not touch any existing row-write path.

   This was verified by observing zero column formatting on first attempt and consulting
   the live SDK source.

3. **CodeQL `py/clear-text-logging` FP on operator CLIs: anatomy and resolution path.**

   `portal_admin.py` is an operator CLI (`python portal_admin.py list-users`). The
   bearer token (sensitive source) flows as an argument into `admin_request()`, whose
   return value is the full HTTP response — which the CLI prints. CodeQL's interprocedural
   taint analysis propagates the bearer-token taint onto every print of `admin_request`'s
   return, including list-users output that contains no secrets. One of three FPs was
   cleared by refactor (stopped echoing the raw response dict). The two remaining FPs
   are unfixable without contorting correct code (e.g., calling `admin_request` only for
   side effects and never printing its return, which breaks the CLI's purpose).

   Resolution: operator dismisses alerts #11 + #13 via `codeql-fp-triager` agent +
   GitHub UI. CC is hook-blocked from dismissing CodeQL alerts directly. Not a
   suppression of a real finding — `ops-stds-enforcer` confirmed security CLEAN.

4. **Config-gate pattern for inert SoR-path changes.**

   PR #189 changes where Box files land (new mirror-tree branch). Merging + pulling
   the code before the operator sets the ITS_Config key is safe because the new branch
   is unreachable (config absent → legacy path). This allows the PR to land and be
   verified in CI without triggering a live SoR switch. The operator activates by
   creating the root Box folder and setting the key.

   Alternative considered: feature-flag in code / env var. Rejected — ITS_Config is
   the canonical runtime-toggle mechanism; using it here is consistent.

5. **`safety_naming.py` as shared naming source of truth.**

   Box folder names and Smartsheet week-sheet names were computed by separate,
   independently-maintained string-building functions. PR #189 extracts this into a
   single `safety_naming.py` module. Both Box and Smartsheet paths delegate to it;
   the output is byte-identical to the prior code by construction (verified via test).
   This was the prerequisite for the Box mirror-tree branch to produce folder names
   consistent with what Smartsheet displays.

6. **Require-up-to-date branch serializes batch merges.**

   GitHub branch protection requires "up-to-date before merging." The first merge in
   a batch advances `main`, and all sibling PRs become BEHIND. Each subsequent PR
   requires `gh pr update-branch` (triggers a fresh CI run) before merging. This
   serializes Phase 7 batch merges even when the PRs are independently correct.
   Plan overhead: ~3–5 minutes per PR for CI on the updated branch.

## Live actions this session

- **One-time styling pass:** `scripts/style_safety_portal_sheets.py` applied to 3
  static sheets (ITS_Active_Jobs, ITS_Forms_Catalog, WSR_human_review) + 7 week sheets
  (all active week sheets at time of run). Status cell coloring live.
- **JOB-000008 "ZZ Portal Proof" deactivated:** Active field set to Inactive; removed
  from the portal job dropdown. Orphan week sheet `1966431334780804` noted for deletion
  in the next tidy pass.

## Open items / next session

**Three activation tracks before the portal is production-ready:**

(a) **Admin route (PR #185):**
  1. Run `codeql-fp-triager` agent; apply GitHub UI dismissal for alerts #11 + #13.
  2. Merge PR #185.
  3. Set Worker secret `PORTAL_ADMIN_API_TOKEN` (Cloudflare dashboard).
  4. Set Keychain `ITS_PORTAL_ADMIN_TOKEN` (byte-equal to the Worker secret).
  5. Apply migration 0006 to live D1 **before** redeploying the Worker.
  6. `npm run deploy` (redeploy Worker with admin route active).
  7. `python portal_admin.py add-user` to provision the first admin user.

(b) **Box mirror tree:**
  1. Create "ITS Safety Portal" root Box folder in the mirror tenant.
  2. Set `safety_reports.box.portal_root_folder_id` in ITS_Config.
  3. `git -C ~/its pull` (picks up PR-K `ecb06d9`; config gate activates mirror tree).

(c) **Custom domain:**
  1. Cloudflare dashboard: add `safety.evergreenmirror.com` as a custom domain for the
     Worker (or run `npm run deploy` which will pick up `wrangler.jsonc` routes).

**Other pending operator steps:**
- `git -C ~/its pull` to pick up PR-K (`ecb06d9`) — not yet pulled as of session close.
- Add `Job ID` AUTO_NUMBER column to ITS_Active_Jobs (Smartsheet UI only — errorCode 1008
  blocks API creation).
- Create "New Job" form on ITS_Active_Jobs.
- Fill Address + contact cells (PM task; 6 cells blank).
- Delete orphan week sheet `1966431334780804`.
- WPR_Pending_Review sheet + Job Slug column: final removal (decommission-by-doc complete;
  no live runtime references).

## What was NOT touched

- Invariant 1 (External Send Gate): `weekly_generate.py` and `box_client.py` changes add
  no send capability. The Worker admin route has zero transmission capability.
- Invariant 2 (Adversarial Input Handling): unchanged. `safety_naming.py` processes no
  external input.
- `portal_poll.py` daemon: unchanged. Loaded and running; not reloaded this session.
- Any live daemon tree pulled during session (PR-K not yet pulled; live daemons continue
  on `025215d`).
- Blueprint doctrine files: no doctrine edits this session.
- Cloudflare Worker: not redeployed. Custom domain in `wrangler.jsonc` is inert until
  next `npm run deploy`.

## Lessons captured to memory

- `project_safety_portal_state.md`: full replacement proposed by `session-close-maintainer`
  covering Phase 7 batch state, activation punch-list, and three activation tracks.
- `feedback_require-up-to-date-branch.md`: new entry — GitHub branch protection
  serializes batch merges; `gh pr update-branch` required before each merge once main
  advances.
- `claude-code-info-gap.md` (§5/§6/§8): updated with Phase 7 batch (CodeQL FP on
  operator CLIs, Smartsheet column-format attribute-only constraint, Box
  version-on-conflict, config-gate pattern, `safety_naming.py`, require-up-to-date
  serialization).
- `memory-archive.md` §G26: updated / extended to cover PRs #179–#189.
- `docs/tech_debt.md`: two new entries (Smartsheet column FORMAT attribute-only;
  PR #185 blocked on CodeQL dismissal + migration 0006 order dependency).

## Cross-references

- Prior Safety Portal session log (Phase 5 WSR rewire):
  [`2026-06-05_safety-portal-wsr-rewire-pull-model.md`](2026-06-05_safety-portal-wsr-rewire-pull-model.md)
- Blueprint session log (same date, planning-side): `../its-blueprint/session-logs/2026-06-07_safety-portal-deploy-reconciliation.md`
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — Smartsheet column FORMAT; PR #185 CodeQL block; pre-mirror-tree Box orphans
- `docs/runbooks/safety_portal_submission.md` — §43 successor runbook (admin + Box activation steps)
- `shared/box_client.py` — `upload_bytes_or_new_version` + `_find_child_file`
- `safety_reports/safety_naming.py` — shared naming source of truth
- `smartsheet_client.apply_column_styles` — column format must use model ATTRIBUTE
- `worker/migrations/0006_*.sql` — `users.disabled`; must apply before PR #185 redeploy
- `safety_reports/portal_admin.py` — CodeQL FP alerts #11/#13 pending dismissal
- Op Stds v16 §1 (External Send Gate — Worker admin route is send-free)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD)
