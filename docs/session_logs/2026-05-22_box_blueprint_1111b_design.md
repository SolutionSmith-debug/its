# 2026-05-22 — Box 1111B blueprint design (1111A forensic + canonical redesign)

Pure documentation absorb of a parallel chat-session output. The chat forensically walked the Box 1111A template (folder ID `382384021749`) and all 6 active project clones in the sandbox tenant (Bradley 1, Bradley 2, Brimfield 1, Brimfield 2, Huntley, Rockford); designed the 1111B canonical blueprint adopting universal zero-padded numeric prefixes with restart-at-each-level and `99.NN` sort-to-end convention. 1111A is untouched; 1111B Box build (~91 ops) is held pending coordination with the safety-reports work stream.

The blueprint deliverable itself lives in the Anthropic project as `ITS_Box_Blueprint_1111B_2026-05-22.docx` (containing the full 131-folder tree, conventions reference, and code-impact map). This session log captures the substance, decisions, and code-side TODO markers in-repo.

No code-path migrations. No Box operations. No test changes. Pure absorb.

## Forensic findings (1111A as of 2026-05-22)

### Seven conflicting prefix conventions coexist, sometimes within the same parent

1. **Letter prefix `A.–Z.`** — Field root, `Field/A. Onsite Reporting`, `Field/B. Approved Plans IFC`, `Office/2. Accounting`.
2. **Number prefix restarting at 1** — `ITS DATA` root (`1.–12.`), `Office` root (`1.–9.`), `Portfolio/6`, `Portfolio/12`, `Field/F`.
3. **Hierarchical decimal matching parent** — `Office/3` (`3.1–3.3`), `Office/4` (`4.1`), `Office/6` (`6.1–6.6`), `Office/7` (`7.1–7.11`), `Office/8` (`8.1–8.8`), `Office/9` (`9.1–9.9`).
4. **`99.X` sort-to-end with decimal** — `Office/5` (`99.1–99.4`), `Portfolio/2` (`99. Templates`).
5. **`Z.` sort-to-end marker** — `Portfolio/2` (`Z. Example Specs`).
6. **Bare names, no prefix** — `Portfolio/7` (`Sub Invoices`), `Portfolio/8` (`OCOs`, `RFIs`, `SCOs`), `Field/D` (`Templates`).
7. **No subfolders at all** — `Portfolio/1, 3, 4, 5, 9, 10, 11`; `Field/C, E`.

### Typos propagating to all 6 clones

- `Coorespondance` ×2 occurrences (should be `Correspondence`)
- `Structual Calculations` (should be `Structural Calculations`)
- `Owner Correspond` (truncated word; should be `Owner Correspondence`)
- Possessive-S on acronym plurals: `JSA's`, `DFR's`, etc. (should be `JSAs`, `DFRs`)

### Cross-cutting logical-content duplication

- **RFIs / OCOs / SCOs** appear in both `Portfolio/8` and `Office/3` with different conventions.
- **Closeout** appears in both `Portfolio/12` and `Field/F`.
- **Permits** appear in `Field/E`, `Office/8`, and `Portfolio/12/6`.
- **Schedules** appear in `Portfolio/3` and `Field/D`.
- **Submittals** appear in `Portfolio/10` and `Office/4`.
- **Templates** appear in 3 different locations with 3 different styles.
- **Correspondence** appears in 3 different locations with 2 different typos.

## Locked decisions

| Decision                                  | Resolution                                                                                       |
|-------------------------------------------|--------------------------------------------------------------------------------------------------|
| Scope split (Portfolio vs Office)         | Keep both. Intentional duplication preserved.                                                    |
| Letter vs number                          | Universal zero-padded numeric (`01., 02., …`) at every level. Letters retire.                    |
| Depth behavior                            | Restart at each level. Folder names stay short; path conveys ancestry.                           |
| Sort-to-end                               | `99.NN` reserved exclusively for template/copy-folder placeholders. `Z.` retires.                |
| Bare names                                | Eliminated. Every subfolder gets a prefix.                                                       |
| Typos                                     | Fixed at 1111B.                                                                                  |
| `Portfolio` prefix                        | Applied uniformly to all 12 top-level Portfolio folders.                                         |
| Hyphens vs spaces                         | Hyphens only for structural compounds (`De-Comm`, `As-Built`, `Geotech-Pile`).                   |
| Migration                                 | Build 1111B fresh next to 1111A; 1111A untouched as comparison.                                  |

## 1111B canonical tree (top level only — full tree in chat docx)

```text
1111B (Copy for new projects)/
├── (Project # & Name) Field/        [01.–06., letters retired]
├── (Project # & Name) Office/       [01.–09., decimal children retired in favor of restart-at-level]
├── 01. Portfolio Client Docs/
├── 02. Portfolio Buyout/             [99.01 Templates, 99.02 Example Specs]
├── 03. Portfolio Schedules/
├── 04. Portfolio Dev Docs/
├── 05. Portfolio Engineering Gen/    [+Portfolio prefix]
├── 06. Portfolio Owner Correspondence/   [typo fix]
├── 07. Portfolio Financials/         [01. Sub Invoices]
├── 08. Portfolio Change Management/  [01. RFIs, 02. OCOs, 03. SCOs — workflow order]
├── 09. Portfolio Utility Documents Tracking/   [+Portfolio, hyphens→spaces]
├── 10. Portfolio Submittal Logs/     [+Portfolio]
├── 11. Portfolio De-Comm Bonds/      [+Portfolio, structural hyphen kept]
└── 12. Portfolio Closeout/
```

Total footprint: **131 folders**. Full tree, conventions reference, and code-impact map in `ITS_Box_Blueprint_1111B_2026-05-22.docx` (Anthropic project).

## Code-path impact map

### Immune to renaming (use folder IDs, not names)

- `shared/sheet_ids.py` `FOLDER_PROJECT_*` constants.
- `shared/defaults.py` `BOX_PROJECT_FOLDERS` dict.
- `ITS_Config safety_reports.*` job-folder rows.

### Affected by renaming (by-name lookups)

- `safety_reports/intake.py` — `BOX_SUBPATH_BY_CATEGORY` tuple paths use the Field-tree letter-prefixed names (`"A. Onsite Reporting & Tracking"`, `"A. Safety Plan & Reports"`, `"B. Project Reports & Trackers"`). These need the `A.` → `01.` and `B.` → `02.` rewrites at 1111B promotion.
- `safety_reports/weekly_generate.py` — does not currently do Box folder lookups (it writes Smartsheet rows only), but a forward-looking TODO marker is placed for the case where the file grows to upload approved-WPR PDFs to Box.
- `ensure_current_week_folder` (`safety_reports/week_folder.py`) — operates on Smartsheet folders, NOT Box folders. Mention here is forward-looking only.
- `box_migration/parse_job_v3.py` — regex patterns were designed for messy legacy paths (`0.`, `1.5.`, `z.`, `- Copy`). Should match clean 1111B paths but worth a verification run when 1111B exists.

## TODO markers added in this PR

Four identical-spirit TODO comments placed at code anchor points so the future migration session can grep `(post-1111B)` to find every site:

1. **`shared/sheet_ids.py`** — above the `FOLDER_PROJECT_BRADLEY_1` constant: TODO to regenerate the constants from the new clones when 1111B replaces 1111A as canonical.
2. **`safety_reports/intake.py`** — above the `BOX_SUBPATH_BY_CATEGORY` dict: TODO listing the three specific letter-prefix → number-prefix renames.
3. **`safety_reports/weekly_generate.py`** — in the module-level constants area: same TODO with a brief preface noting this module doesn't currently do Box lookups (forward-looking placement).
4. **`box_migration/parse_job_v3.py`** — above the v3 regex signature block: TODO noting regex should match 1111B paths but a verification run is warranted.

## Held-state rationale

The chat session designed 1111B alongside parallel cc work on safety_reports:

- **PR #63** (R3 Session 2, weekly_generate.py) shipped 2026-05-22 21:36Z.
- **PR #65** (single-shot retry on transient 404 + GENERATION_FAILED placeholder) shipped 2026-05-22 ~01:30Z next-day (concurrent with this absorb).

The 1111B materialization (~91 clone-and-rename ops) was held pending the retry primitive landing — because the build itself will hit the SDK staleness pattern PR #65 mitigates. Now that PR #65 is in `main`, 1111B is no longer blocked from a code-primitive perspective; the build remains held pending operator coordination.

## Tech-debt entries

These are captured in this session log rather than appended to `docs/tech_debt.md` because they are blueprint-track items, not in-repo execution-layer debt. If/when 1111B promotion lands, the migration session will create the equivalent in-repo tech-debt entries.

1. **1111B blueprint materialization** — Build 1111B in mirror Box via clone-and-rename (~91 ops). Was blocked on transient-404 retry primitive landing; that primitive shipped 2026-05-22 (PR #65). Now blocked only on operator coordination + the SDK→REST swap tech-debt trigger condition (3+ retry events in 4 weeks OR first user-visible `GENERATION_FAILED`).
2. **Sub-decisions for future refinement** — `Engineering Gen` name ambiguity; Templates location consolidation (3 → 1); `Field/06` vs `Portfolio/12` Closeout merge-or-separate. These are second-pass refinements after the universal-numeric convention lands.
3. **Code-path migration** — When 1111B becomes canonical, update by-name lookups in `intake.py` (`BOX_SUBPATH_BY_CATEGORY`), the forward-looking marker in `weekly_generate.py`, and the future Box upload path in any new send-side scripts. Verify `parse_job_v3.py` regex against clean 1111B paths.

## Cross-references

- **PR #39** — Box OAuth wiring (commit `2ce6ece`).
- **PR #56** — 1111A clone cascade to 6 projects (commit `30bbaa5`).
- **PR #57** — intake.py end-to-end wire-up.
- **PR #63** — weekly_generate.py + WPR pipeline (R3 Session 2).
- **PR #65** — single-shot retry on transient 404 + GENERATION_FAILED placeholder (concurrent with this absorb).
- **Operational Standards v11 §23** — 5-workspace topology.
- **Operational Standards v11 §30** — SDK-vs-Live integration test discipline.

## Verification

- `pytest -q` — **829 passed, 1 skipped, 12 deselected**. Unchanged from PR #65 baseline (brief expected 822 but that was pre-#65; this PR adds no tests so the count stays at 829).
- `mypy .` — **Success: no issues found in 97 source files**.
- `ruff check .` — **All checks passed**.
- CI green on the chore PR.

## Baseline state at session close

- `main` at the merge commit of this PR (added below post-merge).
- 1111A in mirror Box is untouched — the 6 project clones still reference 1111A as their template (per `shared/defaults.py BOX_PROJECT_FOLDERS`).
- 1111B does not yet exist in mirror Box — held pending operator coordination + post-PR-#65 SDK signal (`retries_attempted` counter).
- All 4 TODO markers in code carry the same `(post-1111B)` grep tag and reference this session log.

## What's NOT touched

- 1111B Box build (~91 ops) — held pending the durable SDK→REST swap trigger or explicit operator go-ahead.
- Any code-path migration. Only TODO markers in this PR.
- 1111A template or the 6 project clones — both unchanged.
- `shared/defaults.py BOX_PROJECT_FOLDERS` — still correctly references 1111A clones.
- Test suite — no behavior change, no tests added or modified.
- `docs/tech_debt.md` — blueprint-track tech-debt items live in this session log until 1111B promotion creates the equivalent in-repo entries.

## Sequencing context

This PR is a documentation absorb of the chat-session output; it unblocks no other in-repo workstream directly. The 1111B materialization session — when it lands — will:

1. Build 1111B in mirror Box via the (now-retry-aware) Box client.
2. Promote 1111B to canonical (update `shared/defaults.py BOX_PROJECT_FOLDERS` + the per-project folder IDs).
3. Apply the rewrites flagged by the 4 TODO markers.
4. Verify `parse_job_v3.py` regex against clean 1111B paths.

Until then, the TODO markers + this session log are the breadcrumbs.
