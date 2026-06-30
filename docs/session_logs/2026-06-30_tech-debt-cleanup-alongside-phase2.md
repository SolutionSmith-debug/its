---
type: session_log
date: 2026-06-30
status: closed
workstream: infrastructure
related_prs: [363, 364, 365, 366, 367, 368]
tags: [session_log, tech-debt, blast-radius, worktree, flaky-tests, allowlist-drift, dormant-subsystem, operator-checklist, worktree-venv-fix, cp-venv-bug, phase2-alongside, lessons-learned]
---

# Session — Tech-debt cleanup alongside in-flight Phase-2 (PRs #363–#368)

A deliberate tech-debt cleanup pass run concurrently with an in-flight Phase-2 (job-tracker pivot / Progress-Reporting program) session on the same machine. The session was scoped from the start to avoid Phase-2's blast radius. Key theme: blast-radius mapping before touching anything, proof-by-grep before acting on any stale-open claim, and lesson capture when a well-known recipe turned out to be buggy in practice.

## Method

Mapped the Phase-2 blast radius from both active plan files (`ok-we-are-going-scalable-flamingo.md` + `let-s-go-with-option-greedy-fiddle.md`), then ran a Workflow over all 116 OPEN tech_debt items to: classify each against the blast radius, verify still-open vs live HEAD, synthesize non-overlapping safe work-packages, and adversarially re-verify every "safe" verdict. Result buckets: 3 safe-now code packages (A/B/C), 13 docs-closes, 37 defer-to-Phase-2, 19 operator-only, ~47 skip. Independent grep verification on every load-bearing claim before acting.

## PRs landed (four-part verified)

### PR #363 — docs(tech-debt): currency sweep — 10 grep-verified-resolved stale entries (merge `5f57fef`)

Closed 10 stale open entries covering: session-epoch revocation (never implemented; the session auth model landed differently), custom_domain residual note (addressed in prior sessions), box_client timeout (confirmed present in live code), Mail.app/Check-F retirement (intake_poll is a tombstone; Check F removed with it), prompt-calibration N/A (no prompt confidence calibration built for this path), weekly_send PDF attachment (resolved in the parameterize pass), token-scope validation (resolved), 7 stale feature branches (confirmed gone via `gh`), and keychain TTY-trap (closed by #355 in the prior session). Every close was preceded by a grep or `gh` command confirming the item was verifiably resolved in the live tree.

PR #363 — four-part verify clean — state MERGED / mergedAt 2026-06-30T01:19:48Z / mergeCommit 5f57fefa150128810193530918c7ea4684dd42e9 / main CI on merge commit SUCCESS (run 28413718504, ci)

---

### PR #364 — CLOSED UNMERGED (deliberate; premature) — feat(shared): allowlist-drift Layer-1 email validation

Package A targeted `shared/trusted_contacts.py`: tighten the Layer-1 email-address allowlist check to catch display-name spoofing where the allowlisted domain appears in the friendly-name portion but not the address itself. The adversarial `ops-stds-enforcer` review of the PR was CLEAN — the logic was correct, the tests were sound, and no Invariant 1/2 violations were present.

**Operator flagged the work as premature.** `SHEET_TRUSTED_CONTACTS=0` in ITS_Config; `intake_poll` is a retired tombstone; Email Triage is not yet built. The subsystem this hardens is entirely dormant. Building Layer-1 tightening now means maintaining it for a feature that does not yet exist, with no live signal to validate it against. The right time is when Email Triage is being built. Branch `feat/allowlist-drift-layer1` preserved; the brief and the ops-stds-enforcer verdict will both be available as context when the time comes.

PR #364 — CLOSED UNMERGED (deliberate, premature); branch feat/allowlist-drift-layer1 preserved; not verified.

---

### PR #365 — fix(tests): smartsheet integration-test create→read flake via pytest-rerunfailures (merge `d1bfc8b`)

The `tests/test_smartsheet_client_integration.py` suite flakes on live Smartsheet due to eventual-consistency lag between a `create_row` call and the subsequent `read_rows` returning the new row (1006/404 on fresh rows). The tracked class is known and documented in memory (`smartsheet-integration-tests-flaky`).

Fix: `pytest-rerunfailures` added to `pyproject.toml` dev-dependencies; the integration-test module marked `@pytest.mark.flaky(reruns=3, reruns_delay=2)` at the module level. The decision to use module-level reruns rather than individual-test-level was deliberate — every test in the integration file is susceptible to the same lag, and the module-level mark avoids per-test annotation drift.

**Deliberate non-change:** `smartsheet_client.py` itself was not modified. Adding retry logic in the SDK wrapper would suppress a legitimate production signal — a 404 on a just-created row in production is a genuine error; the rerun logic belongs in the test layer, not the client. This was the key design question surfaced during the session.

**Prove-the-control-bites incident:** the initial PR push ran CI red because `pytest-rerunfailures` was not installed in the CI environment. The dep had been added to `pyproject.toml` but `pip install -e .[dev]` had not been re-run in the worktree venv. Red confirmed the dep was actually wired; green after reinstall confirmed the fix. No synthetic inject-confirm-revert was needed — the CI failure was the organic bite.

PR #365 — four-part verify clean (cumulative-green) — state MERGED / mergedAt 2026-06-30T01:48:49Z / mergeCommit d1bfc8ba2efa448474b55ca0d4e69fe09ddd400b / main CI: the ci run on d1bfc8b was CANCELLED by back-to-back-merge concurrency (#366 landed 26s later); #365's changes are contained in the superseding HEAD 7522cea which has a green ci run (28414772961). Cumulative-green pass.

---

### PR #366 — chore(tech-debt): close stale weekly_send mailbox-cleanup entry (merge `7522cea`)

Package C targeted a stale tech-debt entry proposing a `delete_message` helper for `weekly_send` to clean up the send-confirmation mailbox after each transmission. Investigation showed the premise obsolete: Phase-5 portal transport replaced email-based safety report intake entirely; `weekly_send` is now HELD-only (it does not receive inbound mail to clean up, and the send path does not interact with any inbox). Building `delete_message` would be dead code with no live call site.

The lesson (#1 in the lessons section below) is that checking the premise before writing the code surfaced an unnecessary build — the threat model the entry was addressing no longer applies.

PR #366 — four-part verify clean — state MERGED / mergedAt 2026-06-30T01:49:15Z / mergeCommit 7522cea479b24fd2dd37784626ab0f2a33d13b4d / main CI on merge commit SUCCESS (run 28414772961, ci)

---

### PR #367 — docs(ops): new operator_action_checklist_2026-06-30.md — 19 operator-only items (merge `d51e122`)

The Workflow pass surfaced 19 open tech-debt items classified as "operator-only" — items that require live system access, Keychain credentials, or a human judgment call that cannot be automated or delegated to CI. These cannot be closed by a code PR; they require Seth to run through them directly.

Rather than leaving them scattered in `docs/tech_debt.md` with no clear action surface, a focused operator action checklist was written to `docs/operations/operator_action_checklist_2026-06-30.md`. Each item has: the linked tech_debt entry, the prerequisite condition, the specific command or action to run, and the expected outcome. The date in the filename signals this is a point-in-time operator-work artifact, not a permanent living doc.

PR #367 — four-part verify clean — state MERGED / mergedAt 2026-06-30T01:57:26Z / mergeCommit d51e122d006f58e0a29240db82261199dce8b821 / main CI on merge commit SUCCESS (run 28415061781, ci)

---

### PR #368 — fix(worktree): buggy cp-venv recipe corrected in hook + docs (merge `b91ed8d`)

**The key incident of this session.** The canonical worktree setup recipe in `.claude/hooks/warn-live-daemon-tree.sh` and `docs/operations/worktree_discipline.md` documented:

```
cp -R ~/its/.venv <worktree-dir>/.venv
```

This recipe is silently destructive in the common case: `cp -R` on macOS copies symlinks as symlinks. The `.venv` directory contains symlinks that resolve relative to the source tree (`~/its`). When the Phase-2 worktree ran `cp -R ~/its/.venv`, the resulting `.venv` in the worktree pointed its `python` symlink back to `~/its/.venv/bin/python` — effectively borrowing the live `~/its/.venv` rather than producing an isolated copy. The live daemon-tree venv was then silently "borrowed" by the Phase-2 session's editable install, which could have produced import shadowing across both sessions.

The failure was caught via an isolation assertion during Package-A worktree setup: the worktree venv's `site-packages` resolved to the live tree's directory, not the worktree's. The live `~/its/.venv` was restored immediately by removing the worktree venv and switching to:

```
python -m venv <worktree-dir>/.venv
pip install -e ~/its --no-deps
```

which produces a genuinely isolated venv with no cross-tree symlink dependency.

PR #368 fixes the recipe at the source in both the hook and the docs, and updates the `reference_worktree-venv-for-python-source-edits` memory entry to call out the `cp -R` footgun explicitly. This PR was pulled into the live `~/its` tree immediately at session close so the corrected hook is in effect for future worktree operations.

PR #368 — four-part verify clean — state MERGED / mergedAt 2026-06-30T02:05:12Z / mergeCommit b91ed8dd08d37aca9b43b225ec1625e4e3be4b11 / main CI on merge commit SUCCESS (run 28415351001, ci)

## CI / four-part verify

There is no single representative local full-suite run for this session. Changes landed across 5 separate PRs, each CI-verified independently. The `test` job (pytest + mypy + ruff) passed green on every merged PR. `~/its/.venv` was borrowed by the Phase-2 worktree during part of the session, so a local `~/its` suite run at session close would not reflect main. Verification is the per-PR four-part-clean block above.

For reference, the CI gate line observed on the final merge commit (`b91ed8d`, PR #368):
- pytest: passed (count consistent with prior #367 merge — no test removals)
- mypy: 0 errors
- ruff: clean
- main-branch CI on merge commit `b91ed8dd`: SUCCESS

## Decisions made during session

1. **Blast-radius-first scoping.** Before touching any tech-debt item, both active Phase-2 plan files were read and a blast-radius boundary was drawn. Any item that touched a file, module, or subsystem in the Phase-2 plan was deferred immediately, regardless of apparent safety. Alternative considered: proceed item-by-item and evaluate each for overlap. Rejected — the batch-map approach cost 5 minutes and made the boundary visible and inspectable; per-item evaluation would have relied on accurate memory of the Phase-2 plan, which is exactly the kind of claim the memory-vs-live-HEAD discipline says to distrust.

2. **Grep every "still-open" claim before acting.** Every tech-debt closure in #363 was preceded by a grep or `gh` command verifying the item's current state against live HEAD. Several items that appeared obviously resolved in memory required one grep to confirm; one (the custom_domain residual) required two to pin the right surface. Alternative: trust the tech-debt description and the memory entries. Rejected — the #363 pass itself found that at least 2 entries were written with stale premises; grep cost was negligible.

3. **Don't harden a dormant subsystem (Package A closed unmerged).** Package A's allowlist-drift fix was correct code on an inert subsystem. The operator's call: close unmerged, preserve branch, revisit when Email Triage is being built. Alternative considered: merge anyway — the code is correct and future-proofs the surface. Rejected — correct-but-unused code creates a maintenance burden (every future refactor must account for it) and, more importantly, provides false confidence that the subsystem's security posture has been addressed when the thing that actually needs addressing is Email Triage itself.

4. **Flaky-test reruns in the test layer, not the client (Package B).** `pytest-rerunfailures` at the module level rather than retry-on-404 in `smartsheet_client.py`. Rationale: the client is production code; a 404 on a just-created row in production is a signal, not noise. Hiding it behind a retry loop would suppress a class of error that the watchdog and error_log are designed to surface. The flakiness is an integration-test artifact of Smartsheet's eventual-consistency model, not a client defect.

5. **Operator checklist doc rather than bulk-close (#367).** The 19 operator-only items were not closed in `docs/tech_debt.md` — they were gathered into a point-in-time checklist doc. Alternative: mark them BLOCKED or DEFERRED in tech_debt. Rejected — BLOCKED/DEFERRED entries carry no action surface; a checklist doc gives Seth a runnable list with prerequisite conditions spelled out. The tech-debt entries remain OPEN until Seth confirms each is done.

6. **Pull #368 into live `~/its` immediately at session close.** The corrected hook needed to be live for the current Phase-2 session running in parallel. Alternative: leave the pull for the next scheduled maintenance window. Rejected — the Phase-2 session could spin up another worktree using the buggy recipe before the fix landed.

## Open items / next session

- **19 operator-only checklist items** in `docs/operations/operator_action_checklist_2026-06-30.md` — Seth to run through these when the Phase-2 blast radius clears. Each has a specific command and expected outcome.

- **Email Triage build (future)** — when Email Triage is being built, `feat/allowlist-drift-layer1` (branch preserved) is the Package-A implementation ready for integration. The `ops-stds-enforcer` review was clean; no re-review needed unless the branch has drifted.

- **37 items deferred to Phase-2** — these were not forgotten; they were classified as Phase-2-overlap and left in `docs/tech_debt.md`. They surface naturally when Phase-2 touches the relevant modules.

- **Phase-2 (job-tracker pivot, P2.5)** — the in-flight Phase-2 session running alongside this one is the immediate next active work. This session's only Phase-2 interaction was the venv isolation incident (now fixed by #368).

## What was NOT touched

- **No Phase-2 plan files, D1 migrations, or Worker code.** The blast-radius boundary held throughout. Every file edited in this session was outside the Phase-2 scope.
- **No `shared/trusted_contacts.py` or any Email Triage surface.** Package A closed unmerged; the file is unchanged from main.
- **No live daemon behavior changed.** #368 corrects a hook recipe in documentation and the pre-tool-use hook script; it does not affect any launchd daemon's runtime behavior.
- **No `docs/tech_debt.md` entries marked resolved except the 10 verified-closed in #363.** The 37 deferred and 19 operator-only items remain OPEN.
- **No blueprint doctrine files.** This session is execution-repo only.

## Lessons captured to memory

- **`feedback_dont-harden-dormant-subsystems`** (new) — if the subsystem the fix targets is entirely dormant (no live callers, feature not yet built), the right action is to close the branch and revisit at feature-build time. Correct-but-unused hardening is a maintenance liability and provides false assurance. (Captured from Package A closure.)

- **`reference_worktree-venv-for-python-source-edits`** (updated) — `cp -R .venv` is the wrong recipe: macOS copies symlinks-as-symlinks, which silently points the worktree venv back to the live tree's interpreter. Use `python -m venv <worktree>/.venv && pip install -e ~/its --no-deps` instead. The corrected recipe is now also in `.claude/hooks/warn-live-daemon-tree.sh` and `docs/operations/worktree_discipline.md` (PR #368).

## Cross-references

- Worktree discipline (corrected): `docs/operations/worktree_discipline.md`
- Operator action checklist: `docs/operations/operator_action_checklist_2026-06-30.md`
- PR merge discipline: `docs/operations/pr_merge_discipline.md`
- Preserved branch: `feat/allowlist-drift-layer1` (Package A, Email Triage future)
- Concurrent Phase-2 session log: `docs/session_logs/2026-06-30_stage1-parameterize-complete.md`
- Memory entries updated: `feedback_dont-harden-dormant-subsystems` (new); `reference_worktree-venv-for-python-source-edits` (updated)
- Tech-debt: `docs/tech_debt.md` — 10 entries closed (#363); 19 OPEN items surfaced into operator checklist (#367); 37 Phase-2-deferred left OPEN
