---
type: session_log
date: 2026-07-09
status: closed
workstream: field_ops
related_prs: [487, 488, 489]
tags: [session_log, field-ops, progress-reporting, section54, secret-leak-backstop, redact, error-log, heartbeat-parity, doctrine-manifest, v20, materials-activation, incidents-activation, gitignore-swallowed-test, prove-the-control-bites, adversarial-review, four-part-verify, config-observability, tech-debt-reconciliation]
---

# Session — field-ops mirror-suite full activation + items 1–4 close-out batch + §54 secret-leak backstop

Continuation of the 2026-07-06 M3 arc (that build/deploy is logged in
`2026-07-06_m3-slice2-material-incidents-build-deploy-activate.md`). This span **activated the full
field-ops → Smartsheet mirror suite**, then worked a four-item close-out batch (three PRs), the
headline of which — the **§54 secret/PII-leak backstop** — was almost shipped with a phantom test that
an adversarial review caught.

## Commits landed (all four-part verify CLEAN)

- **#487** `2d544c8` — v20 doctrine tech_debt reconcile (§51 Material-List one-way-up + §23/§24
  seventh-workspace, both verified folded into Op Stds **v20**; M6 `intake.py` FM-v8→v11 docstring;
  #370 "missing index row" shown moot) + **`tests/test_heartbeat_parity.py`** (its#338 — discovery-AST
  §14 cross-daemon parity guard: every daemon's `_write_heartbeat`/`_write_heartbeat_row` must stay a
  thin 1:1 forwarder to the shared `HeartbeatReporter`; prove-it-bites verified).
- **#488** `56fc659` — its#231 "Same as stakeholder" routing copy button (SPA + test); the chain is
  Stakeholder → Safety → Progress.
- **#489** `5a948fd` — **its#340 / §54 runtime secret/PII-leak backstop.** `shared/redact.py` +
  wired into the `error_log` triple-fire; `tests/test_error_log_redaction_backstop.py`;
  `doctrine_manifest.yaml` §54 `dated_exception → enforced`.

## CI runs

Four-part verify CLEAN on all three (state=MERGED · mergedAt non-null · mergeCommit present ·
main-branch CI on the merge commit = SUCCESS): #487 `2d544c8`, #488 `56fc659`, #489 `5a948fd`
(ci + CodeQL both green on the merge commit — the transient CodeQL infra-fail that showed `unstable`
pre-merge cleared; 0 code-scanning alerts on `redact.py`/`error_log.py`).

## Activations (Smartsheet ITS_Config — not PRs)

- **`incidents_enabled → true`** — the M3 ledger pass went live; daemon cycle verified
  `incidents upserted=0 reviewed=0 errors=0` (0 = no filed incidents on active sandbox jobs yet;
  errors=0 = the daemon→Worker→Smartsheet path is healthy).
- **`materials_enabled → true`** — the P7-M2 Material List one-way-up mirror went live
  (`materials upserted=1 errors=0`). With hours + equipment already live, the **field-ops → Smartsheet
  mirror suite is now fully active** (all four standing trackers).
- **Config-observability seeded** — `smartsheet.sheet_count_ceiling`=1500/`margin`=50 (`global`, closes
  M-1 / forensic-#7) + four `progress_reports.*.row_cap_warn_threshold`=15000 rows (cleared the #336
  NO-ROW startup WARNs).

## Decisions made during session

- **Materials activation: reverted, then re-flipped — the doctrine came first.** A `materials_enabled`
  flip was **reverted** the moment the `update_rows` response revealed the row's own in-cell guardrail
  ("Do NOT set true until a Seth-ratified §51 rider is merged") — a doctrine-divergent gate flip is a
  §44 high-capability action, not autonomous. Then a "what's-left" survey **verified against live v20
  doctrine** that the §51 one-way-up rider *was already folded into Op Stds v20* (`operational-standards.md`
  §51 ~L859, changelog + §23 seventh-workspace ~L168/L174) — so the guardrail's precondition was met and
  the cell was stale. Re-flipped, verified live. (→ HOUSE_REFLEXES §5 gate-flip reflex, banked in #486.)
- **The §54 backstop is production code, not a test.** its#340 read as "add a test," but `error_log`
  performed **zero redaction** — a secret in a traceback egressed verbatim to all three CRITICAL surfaces
  (a real key-in-a-traceback once forced a rotation). Escalated rather than fake-passed: built
  `shared/redact.py` (regex backstop, honest "backstop-not-guarantee" framing) and wired it at two choke
  points — `_smartsheet_log` (ITS_Errors cells) + the top of `_alert_critical` (covers the Resend
  subject/body AND the Sentry-leg args). **Design call: egress-only** — the on-Mac local log file is left
  RAW (§54 scopes its guarantee to the "triple-fire"; the local file is full-fidelity forensics behind
  Tailscale, where an operator diagnoses + rotates). Operator merged egress-only; local-file redaction is
  a one-line change if ever wanted.
- **progress-% removal DEFERRED (documented ready-spec).** It touches the worker `/api/fieldops/job`
  CREATE-route INSERT (a trust boundary) + a destructive `ALTER TABLE jobs DROP COLUMN progress`
  migration — low-value dead-code removal on a `NOT NULL DEFAULT 0` dormant column, not worth an
  autonomous trust-boundary edit; parked with the exact surfaces in `docs/tech_debt.md` for a supervised
  worker-reviewed PR.
- **Batch shape: three PRs by concern** (pure-doc/test → SPA → the error_log backstop), risk-graded:
  #487/#488 auto-merged; **#489 held for operator merge** given `error_log`'s blast radius.

## Open items handed off

- **#489 `error_log` local-file redaction** — merged egress-only (§54-literal); one-line change to also
  redact the local file if desired.
- **progress-% cleanup** — ready-spec parked in tech_debt (operator-reviewed; worker INSERT + destructive
  column-drop).
- **`progress@` mailbox (its#460)** — still the one external blocker for progress *sends* (held-safe until
  the mailbox exists; Exchange-admin op, not a terminal command).
- **`redact()` coverage** is a conservative backstop — add a provider shape when a new Keychain secret
  enters (this session already folded in `github_pat_`/`re_`/Sentry-DSN per the review).

## What was NOT touched

- Materials/incidents ship **one-way-up** only — no down-sync (M2b bidirectional receive stays deferred
  per v20).
- No blanket-activation of the remaining dark features (checklist→progress #480, recurring #476) — those
  stay the operator's flip.
- The destructive `jobs.progress` column-drop migration — deferred, not executed.

## Lessons captured to memory

- **`.gitignore` silently swallowed a `*secret*`-named test — a green CI on a missing test proves
  nothing.** `tests/test_secret_leak_backstop.py` matched `.gitignore:40 *_secret*` (a rule meant to
  block committing *credential dumps*), so `git add -A` dropped it, `git status` reported clean, and CI
  ran green because the §54 test **wasn't collected** — the enforcement shipped absent while
  `doctrine_manifest.yaml` claimed `enforced`. Caught by the `ops-stds-enforcer` adversarial review (the
  DoD gate that unit tests structurally cannot replace). Fix: renamed to
  `test_error_log_redaction_backstop.py` + verified `git ls-files`-tracked before re-asserting. → banked
  in HOUSE_REFLEXES §2 (this PR). Reinforces [[feedback_prove-the-control-bites]].
- **`git checkout <file>` on an UNCOMMITTED file reverts ALL its uncommitted edits to HEAD**, not just a
  targeted mutation — a prove-it-bites step wiped the error_log wiring. Use a `cp` backup + restore for
  transient inject-confirm-revert on uncommitted files.
- **Stale-claim reconciliation earns its keep:** of the batch's "still-open" items, several were already
  resolved (§51/§23 in v20, time-entry picker in #403, #462 in #465) — verified against live code/doctrine
  before editing, marked resolved not rebuilt. Reinforces [[feedback_multi-surface-fan-out]] /
  trust-the-live-code.
