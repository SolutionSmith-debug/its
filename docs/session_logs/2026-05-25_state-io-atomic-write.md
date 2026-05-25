# 2026-05-25 — `shared/state_io.py` + atomic-write migration (closes F19 + F23)

PR: [#88](https://github.com/SolutionSmith-debug/its/pull/88) — squash-merged at 2026-05-25T19:07:49Z. Merge commit `36932bdbf9aff010dfee0a6c8fc7afd80e938437`. Four-part PR-landed verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on merge commit = SUCCESS).

First PR of the Phase 1.4 hardening cluster. Establishes `shared/state_io.py` as the canonical entry point for daemon-managed state-file writes and migrates seven callsites across `safety_reports/intake_poll.py` + `safety_reports/weekly_send_poll.py` to atomic-write + sidecar-flock semantics. Closes forensic-audit findings F19 (atomic-write seen-set) and F23 (concurrent-writer lock on the shared `heartbeat_row_ids.json`).

## Purpose

Two correctness gaps in the existing daemon state-file writes:

- **F19** — `Path.write_text(json.dumps(...))` is NOT crash-safe. A mid-write crash leaves a truncated file; subsequent reads can hit `json.JSONDecodeError` and silently reset state (the existing read-paths return `{}` on decode failure, which is fine for a fresh `safety_intake_processed.json` but means a corrupted file is undetectable from the recovery path).
- **F23** — `~/its/state/heartbeat_row_ids.json` is SHARED between `intake_poll` and `weekly_send_poll` with NO locking. Both daemons do read-modify-write triples on the same file keyed by `daemon_name`. The per-daemon `safety_intake.lock` / `weekly_send.lock` flocks protect against same-daemon overlap; they do NOT protect against cross-daemon overlap. The race: weekly_send_poll reads, intake_poll writes, weekly_send_poll writes back — intake_poll's entry is clobbered.

`shared/state_io.py` provides the correctness floor; `shared/heartbeat.py` consolidation (the structural cleanup that would deduplicate the helpers across both daemons) remains a separate tech-debt entry deliberately out of scope for this PR.

## Pre-flight verification

Per the brief's pre-flight checklist (verbatim from the brief, executed before any code touched):

1. ✓ `shared/state_io.py` does not exist (`ls` → "No such file or directory").
2. ✓ All seven migration callsite line numbers match: intake_poll 261/273/326/341, weekly_send_poll 184/228/242. weekly_send_poll:352 (watchdog marker) confirmed as the OOS marker; not on the migration list.
3. ✓ `shared/alert_dedupe.py::_dump_state` uses the existing same-FD flock pattern (lines 178-184, 193-199); not migrated this PR — sequencing decision 2026-05-25 keeps the new helper landing clean before alert_dedupe's pivots.
4. ✓ `tests/conftest.py` autouse fixtures from PR #74 in place (`_mock_keychain` + `_mock_kill_switch_state`); state_io's tests inherit them but use neither, so they're no-ops.

## Code changes

### New files
- `shared/state_io.py` — ~140 lines. `atomic_write_json(path, data)` + `atomic_write_text(path, text)` + `with_path_lock(path)` context manager + typed `StateLockTimeoutError`. Module-level constants `_LOCK_RETRY_ATTEMPTS=5` / `_LOCK_RETRY_DELAY_SECONDS=0.05` mirror `shared/alert_dedupe.py` so the two state-file callers share one timing knob.
- `tests/test_state_io.py` — 17 unit tests. Includes the F23 concurrent-thread regression (two threads RMWing the same shared file — both writes land) and the sidecar-survives-`os.replace` regression that proves the lock pattern is correct.

### Modified files
- `safety_reports/intake_poll.py` — 4 callsites migrated. SEEN_PATH (line 261) → `atomic_write_json`. HEARTBEAT_PATH (line 273) → `atomic_write_text`. HEARTBEAT_ROW_STATE_PATH persist (line 326) + invalidate (line 341) — wrapped read-modify-write triples inside `with_path_lock` with lock-timeout WARN handler. Import added: `state_io`.
- `safety_reports/weekly_send_poll.py` — 3 callsites migrated. HEARTBEAT_PATH (line 184) → `atomic_write_text`. HEARTBEAT_ROW_STATE_PATH persist (line 228) + invalidate (line 242) — same RMW-under-lock pattern as intake_poll. Import added: `state_io`.
- `CLAUDE.md` — new `shared/state_io.py` row in the stubbed-vs-real table between `alert_dedupe` and `resend_client`; ARCH-2 line extended to note the new write mechanism (path + semantics unchanged; only the write hardened); "What NOT to do" rule added: no direct `Path.write_text` / `Path.write_bytes` on any file under `~/its/state/`.
- `docs/tech_debt.md` — new CLOSED entry near the top for F19 + F23, references the forensic audit + the seven migrated callsites + the lock-timeout WARN contract.

## Decisions made during session

- **`error_log` API deviation from brief.** The brief's lock-timeout WARN snippet called `error_log.log_error(category=..., severity="WARN")`, which is not the actual API. The real `shared/error_log.py` exposes `log(severity, script, message, *, error_code=...)` and the existing `daemon_health_write_failed` callsites in both daemons use that signature verbatim. Used the canonical signature; mirrors the existing pattern. Per [[feedback_verify_ci_diagnosis_before_fix]], briefs occasionally state stale or wrong things — pause before applying. This was the second hit on that pattern (the first was a stale CI failure cause; this is a stale API signature).
- **Sidecar-flock pattern, not data-file flock.** The lock lives at `{path}.lock`, never on the data file itself. `atomic_write_json` / `atomic_write_text` swap the inode of the data file via `os.replace`; a flock held on the data file would be invalidated by every atomic write (the kernel-side lock is per-inode, the held FD ends up pointing to the now-orphaned inode). The sidecar file is never replaced, so the lock survives every atomic write inside the context. Regression test (`test_sidecar_lock_survives_atomic_write_on_data_path`) makes the property explicit.
- **Lock wraps the RMW triple, not just the write.** Brief was explicit about this; reinforced by the F23 failure mode (the read can be interleaved by the other daemon if only the write is locked). The two heartbeat-row helpers each move their internal read + mutate + write block inside the `with_path_lock` context. Inside the lock, the existing `if path.exists() / try: json.loads / except: current = {}` recovery branch is preserved verbatim — not collapsed to the brief's simplified snippet which had a slightly different recovery (`pass` not `current = {}` and `current[daemon_name] = row_id` not `{"row_id": ..., "total_cycles": ...}`). Per [[feedback_preservation_over_refactor]] the actual code's specifics win over the brief's general-pattern snippet.
- **Lock-timeout fails open (WARN + skip), does not raise.** Heartbeat write must never block daemon primary work (CLAUDE.md "Operator visibility surface"). On `StateLockTimeoutError`: log a WARN with `error_code="daemon_health_write_failed"` (mirroring the existing failure path) and continue the cycle. The next cycle's RMW re-tries; missing one row update is benign because total_cycles is monotonic and the row_id cache resolves on its own 404.
- **Same-FD flock vs. sidecar flock — chose sidecar for state_io.** `shared/alert_dedupe.py` uses same-FD flock (open the data file with `a+`, hold the FD while reading + writing through it). That works because alert_dedupe writes via the held FD itself (`fh.seek(0); fh.truncate(); fh.write(...)`), so the inode never changes. state_io writes via `os.replace` (the only way to be crash-safe on POSIX), so same-FD flock is incompatible — hence the sidecar. The alert_dedupe migration (PR 2 of the cluster) will pivot to the sidecar pattern too once state_io is in place; sequencing this PR first means alert_dedupe lands as a clean refactor against an established helper.
- **`shared/heartbeat.py` consolidation deliberately deferred.** Brief explicitly carves this out (the 2nd-consumer extraction trigger from Op Stds v11 §14 is met, but consolidation is a separate ship). PR #88 is the correctness floor; the structural cleanup that would deduplicate the seven heartbeat helpers across both daemons is the next consolidation PR's job. Tech-debt entry at `docs/tech_debt.md` line 665 remains open.
- **`sort_keys=True` in `atomic_write_json` — accepted minor on-disk diff change.** The previous code used `json.dumps(seen, indent=2)` without `sort_keys`. The new helper uses `sort_keys=True` per the brief's spec (deterministic output is the canonical pattern). Effect: existing on-disk files will have their keys re-ordered on first write — benign because all readers do `json.loads` + dict-key lookup. No semantic change.
- **Test approach: threading, not subprocess, for the F23 regression.** `fcntl.flock` is per-FD (not per-PID) on macOS / Linux, so two threads in one process opening the sidecar each get their own FD and the kernel serializes them just as it would for two processes. Threading is faster + simpler in a pytest context. The contention is genuine (one thread holds the lock; the other sees `BlockingIOError` and retries 5×50ms).

## CI runs

- **Build #1** (push to `feat/state-io-atomic-write`) — PR-build `test` workflow → SUCCESS. Polled to completion before squash-merge.
- **Build #2** (push to `main` via squash-merge commit `36932bd`) — main-branch `test` workflow → SUCCESS. Run [26415818170](https://github.com/SolutionSmith-debug/its/actions/runs/26415818170), `test in 46s`. Step 4 of the four-part PR-landed discipline satisfied.

## Verification

| Stage | Result |
|-------|--------|
| pytest -q (full suite) | **1050 passed / 16 deselected** (+17 from 1033 baseline; `tests/test_state_io.py` adds 17). |
| mypy on the four touched files | **0 errors / 4 source files** (`shared/state_io.py`, `safety_reports/intake_poll.py`, `safety_reports/weekly_send_poll.py`, `tests/test_state_io.py`). |
| ruff check on the four touched files | **clean** (two findings auto-/manually-fixed pre-commit: import-block blank line in `test_state_io.py`; `Boom` → `BoomError` N818). |
| main-branch CI on merge commit | **SUCCESS** (run 26415818170, headSha=36932bd, 46s). |

### Operator-side manual smoke (sandbox, pre-merge)

- ✓ Both daemons exit=0 on a clean cycle.
- ✓ `~/its/state/heartbeat_row_ids.json` valid JSON; atomic write confirmed for intake_poll (row_id=7461022174478212, total_cycles=5543).
- ✓ Sidecar lock file present at `~/its/state/heartbeat_row_ids.json.lock`.
- ✓ Both local heartbeat `.txt` files updated with valid ISO timestamps via `atomic_write_text`.
- ✓ Zero stray `*.tmp.*` residue in either post-cycle state.

Two non-blocking flags surfaced — pre-existing, not PR #88 regressions:
- `safety_intake_processed.json` doesn't exist on this machine (fetched=0 on the smoke cycle means no new seen-set entries to persist; F19 unit test is the canonical correctness proof — see `test_atomic_write_json_writes_valid_json` + `test_atomic_write_json_concurrent_readers_never_see_torn_writes`).
- `weekly_send_poll`'s `ITS_Daemon_Health` row is not seeded — surfaces as WARN "seeder needed" during cycle; daemon continues fail-open per ARCH-2; F23 concurrent-thread regression test is the canonical protection assertion.

## Subtleties found mid-implementation

- **Brief-snippet shape vs. actual existing code.** Brief's "current" snippet for the heartbeat-row persist had `except: pass` + `current[daemon_name] = row_id` (just the int). Actual existing code has `except: current = {}` + `current[daemon_name] = {"row_id": ..., "total_cycles": ...}` (dict-valued). Preserved the actual existing structure — the brief was sketching the general pattern, not the exact code.
- **Test-suite count.** Baseline pre-PR was 1033 passed. State_io adds 17 tests; final count is 1050 passed. No existing tests broke despite the `_persist_heartbeat_row_state` / `_invalidate_heartbeat_row_state` helpers now wrapping their bodies in `with_path_lock` — the existing `heartbeat_state_in_tmp` autouse fixture in `tests/test_intake_poll.py` redirects `HEARTBEAT_ROW_STATE_PATH` to a tmp path, and the sidecar `.lock` file lands next to it without any test-side accommodation needed.
- **ruff N818 — `Boom` exception class.** Test-only helper exception inside `test_with_path_lock_releases_on_exception_in_body`; renamed `Boom` → `BoomError` to satisfy `N818`. Trivial.
- **`_make_temp_path` factored out, not inlined.** Both `atomic_write_json` and `atomic_write_text` use the same `{name}.tmp.{pid}.{random_suffix}` pattern; the helper is 3 lines and used twice. Minor DRY win, kept private.
- **`secrets.token_hex(4)` for the temp-file suffix.** 8 hex chars (32 bits of entropy); collision-resistant enough for per-process per-call temp paths without overkill. PID+suffix combined gives effectively zero collision probability across the small population of concurrent writers (≤2 daemons today; ≤handful long-term).

## Open items handed off

1. **Operator action — seed `weekly_send_poll` row in `ITS_Daemon_Health`.** Pre-existing gap unrelated to this PR; surfaces as WARN "seeder needed" on every cycle until done. Suggested seeder script pattern: mirror `scripts/migrations/seed_safety_intake_polling_config.py` but writing to ITS_Daemon_Health with `daemon_name="safety_reports.weekly_send_poll"` + the standard column defaults.
2. **PR 2 of the Phase 1.4 hardening cluster — `shared/alert_dedupe.py` migration.** Pivot from same-FD flock to the new state_io sidecar-flock pattern. The existing `_acquire_lock(fh)` / `_dump_state(fh, state)` helpers retire; the four public functions (`should_fire`, `record_fire`, `list_expired_summaries`, `mark_summarized`, `delete_entry`) move to `with state_io.with_path_lock(STATE_FILE):` + `state_io.atomic_write_json(STATE_FILE, state)` inside. Test suite at `tests/test_alert_dedupe.py` should pass unchanged (fixture redirects `STATE_FILE` to tmp_path; sidecar lands next to it).
3. **PR 3+ of the cluster.** Per the brief's "Sequencing context" section: F02 + F22 paired, F08 + F09 paired (circuit_breaker.py consumes `state_io.atomic_write_json`), then F16/F17/F18+F03/F04/F10 in parallel-safe order.
4. **Tech-debt entry remaining open.** `shared/heartbeat.py` + `shared/runner.py` extraction (`docs/tech_debt.md` line 665) — the seven heartbeat helpers are still inline-replicated across intake_poll + weekly_send_poll. This PR hardens their underlying write mechanism; consolidation is the next structural cleanup.

## What was NOT touched

- `shared/alert_dedupe.py` — separate PR 2 per sequencing decision 2026-05-25.
- `shared/heartbeat.py` (does not exist yet) — extraction deferred to the consolidation PR.
- `safety_reports/weekly_send_poll.py:352` — the watchdog Check C marker (`{WATCHDOG_JOB_SLUG}.last_run`). Forensic marker, not canonical daemon state. Explicitly OOS per brief.
- `STATE_DIR` path itself — stable at `~/its/state/`; no path renames.
- ITS_Config rows — no new config plumbing.
- Doctrine v-bumps in `its-blueprint` — the audit (`audits/2026-05-25_forensic-audit.md`) and grill session (`session-logs/2026-05-25_safety-portal-grill.md`) already absorbed the planning-side decisions; FM Invariant 1 / 2 + Op Stds §31 unchanged.
- Existing read-paths (`_load_heartbeat_row_state`, `_load_seen`) — reads outside the lock are intentionally OK. `os.replace` guarantees a reader sees either the old or new file, never a torn write; stale reads on the row-id cache are benign because the next cycle re-resolves on 404.
- F19's hardened idempotency layer (message_id → row_id index) — out of scope for Phase 1 per the existing `intake_poll.py` docstring; this PR closes only the atomic-write half of F19, not the idempotency half.

## Lessons captured to memory

- **[[feedback_verify_ci_diagnosis_before_fix]]** — extended-by-pattern. The memory previously framed this around stale CI failure causes; this session added a second hit on the same pattern in a different domain (a stale API signature inside an engineering brief). The underlying rule is identical: briefs occasionally state stale or wrong specifics; pull the actual code/log and pause before applying. No memory update needed — the rule's framing already generalizes.
- **[[feedback_preservation_over_refactor]]** — exercised cleanly. The brief's "current" snippet for the RMW block was a simplified pattern, not a verbatim transcription. Preserved the actual existing code's specifics (the dict-valued entries, the `except: current = {}` recovery branch). The memory's "preserve verbatim; defer abstraction until ≥4 real cases" rule extended naturally to "preserve the actual code, not the brief's simplification."
- **No new memory file warranted.** The session-specific decisions (sidecar-flock pattern, lock-wraps-RMW, fail-open on lock timeout) are properties of the new module's contract and are documented in `shared/state_io.py`'s module docstring + `CLAUDE.md`'s stubbed-vs-real entry + this session log — three discoverable surfaces, no need for a parallel memory file.
