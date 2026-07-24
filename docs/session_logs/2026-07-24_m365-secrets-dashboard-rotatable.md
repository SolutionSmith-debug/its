---
type: session_log
date: 2026-07-24
status: closed
related_prs: [705, 707]
workstream: operator_dashboard
tags: [session_log, operator_dashboard, secrets, keychain, graph, developer_operator, tech_debt]
---

# 2026-07-24 — The three M365 / Graph secrets become dashboard-rotatable (Developer-Operator self-service), detection gap logged

Session focus: make `ITS_MS_TENANT_ID`, `ITS_MS_CLIENT_ID` and `ITS_MS_CLIENT_SECRET` rotatable
from the operator dashboard's Class-C credential surface — a §44 v21.x-rider **Developer-Operator
self-service** improvement on the sole transport for every external send — then log the remaining
*detection* half of the same problem as OPEN tech debt rather than half-building it.

> **Framing correction (applied post-merge, PR #708).** The CC brief, PR #705's title/body and the
> first draft of this log all justified the change as closing a **Successor-Operator Tier-2
> "ship-and-leave hole."** That rationale contradicts the Op Stds v21 **§44 v21.x rider** (ratified
> 2026-07-14, ten days earlier), which scopes the dashboard's **Class-C surface to the
> Developer-Operator only** — "a Successor-Operator does not hold or rotate secrets" — and keeps
> **secrets/auth a FIXED high-capability class that always escalates**. The *code* is
> doctrine-blessed either way: the rider expressly authorizes current-credential-gated
> self-rotation of an operator-held secret **by its holder**, which is exactly what the PIN-gated
> Class-C ceremony does. Only the *rationale prose* asserted the retired model — and it did so in a
> Tier-1 security reference a Successor-Operator reads, where it could be taken as permission to
> rotate a credential doctrine forbids them to touch. Correct framing: this removes a terminal
> round-trip for the Developer-Operator; it does **not** create a Tier-2 capability. Original
> wording is preserved below wherever it is quoted as history.

## Commits landed

- PR #705 (`2359e90`) — feat(dashboard): the three M365 secrets are dashboard-rotatable (closes a
  ship-and-leave hole). Adds `ITS_MS_TENANT_ID`, `ITS_MS_CLIENT_ID`, `ITS_MS_CLIENT_SECRET` to the
  Class-C rotatable-credential registry `_SECRETS` in `operator_dashboard/act/registry.py` as
  `kind="keychain"`, inserted after `ITS_BOX_CLIENT_SECRET`. Same-PR fan-out:
  `docs/references/security_trust_model.md` rotatable-credential table + the sha256 re-record in
  `docs/enablement/manifest.yaml`.
- PR #707 (`d8652b8`) — docs(tech-debt): M365 client-secret expiry has a repair path but no
  detection path. New OPEN `docs/tech_debt.md` entry (tags `operator_dashboard`, `security`,
  `phase-1.5`; severity high), logged at operator request.

**No new code shipped in #705.** `kind="keychain"` routes through the pre-existing `_rotate_keychain`
path in `act/secret_rotate.py`, and `templates/config.html` auto-renders the new rows because it
already loops `_SECRETS` and renders `.note` — the client-secret entry's note carries its expiry
caveat.

## CI runs

- PR #705 — four-part verify clean:

```
PR #705 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-24T18:46:34Z
- mergeCommit: 2359e90490ad7d75cc6fc89f674c16624b125bb7
- main-branch CI on merge commit: SUCCESS
```

- PR #707 — four-part verify clean:

```
PR #707 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-24T19:10:47Z
- mergeCommit: d8652b8d3f440f0a05d557e69471113bdac66ce2
- main-branch CI on merge commit: SUCCESS
```

## Decisions made during session

1. **Rotate-ability for the M365 secrets is worth having — but as Developer-Operator self-service,
   not a Tier-2 capability.** The Entra-ID client secret EXPIRES on a tenant-set lifetime, and
   `shared/graph_client.py` is the sole transport for every external send, so an unnoticed expiry
   takes every send lane down at once. Rotating from the console saves the Developer-Operator a
   terminal round-trip, under the §44 v21.x rider's current-credential-gated self-rotation carve-out.
   *As decided in-session this read:* "A Successor-Operator who cannot re-seed it from the console
   has no recovery path except Seth — a ship-and-leave hole under Op Stds v21 §44 Tier-2."
   **That rationale was wrong** (see the framing correction at the top): §44 scopes Class C to the
   Developer-Operator, and secrets/auth is a FIXED high-class that always escalates, so a
   Successor-Operator meeting an expired Graph credential escalates either way. The decision to make
   the change stands; the justification for it does not. Operator-ratified 2026-07-24; framing
   corrected 2026-07-24 (PR #708).
2. **All three secrets added together, not just the expiring one.** Alternative considered: register
   only `ITS_MS_CLIENT_SECRET`, since it is the one with a clock on it. Rejected because a
   re-registered Entra app changes tenant id, client id and secret *at once* — re-seeding two of
   three leaves Graph fail-closed with a repair the operator can only half-perform.
3. **`kind="keychain"`, deliberately NOT `box_guided`.** `box_guided` exists for the single-consumer
   token that rotates on every use (the Box refresh token, where a paste form would be actively
   wrong). These three are static, re-seedable values, so the plain keychain paste path is correct
   and no new rotation code was needed.
4. **Inserted after `ITS_BOX_CLIENT_SECRET`, not appended.** Keeps the `box_guided` refresh-token
   entry last in the keychain cluster, so the guided-flow entry stays visually terminal in `/config`
   rather than being buried mid-list by the three new rows.
5. **Deliberate one-surface scope extension past the brief's stated "scope: registry only."**
   HOUSE_REFLEXES §1 fan-out check found `docs/references/security_trust_model.md` carries a
   rotatable-credential table that *states it is the complete fixed list* — and it had ALSO drifted
   independently, missing `PORTAL_ESTIMATE_API_TOKEN` and `PORTAL_RFQ_API_TOKEN` (ADR-0004), with a
   `src:` line-ref pointing at `registry.py:371-388` (now 540-585). Judgment call: leaving a Tier-1
   security reference listing 9 of 14 rotatable credentials was a real pre-existing defect that this
   change would have *worsened*, so all five rows were added, the ref and verified-date corrected,
   and the sha256 re-recorded in `docs/enablement/manifest.yaml`.
6. **The detection gap was logged, not built (#707).** #705 closed the REPAIR gap; the DETECTION gap
   remains open in two halves — (a) no advance warning (no watchdog check, no ITS_Config expiry row,
   no lead-time alert; the #705 registry note is *narrated-not-enforced* per Op Stds v21 §52 and
   leans on a human remembering across 6–24 months), and (b) no distinguishable failure signal (a
   Graph auth failure surfaces as whatever CRITICAL the calling send daemon raises, so nothing names
   the expiry — a Successor-Operator escalates on secrets/auth regardless, but cannot even say
   *what* broke).
7. **Candidate detection shapes sketched but deliberately not decided, and the trigger set at the
   cutover config-seed pass.** The expiry date can only be captured once the production Entra app is
   registered, so picking a mechanism now would be designing against an unknown value.

## Verification

- pytest: 4495 passed / 2 skipped / 0 deselected (0 failed)
- mypy: 0 errors / 465 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS (both PRs)

Additional gates run:

- `check_doctrine_drift --strict` — no blocking drift.
- `build_docs_pdfs --check` — all 22 enablement docs current.
- doc-conventions lint + doc-index check — clean on the touched files.

**Prove-the-control-bites** (HOUSE_REFLEXES §2 — run rather than trusting green):

- All three new entries rotate end-to-end through `_rotate_keychain` to `Keychain.set_secret`.
- An unlisted lookalike name, `ITS_MS_NOT_REAL`, is still refused — proving the registry stays
  *bound* rather than silently prefix-matching the new `ITS_MS_*` family.
- `ITS_BOX_REFRESH_TOKEN` still returns `guided` and renders no paste form (the `box_guided`
  carve-out survived the insertion).
- `/config` renders all three rows with working rotate forms and the expiry note.
- The docs-currency gate was confirmed to RED-light on the doc edit *before* the sha was
  re-recorded — the manifest coupling bites as designed.
- No test fixture needed touching: nothing asserts a count on `_SECRETS`.

## Non-obvious operational finding — a registry change is not visible on a browser reload

`SECRETS` is a module-level dict built at **import time**, so the running launchd dashboard process
keeps serving the OLD list until the process restarts. Found live: PID 12470 had been up ~15.5 hours
(since Jul 23 23:34), long predating the merge — a browser reload showed the pre-#705 list and would
have read as "the change didn't land."

Resolved with `launchctl kickstart -k gui/$(id -u)/org.solutionsmith.its.dashboard`, then verified
against the live service: `/healthz` reported `secrets=14` (was 11), and `/config` rendered all three
rows with rotate forms plus the expiry note, with the Box refresh token still showing no paste form.
DASH-12 (the dashboard's one sanctioned self-restart verb) is the in-dashboard equivalent of that
restart.

This generalizes: **any import-time-built dashboard registry change needs a dashboard restart before
live verification means anything.**

## Open items handed off (Seth)

1. **At the cutover config-seed pass:** capture the production Entra app's client-secret expiry date
   and pick a detection shape for the #707 tech-debt entry. The trigger is deliberate — the date does
   not exist until the production app is registered.
2. **The second half of the detection gap** — a *distinguishable* Graph-auth failure signal, so an
   escalation carries a diagnosis instead of a generic send failure. (Secrets/auth escalates to the
   Developer-Operator either way; the value is naming the cause, not avoiding the escalation.)
   Currently a Graph auth failure is indistinguishable from any other send-daemon CRITICAL. Shape
   not decided.
3. **An actual end-to-end Entra rotation has not been exercised.** This session proved the rotate
   *mechanism* (registry → `_rotate_keychain` → Keychain) and the rendered UI; it did not re-seed a
   real re-registered app and then confirm a live Graph send. Worth exercising at or after cutover.

## What was NOT touched

- `scripts/verify_cutover.py` `REQUIRED_SECRETS` (VC-01, lines 239-241) — checked, already carries
  all three. These are **existing** Keychain secrets becoming *rotatable*, not new secrets, so VC-01
  needed no change.
- The host-migration A5 table — same reason; all three already listed.
- `docs/references/integration_reference.md:701` — lists the three M365 secrets, but its columns are
  *store-location*, not rotatability. Correctly left alone rather than reflexively edited.
- No change to `act/secret_rotate.py`, `templates/config.html`, the `box_guided` flow, the Class-C
  auth/elevated-confirm path, or any capability-gating list. #705 is a registry-data change plus a
  doc fan-out.
- No detection mechanism built (no watchdog check, no ITS_Config expiry row, no new error code) —
  logged as OPEN per decision 6/7, not half-built.

## Process notes

- Both changes rode per-task worktrees off `origin/main`: `../its-m365-secrets` with its **own fresh
  venv** for the Python-source edit (the live `~/its/.venv` editable install was verified unchanged
  afterwards), and `../its-td` for the docs-only edit. Both worktrees removed and branches deleted
  after the MERGED verify; `~/its` synced to main (`d8652b8`).
- **Gotcha worth remembering:** an early backgrounded full-suite run reported exit 0 while actually
  failing on an unrecognized `--timeout` arg — the `| tail` pipe masked pytest's real exit code. The
  suite was re-run properly with the exact CI command. A piped command's exit status is the *last*
  stage's; never read a green exit through a pipe as a green suite.

## Lessons captured to memory

- The import-time-registry / dashboard-restart finding is the most reusable item from this session
  and is recorded above; it is a candidate for `docs/HOUSE_REFLEXES.md` §2 (prove-the-control-bites —
  "verify against the *running* service, not the merged source") but was **not** promoted this
  session.
- The `| tail`-masks-exit-code gotcha is likewise recorded here as a candidate for HOUSE_REFLEXES §2,
  not applied.
- The `security_trust_model.md` drift reconfirms HOUSE_REFLEXES §1 (a datum has N implementations;
  reconcile every registry in the same PR) rather than adding a new reflex — the credential list
  lives in the code registry, the security reference, VC-01 and the host-migration table.

## Cross-references

- Doctrine: Op Stds v21 §44 (Tier-2 Successor-Operator / the both-rule; the four FIXED high-class
  categories include secrets/auth) and especially its **v21.x rider, "Developer-Operator credential
  self-service (current-credential-gated)"**, ratified 2026-07-14 — the clause this session's
  original rationale contradicted; §52 (narrated-not-enforced), §43 (successor-remediation
  runbooks), §55.3/§55.4 (four-part landing verify; faithful reporting).
- Blueprint: `workstreams/operator-dashboard/mission.md` — "**Class C is Developer-Operator-only**
  … a Successor-Operator does not hold or rotate secrets."
- Repo: `operator_dashboard/act/registry.py` (`_SECRETS`), `operator_dashboard/act/secret_rotate.py`
  (`_rotate_keychain`), `shared/graph_client.py`, `docs/references/security_trust_model.md`,
  `docs/enablement/manifest.yaml`, `docs/tech_debt.md`.
- House reflexes: §1 (reconcile every registry in the same PR; enumerate all surfaces), §2 (prove the
  control bites), §3 (per-task worktree with its own venv for Python-source edits; the four-part
  landing verify).
- Related: ADR-0004 (the portal estimate/RFQ tokens that surfaced as pre-existing drift in the
  security-reference table), `docs/operations/pr_merge_discipline.md`.
