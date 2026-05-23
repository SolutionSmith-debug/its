# PR Merge Discipline

Canonical verification protocol for landing a PR on `main`. Codified
2026-05-23 (PR #74) after six consecutive PRs (#68 / #69 / #70 / #71 /
#72 / #73) landed with the `push: main` workflow red — the existing
three-assertion verify only inspected `pull_request`-attached checks
(green) and missed the main-branch CI failure (red since Run #229).

## Four-part landed verification

After `gh pr merge --squash --delete-branch`, immediately:

### 1. PR-state triplet

```bash
gh pr view <num> --json mergedAt,mergeCommit,state
```

Assert all three:
- `state == "MERGED"`
- `mergedAt` non-null
- `mergeCommit.oid` present

This is the original PR #34 ghost-prevention check — proves the merge
commit landed (not just that the PR was marked MERGED in a state where
the underlying commit is missing).

### 2. Capture merge commit SHA

```bash
MERGE_SHA=$(gh pr view <num> --json mergeCommit --jq '.mergeCommit.oid')
```

### 3. Wait for the `push: main` workflow run on the merge commit

The PR-attached checks (visible via `gh pr view --json statusCheckRollup`)
are `pull_request`-triggered. They run against the merge PREVIEW commit,
not against `main` post-merge. After the PR merges, a SEPARATE workflow
run fires on `push: main` against the merge commit. That run is what
catches "PR's tests pass against the PR's commit but fail on the
post-merge `main` state."

```bash
until gh run list --branch main --commit "$MERGE_SHA" \
    --json status --jq '[.[].status] | all(. == "completed")' | grep -q true; do
  sleep 30
done
```

### 4. Verify all main-branch runs concluded as success

```bash
gh run list --branch main --commit "$MERGE_SHA" \
    --json conclusion --jq '[.[].conclusion] | all(. == "success")' | grep -q true \
  || { echo "ERROR: post-merge main CI failed on $MERGE_SHA"; \
       gh run list --branch main --commit "$MERGE_SHA" --json databaseId,name,conclusion; \
       exit 1; }
```

## "Functionally not landed" framing

A PR that passes step 1 but fails step 4 is **functionally not landed**
— the merge commit reached `main` but `main`-branch CI is red. Treat as
the analogue of PR #34's ghost case: the GitHub PR-state says MERGED but
the operational effect (green main) didn't land. Surface immediately; do
not record the PR as landed in memory or session log until step 4
passes.

If post-merge CI fails and root-cause analysis shows the failure is
**pre-existing** (this PR didn't introduce it but inherited red main),
still treat as not-landed for discipline purposes. The fix is either:

- Revert this PR until `main` is restored, OR
- Land a CI-fix PR first and then re-land this PR's changes.

"Inherit and propagate" is NOT an acceptable resolution. The PRs that
landed under that framing (PR #68 through PR #73) are the historical
precedent this discipline retires.

## Session log line

Every session log that records a merged PR includes a line for the
four-part check:

```
- pytest: <N> passed / <M> skipped / <D> deselected
- mypy: <E> errors / <F> source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS  ← the new fourth line
```

If the fourth line is anything other than SUCCESS, the session log
records the PR as "merged but not landed" and the next action is the
CI fix, not the next deliverable.

## Retroactive verification at PR #73 (proof of discipline)

Run on 2026-05-23 against PR #73's merge commit `06337bd`:

```
PR #73 merge SHA: 06337bd2b78f066f09d83737f5b240e8516ad4d3
[{"conclusion":"failure","databaseId":26335962586,"name":"ci","status":"completed"}]
```

The fourth-part check correctly returns failure on the existing red
state. Captured here as the proof-of-discipline reference; after this
PR (the CI fix) lands and `main` goes green, future merges are
correct-by-construction.
