---
type: session_log
date: 2026-07-22
status: closed
workstream: infrastructure
related_prs: [649, 650, 651]
tags: [migrations, cutover, box, smartsheet, watchdog, log-rotation, sustained_failure, observability]
---

# 2026-07-21 → 2026-07-22 — Phase-1 pre-builder-family gap migrations + the two-stage `~/its/logs` growth fix (Check W)

## Purpose

Two operator briefs, worked back-to-back: (1) author the four operator-run Smartsheet/Box
provisioning builders missing from the FLIP-precedes-SEED cutover family (three Smartsheet
surfaces + two Box roots predate the family and had no builder), and (2) stop `~/its/logs`
growth (265 MB, ~8 MB/day, 26× in 9 weeks) at the source, then cap what remains with a new
watchdog check. Neither session ran a live create/mutate against the production tenant —
Brief 1 is authoring-only (operator runs the builders by hand at cutover); Brief 2 touches only
the Mac's own log directory.

## Pre-flight findings

- **Brief 1 — two stale-brief facts corrected against the live tenant.** The brief named the
  Safety-Portal folder `Safety Portal`; the live folder is `00_Safety Portal`, and a *separate*
  `00_Form Catalog` folder holds `ITS_Forms_Catalog`. An exact-name find on the brief's name
  would have created a duplicate folder. Separately, **five** sheets named `ITS_Errors` coexist
  in `02 — Logs` — find-first-match is not guaranteed to be the live one, so every find had to
  enumerate and WARN on all matches rather than silently pick one.
- **Brief 2 — the measured drivers, not assumed ones.** Two sources accounted for ~91% of daily
  volume: `shared/required_config.py`'s one-INFO-line-per-resolved-key success path (44.6%,
  driven by one-shot-per-`StartInterval` daemons re-running their config pass every cycle) and
  `shared/error_log.py`'s unconditional stdout echo duplicating the daily file verbatim into
  launchd's `.out.log` (46%, ~0% unique content). Disk headroom was not the problem (1.1 TiB
  free) — corpus legibility and unbounded growth were, so the fix targets sources before adding
  a pruner.

## Code changes

### Brief 1 — PR #649 (`f7fc716`)

Four new operator-run builders in `scripts/migrations/`:

- `build_system_workspace.py` — `ITS — System` workspace + 4 folders (Config/Logs/Queues/Daemons).
- `build_safety_portal_workspace.py` — `ITS –– Safety Portal` workspace + its folders (corrected
  per the pre-flight finding above).
- `build_system_sheets.py` — the 5 System sheets (Config/Errors/Quarantine/Review_Queue/
  Daemon_Health).
- `build_box_roots.py` — the 2 Box mirror-tree roots.

All four are create-only (GET + create-POST only; no mutate/rename/re-parent/re-share/delete),
exact-name adopt-don't-touch, scoped to the minimal provisioning set, idempotent no-op on
re-run, and y/N-confirm-gated before the first create — no secrets ever printed. A **unified
fail-closed adoption policy** now spans all 3 Smartsheet builders (a wrong-plan sandbox
workspace shared into the production identity fails closed on `accessLevel != OWNER` or an
absent `accessLevel`, with no FLIP-BLOCK id leaked into output; an ambiguous PARENT container —
more than one name match — fails closed, and only a *terminal* leaf may adopt-first-and-warn).
`build_box_roots.py` surfaces the authenticated Box login and WARNs on a mismatch against
`EXPECTED_BOX_LOGIN`, naming the account in the y/N prompt itself — Box has no owner-of-root
discriminator, so the human confirmation is the control.

Registry reconciliation landed in the same PR (HOUSE_REFLEXES §1): `verify_cutover.py` VC-03
enrolled the 2 Box `portal_root_folder_id` rows (`non_empty`, no `sandbox_scan` — numeric folder
ids carry no mirror marker); `shared/sheet_ids.py` gained `FOLDER_FORM_CATALOG` and a corrected
`FOLDER_SAFETY_PORTAL` comment; `docs/tech_debt.md` CO-3 amended (presence is now enrollable) and
a new CO-4 opened, flagging the stale `build_its_active_jobs_sheet.py` /
`build_its_forms_catalog_sheet.py` pair as pre-dating the current provisioning model; and
`scripts/migrations/README.md`'s cutover sequence updated to include the four new builders.

**CI note:** a hardcoded `its@evergreenrenewables.com` string tripped the `secrets` job's
production-identity guard on the first push. Fixed by composing the login from bare
`localpart`/`domain` constants at runtime instead of a literal full address.

### Brief 2, Stage 1 — PR #650 (`23888a0`)

`shared/required_config.py`: `resolve_and_log`'s success path now emits **one INFO summary line
per pass**, naming every resolved key with its value and source
(`config resolved N key(s): setting[workstream]=value(source); ...`), replacing one INFO line
*per key*. This satisfies HOUSE_REFLEXES §5's "log each resolved setting with its source"
without the per-cycle multiplication that made the per-key form 44.6% of the corpus. Unchanged:
the per-key `config_row_missing` WARN (stays individually actionable), the already-summarized
`config_read_error` transient WARN, the returned dict, and fail-open semantics.

`shared/error_log.py`: `_local_log`'s `print(line)` is now gated to `severity is not
Severity.INFO` — WARN/ERROR/CRITICAL still echo to stdout (crash/outage visibility preserved),
INFO no longer duplicates into the launchd `.out.log`. The daily `<date>.log` file write stays
unconditional — the complete per-occurrence record is untouched; only the redundant stdout
mirror is trimmed. The two below-INFO `local_log` callers (`smartsheet_client`,
`circuit_breaker`) both pass `Severity.WARN`, so outage-speech survives the gate. Measured
expected effect: −70% of daily log volume.

### Brief 2, Stage 2 — PR #651 (`5c744fa`)

New watchdog **Check W** (`_check_log_dir_rotation`, registered last in `CHECKS`) backed by a new
`shared/log_rotation.py` engine. **v1 never deletes** — archive only; the delete stage is
deliberately deferred pending an off-host copy (a FIXED high-class decision, not taken this
session). Behavior: `logs/<date>.log` older than 14 **local** days gzips in place (verified `.gz`
round-trip before the original is removed); `logs/launchd/<daemon>.out.log` is copied to a
verified `.gz` sibling then truncated **in place** via `os.truncate(path, 0)` — the inode is
preserved because the KeepAlive dashboard's open file descriptor follows the inode, and no
SIGHUP handler exists to make a rename/unlink safe; `logs/launchd/<daemon>.err.log` is never
touched (29–68% unique content — the incident file).

Three inode traps were identified, injection-tested, and re-confirmed after a fix refactor:
(1) never unlink/rename/inode-replace a launchd path — the only op on a launchd file is
streamed-read + in-place truncate; (2) no lsof/fd branch — the "fd held between fires" premise is
a TOCTOU race, so truncation is unconditional; (3) the cutoff uses local `date.today()`, since
`error_log` names the daily file with naive `datetime.now()` — the current-local-date file is
never selected for rotation.

Operational-safety properties: escalation uses the **capped**
`sustained_failure.is_escalation_cycle` ladder (not `has_crossed_threshold`, which would mint one
unrotatable open-CRITICAL per day forever) and is MAINTENANCE-aware; a per-file 1 GiB size cap
skips oversized logs rather than reading them into RAM, paired with streamed 1 MiB-chunk gzip and
a monotonic per-run deadline so the check cannot hang the 07:00 run and starve the F16
UptimeRobot heartbeat; an open-CRITICAL lane-hold (reusing Check B's `_open_critical_rows`)
fail-closes the whole check during an active incident; and row hygiene keeps routine prunes at
INFO (no `ITS_Errors` row — a daily-truncating check must not flood the 20k-row-capped sheet)
while abnormal conditions write an explicit WARN + never-silent `log_dir_rotation` row outside
the CheckResult (so a MAINTENANCE downgrade can't erase it).

Full registry fan-out in the same PR: `CHECKS` list + an exact-list test;
`docs/troubleshooting/tree.yaml` gained a `seth_coresolve` node, the guide was regenerated, and
`docs/enablement/manifest.yaml`'s sha256 was re-recorded; `docs/runbooks/log_dir_rotation.md`
(§43 successor-remediation entry) shipped; `shared/defaults.py` gained the rotation constants;
`.gitignore` narrowly added `logs/**/*.gz`; `tests/test_transient_fence.py`'s LADDER_CONSUMERS
enrolled the new escalation; CLAUDE.md's stubbed-vs-real table gained a row; and the conftest
`_LIVE_STATE_COUNTERS` fixture was updated.

## Ratified deviation from a pinned brief decision (Seth accepted 2026-07-22)

The brief pinned a per-file incident guard — "skip any launchd file whose `st_mtime` is within N
minutes." It was **dropped**. `portal_poll.out.log` (36 MB, the largest `.out.log`) writes every
60s, so its mtime is *always* recent — the guard would never fire, the check would run green
every day while silently never truncating that file, which is exactly the ITS anti-pattern
("never silent" failing silently). The danger of dropping the guard is minor and recoverable:
copy-gz-truncate archives to a verified `.gz` before truncating, `tail -f` survives a truncate,
and the open-CRITICAL lane-hold already covers the real incident case (don't touch logs mid-page).
Option B — restoring the skip with a ~5 MB size-ceiling override so it only protects small,
slow-writing files — is recorded in tech-debt as the future path if the courtesy is ever wanted.

**Operator action item:** run Check W once by hand (`--dry` then one real run, eyeball the
resulting `.gz`) before trusting the 07:00 cron to run it unattended for the first time.

## Verification

**PR #649**
- pytest: 4222 passed / 49 deselected / 0 failed
- mypy: Success (450 source files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

**PR #650**
- pytest: 4140 passed / 49 deselected / 0 failed
- mypy: 445 source files clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

**PR #651**
- pytest: 4287 passed / 49 deselected / 0 failed
- mypy: 452 source files clean
- ruff: clean
- main-branch CI on merge commit: SUCCESS

**Landing verification (four-part, quoted verbatim):**

```
PR #649 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-22T00:08:05Z
- mergeCommit: f7fc71666436ea7f7e0fb012e03d76e9d9e07870
- main CI on merge commit: SUCCESS (test: success, portal: success, secrets: success,
  Analyze (python)/(javascript-typescript)/(actions): success)

PR #650 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-22T00:26:08Z
- mergeCommit: 23888a0ac5423082df6ca2858bb57198b6c9047d
- main CI on merge commit: SUCCESS (test: success, portal: success, secrets: success,
  Analyze (python)/(javascript-typescript)/(actions): success)

PR #651 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-22T15:52:39Z
- mergeCommit: 5c744fa075e4dcd62610ecc5cad610cb6c1cb3a5
- main CI on merge commit: SUCCESS (test: success, portal: success, secrets: success,
  Analyze (python)/(javascript-typescript)/(actions): success)
```

## Live smoke

Neither Brief 1 nor Brief 2 exercised a live tenant this session. Brief 1's builders are
authoring-only per their own blast-radius design — CC never runs a live create against the
production Smartsheet plan or Box; that run is reserved for the operator at cutover. Brief 2's
Check W has not yet had its first real (non-`--dry`) run — see the operator action item above.

## Decisions made during session

- **Unified the 3 Smartsheet builders' adoption policy after an adversarial review found the
  first cut diverged** — rather than ship three builders with three slightly different
  fail-open/fail-closed postures on ambiguous-parent and wrong-accessLevel cases, all three were
  brought to one fail-closed standard and the fixes re-proven by injection before merge.
  Alternative considered: ship the divergence and note it in tech-debt — rejected because these
  builders run once, unsupervised except for a y/N prompt, against the production tenant; a
  policy gap there is not a "fix later" risk class.
- **Corrected the brief's folder/sheet names against the live tenant instead of trusting the
  brief.** An exact-name find against `Safety Portal` (brief's name) vs. `00_Safety Portal`
  (live name) would have silently created a duplicate folder on first run — caught by verifying
  against live HEAD/live tenant state before coding the adopt logic (HOUSE_REFLEXES §1).
- **Dropped the brief's per-file mtime-skip incident guard in Check W** — see the "Ratified
  deviation" section above. Reasoned to Seth rather than silently omitted, and accepted
  2026-07-22.
- **Chose archive-only for Check W v1, no delete stage.** Deleting log data is a FIXED
  high-capability-class action under the operator/successor model's own doctrine (irreversible +
  no off-host copy exists yet); a delete stage is explicitly parked, not built speculatively.
- **Fixed the sources before adding a pruner (Stage 1 before Stage 2), rather than shipping Check
  W first.** The two stages were kept as two PRs so each carried its own clean four-part verify
  and so the pruner's effect could be measured against an already-reduced baseline rather than
  needing to disentangle both changes' contributions after the fact.

## Open items handed off

- **Operator: run Check W once by hand before the first unattended 07:00 cron fire** — `--dry`
  then a real run, eyeball the produced `.gz`.
- **Check W's delete stage remains explicitly deferred** pending an off-host copy of archived
  logs — no timeline set; a FIXED high-class decision when it comes up.
- **CO-4 (tech-debt, opened by #649)** — the pre-2026-06-05-stale `build_its_active_jobs_sheet.py`
  and `build_its_forms_catalog_sheet.py` target a superseded provisioning model; flagged HIGH,
  not resolved this session.
- **Option B for the dropped mtime-skip guard** (a ~5 MB size-ceiling override) is recorded in
  tech-debt as the reinstatement path if the incident-guard courtesy is wanted later.

## What was NOT touched

- No live Smartsheet or Box create/mutate was run against the production tenant — Brief 1 shipped
  authoring-only; the operator runs the builders at actual cutover.
- No delete path was added to `shared/log_rotation.py` — v1 is archive-only by design.
- No doctrine, mission, or ADR file was edited — both briefs were code/tests/docs-corpus work
  within the execution repo.
- `logs/launchd/<daemon>.err.log` files are never touched by Check W — deliberately excluded as
  the incident file (29–68% unique content).

## Lessons captured to memory

- Brief 1's live-tenant name corrections (`00_Safety Portal` / `00_Form Catalog` / five coexisting
  `ITS_Errors` sheets) are another instance of HOUSE_REFLEXES §1 — a chat brief naming a specific
  Smartsheet folder/sheet is a hypothesis until verified against the live tenant, not a fact.
- The Check W mtime-skip deviation is a small case study for "prove the control bites" applied in
  reverse: a guard that *looks* protective (skip recently-written files) can make a check silently
  useless against exactly the highest-volume file it exists to control (`portal_poll.out.log`,
  written every 60s) — worth checking a proposed guard against the worst-case target file's write
  cadence before accepting it, not just against the happy-path file.
- Cross-references: `docs/HOUSE_REFLEXES.md` §1 (trust the live code, never the claim) and §5
  (observable config resolution — the Stage-1 summary line is a direct instance);
  `docs/tech_debt.md` CO-3/CO-4 and the Option-B mtime-skip entry; `docs/runbooks/log_dir_rotation.md`
  (§43 successor-remediation entry for Check W); `docs/session_logs/2026-07-21_dashboard-rep-config-read-fix-hermetic-tests-coverage-gap-hunt.md`
  (the immediately preceding session, whose "auth storm" misdiagnosis is part of why log
  legibility mattered enough to fund this session).
