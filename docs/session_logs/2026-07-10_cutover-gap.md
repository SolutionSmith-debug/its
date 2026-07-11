---
type: session_log
date: 2026-07-10
status: active
related_prs: []
workstream: null
tags: [cutover, aug7_delivery, verify_cutover, gap_analysis, readiness]
---

# Cutover readiness gap — CL-01…CL-33 disposition + verify_cutover baseline (2026-07-10)

## Purpose

Drive the Aug-7 production cutover to readiness: establish the live gap against the §53 gate
(`scripts/verify_cutover.py`) and the v2 checklist (`docs/operations/cutover_checklist.md`), so the
remaining work is a punch-list, not a rediscovery. This is the spine the rest of the readiness work
(the verify_cutover PO/worker_base_url enrollment in this same PR, the repoint change-set artifact
`docs/operations/production_repoint_changeset.md`, and the operator punch-list
`docs/operations/cutover_operator_punchlist.md`) works against. **Nothing here executes the cutover.**

Baseline HEAD `a638fc5` (#523). Every claim below was re-verified against live HEAD (the repo's
verify-first mandate); the ROADMAP snapshot was already partly stale — see "Stale-claim corrections".

## Pre-flight findings — `verify_cutover --allow-sandbox` verbatim (dev box, pre-cutover)

Run from the LIVE `~/its` tree on `main` (so VC-07 reflects the real checkout), read-only:

```
$ python -m scripts.verify_cutover --allow-sandbox
== --allow-sandbox — mirror values permitted; NOT a production verdict ==
[PASS] VC-01 keychain — 15/15 required Keychain secrets present.
[FAIL] VC-02 launchd — launchd label set mismatch (1 missing, 0 orphan).
        not loaded: org.solutionsmith.its.po-send
[FAIL] VC-03 config — 2 of 10 load-bearing config rows failed.
        progress_reports.progress_send.scheduled_send_local [progress_reports]: row MISSING;
        progress_reports.progress_send.polling_enabled [progress_reports]: row MISSING
[PASS] VC-04 daemon-health — all 8 enabled daemon-health rows fresh (< 2x interval).
[PASS] VC-05 review-queue — ITS_Review_Queue reachable (271 pending row(s)).
[PASS] VC-06 alerting — Sentry DSN + Resend key present and shape-valid.
[FAIL] VC-07 git — git tree is not a clean origin/main checkout.
        working tree not clean
[PASS] VC-08 d1-migrations — remote D1 (its-safety-portal-db) has no pending migrations.
[PASS] VC-09 heartbeat-url — system.heartbeat_url configured (https).
verify_cutover: 6 passed, 3 failed, 0 skipped.  (exit 1)
```

### The 3 fails — each explained (all expected / benign pre-cutover, NONE a code defect)

| Check | Failure | Root cause | Class |
|---|---|---|---|
| **VC-02 launchd** | `org.solutionsmith.its.po-send` not loaded | PO send **ships DARK** (`po_send.polling_enabled=false`, seeded); its plist exists but is deliberately not loaded. A send-enable is a high-class External-Send-Gate decision (Seth) — not loaded ≠ broken. | Operator (send-scope) |
| **VC-03 config** | 2 rows MISSING: `progress_reports.progress_send.{scheduled_send_local, polling_enabled}` | The two progress-send config rows are not yet seeded in ITS_Config. `polling_enabled` is a **send-enable gate** (high-class). | Operator (seed; send-gate) |
| **VC-07 git** | working tree not clean | The **dev box** has untracked screenshots (`checklists-*.png`, `.playwright-mcp/`, etc.) from prior UI sessions. The **production host** checkout will be clean (CL-04); this is a dev-box artifact, not a production blocker. | Dev-box artifact |

The 6 passes (VC-01/04/05/06/08/09) are genuinely green today on the dev box. VC-01 (keychain 15/15)
and VC-08 (no pending remote migrations) reflect the dev box + mirror D1 — they re-run on the
production host at cutover (CL-02/CL-18/CL-30).

## CL-01…CL-33 disposition (three buckets, re-verified against live HEAD)

Executor legend: **DONE** (verified complete at HEAD) · **CC** (Claude-Code-executable code/doc — landable this program) · **OP** (operator/Seth: Smartsheet share, DNS, M365 app-reg, Cloudflare deploy/plan/WAF, Box OAuth, Keychain secret, human go/no-go) · **VER** (pure verification run).

| CL | Item | Disposition | Note / verify |
|---|---|---|---|
| CL-01 | Jul-31 host go/no-go recorded | OP (future gate) | `ls docs/session_logs/2026-07-31*` |
| CL-02 | Keychain 15 secrets on prod host | OP (secrets) | VC-01 PASS on dev; re-seed on prod host |
| CL-03 | 11 daemons on prod host only | OP (launchd) | VC-02; po-send dark (see above) |
| CL-04 | prod host clean origin/main | OP | VC-07; dev-box dirty is an artifact |
| CL-05 | prod Azure app-reg | OP (app-reg) | `smoke_test_graph.py` exit 0 |
| CL-06 | EXO Application Access Policy | OP (M365 PS) | `Test-ApplicationAccessPolicy` Granted/Denied |
| CL-07 | prod mailboxes exist | OP | Graph/EXO read per mailbox |
| CL-08 | DKIM/SPF for prod domain | OP (DNS) + VER | `dig TXT/CNAME` |
| CL-09 | portal prod DNS live | OP (DNS+deploy) + VER | `curl -sI https://<prod-portal>/` 200 + asset content-type |
| CL-10 | Resend sender domain verified | OP (Resend) | dashboard Verified |
| CL-11 | workspace approver shares (7 prod approvers) | OP (Smartsheet share) | see `production_repoint_changeset.md` §Approver model |
| CL-12 | ITS_Config production sweep | OP (sweep) — **staged** in `production_repoint_changeset.md` | VC-03 no `--allow-sandbox` PASS |
| CL-13 | gate-flip Description discipline | OP (judgment) | read each row's Description before flip |
| CL-14 | no sandbox residue in code/config | **DONE (as-designed)** | see below |
| CL-15 | Review-Queue Workstream picklist ⊇ live | **REGISTRY half DONE** (parity green) + OP (Smartsheet column) | `_WORKSTREAM_VALUES_GLOBAL` has progress_reports + po_materials |
| CL-16 | Box production identity | OP (Box OAuth) | `box_client…login` == its@evergreenrenewables.com |
| CL-17 | Box roots + routing reseeded | OP | dry get_folder per id |
| CL-18 | remote D1 schema current | OP (migrate) + VER | VC-08 PASS |
| CL-19 | D1 production hygiene | OP (read-only wrangler on prod) | `SELECT username,disabled FROM users` |
| CL-20 | real PM accounts | OP (CLI add-user) | CL-19 users query |
| CL-21 | Paid-plan-or-PBKDF2 | OP (plan decision) — PBKDF2 swap is a staged CC option **not built** | bcryptjs-only today; `curl POST /api/login` 200 |
| CL-22 | WAF /api/login rate-limit | OP (Cloudflare WAF) — rule + probe **staged** in punch-list | 12 rapid bogus POSTs → 429 tail |
| CL-23 | branch protection intact | **OP — GAP: main is UNPROTECTED today** | see below |
| CL-24 | daemon health | VER | VC-04 PASS |
| CL-25 | review queue reachable | VER | VC-05 PASS |
| CL-26 | alerting shape-valid | VER | VC-06 PASS |
| CL-27 | UptimeRobot heartbeat | OP (UptimeRobot) + VER | VC-09 PASS |
| CL-28 | fail-closed send smoke | OP (member/non-member approvals) | Send Status + ITS_Errors row |
| CL-29 | real-recipient wiring (Teala) | OP (ITS_Active_Jobs) | zero mirror/blank on active rows |
| CL-30 | full gate exit 0 on prod host | VER (+ CC pastes log) | `verify_cutover` no flags |
| CL-31 | sealed mirror-secret backup + mirror alive | OP | `curl -sI https://safety.evergreenmirror.com/` 200 |
| CL-32 | Day-7 routing gate armed | OP + CC (session-log/tech-debt) | alerts routed to Seth beyond Day 7 |
| CL-33 | Day-7 review (T+7) | OP (future) | dated session log |

### CL-14 detail (DONE, as-designed)
`grep -rn evergreenmirror --include='*.py'` hits are **all** fallback-default constants
(`DEFAULT_FROM_MAILBOX`, `DEFAULT_WORKER_BASE_URL`, etc.), seed migrations, and smoke scripts — the
runtime VALUES come from ITS_Config, which overrides at runtime (confirmed: code holds only
mirror-domain fallback defaults). No hardcoded runtime mirror value in a live path. The mirror
defaults are intentional fallbacks; the CL-12 ITS_Config sweep is the production repoint. ✅

### CL-23 detail (GAP — main branch is UNPROTECTED)
`gh api repos/SolutionSmith-debug/its/branches/main/protection` → **HTTP 404 "Branch not protected."**
CLAUDE.md's git-guardrails section already flags this as an open follow-up ("this defense belongs at
the GitHub branch-protection layer, server-side authoritative, to be verified separately"). CL-23
requires `test` + `portal` + `secrets` as required status checks on main. **Currently there is no
protection at all.** This is an operator/repo-admin action (a GitHub setting, not code) and is called
out at the top of the operator punch-list. Until it lands, direct pushes to main and behind-branch
merges are not blocked server-side.

## Code changes (this PR)
- `scripts/verify_cutover.py` + `tests/test_verify_cutover.py`: enrolled `po_materials.po_send.from_mailbox`
  (po_send landed, PR #500) + the two previously-unscanned `worker_base_url` copies (progress_reports +
  po_materials Workstream rows) into VC-03 CONFIG_ROWS, closing the mechanical gap the manual CL-14 grep
  used to backstop. Deliberately did NOT enroll `po_send.polling_enabled` (that would demand a send-enable
  at cutover — a high-class External-Send-Gate decision).
- `docs/operations/production_repoint_changeset.md` (Block 4): the staged ITS_Config production sweep
  (from→to mapping + verify command + approver-model note). NOT applied.
- `docs/operations/cutover_operator_punchlist.md` (Block 5): the operator-gated items ordered by calendar.

## Stale-claim corrections (verify-first mandate)
- **Block 2 "generalize compile_now_poll to iterate both workstreams" is STALE — already done.**
  `compile_now_poll.COMPILE_CONFIGS` already carries both `SAFETY_GENERATE_CONFIG` +
  `PROGRESS_GENERATE_CONFIG` (built cross-workstream per §14). No generalization work exists.
- **Block 2 picklist parity is already GREEN.** `_WORKSTREAM_VALUES_GLOBAL` + `review_queue.VALID_WORKSTREAMS`
  both already contain `progress_reports` + `po_materials`. Nothing to add.
- **po_send has LANDED** (PR #500, ships dark) — the verify_cutover PO enrollment is now actionable
  (done in this PR).
- Track-3 ROADMAP "`smartsheet.sheet_count_*` ABSENT" and Track-0 "remaining ② rows absent" were already
  confirmed present in earlier sessions — not re-touched.

## Out of scope / deferred (surfaced, not built)
- CL-21 PBKDF2 code swap — only needed if Workers Paid is unavailable (operator decision); speculative
  auth code not written. Approach recorded in the punch-list.
- `po_send_poll.py:77 DEFAULT_POLLING_ENABLED=True` diverges from HOUSE_REFLEXES §5 (dark-ship
  default-False). The seeded `false` row is load-bearing, so it's belt-and-suspenders; flagged as
  tech-debt, NOT landed autonomously (send-daemon surface).
- Live-clamd EICAR end-to-end smoke — ClamAV portal scanning is wired (`photo_screen.py`) + unit-tested
  (mocked); a live prove-it-bites smoke needs clamd on the host (Phase-A/A2). Flagged in the punch-list.

## Operator-side actions remaining
All OP-classed rows above, ordered by the master calendar, are enumerated in
`docs/operations/cutover_operator_punchlist.md`. The single highest-leverage NEW gap this pass surfaced
is **CL-23: enable branch protection on `main`** (currently absent).
