---
type: session_log
date: 2026-07-10
status: closed
workstream: null
related_prs: [504, 505, 506, 507, 508, 509, 510, 511, 512]
tags: [session_log, purchase_orders, po_materials, config-editor, section50, privileged-actuation,
  adr-0002, po-naming, multi-surface-fan-out, self-defeating-ci-test, four-part-verify,
  ci-cancelled-run, doc-drift, adversarial-review, pentest, vendor-region, dont-build-against-unseen-sot]
---

# Session — PO follow-ups (ship-to / catalog / config-view / naming) + the generic §50 config-editor 3-slice vertical (PRs #504–#512, #511 stuck)

Large multi-thread session picking up loose ends from the just-completed PO-generator program
(S1–S8, merged earlier the same day as #492–#503, out of this log's scope) and then building a new
capability on top of it: a generic, workstream-registry §50 config editor (queue → Mac actuator →
SPA), live-activated on the mirror with the operator watching. Eight PRs squash-merged clean; a
ninth (#511) is a genuine, diagnosed-not-patched blocker left OPEN as the next session's first item.

## Commits landed

- **#504** `0c8c64c` — ship-to auto-fill completion (S6 follow-up): new `GET /api/po/jobs/:job_id/ship-to`
  (send-free Worker, `cap.po.manage`) reads the routing SoR (`jobs.address`/`stakeholder_*`) that only
  the internal-token tier could see before, so the PO builder's ship-to step now fills without
  widening any capability.
- **#505** `5096f06` — links the **existing** `material_catalog` (migration `0019`, no new table) into
  the PO builder as a pick-list (`GET /api/po/materials`, gated `cap.po.manage` not
  `cap.materials.receive`) and moves the Materials Catalog Home card into Administration.
- **#506** `fd77cc0` — read-only **PO Configuration** admin view (purchaser / tax / terms), consuming
  the existing `GET /api/po/config` + `GET /api/po/terms` routes. No new Worker surface, no
  migration. Ships alongside **ADR-0002**, which records the deferred edit-path design.
- **#507** `fd21816` — job name into the PO PDF title/filename across all four naming surfaces via one
  new canonical helper, `po_materials/po_naming.py` (Box file, Smartsheet attachment, emailed
  attachment, internal PDF `/Title`) — the multi-surface-fan-out lesson applied up front instead of
  fixed after a partial pass. Also job-prefixes the shared `WeeklyReportEnvelope` attachment name,
  which is used by **both** progress and safety — an intentional, disclosed customer-visible change
  to safety's live email attachment filename (subject/recipients/bytes unchanged).
- **#508** `1a2fdd7` — config-editor **slice 1/3**: send-free cloud queue (`migrations/0045`,
  `worker/config.ts`, `CONFIG_REGISTRY` with `po_materials` real + a documented `subcontracts`
  placeholder), cloned from the form-editor `publish_requests` lease/claim/stamp pattern.
- **#509** `902c1fd` — config-editor **slice 2/3**: `po_materials/config_actuator.py` + `config_apply.py`
  — the **fully-automatic** Mac actuator (pull → claim → validate-vs-HEAD → commit+PR+wait-CI+merge-
  on-green → deploy → stamp `live`). Ships dark (`polling_enabled=false`). See Decision 6 below — this
  diverges from ADR-0002's drafted default.
- **#510** `e0ae3d7` — config-editor **slice 3/3**: `PoConfigPage` becomes editable (purchaser/tax/terms
  forms + a live status-monitor stepper + a visibly-disabled subcontracts placeholder card).
- **#512** `be6b423` — two reproducibility gaps found during the live activation itself: the seeder
  didn't mirror `worker_base_url` under the `po_materials` workstream tag (fresh install would
  fail-closed on an empty URL), and the runbook was missing the "`install.sh load`, not a raw `cp`"
  gotcha that bit the activation live (a raw-copied plist is an exit-78 template with `__ITS_HOME__`
  unsubstituted).

**Not merged — left OPEN, diagnosed:**
- **#511** `chore(po-config): purchaser: Evergreen Renewables LLC -> config_version 2 (req 1)` — the
  actual first live purchaser edit, queued by the just-activated actuator. Stuck permanently: `test`
  and `portal` CI checks `FAILURE`. Root cause (see Decision 9 / tech_debt CE-3): CI hard-pins the
  live editable-config content, so any legitimate edit fails its own regression test. Nothing on
  `main` broke — the edit simply never merges.

## CI runs

Four-part verify run per PR (`gh pr view --json mergedAt,mergeCommit,state` then
`gh run list --branch main --commit <sha>`):

| PR | mergeCommit | state/mergedAt | `ci` job | `Push on main` | Verdict |
|---|---|---|---|---|---|
| #504 | `0c8c64c7` | MERGED @ 2026-07-10T05:02:45Z | success | success | clean |
| #505 | `5096f062` | MERGED @ 2026-07-10T05:12:52Z | success | success | clean |
| #506 | `fd77cc01` | MERGED @ 2026-07-10T05:47:00Z | success | success | clean |
| #507 | `fd218160` | MERGED @ 2026-07-10T14:05:06Z | success | success | clean |
| #508 | `1a2fdd73` | MERGED @ 2026-07-10T14:43:50Z | success | success | clean |
| #509 | `902c1fd9` | MERGED @ 2026-07-10T15:23:56Z | **cancelled** | success | **see anomaly below** |
| #510 | `e0ae3d7e` | MERGED @ 2026-07-10T15:24:32Z | success | success | clean (also retroactively covers #509) |
| #512 | `be6b4237` | MERGED @ 2026-07-10T16:24:08Z | success (was `in_progress` at first check) | success | clean |

**#509 anomaly, disclosed rather than rounded up.** The literal `gh run list` output for #509's merge
commit:

```json
[{"conclusion":"cancelled","databaseId":29103514917,"name":"ci","status":"completed"},
 {"conclusion":"success","databaseId":29103514510,"name":"Push on main","status":"completed"}]
```

`gh run view 29103514917` confirms `headSha=902c1fd9…`, `createdAt=15:23:58Z`,
`updatedAt=15:24:51Z` — cancelled, not failed. Cause: PR #510's merge commit (`e0ae3d7`) pushed to
`main` 36 seconds later (`mergedAt=15:24:32Z`), and GitHub Actions' concurrency group auto-cancelled
the still-running `ci` job for the superseded commit — the two PRs in this same 3-slice arc landed
back-to-back. The **immediately-following** `ci` run, on `e0ae3d7` (a superset tree that includes
every line #509 touched), completed at `2026-07-10T15:28:44Z` with `conclusion: success` across
`test`/`portal`/`secrets`. So #509's own merge-commit CI never independently reached a completed
`success` — the four-part discipline's literal step 4 fails for that one commit — but the very next
main-CI run, containing #509's code, is green. Recorded here per the "PR #34 ghost" discipline: this
is the honest middle case, not silently normalized to "SUCCESS" and not miscategorized as a real
failure.

**#512 note.** Its `ci` run was still `status: in_progress` at the first check (~4 min after push,
in line with prior same-workflow durations); re-polled once and confirmed `status: completed`,
`conclusion: success` before writing this log.

**Cumulative local re-verify at current HEAD (`be6b423`, post-#512) — the four-part block:**

```
- pytest: 2947 passed / 0 skipped / 48 deselected
- mypy: 0 errors / 304 source files
- ruff: clean
- main-branch CI on merge commit be6b423: SUCCESS
```

(`.venv/bin/python -m pytest` → `2947 passed, 48 deselected, 1 warning in 37.85s`; `.venv/bin/mypy .`
→ `Success: no issues found in 304 source files`; `.venv/bin/ruff check .` → `All checks passed!`.
Matches the CI log for the #512 `ci` run byte-for-byte on the mypy/ruff lines; the CI log's own
pytest step never printed its final summary line in the captured log text — a pytest-cov ordering
quirk, not a discrepancy — so the count above is the local re-run against the identical merge
commit.)

**#511 (not merged) checks, for the record:** `state=OPEN`, `mergeable=MERGEABLE`; `test` **FAILURE**
×2, `portal` **FAILURE** ×2, `secrets` SUCCESS, CodeQL SUCCESS — matches the live description already
banked in `docs/tech_debt.md` CE-3.

## Decisions made during session

1. **Ship-to auto-fill reads the routing SoR through a new narrow route, not the internal token
   tier.** `GET /api/po/jobs/:job_id/ship-to` is gated on the PO builder's own `cap.po.manage` rather
   than exposing the internal-token `pending-jobs` route to the browser — keeps the privilege
   boundary the same shape as everything else in the builder (#504).
2. **Material catalog PO-builder read is a new `cap.po.manage`-gated route, not a reuse of
   `/api/fieldops/materials`.** Reusing the field-ops route would have handed a pure PO admin the
   field-ops `cap.materials.receive` capability just to read a pick-list; the new route keeps every
   PO-builder read under one cap (#505).
3. **PO Configuration ships read-only first; the edit is deliberately deferred (ADR-0002).** The
   office gets the majority of the value (catching a wrong entity/tax/terms value by eye) with zero
   actuation exposure; the edit path — which git-commits legal T&C and money-path tax — waits for an
   operator-present build per the §44 "first actuation of a new code-deploy category" precedent
   (#506).
4. **`po_naming.py`: one canonical helper across all four PDF-name surfaces, not a per-surface
   patch.** Applied the multi-surface-fan-out lesson *before* shipping rather than fixing it in a
   follow-up PR the way #289→#290 and #247→#253 had to. Accepted, as a disclosed side effect, that
   safety's shared `WeeklyReportEnvelope` attachment name also gains the job prefix — the
   byte-equivalence pin that existed to catch *accidental* drift was updated deliberately for this
   *intentional* one (#507).
5. **The config editor was built as one coherent §50 vertical, not three unrelated PRs.** Cloud queue
   (#508) → Mac actuator (#509) → SPA editor (#510), each delegated to a build agent against a
   precise written spec, each run through adversarial review (`portal-worker-security-reviewer` /
   `ops-stds-enforcer` — clean or WARN-resolved on every slice) before integration and merge.
6. **Generic `CONFIG_REGISTRY` with a `subcontracts` placeholder, not a PO-only queue.** `po_materials`
   is the only real registry entry today; the placeholder provisions a future subcontract-config
   editor with **zero route changes** — genericize-once over building a second bespoke queue later.
7. **Fully-automatic actuation (C12=A, mirroring `publish_daemon`) was chosen live, overriding
   ADR-0002's drafted default.** ADR-0002 (written at #506) explicitly recommends **propose-mode**
   (commit + open a PR, human merges) as the default for slice 2, reserving fully-automatic as "a
   later operator opt-in per class... NOT the default here, because legal T&C and money-path tax
   carry more downside than a form definition." The operator was asked directly (all-three-editable,
   generic, and automation mode) and chose fully-automatic instead — #509 ships the `publish_daemon`-
   style auto-merge-on-green pipeline, not the ADR's propose-mode. **The ADR text itself was not
   updated to reflect this reversal** — see Open items.
8. **Live activation was Developer-Operator-watched**, consistent with the §44 / ADR-0002 "operator
   present for the first actuation of a new code-deploy category" precedent: token provisioned
   (`ITS_PORTAL_CONFIG_TOKEN`), daemon loaded via `install.sh load org.solutionsmith.its.config-actuator`
   (not a raw `cp` — the plist is a `__ITS_HOME__` template; this exact gotcha bit the activation live
   and is now the #512 fix + runbook note), gate flipped.
9. **The first live edit smoke found a real blocker and it was diagnosed, not silently
   worked around.** The operator's own purchaser edit (PR #511) hit CI red at the `tested` stage and
   is now permanently stuck — `safety_portal/test/po.test.ts:222-223` and
   `tests/test_config_apply.py` both assert the exact *current* live-bundled config content
   (purchaser entity string, `config_version == 2`) rather than its shape, so any legitimate edit
   fails its own regression test. This is the identical self-defeating-CI-test class already named
   in `claude-code-info-gap.md` §5 (PR #222/#228, form-publish catalog counts) recurring on a second
   §50 instantiation. Diagnosed live, root-caused, and banked as tech_debt **CE-3** — the fix (rewrite
   both tests to assert shape/round-trip against a fixed fixture, not the live file's current value)
   is scoped but deliberately **not** applied this session; landing it is the next session's first
   move, then re-submitting or retesting #511. Nothing on `main` broke — the edit simply never merged.
10. **Vendor-region ask BLOCKED, not guessed.** An ask to fill in region data for the PO-corpus
    vendor list (`docs/reports/2026-07-09_po_corpus_analysis.md` §7) against Teala's fuller vendor
    list came up ~25 entries short of what the PO corpus itself documents. Rather than fabricating
    region values for the missing stubs, the ask was parked — the "don't build against an unseen
    source-of-truth" reflex applied to a data-completeness gap, not just a schema one.
11. **A full pentest/stress pass of the deployed portal was run this session**, separate from the
    per-PR adversarial reviews above: no critical/high findings; one MEDIUM (plain HTTP served
    before the HTTPS redirect) — a Cloudflare zone-setting toggle ("Always Use HTTPS"), not a code
    change.

## Open items handed off

- **CE-3 (tech_debt, HIGH, blocks the editor entirely)** — rewrite `po.test.ts:222-223` and
  `tests/test_config_apply.py`'s content-pinning assertions to shape/round-trip against a fixed
  fixture, land that fix on `main` FIRST, then re-submit the stuck purchaser edit (or close #511 and
  let the next attempt retest clean). Full detail already banked in `docs/tech_debt.md`.
- **CE-1 (tech_debt, LOW)** — bring `safety_reports/publish_daemon._fail`'s `failure_reason` to §54
  redact-parity with `config_actuator._fail` (same unredacted pattern, a different portal-facing
  sink than `error_log`'s choke point).
- **CE-2 (tech_debt, LOW)** — the render-side `legal_review` Layer-A refusal stays deferred until the
  two already-shipped terms versions are backfilled to `legal_review: "cleared"` (turning the refusal
  on today would fence every live PO).
- **ADR-0002 doc drift** — the ADR's "Decision" section still states propose-mode is the default and
  its frontmatter `status` is still `proposed`, but the actual #509 build shipped fully-automatic
  (Decision 7 above). Needs a documentation-only follow-up: either amend the ADR's Decision/
  Consequences to record the fully-automatic choice, or mark it superseded by the live decision — a
  future `brief-validator`/`doc-reconciliation-auditor` pass should not treat the ADR's text as the
  current state without checking this log.
- **Doc-conventions workstream-taxonomy gap** — `po_materials`/`purchase_orders` is fully built and
  live (20+ PRs, three daemons) but still absent from `CANONICAL_WORKSTREAMS` (warn-only today;
  tracked in `docs/tech_debt.md`).
- **Vendor-region data (Teala's list)** — blocked pending an operator-provided/verified source for
  the ~25 vendors missing region data; do not fabricate values to close this out.
- **Pentest MEDIUM finding** — flip Cloudflare's "Always Use HTTPS" zone setting for the portal
  domain; operator config action, not a PR.

## What was NOT touched

- **CE-3 was diagnosed, not patched** — no test-content rewrite was attempted this session; the fix
  is scoped in tech_debt for a supervised follow-up, not force-built reactively.
- **ADR-0002 was not edited** to reflect the fully-automatic decision — doctrine/ADR-adjacent text
  edits are outside this session's scope; flagged as an open item instead of silently reconciled.
- **The config-editor's edit path was never left unsupervised on its first live actuation** — the
  Developer-Operator watched activation end-to-end, consistent with the §44 precedent ADR-0002 itself
  cites.
- **Vendor-region data was not invented** to close out Teala's list ask.
- **No doctrine bump.** The config editor is a new *instantiation* of the existing §50 privileged-
  code-actuation gate (already covering `publish_daemon`/`po_poll`/`po_send`), not a new doctrine
  clause — `docs/doctrine_manifest.yaml` / Op Stds v20 needed no change this session.

## Lessons worth folding into memory (next session-close pass)

Not yet banked in `HOUSE_REFLEXES.md`/`MEMORY.md` — surfaced here for the session-close-maintainer:

- **A rapid same-arc back-to-back merge can cancel, not fail, the earlier commit's own CI run** —
  GitHub Actions' concurrency-group auto-cancel fires on a push-triggered workflow when a second
  commit lands on the same ref before the first run finishes (here, #509 → #510, 36 seconds apart).
  The four-part verify's literal step 4 (`conclusion == success` on THAT commit) reads `cancelled`,
  not `success`, even though the very next main-CI run — which contains the "cancelled" commit's code
  — is green. Worth a `pr-landed-verifier` refinement: when a merge commit's `ci` run shows
  `cancelled`, check whether a same-day, same-arc follow-on commit's CI run is green and covers the
  same tree, rather than reading `cancelled` as an unqualified failure.
- **An ADR can go stale within the same session it's written in** — ADR-0002 (#506) drafted
  propose-mode as the actuator default; #509, three PRs later in the same session, built
  fully-automatic instead per a live operator decision. The ADR's own text was never the thing that
  changed hands — ordinary session narrative did — so a `brief-validator`/doc-reconciliation pass
  should not assume an ADR's "Decision" section is current just because it's dated the same day as
  the PR that (partially) contradicts it.
- **`pytest -q` passed twice (once from `pyproject.toml`'s `addopts`, once from an explicit CLI `-q`)
  stacks to `-qq` and silently drops the final summary line** — confusing when trying to extract a
  passed/failed count from a CI log or a local re-run; drop the redundant CLI `-q` (or use `-v`) to
  get the "N passed in Ys" line back.
