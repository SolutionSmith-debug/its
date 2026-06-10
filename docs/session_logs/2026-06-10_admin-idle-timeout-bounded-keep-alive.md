---
type: session_log
date: 2026-06-10
status: closed
related_prs: [258]
workstream: safety_portal
tags: [safety-portal, idle-timeout, keep-alive, session-security, bounded-session, dirty-editor, form-editor, typescript, spa, cloudflare-worker, live-deploy, mirror-deploy, adversarial-review]
---

# Session log — Admin idle timeout widened to 30 min + bounded dirty-editor keep-alive (PR #258)

Single PR. Operator brief asked to widen the admin idle-logout window from 5 to 30 minutes
and to make an open dirty form-editor count as "active" so unsaved work in a backgrounded tab
is not lost to timeout. An adversarial review (3-lens, skeptic-verified) during development
surfaced an unbounded-session risk in the naive keep-alive design, leading to a deliberate
bounded implementation that deviates from the brief's literal instruction. All changes are
TypeScript/SPA only (`safety_portal/`); no Python-side code modified.

## PRs landed

### PR #258 — feat(safety-portal): widen admin idle timeout to 30 min + bounded dirty-editor keep-alive (`23c04e6`)

Three interrelated changes (A/B/C) shipped together because B depends on the new `IDLE_MS`
constant from A and C is copy-only.

**A — Widened the idle window 5→30 min.**

Single source of truth: `ADMIN_IDLE_S = 1800` in `worker/index.ts`; `IDLE_MS = 30 * 60 * 1000`
in `src/lib/useIdleLogout.ts`. Both values cascade to the login cookie (`worker/index.ts:217`)
and the slide cookie (`:296`). `test/admin-idle.test.ts` `max-age` assertions updated 300→1800.

**B — Bounded dirty-editor keep-alive.**

`useIdleLogout(onIdle, paused=false)` gained a `paused` boolean arg. When `paused` is true:

- A 240-second interval fires `GET /api/session` (unauthenticated → ignore;
  authenticated → the response slides the server-side session cookie the same way any
  real user action would).
- Proactive `onIdle` still fires after 30 minutes of no real input — the keep-alive
  slides only inside the idle window; it does not disable or reset the idle clock.

`AdminApp.tsx` threads an `editing` boolean flag. `FormsPage.tsx` sets `editing=true`
when a dirty draft is open (tracked via `editorState !== null && draftDirty`); an
unmount-reset effect ensures `editing` is cleared to false when the component unmounts
(tab-switch cannot pin `editing=true` permanently). `AccountsPage.tsx` does the same for
an open login-editor state.

`FormsPage.editing` and `AccountsPage.editing` each have a corresponding
unmount-reset effect. The `editing=true` case in both pages is guarded by the component
being mounted — a background tab that has unmounted will not hold the flag.

New test file `src/pages/__tests__/FormsPage.editing.test.tsx` verifies the unmount-reset
explicitly (the test suite fails if the `useEffect` cleanup is removed). New file
`src/lib/__tests__/useIdleLogout.test.ts` covers 6 cases: paused=false fires onIdle on
timeout; paused=true does not fire onIdle but keeps-alive; paused→unpaused fires onIdle
normally; keep-alive interval is 240s.

**C — Stale copy fixes.**

"5-minute" / "5 min" references updated across `worker/index.ts`, `src/lib/useIdleLogout.ts`,
`src/pages/FormsPage.tsx`, `src/pages/AccountsPage.tsx`, and `README.md` (including the
user-facing publish-401 "session expired" message and README:282 operator note).

- pytest: 1686 passed / 2 skipped / 0 deselected
- mypy: 0 errors / 198 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS (run 27279541910, workflow: ci)
- portal JS suite: worker-pool vitest 112 passed (8 test files); SPA jsdom vitest 45 passed (9 test files)

PR #258 — four-part verify clean

---

## Live deploy

`npm run deploy` to the live mirror `safety.evergreenmirror.com`. Worker version **276322a3**
deployed 2026-06-10. Post-deploy smoke: SPA index returned `200` with enforcing CSP; `GET
/api/session` unauthenticated returned `401 unauthenticated`. Cookie `max-age` value visible
in the Set-Cookie header on login now reads `1800`.

Post-deploy note: admins holding a 300s-maxAge cookie from before the deploy will upgrade to
the 1800s cookie on their next authenticated request (worst case one re-login required; no
forced session invalidation).

---

## Decisions made during session

1. **Bounded keep-alive, not unbounded (operator decision after adversarial review).**
   - Decision: proactive `onIdle` fires in BOTH modes (paused and unpaused) after 30
     minutes of no real input. The keep-alive slides the server cookie only within the idle
     window; it does not disable or reset the idle clock. An abandoned dirty-editor tab dies
     at approximately 30 minutes; an actively-editing tab is never bounced mid-edit.
   - Alternative considered (brief's literal instruction): suppress `onIdle` entirely while
     `paused=true` (i.e., never fire proactive logout when a dirty editor is open). This
     would have left the session alive indefinitely as long as the editor tab remained open
     with unsaved content — no absolute lifetime.
   - Rationale: a 3-lens adversarial review (role: attacker, role: auditor, role: skeptic)
     found that suppressing `onIdle` while paused creates an unbounded admin session. The
     `SessionClaims` JWT carries no `exp` / absolute-lifetime field; `iat` is re-stamped on
     every slide response, so the 90-day `MAX_AGE` is pushed forward by every keep-alive
     ping. A dirty-editor tab backgrounded on an unattended workstation would keep the admin
     session alive forever — the idle timeout's core intent for unattended workstations is
     defeated. The adversarial reviewer (skeptic) verified this at medium confidence. The
     operator accepted the finding and chose the bounded design. The `draftCache` (PR #250)
     preserves work across the proactive logout, so the 30-minute bound does not lose
     unsaved content.
   - Clause: FM v11 Invariant 2 (adversarial posture on operator-session security); Op Stds
     v18 §1 (External Send Gate — admin session is the gate; its lifetime is load-bearing).

2. **Unmount-reset test gap closed before merge.**
   - Decision: add `src/pages/__tests__/FormsPage.editing.test.tsx` (unmount-reset
     regression test) and `src/lib/__tests__/useIdleLogout.test.ts` (6 keep-alive cases)
     before landing.
   - Alternative considered: ship without the tests; the unmount-reset is a trivial
     three-line `useEffect` cleanup.
   - Rationale: the adversarial review flagged that removing the unmount-reset cleanup left
     the existing suite entirely green — the failure mode (paused=true stuck forever) would
     not be caught by any test. The unmount-reset is the guard for a persistent `editing=true`
     pin on a background tab, which is the specific attack surface the bounded design is
     defending. A load-bearing invariant with zero test coverage is the definition of a gap
     to close before merge.

3. **Single PR (A+B+C), not three separate PRs.**
   - Decision: ship the window change, the keep-alive logic, and the copy sweep in one PR.
   - Alternative considered: three PRs in sequence (A first to widen the window; B after;
     C incidentally with B).
   - Rationale: B's `IDLE_MS` references the same constant established by A; landing A
     independently would leave a 30-min window with the old keep-alive behavior (none) for
     the brief window between merges. C is mechanical copy-only with no logic; separating it
     adds no review value. The three parts are tightly coupled by the constant and the
     security story — reviewability was higher as a unit.

---

## Open items / next session

- **Existing-admin cookie upgrade:** admins with a 300s-maxAge cookie will hold it until
  their next authenticated request. No operator action required; noted for awareness if a
  session question arises in the next 5 minutes post-deploy.
- **`SessionClaims` absolute lifetime:** the bounded keep-alive holds the admin session
  at most 30 minutes past last real input. A formal `exp` / absolute-lifetime field in
  `SessionClaims` would make the lifetime guarantee structural rather than behavioral.
  Deferred; current design is acceptable for mirror phase.
- **CSP enforce flip** (carried): still held pending a live signature-capture smoke + zero
  console-violation confirm.
- **Load the compile-now daemon** (carried): watchdog Check C WARNs on the
  `safety_compile_now_poll` marker until loaded.
- **Untracked artifacts in `~/its`** (carried): `forms-tab-stuck.png`,
  `incident-report-draft.png`, `jha-filled-before-submit.jpeg`, `portal-tour/`,
  `.playwright-mcp/` — throwaway Playwright artifacts; operator to delete.
- **Stale worktrees** (carried): `~/its-*` from prior sessions; force-delete is
  hook-blocked in CC; operator to clean manually.

---

## What was NOT touched

- **`~/its-blueprint`:** exec-repo-only session. No doctrine, mission, brief, or reference
  files modified.
- **Invariant 1 (External Send Gate):** no generation or send scripts modified. The admin
  session is the gate controlling publish approvals; tightening its lifetime bounds (not
  widening) its security surface.
- **Invariant 2 (Adversarial Input Handling):** no external-content processing paths
  modified. `useIdleLogout` and `draftCache` handle only local client state and the
  session-slide endpoint; no untrusted external content is involved.
- **Python side (`safety_reports/`, `shared/`):** both PRs in this session are
  TypeScript/SPA-only. `tests/test_capability_gating.py` unaltered.
- **Worker auth / D1 / HMAC paths:** B's keep-alive uses `GET /api/session` (read-only
  session probe) — no new auth route, no D1 write, no HMAC involvement.
- **`publish_daemon.py` / compile pipeline:** not touched.
- **Form schema / catalog.json:** no form definitions modified.
- **Evergreen production tenant:** deploy targets the mirror
  (`safety.evergreenmirror.com`). Production cutover deferred pending Evergreen go-live.

---

## Cross-references

- Memory-archive entry **§G34** (`~/its-blueprint/references/memory-archive.md`) — fuller
  decision record for the bounded-keep-alive adversarial finding, including the
  `SessionClaims` iat re-stamp chain and the unbounded-session threat model.
- Prior session log (Form Editor UX + draft cache, PRs #249–#250):
  [`2026-06-09_form-editor-ux-draft-cache.md`](2026-06-09_form-editor-ux-draft-cache.md)
- Prior session log (weekly_send hardening + audit findings + incident-report E2E):
  [`2026-06-09_weekly-send-hardening-audit-findings-incident-report-e2e.md`](2026-06-09_weekly-send-hardening-audit-findings-incident-report-e2e.md)
- `safety_portal/src/lib/useIdleLogout.ts` — `paused` arg; bounded keep-alive; proactive
  `onIdle` preserved in both modes
- `safety_portal/src/pages/FormsPage.tsx` — `editing` flag; unmount-reset effect
- `safety_portal/src/pages/AccountsPage.tsx` — `editing` flag; unmount-reset effect
- `safety_portal/src/components/AdminApp.tsx` — `editing` thread
- `safety_portal/worker/index.ts` — `ADMIN_IDLE_S = 1800`; login + slide cookie max-age
- `safety_portal/src/lib/__tests__/useIdleLogout.test.ts` — new; 6 cases
- `safety_portal/src/pages/__tests__/FormsPage.editing.test.tsx` — new; unmount-reset
  regression guard
- `safety_portal/test/admin-idle.test.ts` — updated 300→1800 assertions
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- Op Stds v18 §1 (External Send Gate — admin session lifetime is load-bearing)
- FM v11 Invariant 2 (adversarial posture; session-security reasoning)
