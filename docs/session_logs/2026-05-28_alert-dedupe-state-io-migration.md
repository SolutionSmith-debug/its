---
type: session_log
date: 2026-05-28
status: closed
workstream: infrastructure
related_prs: [103, 88]
tags: [phase-1.4, alert-dedupe, state-io, atomic-write, sidecar-lock, fail-open, op-stds-3.1, op-stds-42, migration]
---

# 2026-05-28 — `shared/alert_dedupe.py` → `state_io` migration (PR 2 of Phase 1.4 hardening cluster)

PR: [#103](https://github.com/SolutionSmith-debug/its/pull/103) — _merge details filled at landing_.

Second PR of the Phase 1.4 hardening cluster. Migrates `shared/alert_dedupe.py` off
its same-FD-flock pattern (`STATE_FILE.open("a+")` + `fcntl.flock` + `_acquire_lock(fh)` +
`_load_state(fh)` / `_dump_state(fh)`) onto the `shared/state_io.py` sidecar-lock +
atomic-write helpers landed in PR #88. Brings `alert_dedupe` into compliance with the
CLAUDE.md rule "no direct `Path.write_text` under `~/its/state/`" and closes the
predecessor's deferred follow-on (tracked in the F19+F23 CLOSED tech-debt entry +
`shared/state_io.py`'s docstring Consumers note). Mechanical migration — zero dedupe
behavior change.

## Pre-flight verification (re-grepped against HEAD `c5cc456`; brief base `a1dc227` is an ancestor, no drift)

The brief was authored against `a1dc227` and explicitly warned its line numbers were
stale. Re-verified everything:

1. ✓ `shared/state_io.py` exposes `atomic_write_json(path, data)`, `atomic_write_text(path, text)`,
   `with_path_lock(path)` (context manager), `StateLockTimeoutError`. Retry budget (5×50ms)
   lives in state_io. Signatures match.
2. ✓ `shared/alert_dedupe.py` had exactly **five** `STATE_FILE.open("a+")` sites (lines
   215/253/302/355/389) — four read-modify-write, one read-only (`list_expired_summaries`).
3. ✓ `tests/test_alert_dedupe.py` had **five** `_acquire_lock` patch sites (212/224/234/479/515) +
   one docstring mention (~330). Two `boom` (RuntimeError) sites, three `lambda fh: False` sites.
   File was 37 tests.
4. ✓ No new F24+ audit finding against alert_dedupe since 2026-05-25.

**Stale brief line-numbers corrected at pre-flight (brief's number → actual):**
- CLAUDE.md alert_dedupe row: 116 → **123**. state_io row: 117 → **124**.
- "no direct write_text" rule: row 209 → **216**.
- tech_debt "follow-on" note: lines 11–13 (now the portal-pivot entry) → **line 27**
  (inside the F19+F23 CLOSED section).
- The smoke-test triple-fire reference (brief 339–347) → the "Smoke harness pattern
  divergence" entry (lines 353–363).
- `prompts/scaffold/*` do NOT exist in the exec repo — they live in `its-blueprint/prompts/scaffold/`.
- Full-suite baseline: brief said 866 → **actual 1062** (the +2 delta the brief cited was right).

## The lock-free-read correctness justification (why callsite #3 is safe without a lock)

`list_expired_summaries()` reads via `_load_state_from_path()`, which is a single
`STATE_FILE.read_text()` — one `open()` + one `read()`. The `open()` syscall binds the
returned fd to whatever inode `STATE_FILE` points to *at that instant*; the fd stays
pinned to that inode for its lifetime regardless of later directory-entry changes.

Writers never mutate a live inode in place. The OLD code did `fh.seek(0); fh.truncate();
fh.write(...)` — an in-place mutation that created a torn-read window, which is exactly
why the old reader had to hold the flock. The NEW writer path (`state_io.atomic_write_json`)
writes the complete new content to a fresh temp inode, then `os.replace(tmp, STATE_FILE)`
— a `rename(2)` that atomically repoints the directory entry from the old inode to the
temp's inode. The old inode is never truncated; it is merely unlinked (and survives as
long as any reader holds it open).

Therefore the reader always sees ONE complete file:
- `open()` before a concurrent `os.replace` → fd pinned to the old, complete, immutable inode.
- `open()` after the `os.replace` → resolves to the new, complete inode.
- There is no instant at which `STATE_FILE` resolves to a half-written file, because the
  temp is fully written before the atomic rename, and the rename is atomic.

A lock would only serialize the reader against writers — but with no torn-read window to
protect against, it adds latency and contention against genuine CRITICAL writers for zero
safety. Writers still lock because two concurrent read-modify-write cycles could lose an
update (the lost-update problem — distinct from torn reads); the reader performs no write,
so it cannot lose an update and needs no lock. Staleness is benign: an entry the snapshot
shows as expired is still expired next sweep, a just-(re)opened window is excluded anyway,
and the sweep's actual mutations (`mark_summarized` / `delete_entry`) re-read fresh state
*under the lock* before writing — so the lock-free snapshot is advisory, never load-bearing
for a mutation decision.

## Code changes

### `shared/alert_dedupe.py`
- DELETED `_acquire_lock(fh)`, `_load_state(fh)`, `_dump_state(fh, state)`, and the
  module-level `_LOCK_RETRY_ATTEMPTS` / `_LOCK_RETRY_DELAY_SECONDS` constants (state_io owns the retry budget).
- ADDED `_load_state_from_path() -> dict[str, dict[str, Any]]` — single `read_text()`,
  fail-open to `{}` with the **same marker text** as the retired `_load_state` (corrupt-JSON
  and non-object-root markers preserved verbatim). Safe locked (writers) or unlocked (reader).
- Migrated the four R-M-W functions (`should_fire`, `record_fire`, `mark_summarized`,
  `delete_entry`) to `with state_io.with_path_lock(STATE_FILE):` + `state_io.atomic_write_json`.
  Each gained an `except state_io.StateLockTimeoutError` clause **before** the broad
  `except Exception` (ordering is load-bearing: the timeout subclasses Exception) — both
  route to the same per-function fail-open value, split only so the timeout case carries
  the §3.1 rationale comment.
- `list_expired_summaries` is now lock-free (kept its `exists()` early-out; reads via
  `_load_state_from_path`; fail-open via the broad `except Exception` only — no lock to time out).
- Lock-failure marker text preserved ("could not acquire flock on … after retries" + the
  per-function suffixes) so the contract reads identically to the operator.
- Imports: dropped `fcntl` + `time`; added `state_io` to the `from . import …` line.
- §42 module docstring rewritten to the four headings (Purpose / Invariants / Failure
  modes / Consumers), preserving the State JSON schema, PR α / PR β API tiers, Out-of-scope,
  and Cross-references beneath them. Two required §42 rationale comments added (should_fire
  timeout catch; list_expired_summaries lock-free read).

### `tests/test_alert_dedupe.py`
- Added module-level `_make_failing_lock(exc_class=StateLockTimeoutError, message="test")`
  (`@contextmanager` whose `__enter__` raises) + imports (`contextlib`, `StateLockTimeoutError`).
- Re-pointed all 5 lock-failure tests from `monkeypatch.setattr(alert_dedupe, "_acquire_lock", …)`
  to `monkeypatch.setattr("shared.state_io.with_path_lock", _make_failing_lock(...))`. The two
  former `boom` sites now raise `RuntimeError` (proving the broad `except Exception` fail-open);
  the three former `lambda fh: False` sites raise `StateLockTimeoutError` (proving the timeout catch).
- Updated the docstring of `test_concurrent_should_fire_calls_serialize_via_flock` to reference
  the `state_io.with_path_lock` contract instead of the deleted `_acquire_lock`.
- Added 2 new tests: `test_should_fire_returns_True_on_StateLockTimeoutError` (explicit D2 proof;
  brief-mandated name, carries `# noqa: N802` for the capitals) and
  `test_atomic_write_failure_leaves_no_tmp_residue` (forces `os.replace` to fail on the
  suppressed write path; asserts no `*.tmp.*` residue — regression guard on PR #88 cleanup
  as a new consumer leans on it). 37 → **39** tests.

### Docs
- `CLAUDE.md` alert_dedupe row (123): fcntl description → state_io write mechanism + lock-free
  reader note; lineage gains "+ PR #103 (state_io migration)". state_io row (124): alert_dedupe
  added as live consumer; "separate follow-on PR" clause removed.
- `docs/tech_debt.md` (F19+F23 CLOSED section): alert_dedupe migration noted as LANDED in PR #103;
  all three `~/its/state/` consumers now compliant with the row-216 rule.
- `shared/state_io.py` docstring Consumers: alert_dedupe listed; follow-on-PR sentence dropped.

## Decisions made during the session

- **Brief test name vs. ruff N802.** The brief mandated
  `test_should_fire_returns_True_on_StateLockTimeoutError` verbatim, but ruff `N` (pep8-naming)
  is active with no test-file exemption, so the capitals trip N802. Kept the brief's exact name
  and added `# noqa: N802` — honors the literal name AND the ruff-clean gate. (The alternative,
  lowercasing, would have silently dropped the brief's verbatim instruction.)
- **Marker text preserved over §42 informativeness.** Per the anti-pattern "DO NOT change …
  marker text," the lock-timeout markers keep the exact old "could not acquire flock … after
  retries" phrasing (not the state_io exception's own message). The §3.1 rationale lives in the
  in-code comment, not the marker — so existing test assertions hold unchanged and the operator
  sees an identical contract.
- **Reader-test mapping confirmed.** The brief flagged that any of the 5 patch sites targeting
  the now-lock-free `list_expired_summaries` would need re-pointing to a load/JSON exception
  instead. Verified: **none of the five** targeted the reader (they hit should_fire ×2,
  record_fire, mark_summarized, delete_entry). The reader's corrupt-state path is already covered
  by `test_list_expired_summaries_returns_empty_when_state_corrupt`, which exercises
  `_load_state_from_path`'s marker branch lock-free. No reader test needed adjustment.
- **`STATE_DIR.mkdir` calls dropped from writers.** `with_path_lock` creates the lock-file parent
  and `atomic_write_json` creates the data-file parent, so the explicit per-function mkdir was
  redundant. Removing it is behavior-preserving (the dir still gets created on first write).

## Settled rulings honored (not re-opened)

- Fail-open-on-timeout (D2) — doctrine (Op Stds §3.1). `StateLockTimeoutError` is caught and
  routed to fail-open in every function; never propagated.
- Four-heading §42 docstring (D3) — present.
- Lock-free read for `list_expired_summaries` — ratified; implemented with the rationale comment.

## Verification

| Stage | Result |
|-------|--------|
| pytest -q (full suite) | **1064 passed / 16 deselected** (+2 from 1062 baseline; `test_alert_dedupe.py` 37 → 39). |
| mypy . | **0 errors / 131 source files.** |
| ruff check . | **clean.** |
| main-branch CI on merge commit | _filled at landing_. |

Four-part PR-landed verify: _filled at landing_.

### Operator-side manual smoke (sandbox, pre-merge)

_Filled from operator paste-back per `its-blueprint/prompts/scaffold/manual-smoke.md`
(7-assertion checklist; no 8th no-clobber check — single-writer file)._

## Out of scope

- `shared/heartbeat.py` / `shared/runner.py` consolidation (still tech-debt).
- Any dedupe behavior change (window resolution, key granularity, marker text, fail-open returns).
- Watchdog Check G summary sweep (already shipped, PR #44).
- Multi-machine state sync (Phase 4+).
- Blueprint cascade — none this PR. (The Op Stds §42 worked example still cites alert_dedupe as
  "pending migration in PR 2"; that illustrative line is now historical but left untouched per
  the no-blueprint-cascade fence — a trivial future opportunistic cleanup, not a blocker.)

## Cross-references

- Predecessor PR #88 — `shared/state_io.py` (merge `36932bd`); session log
  `docs/session_logs/2026-05-25_state-io-atomic-write.md`.
- Audit F19 + F23 — `its-blueprint/audits/2026-05-25_forensic-audit.md` (closed by PR #88;
  this PR extended scope to the third consumer).
- Doctrine: Op Stds §3.1 (push-vs-record), §42 (self-documentation), §14 (preservation —
  applies inversely: migration because PR #88's helper now exists and row 216 forbids the old pattern).
- Scaffold: `its-blueprint/prompts/scaffold/shared-module-migration.md` v1.
