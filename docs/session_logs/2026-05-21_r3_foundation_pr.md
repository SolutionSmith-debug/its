# 2026-05-21 — R3 Foundation PR

PR: [#54](https://github.com/SolutionSmith-debug/its/pull/54) — squash-merged at 2026-05-21T14:50:29Z. Merge commit `ed46a966c97d6f16f5d9900faadc195bcf1e5dba`. Three-assertion verify clean.

Closes the Q4-Q8 cascade per `ITS_Q4-Q8_Resolution_2026-05-21.docx`. After this PR the next session for safety_reports is purely "wire `intake.py` against the helpers and constants that already exist."

## What landed

- `shared/sheet_ids.py` — 2 new workspaces, 5 new portfolio/ops/archive sub-folder constants, 12 new project-tree folder constants (6 active + 6 Field Reports), 2 reverse-lookup maps.
- `shared/defaults.py` — `FOREFRONT_CUSTOMER_NAME` + `BOX_PROJECT_FOLDERS` (empty-string values; loud-fail on first use during R3 session 1).
- `shared/smartsheet_client.py` — 3 new REST helpers: `find_folder_by_name_in_folder`, `create_folder_in_folder`, `create_sheet_in_folder_from_template`.
- `safety_reports/week_folder.py` (new) — `WeekScaffold` dataclass + `ensure_current_week_folder` (idempotent find-or-create on the per-week Field Reports folder + Daily Reports / Weekly Rollup sheets; race-safety post-create find with WARN-log; KeyError on unknown project).
- `scripts/migrations/seed_safety_recipients_config.py` (new) — 7 `safety_reports.recipients.*` rows seeded live (6 per-job + `_default`), all `Value=[]`.
- `docs/tech_debt.md` — 2 new OPEN entries (week-folder race, Daily Reports missing Box Link column).
- `safety_reports/README.md` — Decision-state section: 4 deferred → resolved.

## Non-obvious decisions

- **Body-shape gotcha (Copy Sheet)** — initial `create_sheet_in_folder_from_template` used `{"destination": {"type": "folder", "id": ...}, "newName": ...}` (matches some Smartsheet endpoints). The Copy Sheet endpoint rejected it with HTTP 400 errorCode 1008 ("Unknown attribute 'destination'"); the correct shape is flat `destinationType` + `destinationId` keys. Caught by the new `test_ensure_current_week_folder_round_trip` integration test before the PR went up — same class of bug as PRs #47/#48/#49/#51, same mitigation (integration coverage on every new SDK wrapper that does write verbs). Documented inline in the helper's docstring.
- **Integration-test parent folder** — brief surfaced this as an open clarification: `FOLDER_SYSTEM_CONFIG` (precedent from existing integration tests) vs. a new `FOLDER_INTEGRATION_SANDBOX` constant. Went with `FOLDER_SYSTEM_CONFIG` in auto-mode; the `_int_*` sandbox-name pattern keeps test state visually distinct, and the alternative would have required operator-pre-creating a sandbox folder before the PR could land. Easy to revisit in a follow-on PR if `FOLDER_SYSTEM_CONFIG` ever feels semantically wrong in practice.
- **Phase 3 scope grew by 2 helpers** — brief explicitly required `find_folder_by_name_in_folder`. `create_folder_in_folder` + `create_sheet_in_folder_from_template` weren't enumerated in Phase 3 but were named in Phase 4 as mock targets; both don't exist in `shared/smartsheet_client.py`. Added them as direct REST (symmetry with the find helper). Error-translation block now appears 4x in the module — at the §14 preservation-vs-abstraction threshold; deferred extraction to a follow-on PR focused on it (per `feedback_pr_scoping_narrow`).
- **Pre-flight test-count drift** — brief stated `pytest 655 pass / 2 skip / 4 deselected = 663 collected` on main `ece15d5`; live showed `663 passed, 2 skipped, 4 deselected`. The 2 skip + 4 deselected exact-match; pass count divergence is brief author's stale snapshot. Surfaced per verify-before-fix discipline and proceeded.

## Verification

- `pytest -q`: 680 pass / 2 skip / 6 deselected (+17 unit pass, +2 deselected integration vs. baseline).
- `pytest -m integration`: 6 pass (4 existing + 2 new; one existing test, `test_update_column_options_round_trip_picklist`, flaked once on read-after-create eventual-consistency, passed on retry — known Smartsheet behavior, not a regression).
- `mypy .`: 0 issues / 85 source files. `ruff check .`: clean.
- Recipients-config migration ran live → 7 rows created. Re-run idempotent (`exists` × 7).
- REPL smoke: `ensure_current_week_folder("Bradley 1")` ran twice; first run created folder `149293827942276` + sheets `2821831617630084` (Daily Reports) + `6059871886593924` (Weekly Rollup) in Bradley 1 Field Reports tree; second run returned identical IDs. Smoke residue cleaned via REST DELETE.

## What's next

R3 session 1 — wire `safety_reports/intake.py` against:

- `sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[project_name]` for the per-project Field Reports folder.
- `week_folder.ensure_current_week_folder(project_name)` per inbound email so the Daily Reports row-write target always exists.
- `defaults.BOX_PROJECT_FOLDERS[project_name]` for the Box filing path (operator will fill the empty-string values from the `1111A (Copy for new projects)` template before that session starts).
- `safety_reports.recipients.<job>` ITS_Config rows for the recipient routing (operator fills via sheet edit informed by a Teala email).

Open tech-debt items the next session should plan around: `docs/tech_debt.md` entries `safety_reports week-folder create-find race condition` and `Daily Reports schema gap — no Box Link column`.
