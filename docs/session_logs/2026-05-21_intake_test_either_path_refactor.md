# 2026-05-21 — Intake-test either-path refactor

PR: [#58](https://github.com/SolutionSmith-debug/its/pull/58) — squash-merged at 2026-05-21T17:57:14Z. Merge commit `8afb66aeff4a731c32128a037e21913334e97fa1`. Three-assertion verify clean.

Fifth PR of the day. Closes the over-specificity gap surfaced when the post-PR-#57 live smoke ran for the first time against a Keychain that now had `ITS_ANTHROPIC_KEY` seeded.

## What landed

`tests/test_intake_integration.py` refactored:

1. **XOR assertion across both possible routing paths** — Daily Reports row by marker OR `ITS_Review_Queue` row by Source File. Exactly one must be present. Both → duplicated-write bug; neither → silently-dropped message.
2. **Per-run print line** surfacing which path fired and (for review-queue) the Reason.
3. **Defensive cleanup in finally for all three sheets** — Daily Reports + ITS_Review_Queue + ITS_Quarantine (quarantine for symmetry — no current test path leads there, but future tests covering the quarantine branch get the cleanup discipline pre-pinned).
4. **No changes to intake.py code, confidence threshold, or synthetic email content** — the pipeline works; the test contract was the only thing that needed updating.

## What surfaced the gap

PR #57's session log noted that the integration test skipped at that PR's local run because `ITS_ANTHROPIC_KEY` wasn't yet in Keychain. Between PR #57 close and the start of this session, the key got seeded (operator action). The first live run that actually exercised the full pipeline returned `confidence=0.72` on the synthetic input — exactly under the 0.75 threshold — and routed correctly to ITS_Review_Queue with `Reason=low-confidence-extraction`. The test's happy-path-only assertion then failed, despite the pipeline behaving perfectly.

Important meta-lesson: an integration test that's never been run against the live target is essentially uncalibrated. PR #57 shipped a test that COULD pass on the happy path under permissive model output but would fail on cautious model output. The real-world confidence distribution wasn't visible until the live run. Future integration tests for AI-classifier pipelines should default to XOR-across-paths rather than single-happy-path assertions, by construction.

## Verification

Live `pytest -m integration tests/test_intake_integration.py -v -s` ran twice on the refactor branch; both passed:

  - Run 1: `[intake-test] Path: ITS_Review_Queue row (row_id=3891987700711300) — gate routed (Reason='low-confidence-extraction')`
  - Run 2: `[intake-test] Path: ITS_Review_Queue row (row_id=4783860208304004) — gate routed (Reason='low-confidence-extraction')`

Distinct row IDs across runs prove the in-finally cleanup is working — otherwise run 2 would have found run 1's orphan row alongside its own. Post-run inspection of `ITS_Review_Queue.Workstream=safety_reports`: zero rows. Cleanup is bulletproof.

Baseline unchanged from PR #57 close: `pytest 722/1/7`, `mypy 0/91`, `ruff` clean. This PR touches only the deselected integration test file.

## Non-obvious decisions

- **Used `Source File` column as the ITS_Review_Queue primary key.** intake.py writes `email_path` (the absolute path to the .eml) as `source_file` on every review-queue call (`safety_reports/intake.py:855` area). pytest's `tmp_path` includes a unique UUID per test invocation, so the source-file value is a clean per-run identifier. Cleaner than searching the Payload JSON for the marker.
- **Defensive cleanup runs in finally regardless of which path fired.** The test could fail mid-way (XOR assertion, e.g., if a future intake.py bug produced both rows); the cleanup needs to handle that case AND the "passed" case AND the "main() raised partway" case. Search-and-delete in finally covers all three.
- **Quarantine cleanup is symmetry, not necessity.** The current test allowlists the sandbox sender in setUp so quarantine never fires. But the search-and-delete is cheap and the symmetry across the 3 possible sheets pins the cleanup discipline. If a future test exercises the quarantine branch, the cleanup is already in place.
- **Did NOT lower the confidence threshold for the test.** The 0.75 default is operationally meaningful; bypassing it via test-only patching dodges the test of the actual config. The test now validates the contract that intake.py owes regardless of threshold value.
- **Did NOT modify the synthetic email content** to coax higher confidence. That would be testing the model's classification behavior rather than the pipeline's routing behavior. The pipeline correctly routed a borderline case to the review queue — that IS the test we want.

## Baseline state at session close

- Main at `8afb66a` (PR #58 merge commit) — wait, this is wrong, let me get the actual HEAD.

  Actually `8afb66a` IS the merge commit; HEAD after this session log lands will be one more commit ahead.

- pytest 722 / 1 / 7. mypy 0/91. ruff clean.
- Integration test now self-cleans and accepts either routing path.
- R3 session 1 fully closed. R3 session 2 (`weekly_generate.py`) ready to start.
