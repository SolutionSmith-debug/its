---
type: session_log
date: 2026-07-23
status: complete
related_prs: [673, 675, 676, 679, 680, 683, 685, 686, 687]
workstream: infrastructure
tags: [session_log, standup, wipe, cutover, migrations, aug7_delivery]
---

# Session log — 2026-07-23 · Stand-up process optimization (Brief A)

Hardened the tenant wipe / stand-up / regen tooling family against the three-lens
review that followed the 2026-07-23 full sandbox rebuild (`logs/reviews/2026-07-23_opt_{simplify,operator,runtime}.json`).
Ten PRs, all four-part-verified. Parallel session; the dashboard session's
**#674** and the archive session's **#678** landed mid-flight and were reconciled
in-branch.

## What landed and why THIS approach

**#673 — P0: wipe dump fails CLOSED on transient errors.** `wipe_tenant.dump_workspace`'s
`except (requests.HTTPError, RuntimeError)` classified 429/5xx/timeouts and the
`sheet_dump_truncated` artifact as "unreadable — skip and delete anyway". The dump is
the sole row-restore source, so that was a fail-open data-loss path. Chose a NEW
family-lib `scripts/migrations/_rest_retry.py` (bounded retry, exhaustion propagates,
`raise_for_status=False` mode) over inline retry so standup's restore-rows/shares POSTs
could ride the same seam — the share-restore path was silently WARN-dropping a
rate-limited add, narrowing an F22 approver set. "unreadable" now classifies ONLY on
permanent signatures (404 / errorCode 1006/1115). **Audited the existing dump**: its 4
unreadable sheets are exactly the known 404 ITS_Errors shells — the rehearsal restore
was NOT lossy.

**#676 — non-interactive contract over the blind `y\n*8` feed.** Picked
`STANDUP_NONINTERACTIVE=1` + closed stdin (an unexpected prompt fails LOUD) over the
interim single-`y` stopgap: the latent hazard is a FUTURE builder growing a destructive
prompt, and only a closed-stdin contract makes that fail-loud instead of silently
confirmed. Extracted `seed_its_config`'s inline `input()` into the family `_confirm`
shape; the other five seams gained the carve-out in place. Also shipped run-state
`--resume` and streamed prefixed child output in the same PR (all standup.py ergonomics).

**#679 — `finish` subcommand.** The by-hand epilogue became mechanism. Key design call:
the fleet-reload POSTURE table is IN CODE and fail-dark (`--posture dark` excludes the 5
send-dispatch plists; never inferred from ITS_Config — a gate row reading `true` must not
pull a send daemon up), and the gate-flip report is READ-ONLY (every flip stays a §44
human decision). **Reconciled with #674 mid-flight**: the dashboard session landed the ACT
fence (marker `standup_in_progress.json`, 6h fail-open, cleared only on standup
completion) while this was in branch — adopted its mechanism wholesale and DELETED this
branch's own `_run_marker.py` context-manager design; closed its#677 as superseded.

**#680 — CL-12 repoint actuator** and **#685 — CL-11 shares + VC-10.** Both build-only,
Seth-attended to run. Both went through `ops-stds-enforcer` adversarial review as
definition-of-done (operator-supplied-data → Smartsheet write surfaces):
- #680 caught a **BLOCK**: the §E send-scope exclusion was a blocklist that missed
  `scheduled_send_local`; since `--map` takes an arbitrary path, a crafted map could carry
  it. Fixed by inverting to an A–D setting-name ALLOWLIST (a blocklist under-approximates;
  a future `send_window_local` would slip through). Two RED-light tests added.
- #685 caught **WARN advisories**: the mirror-domain pin gap (a manifest typo would blind
  both residue checks — a leftover mirror USER share GRANTS live F22 authority), fixed by
  pinning `EXPECTED_MIRROR_DOMAIN` in the seeder + `SANDBOX_DOMAIN_MARKER` in VC-10; and a
  missing live payload-shape smoke, added (`test_list_workspace_shares_live`).

**#675 / #683 / #686** — doctrine_manifest in the regen remap scope (a proven #670 miss);
scoped `--retry-missing` to the unresolved constants' workspaces (overlay merge so a
filtered retry never degrades a resolved constant); docs collapse (checklist/punchlist/
README around the one-step stand-up, **CL-12 "all gates true" doc bug fixed**).

**#687 — run-branch mode.** Per-run `standup/run-<UTC>` with per-stage checkpoint commits
(pathspec excludes `logs/` — the dumps are untracked-not-ignored and a naive `add -A`
would commit multi-MB JSON), `--resume` merge-main fix flow (conflicts STOP, never
auto-resolved), landing-PR push. Default ON; committing mid-run is safe only inside the
daemons-down envelope.

## Deliberately NOT built (review-ratified)

Scratch-prefix dress rehearsal (28-file name-canon fan-out + would need a production wipe
tool); builder parallelization (regen's shared-file rewrites force serialization); a
production wipe variant (rollback is repoint-back, partial stand-ups RESUME — rationale
now recorded in the migrations README); seeder-engine consolidation (§14 — the 11-copy
engine dedup clears the reuse bar but needs Seth's sign-off, `opt_simplify` #7); generic
dump-restore extraction (deferred until the demo-tracker restore need is real);
cross-invocation regen caching (fresh subprocess reads are the correctness property).

## Flagged for the operator / Seth

- Run `test_list_workspace_shares_live` (integration) before trusting VC-10 at cutover.
- A mirror-tenant `production_repoint.py --commit` dry-run before Aug-3 (belt-and-braces).
- Shares/repoint open questions in the #685/#680 PR bodies (access levels all EDITOR,
  per-workspace narrowing, GROUP-fail posture, the 7 accounts must exist first).
- Local branch `fix/worker-coverage` (PR #641 CLOSED-unmerged) carries a gitleaks
  generic-api-key finding at `16439fc` (only on that branch, never on main) — worth a
  MERGED-verify + `git update-ref -d` cleanup.

## Four-part landing verify (all 10 PRs — verbatim)

```
PR   state    mergeCommit   main-CI-on-merge-commit
673  MERGED   747e2207      completed success
675  MERGED   a4f6d5c5      completed success
676  MERGED   bf574a09      completed success
679  MERGED   93563cc1      completed success
680  MERGED   de6a3d68      completed success
683  MERGED   c7d8b09b      completed success
685  MERGED   57f48add      completed success
687  MERGED   161226b5      completed success
686  MERGED   fc27f759      completed success
```
(its#677 handed off then closed as completed-by-#674.)

## Gate (final worktree state, pre-merge of the last PRs)

- pytest: 4434 passed / 2 skipped / 49 deselected
- mypy: 0 errors / 462 source files
- ruff: clean
- main-branch CI on every merge commit: SUCCESS
