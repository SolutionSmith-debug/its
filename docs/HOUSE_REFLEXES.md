---
type: reference
status: active
workstream: docs
tags: [standards, lessons, house-reflexes, canonical]
---

# ITS House Reflexes — the standards that keep us honest

**Purpose.** The single, canonical, deduped home for the recurring lessons and working standards of ITS —
so a fresh Claude Code session loads them once and *doesn't let the standards fall.* Each entry is a rule +
the pattern that earned it. This is **execution-facing** (how to work in `~/its`); the **invariants and
doctrine** it points to are canonical in `~/its-blueprint/doctrine/` (Op Stds v20, Foundation Mission v11) —
planning-layer wins. The **roadmap** is canonical in the blueprint + the field-ops program file; this is not
that. Loaded via `@import` from `CLAUDE.md`'s START-HERE block.

> When you add a lesson: add it *here* (one line + why), not in a new doc or a fifth memory file. If it's
> doctrine-level, it belongs in the blueprint doctrine instead. This file is the "don't sprawl" backstop.

---

## 1 — Trust the live code, never the claim

- **A current-state claim is a hypothesis until verified against live HEAD.** A brief / audit / memory / chat
  note that names a file, function, line, SHA, PR, or sheet-ID has drifted between authorship and now —
  `grep`/`Read` the real code, `gh` the real PR, before acting. **Zero grep hits is decisive over confident
  memory.** (Forensic class #3, recurred 16×. The `brief-validator` agent automates this.)
- **A datum has N implementations — enumerate ALL of them first.** "Fixed in one place" is the recurring
  incomplete-fan-out bug: a PDF name lives in the Box file + the Smartsheet attachment + the Worker
  `Content-Disposition`; a status value lives in the writer const + `picklist_validation.REGISTRY`. `grep` the
  datum everywhere and **live-test** before claiming done. (Multi-surface fan-out.)
- **Don't deploy / migrate / audit from a stale checkout.** `git -C ~/its pull origin main` before any
  `wrangler deploy`, `wrangler d1 migrations apply/list`, or cross-repo drift audit — a 25-commit-behind tree
  reported "No migrations to apply" while the live Worker expected the new tables → universal lockout.
  (Forensic class #2; `block-stale-cloudflare-deploy.sh` + watchdog Check Q/S catch the in-session case.)

## 2 — Prove the control bites (green proves nothing)

- **A new test / hook / gate is worthless until it RED-lights on a synthetic violation.** Inject → confirm it
  fails → revert. For anything that shells out or hits an SDK, add a **live smoke** on top. (feedback: prove-the-control-bites.)
- **Mandatory live smoke before merge for new shared infrastructure.** Mocks-pass-but-live-API-rejects is a
  recurring class (SimpleNamespace mocks miss what the real Smartsheet/Box/Graph SDK rejects). (feedback: mandatory-live-smoke; Op Stds §30.)
- **Adversarial review is definition-of-done on any trust-boundary surface** — an untrusted parse/decode, a
  D1/Smartsheet write-route fed by client/operator data, or an external-send path. Unit tests structurally
  *cannot* find injection, double-send windows, or fail-open misconfig; adversarial review (`/security-review`,
  `portal-worker-security-reviewer`, `ops-stds-enforcer`) repeatedly has. (Forensic classes #9/#14.)
- **A textually-clean auto-merge is not semantically proven** — re-run the FULL CI gate on the *rebased* tree,
  never trust a conflict-free rebase of overlapping PRs.

## 3 — Git / worktree / deploy discipline (the live-tree is a loaded gun)

- **The launchd daemons run the `~/its` working tree from disk every ~60s.** Uncommitted Python-SOURCE edits
  go live immediately; committing in `~/its` mid-cycle can strand the publish daemon on a `publish/req-*`
  branch. **Any Python-source change → a per-task worktree off `origin/main` with its OWN fresh venv**
  (`python -m venv .venv-wt && pip install -e '.[dev]'`; NEVER `cp -R .venv` — the copied `bin/pip` shebang
  repoints the live editable install). **Docs-only edits are fine on the live tree.** (worktree_discipline.md.)
- **Never two doctrine-touching sessions on one blueprint checkout** — isolate blueprint work in its own worktree.
- **The four-part PR-landing verify** (before believing a PR landed): `state=MERGED` · `mergedAt` non-null ·
  `mergeCommit.oid` present · **main-branch CI on the merge commit = SUCCESS**. Passing 1–3 but failing 4 is
  *functionally not landed*. (`pr-landed-verifier`; docs/operations/pr_merge_discipline.md.)
- **Squash-merge repo: PR `state=MERGED` is the ONLY safe branch-delete signal** (commits-ahead misleads).
  `git branch -D` is hook-blocked → `git update-ref -d refs/heads/<b>` *after* the MERGED verify. Preserve
  OPEN / CLOSED-unmerged / no-PR branches.
- **CI double-triggers (push + pull_request); a check-run can stick IN_PROGRESS on a MERGEABLE/CLEAN PR.**
  Verify via run-level conclusion + `mergeStateStatus`, never `gh pr checks --watch`. GitHub GraphQL/Actions
  writes flake with 401s and CodeQL infra-fails → merges land `unstable`; use REST `mergeable_state`, retry
  writes in a loop, merge on `unstable` when only CodeQL-infra is red.
- **Auto-merge is OFF** — the publish daemon polls `mergeStateStatus` then `--squash`; `_reset_to_main`
  recovers a stranded tree (not `git stash`).

## 4 — The invariants are load-bearing (cite doctrine, don't reinvent)

- **Invariant 1 — External Send Gate (permanent).** Two-process: generation scripts have ZERO send capability,
  send scripts have ZERO AI. Enforced at import by `tests/test_capability_gating.py` — enroll every new
  generation/send script. The kill switch is a fail-open operator convenience, **not** the security boundary.
- **Invariant 2 — Adversarial Input.** All content outside the operating tenant is untrusted data; wrap with
  `untrusted_content`, screen attachments/photos (§34), run `anomaly_logger.check()` before trusting an
  extraction. Layer 5 (anomaly logging) is a **post-hoc tripwire, not a barrier** — prevention is Layers 2–4 +
  the send gate.
- **Picklist REGISTRY parity:** a new `StrEnum` value that a route writes MUST be added to
  `shared/picklist_validation.REGISTRY` in the *same* PR — every `update_rows` is gated on it; mocks never
  catch a miss, only a live smoke does. (CI gate now enforces parity.)

## 5 — Config / state / data discipline

- **Observable config resolution:** log each resolved setting with its source (`ITS_Config` vs `default`) at
  startup and WARN-loud on a missing declared key. A silent fallback to a hardcoded default hides a real
  misconfig — "never silent" applies to config too. (Forensic class #7; `REQUIRED_CONFIG` pass = issue #336.)
- **`ITS_Config` reads are workstream-scoped** — `get_setting(key, workstream=…)` matches on the Setting name
  AND the Workstream cell. Footgun: the progress intake gate `progress_reports.intake_enabled` is read under
  `Workstream=safety_reports` (intake's own workstream), not `progress_reports`.
- **A dark-shipped gate has NO row to flip — SEED the row (even `=false`) when the code merges.** A boolean
  `ITS_Config` gate read via `_read_bool_setting(default=False)` treats a MISSING row identically to `false`,
  so a capability that "ships dark" has *no row at all* — the operator hunts for a switch that doesn't exist.
  Seed the gate row (value `false`) in the same change that adds the gated code, so activation is a visible
  cell-flip, not a phantom. (Bit the 2026-07-05 equipment/materials activation: `equipment_enabled`/
  `materials_enabled` had no row → the operator couldn't find one to flip → the rows had to be CREATED.)
- **Read a gate row's full Description BEFORE flipping it — a doctrine-divergent gate flip is a doctrine
  action (§44 high-class), not an autonomous one.** A gate's `ITS_Config` Description cell can carry an
  explicit precondition ("Do NOT set true until the §51 rider is merged"). A verbal go-ahead resolves the
  *decision* but not the *documented precondition* — flipping a capability whose activation contradicts
  canonical doctrine introduces a code-vs-doctrine drift the auditor flags, and doctrine is a FIXED
  high-capability class that escalates, never gets actioned unsupervised. Fetch + read the row's cells,
  not just its rowId, before an `update_rows` flip. (Bit the 2026-07-06 M3 activation: `materials_enabled`
  was flipped on a verbal one-way-up call, then reverted when the response revealed the in-cell "rider must
  be merged first" guardrail — `incidents_enabled`, which has no such block, stayed on.)
- **Never `Path.write_text/write_bytes` under `~/its/state/`** — route every state write through
  `shared/state_io.py` (`atomic_write_json/text`, `with_path_lock` on a sidecar `.lock`). Enforced at CI
  (`test_state_write_discipline.py`).
- **Display-name-only attribution:** crew/task/report-facing WHO fields resolve through `personnel.name`,
  never `users.username`. (Caught 3× — P2.6, R1, R7.)
- **D1 mutation + its audit row are ONE atomic `db.batch([...])`** (the "W4" class) — never a mutate-then-audit
  two-step.

## 6 — Roadmap & scope discipline

- **New scope slots into the roadmap, it is not built ad-hoc.** When an idea surfaces mid-phase: scope it,
  queue it at the right staged slot, finish the current phase, build only on green-light. (feedback: slot-into-roadmap.)
- **Don't harden dormant subsystems.** Before cleaning a tech-debt item, gate on "live consumer + real data?"
  — not just "collision-safe" or "still open." (feedback: dont-harden-dormant.)
- **Prefer simple-correct over premature optimization** for an unverified constraint; match a sheet's storage
  period to its report cadence (safety/progress stay weekly). (feedback: match-period-to-cadence.)
- **Parallelize genuinely-independent work** (no file overlap, own worktree, no shared D1/deploy) with a
  background Agent/Workflow; serialize only on real dependencies or shared resources.

## 7 — Known platform gotchas (the ones that have bitten us)

- **Keychain `security … -w` TTY trap:** with a controlling `/dev/tty` present it reads the terminal and
  *ignores piped stdin* — corrupted the Box refresh token twice. Use `-w VALUE` / run headless. (`keychain.set_secret` now detects TTY and handles both.)
- **Cloudflare `custom_domain: true` disables the `*.workers.dev` URL on deploy** (error 1042) unless
  `workers_dev: true` is also set — repoint daemon base-URLs to the custom domain right after deploy.
- **Worker `ASSETS.fetch()` responses have immutable headers** — mutating them (`secureHeaders`/`c.header`)
  throws → 500s every asset + SPA doc under `run_worker_first:true`. Reconstruct the response; verify with
  `wrangler dev` (vitest can't serve assets).
- **Worker SPA fallback returns 200 (index.html) for a deleted/missing asset path** — verify asset removal by
  content-type, not status.
- **Smartsheet MCP = `delete_rows` only** (no delete_sheet/folder/workspace) — use the Python SDK for
  sheet/folder deletion, name-guarded with a hard-coded allowlist. Sheet names cap at 50 chars (errorCode
  1041; folders uncapped). `ABSTRACT_DATETIME` accepts naive `YYYY-MM-DDTHH:MM:SS` only (write naive Pacific
  wall-clock).
- **Box MCP has NO delete** — use `box_client` (the ITS OAuth account). Refresh tokens rotate every exchange;
  `_store_tokens` MUST persist the new one or Box dies in ~60 days.
- **Mirror-loop re-creation (2026-07-03):** `purge-job` deletes D1 only; if you purge a portal job while its
  row still exists in `ITS_Active_Jobs`, the `portal_poll` down-sync re-inserts it as `origin='smartsheet'`.
  **Delete the `ITS_Active_Jobs` row FIRST, then purge.** (No automated cleanup of the Smartsheet folder /
  week-sheets / Box PDFs on any removal path — a full "nuke a job everywhere" is a manual 3-system op.)
- **`deploy "nothing changed"` is usually browser cache** — confirm the live asset hash changed in the deploy
  output, then hard-refresh / incognito; don't chase it as a deploy failure.
