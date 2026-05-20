# Session log — 2026-05-20 person_tag regex refinement

## Purpose

Implement Direction (A) from `docs/person_tag_audit_2026-05-19.md`: remove
the third alternation (`-\s*[A-Z][a-z]+\s*$`, "trailing-capitalized-word
after dash") from `PERSON_TAG_IN_SUBJECT` in `box_migration/parse_job_v3.py`,
add the test coverage the audit doc spelled out, and close the only Low-
severity open item in the Foundation Scaffold Update v6 queue. Follow-on to
PR #30 (2026-05-19 audit) and PR #33 (R2 watchdog Session 1, cc0f191) — both
landed cleanly under the same brief-discipline pattern.

## Pre-flight findings (Op Stds v9 §13)

Eight items surfaced during the verify-before-fix sweep. None blocked work.

1. **Live regex matches the audit doc verbatim.** `box_migration/parse_job_v3.py`
   lines 642–646 quote the same three-alternation form as
   `docs/person_tag_audit_2026-05-19.md` §"The current regex" and the open
   `docs/tech_debt.md` entry. No drift since the audit.

2. **No prior test file for `PERSON_TAG_IN_SUBJECT`.** `grep -rn
   "PERSON_TAG_IN_SUBJECT\|person_tag" tests/ box_migration/` returned only
   the three lines inside `parse_job_v3.py` (declaration + `detect_chaos`
   consumer + ChaosFlag name). Created `tests/test_person_tag.py` new,
   following the sys.path.insert pattern from `tests/test_parse_vendor_sub.py`
   (which itself mirrors `test_parse_subsubject.py`; `box_migration/` is not
   a package).

3. **Consumer path is chaos-flag-only.** `detect_chaos` in `parse_job_v3.py`
   calls `PERSON_TAG_IN_SUBJECT.search(name)` and reads only `m.group(0)` for
   the `ChaosFlag.match` field. No `m.group(N)` group-index dependency
   anywhere, so removing one alternation has no downstream ripple. The brief's
   "if structurally consumed, surface before changing" check passes
   negatively — the audit's "chaos-flag-only" characterization holds.

4. **`tech_debt.md` closure conventions** identified from the two
   2026-05-19 closes (V/S vendor-sub and ISO date prefix entries): heading
   `## <name> [CLOSED YYYY-MM-DD]`, body opens with "Resolved by…",
   coverage delta cited, tests-file path called out, final `Resolution: see
   commit on the `<branch>` branch (squash-merged), and `docs/session_logs/
   <slug>.md`.` Matched style verbatim.

5. **`pyproject.toml` per-file-ignores** for `box_migration/*` need no
   change — `["I001", "F401", "UP042", "UP045"]` is unrelated to the
   import set this PR touches (no new imports added).

6. **Stale comment beside the regex.** Lines 640–641 originally said
   `# Specifically catches "for ZACK", "Teala <something>", "<something>- Jason"`
   — the third example references the removed alternation. Updated for
   consistency (brief §1 explicitly authorized this nearby touch).

7. **Reconcile re-measurement (brief Q4) not feasible locally.** The
   reconcile harness reads from `~/Downloads/Box_listings_for_Seth/`
   (deliberately outside the repo per the operator's "don't commit customer
   portfolio names into git" decision documented in
   `box_migration/reconcile_box_listings.py:42-44`). The directory isn't
   present on the cutover MacBook session. The "138 → ~2–4" delta is
   captured in the tech_debt closure as a projection from the 2026-05-19
   audit; if Customer 2 onboarding wants a fresh measurement, regenerate
   listings and re-run the reconcile.

8. **`ACTIVE_PRODUCTION_CORPUS` references** to person-tag cases at
   `parse_job_v3.py:974-975` (`Teala Organize folder` and
   `11. EPC Contract Redlines for ZACK`) are alt-2 / alt-1 cases — both
   still match. Not affected by the change. No corpus edits needed.

## Code change

Single 5-LOC edit in `box_migration/parse_job_v3.py`:

```python
PERSON_TAG_IN_SUBJECT = re.compile(
    r'(\bfor\s+[A-Z]{3,}\b|'                            # "for ZACK"
    r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b)'  # "Teala Organize folder"
)
```

Plus a 4-line comment update above the regex pointing future maintainers
at the audit doc + this closure for the rationale. No changes to
`detect_chaos`, the corpus, or any other regex.

## Test coverage

`tests/test_person_tag.py` (new), 27 tests across three groups + a small
consumer-path integration. All pass on first run (0.03s).

| Group | Count | Purpose |
|---|---:|---|
| A — alt 1 positive regression  | 3 | `for ZACK` and two boundary-case 3-cap forms. |
| A — alt 2 positive regression  | 4 | Every allowlist verb: Organize, Cleanup, Notes, Files. |
| B — audit FP negative locks    | 13 | Every confirmed FP from `docs/person_tag_audit_2026-05-19.md` rows #1–#12 + sample #19. Prevents accidental reintroduction of alt 3. |
| C — known TP loss acceptance   | 5 | `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` list, cross-referenced to audit doc samples #15–#20. Locks the "we accept these as a tradeoff" decision so a future maintainer must read the audit before re-adding alt 3. |
| Consumer-path integration      | 2 | `detect_chaos()` emits flag for a TP, skips it for `9. Utility-Documents-Tracking` (most-common audit FP shape). |

Pytest count: **402 → 429**, 2 skips unchanged. Delta matches estimate
(brief said ~18–22; +27 reflects explicit coverage of all four alt-2 verbs
and all 13 audit FPs rather than a representative sample).

## Tech_debt closure

`docs/tech_debt.md` entry `parse_job_v3: person_tag_in_subject chaos
over-match` moved `[OPEN]` → `[CLOSED 2026-05-20]`. Resolution stanza
matches the file's existing convention: opens with "Resolved by…",
includes a code snippet of the refined regex, cites coverage delta as
a projection (with the "listings not present locally" caveat), enumerates
the 27 new tests by group, ends with the standard `Resolution: see commit
on the <branch> branch (squash-merged), and docs/session_logs/<slug>.md`
sentence.

`docs/person_tag_audit_2026-05-19.md` itself **not modified** per brief
anti-pattern §2 — it remains historical context for the decision.

## Decisions made during session

Beyond the pre-locked Direction A choice in the brief:

- **Group C as acceptance lock, not just absent test.** Brief specified
  `KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` as a named list with a cross-
  referenced comment block. The lock is structural — a future PR that
  "fixes the missing coverage" by reintroducing alt 3 will also fail
  Group B, but the explicit Group C test name surfaces the audit context
  in the failure message itself. Without Group C, the failure looks
  accidental; with it, the audit doc reference is in the assert message
  ("see docs/person_tag_audit_2026-05-19.md"). Worth the 5 tests.

- **Consumer-path integration tests (2 e2e on `detect_chaos`)** added
  beyond the brief's three groups. The brief targeted ~18–22 regex-level
  tests; I added two end-to-end checks against `detect_chaos()` to lock
  the consumer contract that `m.group(0)` flows into `ChaosFlag.match`.
  Cheap, and the integration is the actual behavior the operator sees.

- **Comment update beside the regex (brief §1 authorized).** Original
  comment listed `<something>- Jason` as an example of what the regex
  catches — that example references the removed alt 3. Rewrote to point
  at the audit doc + tech_debt closure for the rationale. Preservation-
  over-refactor §14 still honored: no structural change to surrounding
  code; comment-only update bounded to the lines directly describing the
  edited regex.

- **Coverage delta stated as projection, not measurement.** The brief's
  Q4 allowed re-measuring against current fixtures if cheap. Listings
  aren't present locally (see pre-flight finding 7); rather than
  fabricating a measurement, the tech_debt closure says "projection from
  the 2026-05-19 audit" with the caveat. Lower-confidence number, higher-
  confidence honesty.

## Verification

- `ruff check .` — clean.
- `mypy .` — 0 errors (per Op Stds v9 §28 baseline; no source-file delta
  beyond a comment + regex character-class shrink).
- `pytest -q` — **429 passed, 2 skipped** (was 402 + 27 = 429; matches).

## Out-of-scope notes (per brief anti-patterns)

Honored every "do not" in the brief:

1. **Direction B (allowlist refinement) and C (INFO severity)** not
   implemented. Both remain documented in `docs/person_tag_audit_2026-05-19.md`
   as alternatives-not-taken.
2. **`docs/person_tag_audit_2026-05-19.md` not modified.**
3. **First two alternations not changed** — kept verbatim. No sharpening
   opportunity surfaced during the work that would justify scope expansion.
4. **No downstream consumer change.** `detect_chaos` and every caller
   untouched. Pattern-narrowing, not behavior-change.
5. **No helper extraction or surrounding-code refactor.** Preservation-
   over-refactor §14 honored — still at one real case for this regex,
   not the §14 ≥4 threshold.
6. **`KNOWN_TP_LOSSES_NO_LONGER_FLAGGED` acceptance lock present** with
   audit-doc cross-reference comment.
7. **No other regexes in `parse_job_v3.py` modified** even though similar
   over-match patterns might exist elsewhere. No observations to surface;
   nothing else looked structurally similar during the targeted read of
   lines 600–700.
8. **No bare-except added.** No exception handling changes at all (no
   regex compilation can fail at this character-class level; if it did,
   import-time `re.compile()` would fail loud).

## Sequencing context

- Independent of: Box Layer 2 JWT wait (Daniel's permission grant), R2
  Session 2 planning (Checks C/D/E/F), alert-routing dedupe brief.
- Closes the only Low-severity open item from Foundation Scaffold Update
  v6 queue (person_tag_in_subject over-match). No carry-forward.
- Follows PR #33 (R2 Session 1 watchdog, merged cc0f191) cleanly on main;
  no rebase conflicts expected.
- Op Stds v9 invariants honored: §13 verify-before-fix (8-item pre-flight
  swept all brief assumptions), §14 preservation-over-refactor (no helper
  extraction, comment touch bounded), §28 mypy-baseline-0 (unchanged).
