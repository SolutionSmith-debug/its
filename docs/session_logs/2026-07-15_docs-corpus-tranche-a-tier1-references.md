---
type: session_log
date: 2026-07-15
status: closed
related_prs: [598]
workstream: docs
tags: [documentation-corpus, tier-1, references, extraction-first]
---

# 2026-07-15 — Documentation corpus, Tranche A (Tier-1 system references)

## Purpose

First tranche of the system-documentation-corpus + interactive-troubleshooting-tree program
(executes the `feedback_documentation-program` directive). Tranche A = the 8 net-new Tier-1
operator references, extraction-first: every factual claim verified against live code with an
invisible `<!-- src: file:line | verified DATE -->` comment (stripped from the PDF, auditable in
git). Charter: accuracy over volume — a wrong reference is worse than none.

## Pre-flight findings

- **Prerequisites (2026-07-14 debt-zero session) confirmed landed:** the `--upload` Box publish
  leg (#588), the production-email scrub (#584), and the `operator_dashboard.md` delta pass
  (refreshed Jul 14). Baseline clean at `94edcc9`.
- **Terrain extracted (not assumed):** 20 registered watchdog checks (letters A–V; 21 `_check_*`
  defs, one shared catch-up helper), **16** launchd daemon plists, 6 `held_*` send-status values,
  53 D1 migrations, 38 Worker `.ts` files, `TRACKED_JOBS`=12.
- **Key extraction subtlety:** the launchd plists carry a `__POLL_INTERVAL_SECONDS__` template
  placeholder — the real interval is substituted by `scripts/launchd/install.sh` at install time
  (portal-poll 60 / weekly-send 900 / compile-now 90 / progress-send 900 / fieldops-sync 90 /
  po-poll 90 / po-send 900 / subcontract-poll 120). Daemon intervals were sourced from there, not
  the plist literals.

## Code changes

- **8 Tier-1 references** in `docs/references/` (3,381 lines): `system_architecture`,
  `daemon_reference`, `data_model_reference`, `integration_reference`, `security_trust_model`,
  `escalation_matrix`, `glossary`, `documentation_index`. TEXT diagrams only (ASCII box-art / GFM
  tables), h1–h4, no images. Red-lines honored (no secret values/PINs, role emails only, no pasted
  blueprint text — §-cite paraphrase, no attack-recipe framing).
- **`docs/enablement/manifest.yaml`** — registered all 8 with fresh sha256 + an `audience:` tag.
- **`docs_pdf/manifest.py`** — added an optional `audience: str = ""` field to `ManifestEntry` +
  read it in `load_manifest` (backward-compatible; older guides default to "").
- **`tests/test_docs_pdf.py`** — expanded the committed-manifest key set to 21; added
  `test_manifest_carries_audience` + `test_manifest_audience_defaults_when_absent`.
- **`docs/references/README.md`** — AUTO-INDEX regenerated for the new refs (the two unrelated
  top-level README index-drifts the regen surfaced were reverted to keep the PR scoped).

## Method (non-obvious decision)

Built via a **Workflow**: 6 parallel deep-extraction drafters (one per content doc, each reading
live source + writing with mandatory src comments) → an **adversarial per-doc verifier** that
independently re-checked claims against live code. The 2 aggregators (`glossary`,
`documentation_index`) were authored directly. Every verifier refutation was re-verified against
live code and corrected:

| Doc | Refutation → fix |
|-----|------------------|
| data_model | `ReviewStatus` was missing `IN_REVIEW` (5 values); `roles` missing seeded `manager`; `cap.*` count 18 → **26** (grep-verified); row-cap re-attributed to `SHEET_ROW_HARD_CAP`, not a division |
| integration | Box OAuth endpoint src `83-97` → `68-69`; `SESSION_SIGNING_SECRET` = HMAC-signed cookie (not bcrypt); `portal_client` "oldest-first" cite → + Worker SQL |
| security | `ITS_OPERATOR_PIN` IS in-dashboard-rotatable (`pin_change.py`); honesty-test src `44-220` (data lists) → `567/596/663` (the test defs) |
| escalation | circuit_breaker runbook anchors `:85`/`:127-129` → `:78-81` (low-class clear) / `:85-94` (escalate) |
| system_arch | verifier clean (42/42); daemon_reference self-verified in main loop (spend-limit killed its verifier) |

## Verification

- pytest: **3512 tests collected**, full suite green (CI `test` job pass — 2m49s / 3m56s; local
  `pytest` exit 0 twice pre-commit)
- mypy: clean — no issues in 378 source files
- ruff: clean
- build_docs_pdfs --check: green (21 docs current); every new doc renders via
  `test_every_manifest_doc_renders`
- main-branch CI on merge commit `618dd36`: **SUCCESS** (`ci=completed/success`, `Push on main=completed/success`)

## Out-of-scope notes

- Tranches B–E deferred (clean boundary): B troubleshooting `tree.yaml` + coverage tests, C the
  dashboard `/troubleshoot` route, D the Tier-2 currency pass (13 guides + 36 runbooks), E
  distribution (Box `--upload` full corpus + the one live `ITS_Documentation_Index` sheet + a
  dashboard docs page).
- The Tranche-A extraction Workflow hit the monthly spend limit mid-run (killed one verifier);
  remaining work continued in the main loop. Prefer main-loop / smaller fan-out for Tranche B.

## Operator-side actions remaining

- **Box publish first-activation** (Tranche E, post-flip, production host, `--dry-run` first) —
  add to the cutover punch-list.
- **Seth 15-min review** of `escalation_matrix.md` + `security_trust_model.md` before those two
  are published — they speak with the system's authority on who-does-what and warrant the owner's
  eyes.

## Merge verification quartet output

PR #598 — `state=MERGED`, `mergedAt=2026-07-15T14:02:31Z`, `mergeCommit=618dd36214d0f1f1155cc9eb30838b9f0ef019bf`.
- pytest: 3512 collected / full suite green (CI test job)
- mypy: clean / 378 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS
