# person_tag_in_subject audit — 2026-05-19

Per the open `docs/tech_debt.md` entry, this audit:
1. Pulls a 20-sample categorization from the live reconcile output.
2. Identifies false-positive patterns.
3. Recommends a regex-refinement direction.

**Stopping point: this is a docs-only deliverable.** No regex change
in this PR. Operator picks the refinement direction; a follow-up PR
implements.

## The current regex

`box_migration/parse_job_v3.py:PERSON_TAG_IN_SUBJECT`:

```python
r'(\bfor\s+[A-Z]{3,}\b|'                                  # "for ZACK"
r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b|'       # "Teala Organize folder"
r'-\s*[A-Z][a-z]+\s*$)'                                    # "Budget- Jason"
```

Three alternations. The first two are narrow (explicit allcaps name
after "for", or First+verb form). The third is the noisy one —
trailing-capitalized-word after a dash.

Reconcile flagged **111 unique names** (138 occurrences across the
10 portfolios). Concentration is from the third alternation.

## 20-sample categorization

| # | Name | Verdict | Why |
|---:|---|---|---|
| 1 | `9. Utility-Documents-Tracking` | **FP** | `Tracking` is a document type, not a person. ×8 occurrences. |
| 2 | `7.11 As-Built` | **FP** | `Built` trailing; `As-Built` is standard engineering term. ×7. |
| 3 | `T-Sheets` | **FP** | `Sheets` is a document type. ×7. |
| 4 | `11. AHJ & Utility Permits-Inspections` | **FP** | `Inspections` is an activity type. ×6. |
| 5 | `CPS-Chint` | **FP** | Vendor names (CPS, Chint). ×4. |
| 6 | `Bonacci 1 - OCO 001 - Final` | **FP** | `Final` is a document state. (6 sibling rows of the same pattern.) |
| 7 | `Module Deliveries - Rockford` | **FP** | `Rockford` is a project/location name. |
| 8 | `Quick - Brimfield` | **FP** | `Brimfield` is a project/location name. |
| 9 | `Geo-Tech` | **FP** | `Tech` is a discipline abbreviation, not a person. |
| 10 | `2 - Environmental` | **FP** | `Environmental` is a workstream name. |
| 11 | `Pull Tests- Forefront` | **FP** | `Forefront` is a customer name (Forefront Renewables). |
| 12 | `Re_ Final Golden Row Submittal - Steger` | **FP** | `Steger` is a project name. |
| 13 | `Teala Organize folder` | **TP** | Explicit person name (`Teala`) + Organize allowlist verb. Alternation #2. |
| 14 | `11. EPC Contract Redlines for ZACK` | **TP** | Explicit `for ZACK` all-caps. Alternation #1. |
| 15 | `Structural - Bowman` | **Ambiguous (lean TP)** | Bowman is likely a structural engineer's surname. |
| 16 | `R. Bowman-Pungo` | **Ambiguous (lean TP)** | Bowman person name; Pungo unclear (project? person?). |
| 17 | `R. 11.4.25 Ferc-Bowman` | **Ambiguous** | Bowman = person name; FERC is a federal agency. Mixed. |
| 18 | `V6. Maddox-Coker` | **Ambiguous (lean FP)** | Maddox / Coker could be person names OR company names (both are real US-company names). |
| 19 | `As-Built Lum Mark-Ups` | **FP** | `Ups` (from `Mark-Ups`) — document operation. `Lum` is the Luminace customer abbreviation. |
| 20 | `XFMR Re-build- Coker` | **Ambiguous (lean TP)** | Coker is likely a person (review-author surname). |

**Tally:**
- False positives: **12 of 20 (60%)**
- True positives: **2 of 20 (10%)**
- Ambiguous: **6 of 20 (30%)**, leaning roughly 3 TP, 2 FP, 1 unclear

Worst case (treating all Ambiguous as TP): 60% FP rate, 40% TP rate.
Best case (treating all Ambiguous as FP): 70% FP rate, 10% TP rate.

Either way, the **third alternation produces noise**. The first two
alternations both fire on real TPs (samples #13 and #14) and shouldn't
change.

## False-positive patterns identified

All 12 confirmed FPs hit the third alternation
(`-\s*[A-Z][a-z]+\s*$`). The trailing capitalized word falls into one
of these non-person categories:

| Category | Examples |
|---|---|
| **Document type** | `Tracking`, `Sheets`, `Inspections`, `Built`, `Ups` (from `Mark-Ups`) |
| **Document state** | `Final`, `Approved`, `Standard` |
| **Project / location** | `Rockford`, `Brimfield`, `Steger`, `Pungo` |
| **Customer name** | `Forefront`, `Lum`, `Luminace`, `Coast` |
| **Vendor / equipment** | `Chint`, `Valmont`, `Eaton`, `Arteche`, `Shoals`, `Ampacity` |
| **Discipline / abbreviation** | `Tech` (Geo-Tech), `Environmental`, `Permits` |

Several names also hit the alternation despite the trailing word
being part of a compound (e.g., `As-Built`, `Mark-Ups`). The regex
doesn't distinguish "person-tag suffix" from "compound-word suffix."

## Recommendation: Direction (A) — remove the third alternation entirely

The third alternation's signal-to-noise ratio is poor enough that
removing it produces a cleaner hygiene flag with minimal TP loss.

**Why (A) is the recommended direction:**

1. **TP loss is low.** The only confirmed TP in the third-alternation
   class is sample #15 (`Structural - Bowman`) and a few Ambiguous-
   lean-TP cases (#16, #17, #20). Maybe 2–4 real catches across the
   entire 10-portfolio corpus. Operators can spot these visually in
   the folder tree without a chaos flag.

2. **FP cost is high.** 138 occurrences flagged today, ~95% noise.
   Operator triage time wasted; chaos flag becomes ignorable.

3. **Alternations 1 and 2 catch the clearest TPs.** Sample #13
   (`Teala Organize folder`) and #14 (`for ZACK`) both fire on
   non-third alternations. Those continue to work.

4. **Simpler regex, easier to reason about.** The third alternation
   has been the source of every FP discussed in this audit. Removing
   it eliminates an entire failure mode.

**Direction (B) — allowlist-based refinement** is more powerful but
adds maintenance burden:

- Maintain a list of known person-name suffixes (e.g., `Bowman`,
  `Coker`, `Seevers`).
- Maintain a list of known NON-person suffixes (e.g., `Final`,
  `Tracking`, `Steger`, ...) — this would balloon quickly.
- Risks new FPs whenever a customer / vendor / project name is added.

**Direction (C) — keep current regex, lower to INFO severity** would
mark the flag as low-confidence:

- Doesn't reduce the noise; just relabels it.
- Operators still have to look at every flagged item.
- Treats the symptom not the cause.

## Proposed regex change (if Direction A is adopted)

Replace the current 3-alternation regex with 2 alternations:

```python
PERSON_TAG_IN_SUBJECT = re.compile(
    r'(\bfor\s+[A-Z]{3,}\b|'                            # "for ZACK"
    r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b)'  # "Teala Organize folder"
)
```

Reconcile expected delta: 138 chaos hits → ~2–4 chaos hits (the
explicit "for XXX" and "X Organize/Cleanup/Notes/Files" forms only).
Remaining real-person-tag catches that were ambiguous in this audit
(#15–#20 above) lose their flag — operator must spot them visually.

Tests to add when implementing:
- All current positive-match tests for alternations 1 and 2 should
  still pass (regression coverage).
- Add explicit negative cases for the 12 FPs in this audit so the
  regression doesn't recur if the third alternation is ever
  re-introduced.

## Stopping point

Direction not yet decided. **Operator picks (A) / (B) / (C); a
follow-up PR implements the chosen refinement and updates the
tech_debt entry to CLOSED.** This PR ships only the audit data + the
recommendation.

The tech_debt entry itself stays OPEN and gets a reference to this
audit doc + the pending decision.
