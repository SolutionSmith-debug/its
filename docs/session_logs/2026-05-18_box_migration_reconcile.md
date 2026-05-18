# 2026-05-18 — box_migration reconcile + parse_subsubject

Reconciliation pass over `box_migration/parse_job_v3.py` against the live
10-portfolio Box folder export the operator received this afternoon
(`~/Downloads/Box_listings_for_Seth/folders__*.txt`, 5,471 unique folder-name
strings across 10 portfolios, ~48,594 path lines total).

Deliverable per the original brief: coverage report + parser patches for
identified gaps. This PR ships:

- A reproducible reconcile harness (`box_migration/reconcile_box_listings.py`).
- A new entry point in v3 (`parse_subsubject` + `SubsubjectParse`) to
  recognize a real folder-organizing taxonomy the original v3 deep-dive missed.
- 27 unit tests covering positive matches, negative-collision avoidance
  against existing parsers, and the `\d{1,2}` boundary cap.
- Full per-portfolio coverage report at
  `docs/reports/2026-05-18_reconcile_full.md` (928 lines).
- Two new `docs/tech_debt.md` entries for patterns surfaced during the
  post-fix sanity check but deliberately deferred (see "Sanity check
  findings" below).

## Coverage delta

Reconcile was run twice — before and after the parser patch — against
the same 5,471 unique-name input set:

| Claim                | Before | After  | Delta |
|----------------------|-------:|-------:|------:|
| active_subjob        | 985    | 985    | —     |
| portfolio_subject    | 51     | 51     | —     |
| development_subject  | 15     | 15     | —     |
| **subsubject (new)** | —      | **471**| **+471** |
| canonical_non_job    | 912    | 912    | —     |
| identifiable_job     | 31     | 31     | —     |
| unclaimed            | 3,477  | 3,006  | −471  |

**Unclaimed share: 63.6% → 54.9% (−8.7 pts).** All 471 names moved from
unclaimed cleanly into the new `subsubject` claim — no collateral
movement between other claims, confirming the new patterns don't
interact with the existing 5 sub-job formats, the canonical-subject
recognition, or the schema classifier.

## Strategic findings

These are the substantive observations from the reconcile, beyond the
numeric delta. They drive what comes next outside this repo.

### 1. `subsubject` is concentrated in subjects 5 (Engineering) and 8 (Permitting)

Of the 471 claimed names, the vast majority sit under `5. Engineering`
or `8. Permitting` parent subjects. Portfolios that use the N.M layer
under those parents — KSI 4 IL, Forefront, Almon, Steger & Roxbury,
Keystone — show movement in the 45–82 range. Portfolios that do NOT
use the layer show movement of 0–10:

- Bonacci 1&2 (single-project schema): 6 claims. Single-project layouts
  don't have the elaborate sub-subject taxonomy.
- Dolphin and Shoestring (development-phase schema): 10 claims. Dev-phase
  taxonomy uses different organizational primitives.

Distribution matches the schema model — confirming the patch is
schema-aware in the sense that it claims things where they exist, and
appropriately produces nothing where they don't.

### 2. N.M and N.M.K shift the Box ↔ Smartsheet Reconciliation v1 questions

The Reconciliation v1 analysis in the planning project bounded itself
at the 12 top-level subjects and implicitly treated them as leaves.
They are not — the N.M / N.M.K layer is real organizational content
inside several of those subjects.

Two of the four deferred Reconciliation v1 questions shift as a result:

- **Gap-fillers** ("which top-level subjects have no Smartsheet
  representation?"). Some of those "no representation" subjects already
  have internal taxonomy in Box that should inform the gap-filler
  design. They're not blank slates; they're under-modeled.
- **Parent rollup** ("how do sub-folders roll up into a parent subject
  for reporting?"). Rollup needs to decide whether sub-subjects roll up
  into their N. parent or whether they're independently addressable
  Smartsheet rows. The decision touches Permitting (8.x) and Engineering
  (5.x) most — exactly the subjects with the deepest sub-subject trees.

Carry-over: Box ↔ Smartsheet Reconciliation v1 addendum in the planning
project. Not this repo.

### 3. `Na.` is recurring customer-specific taxonomy, not chaos

`1a. Lum Review of IFC ELEC Drawings` appears in 6 portfolios under
`12. Portfolio Closeout/2. SU&C Completion (K1)/5. Redline & As-Built
Drawings/1. ELEC/`. That's not a one-off — it's a customer-specific
sub-layer that exists in the canonical Closeout tree across half the
portfolios. Parses cleanly as `digit_letter`. If/when Closeout rollup
is designed in Smartsheet, the `Na.` layer is a real input.

### 4. "Lum" inside Luminace project = redundant customer-name encoding

The `1a. Lum Review of IFC ELEC Drawings` example surfaces a separate
hygiene observation: the folder name encodes "Lum" (Luminace) as a
review-author tag, but the folder already lives inside the Luminace
portfolio (`2024.335 Forefront - Luminace`). The customer name is
redundant in the folder name and adds noise without information.

This is a Box organizational observation, not a code change. Worth a
flag in the Box ↔ Smartsheet Reconciliation v1 addendum as part of the
"folder-name hygiene drift" discussion. Not actionable from this repo.

### 5. Long-tail sanity check: no third structural pattern blocking ship

Ran a post-fix sanity check on the residual 3,006 unclaimed: histogram
by leading-char class, leading-digit deep-dive (top 30), suspicious-
punctuation filter, ISO-date-prefix filter.

Findings — captured in detail under "Sanity check findings" below:

- Two candidate structural patterns surfaced (`V{NN}.` / `S{NN}.`
  vendor-sub enumeration and `YYYY-MM-DD <Name>` ISO date prefix).
  Volume is small (~30 and ~13 unique names respectively); concentrated
  in specific portfolios rather than universal. **Deliberately not
  patched in this PR** — see scope discipline below.
- A borderline category of priority-prefix variants with different
  separators (`7 - Permits`, `2- ALTA`, `2_Utility Studies`). Less
  confident these are structural; not pursued.
- Equipment specs (`125kw`, `250kW 600v`) — descriptive content, not
  structural, no parser work warranted.

The 3,006 remaining unclaimed after these are accounted for are
free-text engineering / procurement leaf names with no further
structural pattern.

## Sanity check findings

Spot-checked the long tail of unclaimed names to confirm no further
structural pattern was hiding. Histogram: 2,106 leading-uppercase
unclaimed (free-text), 199 leading-digit unclaimed, 176 leading-lower-
case unclaimed, ~12 other. The leading-digit and leading-uppercase
buckets are where structural patterns would hide; both were inspected.

**Candidate 3: `V{NN}.` / `S{NN}.` vendor-sub enumeration.** Examples:
`V12. EPEC`, `V17. CAB`, `V22. Chint`, `S10. Well Demo`, `S11. Erosion
Control Consulting INC`. ~30 unique names, 2–3 portfolios (Forefront
appears to be the heaviest). The existing `SUBJOB_LETTER_UC` regex
caps the post-letter digit at one (`\d?`); `V12.` falls through with
two digits. Structurally clear: V = Vendor, S = Sub, followed by an
enumeration number and a name.

**Candidate 4: `YYYY-MM-DD <Name>` ISO date prefix.** Examples:
`2024-12-04 Brimfield 1 IFC CAD`, `2024-12-13 Rockford IFC CAD - V2`,
`2025-09-15 BBCHS PBASE`. ~13 unique names, low volume, consistent
shape. Existing `parse_date_prefix` only handles `R. M.D.YY <topic>`
and `S. M.D.YY <topic>` forms — ISO doesn't match.

Both candidates have concrete tech-debt entries with regex sketches,
volume estimates, and test snippets so a future session can act
without re-running the sanity check. See `docs/tech_debt.md`.

**Borderline (not promoted to tech_debt):** non-canonical priority-
prefix variants — `7 - Permits`, `2- ALTA`, `1 - Design & Energy Study`,
`2_Utility Studies`, `1_ISA`. 6–8 unique names. Could be priority-prefix
variants with different separators, or could be deliberate free-text
folder names that happen to start with a digit. Confidence is too low to
add to tech_debt without cluttering the file with maybes. If they
re-surface in a future reconcile pass with more signal (e.g., same
operator using both patterns interchangeably), they earn an entry then.

**Closed (no further action):** equipment specs (`125kw`, `250kW 600v`,
`350kW 800V`) and similar — descriptive content, not structural. No
parser work warranted. Noted here so a future sanity check doesn't
re-litigate.

## Scope discipline: why sanity-check findings are deferred

The original deliverable was *"patch the identified gap."* Sub-subjects
was the gap — surfaced by the first reconcile run, confirmed by inline
tracing in the raw data, claimed by `parse_subsubject`, verified by the
delta.

V/S and ISO emerged from the post-fix sanity check. The sanity check is
meant to **verify completeness of the original work**, not to discover
new scope. Extending the PR now would conflate the two purposes and
turn every sanity check into a discovery tool — which would make
sanity checks something to dread rather than do.

Direct precedent: v2's 4 closed schemas and v3's 4 active schemas
shipped as separate passes (commit history reflects this — v3 is its
own commit `4b3e5c0` from 2026-05-17, not an extension of v2). Same
discipline applies here. Each parser pass has a defined input
(here: the reconcile output), a defined output (here: the patch +
report), and a clean stopping point.

The tech_debt entries make the deferral safe: a future session can act
on V/S or ISO without re-discovering them, with the regex sketch +
volume estimate + test snippets all already written down.

## Per-portfolio detail

Full report at `docs/reports/2026-05-18_reconcile_full.md`. The
following table summarizes per-portfolio movement; full
top-level-claim tables and chaos-flag tallies live in the report.

| # | Portfolio | Schema | active_subjob | subsubject | unclaimed |
|---:|---|---|---:|---:|---:|
| 1 | 2025.201 KSI 4 IL | active_portfolio_modern | 139 | 70 | 510 → 440 |
| 2 | 2024.335 Forefront - Luminace | active_portfolio_modern | 148 | 82 | (see report) |
| 3 | 2023.126 Oregon - Kendall | active_modern | 112 | 59 | (see report) |
| 4 | 2025.358 Keystone (Coast) | active_portfolio_modern | 83 | 45 | (see report) |
| 5 | 2025.108 Bonacci 1&2 (Generate) | active_single_project | 119 | 10 | (see report) |
| 6 | 2025.364 Steger & Roxbury | active_portfolio_modern | 130 | 76 | (see report) |
| 7 | 20171-20176 OR Portfolio (SPI) | active_modern | 52 | 6 | (see report) |
| 12 | 2024.112 Almon, Lomaside, Perrydale (Hawthorne) | active_portfolio_modern | 87 | 67 | (see report) |
| 13 | 2025.112 Kendall CSP Portfolio 5 | active_portfolio_modern | 87 | 55 | (see report) |
| 15 | 2025.127 Dolphin and Shoestring | active_portfolio_modern | — | — | (see report) |

Schema classification reproduces the v3 deep-dive expectations exactly:
7 `active_portfolio_modern`, 2 `active_modern`, 1 `active_single_project`.
No portfolio fell into `UNKNOWN`. The schema classifier didn't need a
patch — it was already correctly identifying the schemas; the gap was
purely in the recognition of folders one layer deeper than the schema
classifier looks.

## Why a new entry point rather than extending parse_folder

Consistent with v3's existing architecture, which already added
`parse_active_subjob`, `parse_portfolio_subject`, and
`parse_development_subject` as new entry points alongside `parse_folder`
rather than growing `parse_folder` further. `parse_subsubject` follows
the same shape: takes a raw name, returns an `Optional[SubsubjectParse]`,
no side effects, no dependency on parent context. The reconcile harness
inserts it into the claim chain between `development_subject` and
`canonical_non_job`. v1, v2, and existing v3 entry points: untouched.

## Why \d{1,2} cap on each numeric segment

Bounded so the new patterns cannot collide with existing sub-job ID
recognizers:

- `\d{4}.\d{2,3}` (modern job IDs like `2025.201`) won't match because
  the parent segment is capped at 2 digits.
- `\d{3,4}-\d{3,4}` (SPI dashed legacy) — different separator entirely.
- `\d{3}.\d+` (Forefront 3-digit prefix like `335.1`) won't match
  because the parent segment is capped at 2 digits.

Boundary cases asserted by tests (`100.1 Hypothetical` and `7.100
Hypothetical` both return None).

## Chaos detection (independent of claim chain)

Chaos flags ran on every unique name regardless of whether it was
claimed. Six chaos patterns saw matches; full per-portfolio breakdown
in the report. Global ranking:

| Pattern | Count |
|---|---:|
| person_tag_in_subject | 138 |
| pre_canonical_zero | 35 |
| double_space | 19 |
| unfilled_placeholder | 18 |
| instructional_name | 9 |
| date_prefix_lowercase | 7 |
| generic_new_folder | 6 |
| archive_letter_z | 4 |
| box_drive_copy | 2 |
| duplicate_suffix | 1 |
| exclamation_emphasis | 1 |
| sub_decimal_insert | 1 |

`person_tag_in_subject` (138) is by far the most common, suggesting
the regex may be over-matching on legitimate dash-customer-paren
folder-naming conventions. Worth spot-checking in a future session
before treating that pattern as a high-signal hygiene flag.

## Gates

- `ruff check .` — clean. Existing `box_migration/*` per-file-ignores
  in `pyproject.toml` cover the new regex constants and dataclass.
- `pytest -q` — **211 passed, 2 skipped** (was 184 + 2; +27 new tests
  from `tests/test_parse_subsubject.py`).
- `mypy` not run on `box_migration/*` per the repo's current scope.

## What's NOT in this PR

- **V/S and ISO parser patches.** Deferred to focused follow-up PR(s)
  via tech_debt entries. Reasoning above under "Scope discipline."
- **Borderline priority-prefix variants.** Confidence too low; not
  tracked in tech_debt.
- **`TEST_CORPUS` additions to v2.** The corpus is a private smoke-runner
  inside v2; v3's new patterns are covered by
  `tests/test_parse_subsubject.py` instead. Preservation-over-refactor
  — don't poke v2 for additions that have a cleaner home.
- **Refactoring v3's existing entry points.** Specifically didn't touch
  `parse_folder`, `parse_active_subjob`, or `classify_schema`.
- **The `person_tag_in_subject` over-match question.** 138 flagged
  names is a lot; some are likely false positives on legitimate naming
  patterns. Spot-check belongs in a different session.

## Followup that becomes possible

- **Box ↔ Smartsheet Reconciliation v1 addendum** (planning project,
  not this repo). Two open questions shift: gap-fillers and parent
  rollup. Plus the `Na.` and Lum/Luminace hygiene observations land
  there.
- **V/S vendor-sub parser** (`docs/tech_debt.md` entry).
- **ISO date prefix in `parse_date_prefix`** (`docs/tech_debt.md` entry).
- **Path-aware reconcile.** The current harness classifies folder
  *names* in isolation. A path-aware pass could verify that, e.g., a
  `subsubject` claim only appears at depth ≥1 inside a canonical
  `N. Subject` parent — and flag any "wrong-place" instances. Lower
  priority; existing chaos detectors cover the most egregious
  misfilings.
- **TEST_CORPUS upstream into v2.** When v2 next sees an organic edit,
  fold the v3 corpus additions through for parity.

## Sequencing context

Lands on `main` directly after PR #12 (`9b5cbfd`, the parse_job v1/v2
cascade restore). That earlier PR was a prerequisite — without v1 and
v2 in the tree, this reconcile would have been blocked at
`ModuleNotFoundError`. The sequence ran cleanly: discover gap → restore
deps (PR #12) → reconcile + patch (this PR) → defer follow-ups
(tech_debt entries).
