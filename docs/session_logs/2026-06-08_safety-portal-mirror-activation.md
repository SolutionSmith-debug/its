---
type: session_log
date: 2026-06-08
status: closed
related_prs: [185]
workstream: safety_portal
tags: [safety-portal, activation, mirror, portal-poll, weekly-generate, weekly-send, admin-route, box, custom-domain, hmac, smoke, external-send, deploy, keychain]
---

# Session log — Safety Portal mirror activation (end-to-end live proof on evergreenmirror.com)

Operational activation session: brought the Safety Portal from "all PRs merged" to "fully
live + one clean end-to-end happy path proven" on the mirror tenant (evergreenmirror.com).
**No new code was committed to `~/its` during this session.** All work was live infra
configuration (Cloudflare Worker secrets, D1 migration, ITS_Config rows, Keychain, Box folder
seed). The only repo-side changes are this session's close-out doc edits (CLAUDE.md
daemon-state rows, `docs/tech_debt.md`, this session log), which are UNCOMMITTED and left for
the operator to commit. The session closed with one real external send: `weekly_send_poll`
fired unattended on its 07:12 PT cycle and delivered the ZZ Portal Proof week packet to
`seth@solutionsmith.org` — operator confirmed inbox receipt + PDF.

## PRs activated this session

### PR #185 — Phase 7 admin route + session revocation (`f3ad814`)

This session activated PR #185, which was merged in the prior session (2026-06-07) but had
never been deployed or exercised live. Track B activates the admin route, the migration, the
Worker secret, and the Keychain entry — in that order.

PR #185 — four-part verify clean: state MERGED, mergedAt 2026-06-07T18:36:14Z, mergeCommit f3ad8148bc363a770786a6c8ad8f7d505582ebff (short f3ad814), main CI on merge commit SUCCESS on both workflows (ci run 27101247071, CodeQL run 27101246944).

**No new CI ran this session** — this was a live-infrastructure activation session, not a
code-change session. The four-part verify above is the standing proof of PR #185's landed
state, carried forward from the prior session. The session log for the prior session
(`2026-06-07_safety-portal-phase7-styling-box-schema.md`) contains the full PRs #186–#189
verify blocks.

## Pre-flight: brief state correction

The session brief's frozen SHAs were stale before any work began. The brief described `~/its`
as "53c27ac, 3 behind" (needing a pull to pick up PRs #188 and #185). Actual state: `~/its`
was already at `f3ad814` — the operator had already pulled after the prior session. Step A
(pull + align) was a no-op.

`brief-validator` confirmed all other code-shape claims, with one material correction: the
portal Worker lives in `safety_portal/` (not `portal-tour/`). The `portal-tour/` directory
in the working tree is a static screenshot artifact, not a Worker source tree. Migration 0006
adds a `disabled` column to the `users` table; sessions are stateless cookie-HMAC (there is
no session table), which is why per-request revocation reads `users.disabled` on every
protected route rather than invalidating a session row.

## Track B: admin route + revocation activation

### The Keychain saga

The macOS `security add-generic-password -w` flag form prompts the controlling TTY and ignores
piped stdin when run in an interactive shell — it silently accepted a 6-character garbage token
twice (both in Keychain and in the Worker secret). Root cause of the early `portal_admin
list-users` 401: the Worker's `PORTAL_ADMIN_API_TOKEN` and Keychain's `ITS_PORTAL_ADMIN_TOKEN`
were byte-equal, but neither matched the intended secret — they matched each other's garbage.

Fixed with the `-w VALUE` argv form (paste-safe, no TTY interaction). The `security
add-generic-password -a ITS -s ITS_PORTAL_ADMIN_TOKEN -w <value>` form was used to store the
correct token; the Worker secret was reset to the same value in the Cloudflare dashboard.

This is the second time a Keychain stdin-vs-TTY issue has produced a silent wrong value (see
also F04 in `2026-05-28_f17-f04-docstring-sweep.md`). The fix pattern (`-w VALUE` argv) is now
the documented form in `docs/tech_debt.md`.

### harness auto-accept block

The CC harness auto-accept classifier blocked CC from running the three consequential infra
commands (Worker secret put / D1 migration apply / `npm run deploy`) even after the operator's
explicit "go." The operator ran those three commands directly (Path A); CC verified each via
read-only checks afterward. The split was:

- Path A (operator ran): `wrangler secret put PORTAL_ADMIN_API_TOKEN`, `wrangler d1 execute ...
  --file worker/migrations/0006_add_disabled_to_users.sql`, `npm run deploy`
- CC verified: migration applied (queried D1 for the `disabled` column), Worker redeploy
  returned the correct routes

### Deploy and the workers.dev 1042 error

`npm run deploy` correctly redirected via `@cloudflare/vite-plugin` to the generated
`dist/its_safety_portal/wrangler.json` — the Worker was not broken. However, PR-J's
`custom_domain` route declaration in `wrangler.jsonc` with no `workers_dev` key **disabled the
`*.workers.dev` URL** on deploy (wrangler emitted a warning at build time; that warning was
present but unacted on during PR-J). Every `workers.dev` route returned a Cloudflare
`error code: 1042` (workers.dev route disabled by custom domain declaration).

This stranded `portal_poll` and `portal_admin`, both of which read
`safety_reports.portal.worker_base_url` from ITS_Config, which pointed at the `workers.dev`
URL. Diagnosed as workers.dev-disabled (not a code bug): the custom domain
`safety.evergreenmirror.com` was already serving 200 + admin route 401. The fix was to repoint
ITS_Config `safety_reports.portal.worker_base_url` → `https://safety.evergreenmirror.com`.
This is the correct end-state regardless: the custom domain is the stable URL; the
`workers.dev` URL was always a staging artifact.

`portal_poll` recovered after the repoint: it successfully synced JOB-000008 (active=1) from
the Worker, proving health of the pull path.

### Admin route and revocation verified

After the secrets were corrected and the URL repointed:

- `portal_admin list-users` authenticated and returned the user list (401 → 200).
- `portal_admin disable-user test.pm` disabled the test user; that user's existing live
  session returned `401 revoked` on `/api/jobs` (the per-request `requireSession` D1 lookup
  caught the `disabled=1` flag).
- `portal_admin enable-user test.pm` re-enabled the user; session recovered.

## Track C: custom domain

`safety.evergreenmirror.com` was live as a side-effect of Track B's `npm run deploy` — the
zone is Cloudflare-hosted and the custom domain route was already declared in `wrangler.jsonc`
(PR #188). No additional Cloudflare dashboard action was required. The custom domain served
the portal and admin route correctly immediately after deploy.

## Track D: Box mirror tree activation

Created the `ITS_Safety_Portal` root Box folder (ID `388017263015`, owner `seths@evergreenmirror.com`,
collaborator `daniels@evergreenmirror.com` editor role). Set ITS_Config
`safety_reports.box.portal_root_folder_id = 388017263015`. The config gate in PR-K
(`safety_reports/intake.py` and `weekly_generate.py`) activated: the ROOT → per-job → per-week
mirror-tree filing path went live.

## End-to-end smoke (happy path)

The operator submitted a real `equipment-skid-steer` form via the live portal
(`safety.evergreenmirror.com`). The full chain:

1. **Worker queued the submission in D1** (send-free; `portal_poll`'s next pull cycle would
   pick it up).
2. **`portal_poll` pulled + HMAC-verified** the submission on its next cycle → passed the
   structured payload to `intake.process_portal_submission`.
3. **`intake`** filed the per-submission PDF to the Box mirror tree
   (`ITS_Safety_Portal/ZZ Portal Proof/week of 2026-06-06/`), wrote the week-sheet row,
   set `box_verified=1`, posted the mark-filed receipt to the Worker.
4. **`weekly_generate`** compiled the week packet. First run missed JOB-000008 (the
   `list_active_jobs` 60s TTL / Smartsheet propagation had not elapsed); second run compiled
   successfully. The compiled Box PDF: `app.box.com/file/2270754573093`. A WSR row was
   STAGED in `WSR_human_review` (recipient resolved from `ITS_Active_Jobs`, Send Status
   PENDING, no send issued).
5. **`weekly_send_poll`** (unattended, Monday 07:12 PT cycle) detected the row with
   `Approve for Scheduled Send` checked (operator had checked it at 07:08 PT, inside the
   `MON ≥07:00` Pacific window), ran the F22 `verify_approval` gate (approver resolved to
   `seths@evergreenmirror.com` — the Safety Portal workspace OWNER; audit clean), stamped
   `Approved By = Seth Smith` / `Approved At` / `Sent At`, and dispatched
   `weekly_send.send_one_row` → real Graph send from `safety@evergreenmirror.com` to
   `seth@solutionsmith.org` with the compiled Box packet attached. Row flipped to `SENT`.
6. **Operator confirmed inbox receipt and PDF.**

Daemon-health: ITS_Daemon_Health OK, 1 item sent. ITS_Errors had zero rows on 2026-06-08.

## Revocation finding (defense-in-depth gap)

Disabling `test.pm` via `portal_admin disable-user` correctly 401'd the user's existing live
session on `/api/jobs` (the `requireSession` middleware checks `users.disabled` per-request —
fail-closed). However, the operator observed that the disabled user could **still reach
`/api/login`** — `validateUser` (`safety_portal/worker/auth.ts:50-67`) does not gate on
`users.disabled`. This is not a capability bypass (`requireSession` holds on all protected
routes), but it is a defense-in-depth and UX gap: a disabled user can obtain a new session
cookie that `requireSession` will then reject on every subsequent request. The correct behavior
is for `validateUser` to check `disabled` and refuse to issue the cookie at all.

Recorded in `docs/tech_debt.md` (see below).

## Docs updated this session (uncommitted)

- **`CLAUDE.md`**: `portal_poll.py` row changed PLANNED→built (with daemon-health, heartbeat,
  watchdog, capability-gated); `weekly_generate`, `weekly_send`, `weekly_send_poll` rows
  changed NOT-live-verified→live-validated.
- **`docs/tech_debt.md`**: PR-H entry marked CLOSED (migration + secret + deploy done); three
  new OPEN findings added (see below).
- **Auto-memory**: `project_safety_portal_state.md` updated (Phase-7 batch all active; three
  activation tracks complete; outstanding: edge-case test brief, ZZ Portal Proof deactivate,
  worktree cleanup); `MEMORY.md` index updated.

## Findings recorded to docs/tech_debt.md

Three new OPEN entries added this session:

1. **`/api/login` disabled-gate gap.** `validateUser` does not check `users.disabled`; a
   disabled user can obtain a session cookie that `requireSession` rejects. Fix: add a
   `disabled` guard to `validateUser` before `bcrypt.compare`. Low-severity (no capability
   bypass); defense-in-depth + UX.

2. **`custom_domain` route disables `workers.dev` on deploy.** When `wrangler.jsonc` declares
   a `custom_domain` route with no explicit `workers_dev: true` key, `npm run deploy` silently
   disables the `workers.dev` URL (Cloudflare wrangler behavior; warning emitted at build time,
   easy to miss). Fix: add `workers_dev: false` (explicit) or `workers_dev: true` (re-enable
   both). Current end-state: ITS_Config points at the custom domain; `workers.dev` remains
   disabled. Acceptable for the mirror; must be documented before Evergreen cutover.

3. **`scheduled_send_local` not in `seed_its_config.py` + silent fail-open on malformed
   value.** The `weekly_send_poll` scheduled-send window is controlled by the ITS_Config key
   `safety_reports.portal.scheduled_send_local` (expected: `"MON 07:00"`). This key is not
   seeded by `scripts/seed_its_config.py`, so a fresh environment has no row and fails open
   (falls back to the hardcoded default). A malformed value also fails open silently. Fix:
   add the key to the seed script and add a validation step in `weekly_send_poll` startup.

## Key decisions

1. **ITS_Config `worker_base_url` repointed to custom domain; `workers.dev` URL abandoned.**

   After diagnosing the 1042 error as workers.dev-disabled (not a code bug), the fix was
   to repoint the runtime config rather than re-enable `workers.dev`. Rationale: the custom
   domain is the correct stable URL for the mirror tenant; re-enabling `workers.dev` would
   just restore a staging artifact. This also validates the end-state: the full chain runs
   via `safety.evergreenmirror.com` with no `workers.dev` dependency.

   Alternative considered: add `workers_dev: true` to `wrangler.jsonc` and redeploy to
   restore `workers.dev`. Rejected — unnecessary for the mirror; would require another
   deploy cycle; and the custom domain is the correct permanent URL anyway.

2. **Migration 0006 applied to live D1 before Worker redeploy (not after).**

   The migration order was: apply migration → deploy Worker → verify admin route. This is the
   correct order: the Worker code that references `users.disabled` would fail at the D1 query
   level if deployed before the column existed. The prior session log (`2026-06-07`) documented
   this order dependency explicitly in the activation punch-list.

3. **Box root folder seeded manually (not via script).**

   `ITS_Safety_Portal` was created via the Box web UI rather than via `scripts/seed_box.py`
   or similar. Rationale: this is a one-time root folder; the config-gate design means
   intake.py / weekly_generate.py use `box_client.get_or_create_folder` for all sub-folders
   under the root — only the root folder ID needs to exist and be set in ITS_Config. No
   script was warranted for a single manual step.

4. **`weekly_generate` first-run miss (JOB-000008) treated as expected behavior, not a bug.**

   The first `weekly_generate` run after the smoke form submission did not pick up JOB-000008.
   Diagnosed as the `list_active_jobs` 60s cache TTL / Smartsheet row-propagation delay (the
   job had just been activated moments before). The second run compiled correctly. No code
   change; behavior is within the documented TTL. The compile path emits no stdout line on
   success — only skips emit lines — which was an initial source of confusion resolved during
   the session.

5. **`weekly_send_poll` unattended Monday send treated as intentional proof of the full gate.**

   The operator did not manually trigger `weekly_send_poll` — it fired on its normal 07:12 PT
   cycle and processed the approved row without CC involvement. This was the intended test of
   the unattended approval → send flow (F22 verify → stamp → Graph send). The fact that it
   fired without prompting was the correct outcome.

## Open items / next session

- **Revert ZZ Portal Proof (JOB-000008) → Inactive** after validation. Currently still Active.
- **Edge-case test suite** for the full portal → intake → weekly_generate → weekly_send chain.
  Now that the happy path is live-validated, the edge cases (duplicate submission, unknown job,
  failed HMAC, empty week, Box folder conflict) warrant a dedicated brief.
- **`/api/login` disabled-gate fix** (`validateUser` check on `users.disabled`; see finding 1
  above). Low-severity; schedule for Phase 7.1 or next safety-portal session.
- **`seed_its_config.py` gap** — add `safety_reports.portal.scheduled_send_local` to the seed
  script (finding 3 above).
- **Worktree cleanup + branch -D** (operator action; hook-blocked for CC). The
  `~/its-portal-rewire` worktree from the prior session may still exist; operator should
  verify and prune.
- **Blueprint doctrine commit** — any planning-layer updates (mission, Op Stds, memory-archive)
  from this session are a separate `~/its-blueprint` session. The blueprint working tree was
  clean at session open; no blueprint doctrine edits were made here.
- **Evergreen cutover** — the mirror activation is complete; the Evergreen production cutover
  is a separate, out-of-scope milestone.
- **`workers.dev` explicit declaration** — add `workers_dev: false` to `wrangler.jsonc` to
  document the current state explicitly (finding 2 above). Currently implicit.

## What was NOT touched

- **No new code committed to `~/its`** this session. All three close-out doc edits (CLAUDE.md,
  tech_debt.md, this session log) are uncommitted and awaiting operator commit.
- **Invariant 1 (External Send Gate):** the one real external send that occurred
  (`weekly_send_poll` → `weekly_send.send_one_row` → Graph send) went through the standard
  F22 approval gate — operator checked `Approve for Scheduled Send`, the poller verified and
  stamped the approver, then dispatched. No new send-path code was written or bypassed.
- **Invariant 2 (Adversarial Input Handling):** unchanged. All portal submission content
  processed through the existing `wrap()` + `anomaly_logger.check()` path.
- **`~/its-blueprint` doctrine files:** no doctrine edits this session. The brief's reference
  to "8 uncommitted files" in `~/its-blueprint` was investigated: those files are NOT present.
  The blueprint working tree was clean at session open.
- **Evergreen production tenant:** this entire session was mirror-only (evergreenmirror.com /
  `seths@evergreenmirror.com`). No Evergreen production data touched.
- **`portal_poll.py` code:** already built and deployed from PR #174. Not modified this
  session; just restarted after ITS_Config URL repoint.
- **CodeQL alerts #11 and #13** (`portal_admin.py` FP): these were dismissed by the operator
  prior to merging PR #185 (pre-session). Not re-examined here.

## Lessons captured to memory

- **`project_safety_portal_state.md`:** updated to reflect Phase-7 activation complete (all
  three tracks done); ZZ Portal Proof deactivate + edge-case test brief as remaining items.
- **`MEMORY.md`:** index updated.
- **`feedback_run-mypy-before-push.md`:** no change (no code pushed this session).
- **New pattern (Keychain TTY saga):** `security add-generic-password -w VALUE` (argv form)
  is the correct paste-safe invocation. The `-w` flag form prompts TTY and silently stores
  garbage when stdin is piped in an interactive shell. This is the second instance of this
  class (first was F04 in PR #113); the `-w VALUE` form is now the only form documented in
  tech_debt.md and should be the only form used in any future Keychain write scripts.

## Cross-references

- Prior session log (PRs #186–#189 + PR #185 built):
  [`2026-06-07_safety-portal-phase7-styling-box-schema.md`](2026-06-07_safety-portal-phase7-styling-box-schema.md)
- Prior session log (Phase 5 WSR rewire, portal_poll.py built):
  [`2026-06-05_safety-portal-wsr-rewire-pull-model.md`](2026-06-05_safety-portal-wsr-rewire-pull-model.md)
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — three new OPEN findings + PR-H CLOSED
- `safety_portal/worker/auth.ts:50-67` — `validateUser` disabled-gate gap (finding 1)
- `safety_reports/portal_admin.py` — admin CLI; Keychain token must use `-w VALUE` argv form
- `safety_reports/portal_poll.py` — PULL daemon; recovered after ITS_Config URL repoint
- `safety_reports/weekly_send_poll.py` — unattended Monday send; F22 verify + stamp
- `safety_reports/weekly_send.py` — Graph send; recipients resolved from ITS_Active_Jobs
- `worker/migrations/0006_add_disabled_to_users.sql` — applied to live D1 before redeploy
- `shared/portal_hmac.py` — HMAC verify in portal_poll; unchanged this session
- Op Stds v16 §1 (External Send Gate — real send went through standard F22 approval gate)
- Op Stds v16 §43 (successor-remediation runbook; §43 entry for portal_poll in docs/runbooks/)
- `decision_phase5-portal-transport` memory entry — Python PULL model design reference
- `project_safety_portal_state.md` memory entry — updated this session
