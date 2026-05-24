# Session log — 2026-05-20 person_tag regex refinement (redo)

## Purpose

Re-do of [PR #34](https://github.com/SolutionSmith-debug/its/pull/34), which
implemented Direction (A) from `docs/audits/person_tag_audit_2026-05-19.md` — removing
the third alternation (`-\s*[A-Z][a-z]+\s*$`, "trailing-capitalized-word after
dash") from `PERSON_TAG_IN_SUBJECT` in `box_migration/parse_job_v3.py`. PR #34
shipped a working diff (27 tests passed, CI was green), but was closed-without-
merge during a 2026-05-20 branch-cleanup pass that deleted its head branch
before verifying the squash-merge had actually landed. The work itself was
sound; only the merge discipline was missed. This session redoes the work as a
fresh PR with explicit `gh pr view --json mergedAt` verification at the end
(Phase 7 of the redo brief).

Closes the only Low-severity open item from the Foundation Scaffold Update v6
queue.

## Pre-flight findings (Op Stds v9 §13)

Six items swept per the redo brief's Phase 1 + Phase 5. None blocked work; two
worth durable mention.

1. **Live regex still matches the audit doc verbatim** at
   `box_migration/parse_job_v3.py:642-646`. No drift since 2026-05-19; PR #34's
   close-without-merge meant the regex stayed in its pre-refinement form.

2. **`tech_debt.md` person_tag entry was still `[OPEN]`** per design — PR #37
   (the post-R2-Session-2 chore PR) explicitly preserved that status after the
   PR #34 close-without-merge incident. The body of the previous OPEN entry
   has been replaced by the CLOSED resolution stanza in this PR.

3. **No prior `tests/test_person_tag.py` or `tests/test_parse_job_v3.py`** —
   clean slate. PR #34's tests never landed on main, so the new test file in
   this PR is the first test_person_tag.py to actually merge.

4. **Consumption path is chaos-flag-only.** `detect_chaos` in
   `parse_job_v3.py:696` calls `PERSON_TAG_IN_SUBJECT.search(name)` and reads
   only `m.group(0)` for the `ChaosFlag.match` field. No `m.group(N)` group-
   index dependency anywhere; safe to drop alt 3.

5. **`pyproject.toml`** per-file-ignores for `box_migration/*` need no change —
   not adding new import patterns.

6. **The original PR #34 commit `ed94f42` is still in reflog**
   (`HEAD@{17}` as of session start). Per redo-brief Phase 5 Q2's default
   ("re-execute fresh"), I did NOT cherry-pick; the redo PR is a clean fresh
   commit. The reflog SHA remains as a safety net if the redo had hit any
   blocker requiring rollback. The decision rationale: re-executing gives a
   cleaner narrative for this session log + the `_redo` suffix on the log
   path; cherry-picking would have required renaming the bundled session log
   anyway, for the same overall effort.

7. **PR #37 merge confirmation (Phase 0)** completed before any work on this
   branch: `gh pr merge 37 --squash --delete-branch` then `gh pr view 37
   --json mergedAt,mergeCommit,state` returned
   `{"state":"MERGED","mergedAt":"2026-05-20T17:41:46Z","mergeCommit":{"oid":"a691691…"}}`
   — discipline (the corrective for the PR #34 failure mode) honored.

## Code change

Single 5-LOC edit in `box_migration/parse_job_v3.py`:

```python
PERSON_TAG_IN_SUBJECT = re.compile(
    r'(\bfor\s+[A-Z]{3,}\b|'                            # "for ZACK"
    r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b)'  # "Teala Organize folder"
)
```

Plus a 4-line comment update above the regex pointing future maintainers at
the audit doc + this closure for the rationale. No changes to `detect_chaos`,
the test corpus, or any other regex. Same diff PR #34 had.

## Test coverage

`tests/test_person_tag.py` (new), 27 tests across three groups + a small
consumer-path integration. All pass on first run (0.02s).

| Group | Count | Purpose |
|---|---:|---|
| A — alt 1 positive regression  | 3 | `for ZACK` and two boundary-case 3-cap forms. |
| A — alt 2 positive regression  | 4 | Every allowlist verb: Organize, Cleanup, Notes, Files. |
| B — audit FP negative locks    | 13 | Every confirmed FP from `docs/audits/person_tag_audit_2026-05-19.md` rows #1–#12 + sample #19. Prevents accidental reintroduction of alt 3. |
| C — known TP loss acceptance   | 5 | `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` list, cross-referenced to audit doc samples #15–#20. Locks the "we accept these as a tradeoff" decision so a future maintainer must read the audit before re-adding alt 3. |
| Consumer-path integration      | 2 | `detect_chaos()` emits flag for a TP, skips it for `9. Utility-Documents-Tracking` (most-common audit FP shape). |

Pytest count: **470 → 497**, 2 skips unchanged.

## Tech_debt closure

`docs/tech_debt.md` entry moved `[OPEN]` → `[CLOSED 2026-05-20]`. Resolution
stanza opens with the standard "Resolved by adopting Direction (A)…" pattern
matching the file's other 2026-05-19 closures (V/S vendor-sub, ISO date
prefix). Stanza explicitly documents the redo history (PR #34 close-without-
merge → PR #37 OPEN preservation → this PR closes it for real) so a future
audit doesn't reconstruct that timeline from scratch.

`docs/audits/person_tag_audit_2026-05-19.md` **not modified** per redo-brief anti-
pattern §2 — it remains historical context for the decision.

## Decisions made during session

Beyond the pre-locked Direction A choice and the PR #37-merge-first
sequencing:

- **Re-execute over cherry-pick.** `ed94f42` still in reflog; could have
  used `git cherry-pick`. Chose re-execute per brief default — cleaner
  narrative, fresh commit timestamp/author, and the bundled session log
  needed a new `_redo` suffix anyway. Reflog reference noted in pre-flight
  finding #6 as a safety net rather than an executed path.

- **Group C as acceptance lock with cross-reference comment.** Matches
  PR #34's design verbatim. The `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` name
  + the docstring `DO NOT "fix" by reintroducing alt 3 — see audit doc for
  the FP cost analysis` together make the failure mode visible at the
  test-name level, not just the assert message.

- **Consumer-path integration tests (2 e2e on `detect_chaos`)** added
  beyond the three regex groups. Brief targeted ~27; I landed exactly
  that count by structuring as 7 + 13 + 5 + 2.

- **Comment update beside the regex** (4 lines) bounded to the lines
  directly describing the edited regex. Preservation-over-refactor §14
  honored: no structural change to surrounding code, no helper extraction,
  no nearby cleanup.

- **Coverage delta stated as projection, not measurement.** Same caveat
  as PR #34: `~/Downloads/Box_listings_for_Seth/` not present locally;
  the 138 → ~2–4 figure is a projection from the 2026-05-19 audit. If a
  fresh reconcile run is wanted, regenerate listings and re-run
  `box_migration/reconcile_box_listings.py`.

- **Merge discipline applied at both PR boundaries this session** (PR #37
  Phase 0, this redo PR Phase 7). The corrective for the PR #34 failure
  mode is now established as a workflow precedent per the redo brief.

## Verification

- `ruff check .` — clean.
- `mypy .` — 0 errors across 68 source files (Op Stds v9 §28 baseline;
  was 67 post-PR-#37; +1 is `tests/test_person_tag.py`, the only new
  Python file this PR adds).
- `pytest -q` — **497 passed, 2 skipped** (was 470 post-PR-#37 merge;
  delta +27 matches the new tests).

## Out-of-scope notes (per redo brief anti-patterns)

Honored every "do not" in the brief:

1. Directions B (allowlist) and C (INFO severity) not implemented —
   remain documented in the audit doc as alternatives-not-taken.
2. `docs/audits/person_tag_audit_2026-05-19.md` not modified.
3. First two alternations unchanged.
4. Consumer downstream unchanged.
5. No helper extraction or surrounding refactor.
6. `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` acceptance lock present with
   audit-doc cross-reference comment.
7. No other regexes in `parse_job_v3.py` modified.
8. No bare `except` added.
9. PR #37 merge step in Phase 0 honored, with explicit verification.
10. Phase 7 merge verification will be honored at the end of this session.

## Sequencing context

- Independent of: Box Layer 2 JWT wait (Daniel's permission grant), Q2
  alert-routing dedupe brief, V&R v7 Managed Agents Phase 3+ amendment.
- Lands after: PR #37 (post-R2-Session-2 doc cleanup) at `a691691`,
  merged this session per the redo brief's Phase 0.
- Closes the only Low-severity open item from Foundation Scaffold v6 open
  queue. No carry-forward expected.
- Op Stds v9 invariants honored: §13 verify-before-fix (6-item Phase 1
  + 5-item Phase 5 sweep); §14 preservation-over-refactor (no helper
  extraction, comment touch bounded); §28 mypy-baseline-0 (unchanged).
- **Workflow precedent set:** the cc self-merge + verify-mergedAt pattern
  is the corrective for the PR #34 failure mode and is established as
  this session's contribution to repo discipline.
