# CC Handoff Brief — ITS Progress-Reporting program + open field-ops items (2026-06-28)

> **Master plan (authoritative spec):** `~/.claude/plans/let-s-go-with-option-greedy-fiddle.md`
> ("ITS — Progress Reporting + P3 Materials, scaling-reconciled"). This brief is the **state + how-to-resume**
> layer; the plan file has the per-slice detail. Read the plan first, then this.

## TL;DR — where we are

- The Evergreen field-ops portal (Cloudflare Worker + D1 + React SPA) is **LIVE on the mirror**
  (`safety.evergreenmirror.com`), deployed from `~/its` @ `main` (`9ef3d5b`). D1 has migrations **0001–0019**
  applied. Login `test.admin` / `test.admin` (admin). It works.
- This session landed **4 PRs, all four-part-verified**: M1 (materials catalog), P-A1 (sheet-cap), A2 (host
  resilience), the field-ops UI fix. Plus we **diagnosed + fixed a live lockout** (see Gotchas).
- **Active task: Personnel creation + management** (task #22) — exploration was in-flight; **nothing built yet.**
- The big **Progress-Reporting program (Stages 0/1/2 + Track M)** is mostly **open** — see Open Items.

## Landed this session (merged + four-part-verified)

| PR | Slice | Merge commit | Notes |
|----|-------|--------------|-------|
| #325 | **M1** — admin material_catalog (migration 0019 + Worker CRUD + admin SPA) | `ef568c2` | Track M, parallel. Caps reuse 0013. |
| #326 | **P-A1** — `verify_sheet_cap.py` + `shared/sheet_capacity.py` margin-check | `b6ba870` | Stage-0 gate; margin-check = the armed cap tripwire. **Weekly retained** (monthly reverted 2026-06-29). Live finding: SAFETY_PORTAL=7 sheets. |
| #327 | **A2** — single-host resilience (SDK timeouts + keychain-locked + launchd RunAtLoad) | `3b285f5` | First live-safety slice. ⚠ **Operator follow-up: reload plists** (`scripts/launchd/install.sh`) to activate RunAtLoad; Box live smoke deferred to A3. |
| #328 | **field-ops UI fix** — shared `PageShell` + restyle Job/Equipment/Personnel/Materials | `9ef3d5b` | Confirmed working live (after hard-refresh). |

## The proven execution rhythm (use this loop per slice)

1. In `~/its-fieldops`: `git fetch origin main && git checkout -b feat/<slice> origin/main` (ALWAYS off fresh main).
2. **Python slices need the isolated venv** — `.venv-wt` is set up; verify `import shared` resolves to the
   worktree (`/Users/sethsmith/its-fieldops/shared`), NOT `~/its`. Use `.venv-wt/bin/python` for all gates.
3. Build (a **`fork` subagent** with full context works well — it inherits the plan + conventions). Don't commit.
4. Gates: Python = `.venv-wt/bin/python -m pytest -k "not integration" && mypy . && ruff check .`; TS = `cd safety_portal && npm run typecheck && npm test && npm run test:spa`.
5. Review the diff. For **Worker/migration** diffs run `portal-worker-security-reviewer`; for **Python/doctrine**
   run `ops-stds-enforcer`. (M1's review caught a real README-activation gap.)
6. **Live-safety slices** (anything touching `weekly_*`/`portal_poll`/`week_sheet`/the shared clients): pause for a
   **live smoke** + operator sign-off before merge (mocks-pass-live-fails has bitten ≥3×).
7. Commit (precise `git add` of the slice files only — leave `docs/cc-brief_p2.2-next-session.md` + `.venv-wt`
   untracked) → `git push -u origin feat/<slice>` → `gh pr create`.
8. CI: **poll** `gh pr checks <PR>` (the repo double-triggers; verify via run conclusion + `mergeStateStatus`,
   NOT `--watch`). Merge on CLEAN (or UNSTABLE if only CodeQL-infra is red).
9. `gh pr merge <PR> --squash --delete-branch` (the local-branch-cleanup leg errors with "main is held by
   worktree" — harmless; the GraphQL merge succeeds). **Four-part verify**: state=MERGED + mergedAt +
   mergeCommit + **main-branch CI on the merge commit = SUCCESS** (poll the commit's check-runs).
10. The worktree CANNOT `git checkout main` (held by `~/its`). For the next slice, branch off `origin/main` again.

## ACTIVE — Personnel creation + management (task #22)

Operator ask: build personnel CRUD on the (currently read-only) Personnel surface — create personnel **WITH a
portal login account** (name/trade + username/temp-password/role → a `personnel` row + a linked `users` row) AND
**non-account roster personnel** (name/trade, no login); edit (name/trade/active soft-retire) + link/unlink account.

**The surface map is DONE (Explore agent, 2026-06-28) — build from this, no need to re-map:**

- **`personnel` table** (`migrations/0014_urs_core_tables.sql:35`): `id` PK · `name` NOT NULL · `username` TEXT
  **nullable, NO FK** (soft link to `users.username`) · `trade` · `active` (1/0 soft-retire) · `created_at` epoch.
  The two tables are a **two-headed roster** — `users` = login directory, `personnel` = job-site roster; a person
  with an account has a row in BOTH (linked by the username string); no-account = `personnel` only (`username NULL`).
  **No auto-linking today in either direction; no referential integrity** (you can write a dangling username).
- **Capability** (`0013:72`): `cap.personnel.read` (admin only) + `cap.personnel.manage` (admin only). `submitter`
  gets neither. Routes 403 on missing cap.
- **Read surface (keep as-is)**: `worker/fieldops_personnel.ts` — `GET /api/fieldops/personnel` (keyset, `active=1`,
  LEFT JOIN jobs) + `GET /api/fieldops/personnel/:id` (detail + time-entry history); client `src/lib/fieldops_personnel.ts`
  (`fetchPersonnelList`/`fetchPersonnelDetail`); UI `src/pages/FieldOpsPersonnel.tsx`.
- **Reuse for the account-linked path**: account create already exists at **`POST /api/admin/users`** (index.ts ~1471,
  session + `requireRole("admin")`): body `{username, password, role?}` → `normalizeUsername()` (regex
  `lastname.firstname`, 3–64) → `hashPassword()` = `bcrypt.hash(pw, 10)` → INSERT users + `auditStmt` in ONE batch
  → 409 on exists, defaults role `submitter`. (`auth.ts:134/143`, `audit.ts:18`.) The **AccountsPage** drives it.
- **Write pattern to mirror**: `worker/fieldops_equipment_roster_write.ts` — `POST create` / `:id/update` /
  `:id/delete`(soft-retire `active=0`), each = typed-body validation + `badId` guard + mutation+`auditStmt` in one
  `c.env.DB.batch([...])`, UNIQUE→409, unknown id→404 via `changes()`.

**Build list:** `worker/fieldops_personnel_write.ts` (NEW — `POST /api/fieldops/personnel` create ·
`:id/update` · `:id/retire`, all gated `cap.personnel.manage`) + register in `index.ts`; extend
`src/lib/fieldops_personnel.ts` (`createPersonnel`/`updatePersonnel`/`retirePersonnel`); add a **manage mode** to
`FieldOpsPersonnel.tsx` (create form: name req / trade opt / username opt; per-row edit + retire); tests. **No
migration needed** — the nullable username already exists.

**3 product decisions to confirm with the operator before building** (Explore agent's recommendations in parens):
1. **Where account-creation lives** — *(rec: Option A)* keep the two flows separate (account via AccountsPage's
   `/api/admin/users`, then link by typing the username in the personnel create form), vs an inline "also create
   account" toggle on the personnel form (calls `/api/admin/users` then links — more complex). Start simple.
2. **Dangling username on create** — *(rec: validate the `users.username` exists, 422 `unknown_account` if not)*
   vs allow the soft dangling reference. Validating prevents orphaned links.
3. **Default role for an account-personnel** — *(rec: `submitter`/field-PM)*; `admin` must be explicit.

## OPEN ITEMS — the Progress-Reporting program (master plan has full detail)

**Strategic decisions already locked** (don't re-litigate): co-design with the **full Tier-A gate front-loaded**;
**WEEKLY sheets** (both safety+progress; monthly reverted 2026-06-29 — sheet=week, no straddle; the `sheet_capacity` margin-check is the cap tripwire; cap/tier is an operator confirm); **same-PR doc
skeleton + PDF-before-cutover**; **parameterize-not-clone** the security-critical modules via **required (no-default)
config objects** (the contamination gate); ITS-owned Smartsheet+Box SoR (canonical-Evergreen integration deferred);
full external send; manifest-receive materials; costs internal-only-but-config-flippable; incident photos ride a
fenced `portal_poll` pass + Box.

**Stage 0 (foundation — remaining; live-safety):**
- A3 — Box OAuth cross-process refresh-lock + keychain write-lock + 50-day idle marker. (Enables the deferred A2 Box live smoke.)
- A4 — unfiled-queue backlog alert + portal_poll outage escalation.
- A6 — weekly_generate hardening (per-job timeout + memory guard + resumable watermark) **then extract the hardened
  core to `safety_reports/compile_core.py`** (both compiles instantiate it).
- P0 — extract `shared/heartbeat.py` from `weekly_send_poll` + `portal_poll`.

**Stage 1 (parameterize + compile re-core; live-safety):** P1a `week_sheet` → required `(workspace_id,
key_builder)` (no `sheet_period`; safety stays weekly, no migration — monthly reverted 2026-06-29); P1b `weekly_send` → `SendConfig` + the **Workstream-tag
column on WSR (backfill `safety`) + WPR**; P1c `weekly_send_poll` → `DaemonConfig`; P4-core progress compile
instantiates `compile_core` (staggered + host mutex).

**Stage 2 (build on the hardened foundation):** P2 (CC creates the "ITS — Progress Reporting" workspace via
Smartsheet MCP + control folder + `WPR_human_review` build script + `WALKED_ROOTS += progress_reports` + the
**SENDING-inclusive WPR picklist REGISTRY entry** + Progress-Reports-Contact columns on `ITS_Active_Jobs` + §6a
manifest); P3 (category routing — a small `form_code→safety|progress` resolver, flag OFF until P4+P5; golden
mixed-week test); P4 (progress compile, PDFs-only); P5 (progress send + the operability guards built **once over
both review sheets**: `shared/recipient_health.py`, HELD-scan, approver-drift; + the **approver re-share**
activation gate); P6 (rollup numbers — `GET /api/internal/progress-rollup`, amend-collapse + equipment via
`equipment_location`); P7 (period-split structured sheets + the D1→Smartsheet up-sync on `fieldops_sync`, **§50-gated**).

**Track M (materials, manifest-receive):** M2 (per-job Material List + receive — bidirectional sync with the
**field-ownership conflict model**: down-upsert content-only, up-sync delivery-only, never the
`/api/internal/sync` full-replace; after P7+§50); M3 (Material Incidents + photos via the fenced portal_poll pass).

**Optional / polish:** the `weekly-progress-summary` form; UI follow-ups (route the form pages through PageShell;
tracker action messages → `.banner`; a `--danger` button variant for Close-job/Retire-unit).

## Operator follow-ups pending (NOT CC — flag to Seth)

- **A2 launchd activation**: reload the daemon plists (`scripts/launchd/install.sh`) on the host to activate
  `RunAtLoad`; validate auto-start after a reboot.
- **P-A1**: confirm the real Smartsheet per-plan sheet cap + the **$600 (Pro) vs $2,400 (Business)** tier with
  Smartsheet, then set `smartsheet.sheet_count_ceiling` in ITS_Config.
- **§50 doctrine bump** ("D1-as-writer to ITS-owned Smartsheet", v18→v19) — Seth-only; gates P7 + M2 write-back.
  Initiate early (parallel to P0–P6) so it's not the critical-path blocker.
- **meta-002**: define a Tier-3 backup / escalation SLA before the 20-job cutover (~4 new daemons raise the rate).
- **Live D1 migrations**: CC is classifier-blocked — the operator applies new migrations `--remote` **before** the
  Worker deploy. Deploys (`npm run deploy`) are operator-run too.

## Gotchas learned this session (save the operator a repeat)

- **`wrangler d1 migrations list` before `git pull` lies.** `~/its` was 25 commits stale; `list` compared the old
  (0012) migrations folder → "No migrations to apply" while the D1 was actually missing **0013–0019**. The deployed
  Worker then expected the capability tables → `resolveCapabilities` errored → **fail-closed lockout for everyone**.
  Fix was `git pull ~/its` THEN `wrangler d1 migrations apply --remote`. **Always pull `~/its` to latest main
  before list/apply/deploy.**
- **"Nothing changed" after deploy = browser cache.** The new bundle deployed (asset hash changed
  `DvoDs9k6`→`DIq3aURp`); the operator's browser held the old `index.html`. **Hard-refresh (Cmd-Shift-R) / incognito.**
- **Deploy ships from `~/its`** (the live daemon tree). It must be on latest `main` first; the build output is
  `dist/client/` (not `dist/assets/`). `npm run deploy` = `vite build && wrangler deploy`.
- **wrangler must be authed to the account that owns the portal** (not the personal `sethsmithusmc` gmail account
  `a1d033…`) — `wrangler logout && wrangler login` and pick the Evergreen/SolutionSmith account.
- The worktree shares `node_modules` with `~/its`; Python slices need the **isolated `.venv-wt`**.

## Session-close maintenance still owed (run `session-close-maintainer` / `session-log-writer`)

- **Session log** for this arc (#325–#328 + the lockout fix + the UI rework) — not yet written.
- **Memory updates**: evolve `decision_p2.4-parked-no-smartsheet-access` → "ITS-owned SoR; D1→ITS-Smartsheet mirror
  repointed; canonical-Evergreen deferred"; update `project_fieldops-portal-program` with the Progress-Reporting
  program + the 4 strategic decisions; add a reference memory for the **migrations-list-before-pull lockout** + the
  **browser-cache-on-deploy** gotchas. The big plan + the scaling eval (`its/docs/reports/2026-06-28_forensic-scaling-eval-20x20.md`)
  are the binding context.
- Tech-debt: the UI follow-ups; `.dash-section` duplicates `.card` (minor); the §6a doc DoD is owed per Progress slice.
