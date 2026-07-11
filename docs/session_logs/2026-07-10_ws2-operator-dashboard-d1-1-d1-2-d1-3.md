---
type: session_log
date: 2026-07-10
status: closed
workstream: null
related_prs: [516, 519, 523]
tags: [session_log, operator-dashboard, ws2, config-editor, secret-rotation, elevated-confirm,
  verify-first, adversarial-review, four-part-verify, fail-closed, ships-dark, ci-vs-local,
  brief-premise-corrected, ask-user-question, fastapi, htmx, section50, external-send-gate]
---

# Session — WS2 Operator Dashboard: D1-1 read-only core + D1-2 Class-A config editor + D1-3 sensitive tier (PRs #516, #519, #523)

Built the WS2 operator dashboard end to end across three stacked slices in one session — a
local-first FastAPI + uvicorn + Jinja2 app (`operator_dashboard/`, vendored htmx) that observes the
live `~/its` daemon tree read-only (D1-1), then grows a PIN-gated Class-A `ITS_Config` editor (D1-2,
the first mutation surface), then the sensitive tier: Class-B weighted edits + Class-C write-only
secret rotation behind an elevated-confirm ceremony (D1-3). Each slice: verify-first → build →
full gate → live smoke → adversarial-review → fix → four-part landing verify. All ship **dark**
(fail-closed until the operator provisions `ITS_OPERATOR_PIN`).

## Commits landed

Three squash-merges to `main`:

- **`fddbb5e` — feat(ws2): operator dashboard D1-1 — read-only observability core (#516).** 8
  fail-soft `DataSource` panels (launchd `launchctl list`, watchdog Check-C markers, circuit
  breaker, daemon heartbeats, state locks via a non-mutating `fcntl` probe, log tail, TTL-cached
  ITS_Errors + Review-Queue). Vendored htmx; every value redacted + autoescaped. Enrolled
  `operator_dashboard` in the F02 `WALKED_ROOTS`.
- **`663601a` — feat(ws2): operator dashboard D1-2 — Class-A runtime config editor (#519).** The
  first mutation surface: `GET /config` + `POST /act/config`. Operator PIN (Keychain
  `ITS_OPERATOR_PIN`, `hmac.compare_digest`, fail-closed) + Origin allowlist + brute-force lockout;
  fixed `(Setting, Workstream)` registry; per-key typed validators (the no-CI-checkpoint checkpoint);
  send-poller `→true` escalates; every write audits.
- **`a638fc5` — feat(ws2): operator dashboard D1-3 — sensitive tier (#523).** Elevated-confirm
  (re-PIN + typed target name); Class-B weighted edits (identity/trust/endpoint + the global brake
  `system.state` + `config_actuator`); Class-C secret rotation (Keychain write-through + `wrangler
  secret put` on stdin + Keychain-mirror dual-write; Box refresh token guided-only; never reads a
  secret back, never logs a value); Class-D read-only §50 pointer; Class-E read-only display. Plus
  a follow-up `120e95e` making two worker-rotate tests hermetic (CI env difference).

## CI runs

Four-part landing verify (state=MERGED · mergedAt · mergeCommit · main CI on the merge commit) —
all clean:

- **#516** → `fddbb5e`: PR CI green (test/portal/secrets/CodeQL); main-CI on the merge commit
  `test`+`secrets`+CodeQL+`update-pip-graph` SUCCESS (portal cancelled — no portal code touched).
- **#519** → `663601a`: PR CI green; main-CI on the merge commit all SUCCESS.
- **#523** → `a638fc5`: first PR CI run **FAILED** the `test` job (two D1-3 worker tests) — fixed
  (`120e95e`), re-run green; main-CI on the merge commit `test`/`portal`/`secrets`/CodeQL all SUCCESS.

## Decisions made during session

- **Verify-first corrected the brief three times on D1-3** — the highest-value moment of the
  session. The D1-3 brief was authored from a chat-side clone; the live source contradicted it on the
  most sensitive surface: (a) the §50 enqueue route (`POST /api/config/requests`) is **browser-session
  gated**, not bearer, and `portal_client` has no enqueue helper → the dashboard can't enqueue → **Class
  D became a read-only pointer to the §50 SPA, not a dashboard enqueue**; (b) Box refresh-token rotation
  is an **interactive browser OAuth grant** (`setup_box_oauth.py`, §44 Seth-only) → **Class-C Box is
  guided-only, never pasted**; (c) `safety_reports.authorized_approvers` (ITS_Config) is **vestigial** —
  the live F22 authority is the §46 workspace-share membership (`list_workspace_share_emails`) → moved it
  to **Class-E read-only** with a §46 note, not an editable control. The reflex "don't build against a
  misunderstood contract; flag the inconsistency, planning wins" earned its keep.
- **Escalated two genuine scope decisions to the operator (AskUserQuestion), didn't guess.** Because
  the brief's premises were contradicted on a secrets surface, I asked: (1) how active should the
  dashboard be in the §44-high-class ops — the operator chose **"also make the dashboard actively rotate
  secrets"** (live `set_secret` + `wrangler` from the dashboard, an explicit capability grant); (2) the
  send-poller `false→true` flip — the operator chose **self-apply after elevated-confirm + attestation**
  (not escalate-only). Both override the conservative doctrine default, which is the operator's
  prerogative as Developer-Operator; recorded here so the choice is auditable.
- **Observe the LIVE `~/its` tree, not the worktree.** All panel/editor paths resolve to `~/its/...`
  via the owning shared modules' constants (or `config.ITS_HOME`), so the dashboard reads the real
  daemons' state even while its code runs from a worktree/deploys later. The D1-1 review's watchdog
  finding hardened this: pin `import scripts.watchdog` to `ITS_HOME` on `sys.path` (CWD-independent +
  always the live tree's tracked-jobs/windows).
- **Two-process discipline preserved; ITS_Config writes are internal-SoR, not a send.** The editor
  writes only `ITS_Config` (Op Stds §51) and never imports `graph_client.send_mail`/`anthropic`/`resend`
  — the External Send Gate stays with the daemons. `external_send_gate` is Class-E, never editable on
  any route; `system.state` is editable (Class-B, elevated) because the kill switch is a fail-open
  operator convenience, not a security control.
- **Ceremony design.** Class-A = single PIN. Class-B/C = elevated-confirm (re-PIN + type the exact
  target name, constant-time). Chose typing the target's own name as the confirmation (a deliberate
  anti-fat-finger step) over a fixed phrase. After review, consolidated to **one shared PIN throttle**
  (the two ceremonies verify the same secret → one guess budget, not doubled).
- **Secret rotation is write-only by construction.** `secret_rotate.py` never imports/calls
  `get_secret`; a source-level test asserts it. Worker secrets go to `wrangler` on **stdin** (never
  argv), and the byte-equal Keychain mirror is dual-written from the same value; a Worker/mirror desync
  is durably audited (`config_secret_mirror_desync`). Registry-bounded — an unlisted key is refused.
- **Adversarial review ran per slice (multi-lens Workflow + per-finding verify) and repeatedly
  found real issues unit tests can't.** 15 real findings across 3 slices (D1-1: 3; D1-2: 7 all low;
  D1-3: 5, one medium). The D1-3 medium was an **authorization-parity** defect — `config_actuator`
  (a code-committing/deploying daemon, highest blast radius) could be dark→live activated with **no**
  attestation, a *lower* bar than a send-poller; fixed by making its `false→true` require the go-live
  attestation. Every review verified **zero** auth-bypass / secret-leak/readback / write-before-validate
  / self-deploy / Class-E-editable.
- **CI caught a local-green env difference (mandatory-live-smoke class).** Two D1-3 worker-rotate
  tests depended on `~/its/safety_portal` existing (the `_rotate_worker` cwd guard) — true on the Mac,
  **absent in CI** (Linux runner home), so `_rotate_worker` returned "error" before the mocked wrangler.
  Fixed by monkeypatching `secret_rotate._SAFETY_PORTAL` to a real `tmp_path` (hermetic). Local passing
  proved nothing about CI.

## Open items handed off

For the operator (Seth) — all §44 high-class, none doable autonomously:

1. **Activate the ACT surface** (D1-2 + D1-3 are fail-closed dark until then):
   `security add-generic-password -a "$USER" -s ITS_OPERATOR_PIN -w` (a STRONG PIN, not 4-digit) +
   `export ITS_DASH_ALLOWED_ORIGINS="https://<host>.<tailnet>.ts.net"`.
2. **DoD acceptance smokes** (need the PIN + a live write): D1-2 toggle
   `safety_reports.intake.box_filing_enabled` off→on and confirm the daemon + `config_audit` row;
   D1-3 rotate a low-stakes Keychain entry end-to-end (confirm the audit carries no value) + one
   Class-B elevated edit. Steps in `docs/runbooks/operator_dashboard_config_editor.md` +
   `operator_dashboard_sensitive_tier.md` (incl. the Box quiesce→`setup_box_oauth`→smoke flow).
3. **Clean up 3 leftover worktrees:** `git worktree remove ~/its-ws2-dash ~/its-ws2-d1-2 ~/its-ws2-d1-3`
   (force-delete is hook-blocked inside CC).
4. **D1-3b (the remaining piece):** the launchd plist install (make the dashboard a daemon) +
   interval-key edits (install-time, need the launchctl reinstall action). D2 stays the Aug-7
   critical path; the dashboard is the fast-follow.

## What was NOT touched

- **No `shared/` change** — every slice is additive (new `operator_dashboard/` package + tests +
  pyproject deps + F02 allowlist entries). Send-engine code untouched.
- **No launchd plist installed / loaded** (D1-3b), and no daemon quiesced — per the worktree
  discipline and the brief.
- **No live secret rotated, no live config written, no PIN provisioned** — the ACT surface ships
  dark; the acceptance smokes are operator-run.
- **No §50 enqueue built into the dashboard** — deliberately a read-only pointer to the SPA (the
  enqueue is session-authed and the SPA owns it).
- **The D2 send lane and the config-editor SPA** — not this session.

## Lessons worth folding into memory (next session-close pass)

Captured to `project_ws2-operator-dashboard.md` (+ MEMORY.md index) this session:

- The whole WS2 dashboard shape (all 3 slices, routes, the elevated ceremony, secret-rotation
  write-only/registry-bound/Box-guided model, the §50-enqueue-is-the-SPA's-job correction, ships-dark).
- **Verify-first on a chat-authored brief repeatedly out-values the brief** — three premise
  corrections on D1-3 alone. When a security-surface brief's premises are contradicted by source,
  escalate the scope decision (AskUserQuestion), don't guess.
- **CI-vs-local for filesystem-dependent tests** — a test that leans on a real path present on the
  dev Mac but absent in CI passes locally and fails in CI; monkeypatch the path constant to a tmp dir.
