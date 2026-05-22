# 2026-05-22 — 2026-05-22 cascade absorb (repo-side doc reconciliation)

PR: [#61](https://github.com/SolutionSmith-debug/its/pull/61) — squash-merged at 2026-05-22T20:47:43Z. Merge commit `1ca136ac58ddee8a3025e9798cda9ac783b09aa2`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Single chore PR (Option A from the brief) reconciling `CLAUDE.md`, `README.md`, and `docs/tech_debt.md` to the planning-project cascade landed 2026-05-22 (Foundation Mission v8, Operational Standards v11, V&R v7.2, Handover Plan v6.3 + the §31-36 doctrine additions + Invariant 2 Layer 6). Doc-only; zero code paths touched.

## What shipped

- **CLAUDE.md** —
  - Canonical-doc references bumped: FM v7→v8, OS v9→v11, V&R v7→v7.2, HP v6.1→v6.3.
  - Stubbed-vs-Real table: `safety_reports/intake.py` flipped Stub → Working/live-validated end-to-end (12-stage pipeline, PR #57/#59). Added two new rows: `intake_poll.py` (632 lines, polling daemon, 60s launchd cadence) and `week_folder.py` (168 lines, idempotent find-or-create).
  - Invariant 2 §1 rewritten — Mail.app-rule framing dropped (retired in PR #59); now describes the polling-daemon pattern + ITS_Trusted_Contacts mechanism (Op Stds v11 §33) + header-forgery detection.
  - Invariant 2 expanded from 5 → 6 layers: new Layer 6 (attachment screening pipeline, Op Stds v11 §34).
  - "Adding a new workstream" §8: polling-daemon doctrine codified as canonical for intake-bearing workstreams; Mail.app rules deprecated.
  - New "Operator visibility surface" section between Observability stack and "What NOT to do" — documents ITS_Daemon_Health canonical schema + ARCH-1 (Enabled checkbox is report-filter metadata only) / ARCH-2 (row-id cache at `~/its/state/heartbeat_row_ids.json`) / ARCH-3 (Total Cycles lifetime monotonic) invariants.
- **README.md** —
  - Phase 0 row: test count corrected to actual `137→781` (+644 from baseline; the brief said 779, but live count was 781 — see drift note below); added ruff-clean + polling-daemon doctrine sentences.
  - Phase 1 row: intake.py + intake_poll.py + Daemon_Health heartbeat reported live (#57/#59/#60); R3 Session 2 (`weekly_generate.py`) called out as next critical-path target with zero code-side prereqs; Phase 1.4 pre-Customer-1 security cluster (3 deliverables) flagged.
  - `shared/` description expanded to full helper inventory (added Keychain wrapper, review queue, scheduling, sheet IDs, defaults, the API client roll-up).
  - Trigger-primitives sentence rewritten: launchd-driven polling daemons canonical; Mail.app rules deprecated.
  - Doc-version references bumped FM v7→v8, OS v9→v11.
- **docs/tech_debt.md** — six new `[OPEN 2026-05-22]` entries appended:
  1. Picklist-hardening pre-Customer-1 (Op Stds v11 §35).
  2. ITS_Trusted_Contacts sheet replaces ITS_Config JSON allowlists (Op Stds v11 §33 + FM v8 Invariant 2 Layer 1).
  3. Attachment screening pipeline Layers 1-3 (Op Stds v11 §34 + FM v8 Invariant 2 Layer 6).
  4. 5-duplicate ITS_Errors sheets in System/02-Logs (operator UI delete required).
  5. 1 empty duplicate ITS_Daemon_Health sheet (operator UI delete required).
  6. Watchdog Check F retirement / Check H heartbeat-staleness successor.

  Existing Mail.app silent-disable entry verified already `[PARTIALLY MITIGATED 2026-05-22]` — no change needed.

## Decisions made during session

- **Brief drift on test count — actual 781, brief said 779.** Verified local `pytest -q`: 781 passed / 1 skipped / 10 deselected. Brief said 779/3/10. The brief explicitly instructed "If any of these counts changed since 2026-05-22, update README.md test count to match current actual," so I wrote 781 (+644 from baseline) into the Phase 0 row rather than the brief's stale 779. Skipped count is 1, not 3, but the README row doesn't carry a skipped-count field so no further edit.
- **Single PR (Option A) over two PRs (Option B).** Brief listed both options. Picked Option A — the three doc surfaces are tightly coupled (all reference the same cascade event); splitting would create a cross-PR coherency window where the docs disagree on FM/OS version. Doc-only PR with no code blast radius justifies single-shot.
- **Preserved Mail.app silent-disable entry verbatim.** Brief verified the entry was already marked `[PARTIALLY MITIGATED 2026-05-22]` with the right language; no change needed. The new Check H successor entry references this one rather than rewriting it.
- **Used brief's suggested `**Effort:**` + `**Revisit when:**` format for the new tech_debt entries** even though existing entries use varying subsection labels (Resolves at:, Workaround:, Mitigation in place:, etc.). The brief explicitly suggested this format; existing entries don't fit a single template, so consistency-within-the-cluster matters more than file-wide template uniformity.

## CI runs

- Build #1 (push to `chore/2026-05-22-cascade-absorb`) — `test` workflow → SUCCESS. Polled to completion before squash-merge.

## Operator-side actions remaining

The two duplicate-sheet cleanup entries (#4 + #5 in tech_debt) require operator UI delete since Smartsheet MCP has no delete-sheet primitive:

- **System / 02 — Logs** — delete sheets `2704945844277124`, `470411799121796`, `4505679602601860`, `4195780532326276`. Keep canonical `27291433258884`.
- **System / 04 — Daemons** — delete empty sheet `3717381690969988`. Keep canonical `4529351700729732`.

Both non-blocking. Surface in next operator Smartsheet UI session.

## What's NOT touched

- No code paths edited. `safety_reports/`, `shared/`, `scripts/` untouched.
- No test changes. The 781/1/10 counts pre- and post-PR are identical (pre-flight verification only).
- No schema files. `schemas/`, `prompts/`, `SHEET_*` constants unchanged.
- No new memories saved — the cascade-shipped doctrine is the canonical reference; this session's role is repo-side reconciliation, not new policy.
- The Phase 1.4 security hardening cluster (picklist-hardening + ITS_Trusted_Contacts + attachment screening) is now logged in tech_debt but not implemented — it's the next-after-R3-Session-3 critical path per V&R v7.2.

## Baseline state at session close

- `main` at `1ca136a` (PR #61 merge commit).
- pytest **781 passed / 1 skipped / 10 deselected**. mypy **0 errors / 93 source files**. ruff **clean**.
- Daemon: still running, still healthy, still writing to ITS_Daemon_Health row 7461022174478212 every 60 seconds (PR #61 was doc-only — no code touched).
- R3 Session 2 (`weekly_generate.py`) remains the immediate-next critical-path target with zero code-side prereqs.
