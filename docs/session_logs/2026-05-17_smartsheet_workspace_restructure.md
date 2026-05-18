# 2026-05-17 — Smartsheet workspace restructure (operator vs customer separation)

Second session of 2026-05-17. Restructures the Smartsheet workspace topology to separate operator surfaces (anomaly handling, error logs) from customer-facing approval surfaces, provisions the four remaining system/personnel sheets, and closes the parse_job_v3 F841 dead-code item that session 1 flagged.

## Commits landed

| SHA | Title | Purpose |
|---|---|---|
| `1fd6751` | fix(box_migration): remove parse_job_v3 dead-code de-dup attempt | Closes the F841 tracked since session 1 |
| `de922d9` | feat(shared): sheet_ids bootstrap + archive build_human_review | New file holding workspace/folder/sheet IDs from tonight's restructure; archives the now-unrunnable provisioning script |
| `0fdcd75` | docs: tech_debt + CLAUDE.md/README status refresh for workspace restructure | New tech_debt.md (F841 closure + 2 Smartsheet API constraint notes); CLAUDE.md stub-table refresh; README Phase 1 status line |
| _(this commit)_ | docs: session log for 2026-05-17 evening workspace restructure | This file |

## CI runs

| Run | Commit | Result |
|---|---|---|
| [26008844046](https://github.com/SolutionSmith-debug/its/actions/runs/26008844046) | `1fd6751` | green |
| [26008893064](https://github.com/SolutionSmith-debug/its/actions/runs/26008893064) | `de922d9` | green |
| [26008970233](https://github.com/SolutionSmith-debug/its/actions/runs/26008970233) | `0fdcd75` | green |

## Smartsheet state changes (not in git, captured here for the record)

Two new workspaces created, one folder deleted, two sheets moved, four sheets provisioned. All operations via the OAuth-authorized Smartsheet MCP except sheet-move and folder-delete, which are not exposed by the MCP and used the REST API directly with a short-lived token.

### Workspaces created

| Workspace | ID | Purpose |
|---|---|---|
| ITS — System | 680592632244100 | Operator-only — maintainer's surface |
| ITS — Human Review | 8561891980142468 | Evergreen-employee-facing approval queues |

### Subfolders

`ITS — System`: `01 — Config` (164788727768964), `02 — Logs` (5231338308560772), `03 — Queues` (7201663145535364).

`ITS — Human Review`: `01 — Safety Reports` (2486957285631876), `02 — Subcontracts` (1924007332210564), `03 — Purchase Orders & Materials` (2768432262342532), `04 — Email Triage` (8960881749976964), `05 — AI Employee` (1185135518345092), `06 — Personnel` (7377585005979524).

### Sheet provenance

| Sheet | ID | Origin |
|---|---|---|
| ITS_Config | 3072320166907780 | provisioned tonight |
| ITS_Errors | 27291433258884 | provisioned tonight |
| ITS_Quarantine | 8687740798324612 | provisioned tonight |
| ITS_Time_Off | 1506418040459140 | provisioned tonight |
| ITS_Review_Queue | 7243317526876036 | moved from demo workspace (originally provisioned by build_human_review.py) |
| WPR_Pending_Review | 3096105695793028 | moved from demo workspace (originally provisioned by build_human_review.py) |

### Folder deleted

`06 — Human Review` (210126402545540) in Forefront Portfolio — ITS Demo. Emptied via the two moves above, then deleted. The demo workspace is back to 5 customer-facing folders (01 — Active Projects through 05 — Field Reports).

## Decisions made during session

- **Two new workspaces instead of one.** Original proposal had a single "ITS Plumbing" workspace holding both operator queues (anomalies, errors, quarantine) and customer-facing approval surfaces (WPR queue). Seth corrected this — the two surfaces have different audiences and different training requirements. People who handle anomalies need maintainer training; Evergreen PMs and admins who approve WPRs do not. Workspaces split by audience, not by data type.
- **ITS_Time_Off in Human Review, not System.** Personnel admins need to maintain it; they should not be granted access to the operator surface. Lives under `06 — Personnel` at the bottom of Human Review.
- **build_human_review.py archived rather than deleted.** Preservation-over-refactor (Op Stds v7 §14). Git history is the diff reference; script gets a deprecation header and a `sys.exit(1)` guard in `__main__`.
- **Wide-vs-tall ITS_Config: tall.** `kill_switch.py`'s stub docstring described a wide single-row layout, but per-workstream allowlists and reviewer-chain overrides don't extend cleanly to column adds. Tall (key/value) keeps the column count fixed; `kill_switch.py` refactor to read by Setting name is deferred to the smartsheet_client.py wiring session.
- **Schema deviations forced by Smartsheet API constraints.** DATETIME columns rejected at create unless paired with a system column type; fell back to DATE. AUTO_NUMBER rejected at create regardless of primary status; fell back to plain TEXT_NUMBER primaries that code populates. Both captured in `docs/tech_debt.md`.
- **Token-in-chat for the three MCP-gap operations.** The Smartsheet MCP doesn't expose sheet-move or folder-delete. Seth provided a short-lived sandbox API token, used inline in three curl calls (two `POST /sheets/{id}/move`, one `DELETE /folders/{id}`), not retained in any file or env. Rotated by Seth post-session.

## Open items handed off

- `shared/smartsheet_client.py` wiring — next session. Prerequisite: `ITS_SMARTSHEET_TOKEN` in Seth's macOS Keychain (`security add-generic-password -a "$USER" -s ITS_SMARTSHEET_TOKEN -w`). Pattern is the SDK-based wrapper described in Excellence Roadmap §1.2 — typed exception hierarchy mirroring `graph_client.py`, retry-on-429 since the SDK doesn't retry by default.
- `shared/box_client.py` wiring — subsequent session. JWT auth, different SDK; do not bundle with smartsheet_client.py to avoid context-switch tax.
- `ITS_Config` initial row seeding — at minimum `system_state=ACTIVE` so `kill_switch.py` has something to read once it's wired. Land alongside the kill_switch refactor.
- Reviewer-chain initial seed in `ITS_Config` — Teala/Sam/Jacob entries per `shared.defaults.DEFAULT_REVIEWER_CHAINS`.
- `shared/kill_switch.py` refactor for the tall ITS_Config schema (read by Setting name rather than wide single-row column read).

## What was NOT touched

Explicitly out of scope for this session and intentionally left alone:

- `shared/smartsheet_client.py` + `shared/box_client.py` (still stubs)
- `shared/kill_switch.py` (read-by-key refactor deferred)
- `shared/error_log.py` (Smartsheet write path deferred)
- `scripts/watchdog.py` (still stub — depends on smartsheet_client.py)
- `safety_reports/intake.py`, `weekly_summary.py` (workstream sandbox build is a separate concern)
- `tests/test_capability_gating.py` GATED_SCRIPTS / SEND_SCRIPTS lists (still empty — populated when `weekly_generate.py` + `weekly_send.py` land)
- Inline docstring drift in `shared/*.py` and `safety_reports/*` (preservation-over-refactor; revisits only when substance of a referenced section changes)
- Planning-layer doc bumps in the Claude.ai project (cascade event will trigger those)

## Lessons captured to memory

- Smartsheet MCP gaps (sheet-move, sheet-delete, folder-delete) require REST API fallback. The pattern: short-lived token + inline curl + verify-via-MCP after. Token never persisted to file or env. Operational documentation for this pattern can land in a future `docs/operational_patterns/` if a second case appears.
- DATETIME and AUTO_NUMBER constraints captured in `docs/tech_debt.md`. Generalizable rule: trust the smartsheet-python-sdk docs over the API docs for column-type acceptance at create time; the API spec is more permissive than the implementation.
- Workspace topology decision was driven by audience (operator vs. customer), not data type. This pattern likely generalizes to Customer 2 onboarding: each customer gets their own customer-facing workspace; the System workspace stays singular and serves all customers.
