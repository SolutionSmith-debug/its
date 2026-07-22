"""~/its/logs directory-growth bound — the pruner ENGINE for watchdog Check W.

Mirror of `shared/errors_rotation.py` (the ITS_Errors row-cap predicates behind
Check O): pure, dependency-injectable functions so the watchdog check is a thin
caller and every branch is unit-testable WITHOUT touching a real launchd fd. This
is growth Slice 2 — Slice 1 cut the log SOURCES; this BOUNDS the on-disk retention.

**This is an ARCHIVE bound, NOT a cleanup. v1 NEVER unlinks a launchd path and
never deletes a forensic file.** Two retention lanes:

  DAILY   ``logs/<YYYY-MM-DD>.log`` older than ``DAILY_GZIP_AGE_DAYS`` (LOCAL date)
          → ``gzip_in_place``: archive to a verified ``<name>.log.gz`` sibling,
          THEN remove the original ``.log`` (removing the SUPERSET original of a
          verified archive is not forensic loss — the bytes survive in the .gz).

  LAUNCHD ``logs/launchd/<name>.out.log`` → ``archive_and_truncate``: copy the
          current bytes to a verified ``<name>.out.log.<stamp>.gz`` sibling, THEN
          ``os.truncate(path, 0)`` IN PLACE. The ONLY operation this module ever
          applies to a launchd path is *read* or *truncate* — never unlink, never
          rename, never move (see the module-inspectable guard in each fn).

Three traps this design honors (each proven in the Slice-2 probes):

  1. launchd hands the child an O_APPEND fd at spawn and there is NO SIGHUP handler
     anywhere in the system, so the fd follows the INODE, not the directory entry.
     A rename leaves the live file at 0 bytes forever (the KeepAlive dashboard label
     never heals); an unlink orphans the inode (space reclaimed only at close(), so
     a delete would report freeing bytes while freeing NONE). truncate-in-place is
     the only correct op — proven: size 2448 → 0 → 1530, clean resume, zero NULs.

  2. NO ``lsof`` branch. "No fd is held between fires" is a TOCTOU race, not a
     structure (one-shot pollers were sampled mid-cycle holding fds; portal_poll's
     max 83s cycle exceeds its 60s interval). The rule is UNCONDITIONAL truncate for
     every planned launchd .out.log. There is NO per-file mtime skip (F1, dropped
     2026-07-21): a fast always-on daemon (portal_poll writes its .out.log every 60s
     and is the LARGEST target at ~36 MB) always has a recent mtime, so a per-file
     recency skip would defer it on EVERY run and NEVER truncate it — the exact
     growth this module exists to bound. The ONLY incident guard is the WHOLE-LANE
     hold (``run_log_rotation(skip_launchd=…)``), which the watchdog drives from
     "an open CRITICAL is present"; a copy-gz-truncate loses no data either way (the
     bytes are in the verified .gz, and ``tail -f`` follows the inode across a
     truncate). ``is_incident_recent`` is retained as a pure helper but is no longer
     called by either lane.

  3. The daily cutoff is a LOCAL date. ``error_log.py`` names the daily file with
     naive ``datetime.now()`` while the line stamp is UTC, and the host runs EDT;
     a UTC-keyed pruner races the current file near midnight. We key on
     ``date.today()``. A file whose name is not a parseable LOCAL date is NEVER a
     daily candidate (``logs/.DS_Store`` exists today); ``logs/migrations/`` is an
     explicit-allowlist EXCLUDE (committed audit output) and is never walked.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from pathlib import Path

from . import defaults
from .error_log import LOG_DIR

# Re-export the canonical logs dir so callers/tests import one name.
__all__ = [
    "LOG_DIR",
    "LogKind",
    "LogEntry",
    "RotationOutcome",
    "parse_daily_date",
    "classify_entry",
    "scan_entries",
    "plan_daily",
    "plan_launchd",
    "is_incident_recent",
    "gzip_in_place",
    "archive_and_truncate",
    "run_log_rotation",
]


class LogKind(Enum):
    """Classification of a file under ``logs/``.

    DAILY       — ``logs/<YYYY-MM-DD>.log`` (rolling per-day error log).
    LAUNCHD_OUT — ``logs/launchd/<name>.out.log`` (truncate-only lane).
    EXCLUDED    — everything else: ``.err.log``, non-date daily names, already
                  ``.gz``, ``.DS_Store``, the migrations subdir, directories.
    """

    DAILY = auto()
    LAUNCHD_OUT = auto()
    EXCLUDED = auto()


@dataclass(frozen=True)
class LogEntry:
    """A stat'd candidate file. Immutable so planners are pure over their input."""

    path: Path
    kind: LogKind
    size_bytes: int
    mtime: float  # st_mtime, epoch seconds (wall-clock — pair with time.time())
    daily_date: date | None = None  # populated only for LogKind.DAILY


@dataclass(frozen=True)
class RotationOutcome:
    """Summary the watchdog turns into a CheckResult + note (mirrors Check O)."""

    daily_gzipped: int = 0
    daily_bytes_reclaimed: int = 0  # sum of removed .log sizes (now in .gz)
    daily_pending: int = 0  # eligible daily logs left past the per-run cap
    launchd_truncated: int = 0
    launchd_bytes_reclaimed: int = 0  # sum of bytes truncated away (now in .gz)
    launchd_pending: int = 0  # eligible launchd logs left past the per-run cap
    # F4: candidates whose st_size exceeds LOG_ROTATION_MAX_FILE_BYTES — never read,
    # surfaced as ABNORMAL naming the file(s) so a runaway log is operator-visible.
    oversized_skipped: int = 0
    oversized_files: tuple[str, ...] = field(default_factory=tuple)
    deadline_hit: bool = False
    dry_run: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def had_errors(self) -> bool:
        return bool(self.errors)

    def note(self) -> str:
        """One-line human summary, in the Check O ``_rotate_one_sheet`` voice."""
        prefix = "DRY RUN — would " if self.dry_run else ""
        parts = [
            f"{prefix}gzip {self.daily_gzipped} daily log(s) "
            f"(~{self.daily_bytes_reclaimed} B)",
            f"{prefix}truncate {self.launchd_truncated} launchd .out.log "
            f"(~{self.launchd_bytes_reclaimed} B)",
        ]
        if self.oversized_skipped:
            named = ", ".join(self.oversized_files) if self.oversized_files else "?"
            parts.append(
                f"{self.oversized_skipped} file(s) SKIPPED over size cap "
                f"(operator-actionable runaway: {named})"
            )
        if self.daily_pending or self.launchd_pending:
            parts.append(
                f"per-run cap hit: {self.daily_pending} daily + "
                f"{self.launchd_pending} launchd deferred to next run"
            )
        if self.deadline_hit:
            parts.append("DEADLINE hit — remainder deferred to next run")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s): " + "; ".join(self.errors))
        return "[log-dir-rotation] " + "; ".join(parts)


# --------------------------------------------------------------------------- #
# Classification — pure over a path + the logs root.                          #
# --------------------------------------------------------------------------- #


def parse_daily_date(name: str) -> date | None:
    """``"2026-07-21.log"`` → ``date(2026,7,21)``; anything else → ``None``.

    Only the exact ``<ISO-date>.log`` shape parses. ``foo.log`` (non-date),
    ``2026-07-21.log.gz`` (already archived), ``.DS_Store`` → ``None``.
    """
    if not name.endswith(".log"):
        return None
    stem = name[: -len(".log")]
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def classify_entry(path: Path, *, log_dir: Path = LOG_DIR) -> LogKind:
    """Classify a file by its path structure alone (no filesystem read).

    Order matters: the migrations allowlist-exclude and the ``.gz`` /
    ``.err.log`` excludes are checked before the positive DAILY / LAUNCHD_OUT
    matches, so an already-archived or excluded file can never be re-selected.
    """
    name = path.name

    # Explicit-allowlist EXCLUDE: never walk / never classify migrations output.
    try:
        rel_parts = path.relative_to(log_dir).parts
    except ValueError:
        # Not under log_dir at all — refuse to classify as anything actionable.
        return LogKind.EXCLUDED
    if defaults.LOG_DIR_MIGRATIONS_SUBDIR in rel_parts[:-1]:
        return LogKind.EXCLUDED

    # Already archived, or the incident file we must never touch.
    if name.endswith(".gz"):
        return LogKind.EXCLUDED
    if name.endswith(".err.log"):
        return LogKind.EXCLUDED

    parent = path.parent

    # LAUNCHD lane: logs/launchd/<name>.out.log
    if (
        parent == log_dir / defaults.LOG_DIR_LAUNCHD_SUBDIR
        and name.endswith(".out.log")
    ):
        return LogKind.LAUNCHD_OUT

    # DAILY lane: logs/<YYYY-MM-DD>.log (directly under log_dir).
    if parent == log_dir and parse_daily_date(name) is not None:
        return LogKind.DAILY

    return LogKind.EXCLUDED


def _is_temp_archive(name: str) -> bool:
    """True for a ``_make_temp`` orphan: ``.<name>.tmp.<pid>.<rand>[.gz]`` (F7).

    Matches ONLY the temp pattern this module writes — a leading dot plus the
    ``.tmp.`` infixe — never a real ``<name>.gz`` / ``<name>.<stamp>.gz`` archive
    (those have no leading dot and no ``.tmp.``), so the sweep can never reap a
    finished archive.
    """
    return name.startswith(".") and ".tmp." in name


def scan_entries(
    log_dir: Path = LOG_DIR,
    *,
    stat: Callable[[Path], os.stat_result] = os.stat,
    now_epoch: float | None = None,
) -> list[LogEntry]:
    """Walk ``log_dir`` one level deep + the launchd subdir, returning stat'd
    DAILY / LAUNCHD_OUT candidates. EXCLUDED files (incl. everything under
    ``migrations/``) are dropped here. ``stat`` is injectable for tests.

    Only two directories are ever walked: ``log_dir`` itself and
    ``log_dir/launchd`` — ``migrations/`` is never descended into.

    F7 side-effect: a crash-orphaned temp archive (``.<name>.tmp.<pid>.<rand>.gz``,
    older than ``LOG_ROTATION_TEMP_ORPHAN_AGE_SECONDS``) is opportunistically
    reaped here — best-effort, never fatal — so failed runs don't accumulate temp
    siblings that ``classify_entry`` would forever mark EXCLUDED (``.gz``).
    """
    now = now_epoch if now_epoch is not None else time.time()
    entries: list[LogEntry] = []
    dirs_to_walk = [log_dir, log_dir / defaults.LOG_DIR_LAUNCHD_SUBDIR]
    for directory in dirs_to_walk:
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.is_dir():
                continue
            if _is_temp_archive(child.name):
                # F7: reap only a STALE temp (from a dead run) — never one the
                # current run may be mid-write on. Best-effort: any error is left
                # for the next sweep, never surfaced.
                try:
                    st = stat(child)
                    if (now - st.st_mtime) > defaults.LOG_ROTATION_TEMP_ORPHAN_AGE_SECONDS:
                        child.unlink()
                except OSError:
                    pass
                continue
            kind = classify_entry(child, log_dir=log_dir)
            if kind is LogKind.EXCLUDED:
                continue
            try:
                st = stat(child)
            except OSError:
                # Vanished between listdir and stat — skip; next run re-scans.
                continue
            entries.append(
                LogEntry(
                    path=child,
                    kind=kind,
                    size_bytes=st.st_size,
                    mtime=st.st_mtime,
                    daily_date=(
                        parse_daily_date(child.name)
                        if kind is LogKind.DAILY
                        else None
                    ),
                )
            )
    return entries


# --------------------------------------------------------------------------- #
# Planners — pure over an iterable of already-stat'd entries.                  #
# --------------------------------------------------------------------------- #


def plan_daily(
    now_date: date,
    entries: Iterable[LogEntry],
    max_files: int,
    *,
    age_days: int = defaults.DAILY_GZIP_AGE_DAYS,
) -> list[LogEntry]:
    """Daily logs with age > ``age_days`` (LOCAL date), oldest-first, capped.

    The strict ``>`` plus the age filter guarantees the CURRENT local-date file
    (age 0) is never selected — trap #3. Ordering is (date, path) for a stable,
    oldest-first plan.
    """
    eligible = [
        e
        for e in entries
        if e.kind is LogKind.DAILY
        and e.daily_date is not None
        and (now_date - e.daily_date).days > age_days
    ]
    eligible.sort(key=lambda e: (e.daily_date or date.min, str(e.path)))
    return eligible[:max_files]


def plan_launchd(
    entries: Iterable[LogEntry],
    max_files: int,
) -> list[LogEntry]:
    """Non-empty launchd ``*.out.log`` files, LARGEST-first, capped.

    Largest-first spends the per-run/time budget on the biggest reclaim first.
    Zero-byte files are dropped (nothing to archive; the truncate would be a
    no-op producing an empty .gz). ``.err.log`` never reaches here — it is
    EXCLUDED at classification.
    """
    eligible = [
        e for e in entries if e.kind is LogKind.LAUNCHD_OUT and e.size_bytes > 0
    ]
    eligible.sort(key=lambda e: (-e.size_bytes, str(e.path)))
    return eligible[:max_files]


def is_incident_recent(
    mtime: float,
    now_epoch: float,
    skip_minutes: float,
) -> bool:
    """True if ``mtime`` is within ``skip_minutes`` of ``now_epoch``.

    ``mtime`` is ``st_mtime`` (wall-clock epoch), so ``now_epoch`` must be
    ``time.time()`` — NEVER ``time.monotonic()`` (trap: mixing the two clocks
    would make every file look ancient or brand-new). This is an operator
    courtesy (don't stomp a file being tailed), NOT an fd-liveness check (trap
    #2 — no ``lsof``).
    """
    return (now_epoch - mtime) < (skip_minutes * 60.0)


# --------------------------------------------------------------------------- #
# Archive primitives.                                                          #
#                                                                             #
# CORRECTNESS INVARIANT (inspectable): the ONLY filesystem op these two fns    #
# apply to a launchd path is streamed-READ or os.truncate. gzip_in_place (unlinks#
# its original) REFUSES a launchd-subdir path; archive_and_truncate REFUSES a  #
# non-launchd path. There is no unlink/rename/move of any launchd file anywhere#
# in this module. Grep the file for `unlink` / `os.replace` / `rename`: the    #
# only ``unlink`` is on a DAILY ``.log`` (never launchd), the only os.replace  #
# targets are ``.gz`` archive siblings (never a launchd .out.log).             #
# --------------------------------------------------------------------------- #


def _make_temp(path: Path, suffix: str = "") -> Path:
    return path.with_name(f".{path.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}{suffix}")


def _gzip_file_to_temp(src: Path, near: Path) -> tuple[Path, int]:
    """STREAM ``src`` gzip-compressed to a sibling temp of ``near``, then STREAM a
    round-trip verify, returning ``(temp_path, source_byte_length)`` (F4).

    Memory is bounded by ``LOG_ROTATION_GZIP_CHUNK_BYTES`` regardless of file size:
    the source is read in fixed chunks written incrementally to the gzip stream
    (never ``read_bytes()`` of the whole file), and the verify re-opens the temp
    and decompresses it in the same chunk size, comparing a RUNNING sha256 + byte
    length against the source's — no whole-file buffer on either side. Raises on any
    mismatch; the caller must NOT proceed to remove / truncate the source if this
    raised. Temp is cleaned up on any failure.
    """
    chunk = defaults.LOG_ROTATION_GZIP_CHUNK_BYTES
    tmp = _make_temp(near, ".gz")
    src_hash = hashlib.sha256()
    src_len = 0
    try:
        with open(src, "rb") as fin, gzip.open(tmp, "wb") as gz:
            while True:
                block = fin.read(chunk)
                if not block:
                    break
                src_hash.update(block)
                src_len += len(block)
                gz.write(block)
        # Streamed verify: decompress the temp in chunks, hashing as we go, so the
        # fidelity check never materializes the restored bytes in RAM either.
        out_hash = hashlib.sha256()
        out_len = 0
        with gzip.open(tmp, "rb") as gz:
            while True:
                block = gz.read(chunk)
                if not block:
                    break
                out_hash.update(block)
                out_len += len(block)
        if out_len != src_len or out_hash.digest() != src_hash.digest():
            raise OSError(
                f"gzip round-trip verification FAILED for {near} "
                f"({src_len} B in, {out_len} B out)"
            )
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return tmp, src_len


def gzip_in_place(path: Path) -> int:
    """DAILY lane: archive ``<name>.log`` to a verified ``<name>.log.gz`` sibling,
    THEN remove the original ``.log``. Returns the original byte size reclaimed.

    Crash-safety ordering: temp → verify → ``os.replace`` onto the ``.gz`` target
    → unlink the original. The original ``.log`` is removed ONLY after a verified
    archive is in place, so on a single-copy corpus there is never a removed
    original beside a half-written archive. A crash after os.replace but before
    unlink leaves BOTH ``.log`` and ``.log.gz`` — the next run simply re-archives
    (idempotent) and unlinks.

    REFUSES a launchd-subdir path (this fn unlinks its original; launchd files
    must only ever be truncated — trap #1).
    """
    if defaults.LOG_DIR_LAUNCHD_SUBDIR in path.parts:
        raise ValueError(
            f"gzip_in_place refuses a launchd path (must only truncate): {path}"
        )
    target = path.with_name(path.name + ".gz")  # <name>.log.gz — dashboard-safe
    tmp, original_size = _gzip_file_to_temp(path, target)  # STREAMED (F4)
    os.replace(tmp, target)  # verified archive now in place
    path.unlink()  # remove original .log — its bytes survive in target
    return original_size


def _next_archive_path(base: Path, stamp: str) -> Path:
    """Pick a non-clobbering ``<name>.<stamp>.gz`` sibling for a launchd log.

    Naming scheme: ``<name>.out.log.<YYYYMMDD-HHMMSS>.gz`` — the archive TIME to
    the second. The watchdog runs at most daily, so second-resolution stamps
    never collide across runs; on the vanishing chance the exact path already
    exists (two runs in the same second), a ``-NN`` counter is appended so a
    prior archive is NEVER clobbered (this is an archive bound — no ring, nothing
    overwrites an existing archive).
    """
    candidate = base.with_name(f"{base.name}.{stamp}.gz")
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = base.with_name(f"{base.name}.{stamp}-{n}.gz")
        if not candidate.exists():
            return candidate
        n += 1


def archive_and_truncate(path: Path, *, stamp: str) -> int:
    """LAUNCHD lane: copy the current bytes to a verified
    ``<name>.out.log.<stamp>.gz`` sibling, THEN ``os.truncate(path, 0)`` IN PLACE.
    Returns the byte count archived+truncated.

    NEVER unlinks / renames / moves ``path`` — the ONLY op applied to the launchd
    file is *streamed read* (chunked ``open(...).read``) then *truncate*
    (``os.truncate``), which
    preserves the inode the child's O_APPEND fd is bound to (trap #1). Truncate
    happens ONLY after the archive is verified in place (crash-safety): a crash
    before the truncate leaves the full ``.out.log`` intact beside its archive.

    REFUSES a non-launchd path so a reviewer can see by inspection that the
    truncate-only op is confined to the launchd lane.
    """
    if path.parent.name != defaults.LOG_DIR_LAUNCHD_SUBDIR:
        raise ValueError(
            f"archive_and_truncate refuses a non-launchd path: {path}"
        )
    target = _next_archive_path(path, stamp)
    tmp, archived = _gzip_file_to_temp(path, target)  # STREAMED read (F4)
    os.replace(tmp, target)  # verified archive now in place
    # The ONLY mutation of the launchd path: truncate in place. Keeps the inode,
    # so the child's O_APPEND fd resumes writing cleanly. Bytes appended between
    # the streamed read and here are discarded — an accepted minor window; the
    # .out.log is the low-value stream (.err.log is excluded entirely).
    os.truncate(path, 0)
    return archived


# --------------------------------------------------------------------------- #
# Orchestrator — the thin watchdog caller invokes this.                        #
# --------------------------------------------------------------------------- #


def run_log_rotation(
    *,
    log_dir: Path = LOG_DIR,
    now_date: date | None = None,
    now_epoch: float | None = None,
    max_files_per_run: int = defaults.LOG_ROTATION_MAX_FILES_PER_RUN,
    deadline_seconds: float = defaults.LOG_ROTATION_DEADLINE_SECONDS,
    max_file_bytes: int = defaults.LOG_ROTATION_MAX_FILE_BYTES,
    skip_launchd: bool = False,
    dry_run: bool = False,
    monotonic: Callable[[], float] = time.monotonic,
    stat: Callable[[Path], os.stat_result] = os.stat,
) -> RotationOutcome:
    """Scan ``logs/``, gzip aged daily logs, copy-gz-truncate launchd .out.log,
    and return a :class:`RotationOutcome`. NEVER raises — each per-file failure
    is caught, recorded in ``outcome.errors``, and the loop continues (the
    watchdog turns the outcome into a CheckResult; a partial run is normal).

    This is the ONE orchestrator (F6): the watchdog's Check W delegates here rather
    than re-implementing the loop, so the public entry point and the check share a
    single code path AND the incident gate. ``skip_launchd=True`` HOLDS the ENTIRE
    launchd truncate lane (empty plan; every eligible launchd file reported as
    ``launchd_pending``) — the watchdog passes ``skip_launchd = "an open CRITICAL is
    present"`` so a mid-incident operator tailing a ``.out.log`` is never stomped.
    Without this parameter a future caller wiring the "public" entry point would
    truncate during an open-CRITICAL incident; with it, incident-safety travels with
    the single loop.

    F1: the launchd lane truncates EVERY eligible file UNCONDITIONALLY (no per-file
    mtime skip) — a fast always-on daemon (portal_poll, ~36 MB, written every 60s)
    always has a recent mtime and would otherwise be skipped forever, defeating the
    bound. The whole-lane ``skip_launchd`` hold is the real incident guard.

    F4: a candidate whose ``st_size`` exceeds ``max_file_bytes`` is SKIPPED (never
    read) and surfaced via ``oversized_skipped`` / ``oversized_files`` so a runaway
    log is operator-visible instead of hogging the run; archiving itself STREAMS
    (bounded memory) via the primitives.

    Injectable clocks/stat keep every branch unit-testable. ``deadline_seconds``
    is a monotonic wall-clock fuse (Check W is registered LAST so an overrun
    can't delay an alerting check, but the fuse stops it cleanly regardless).
    """
    today = now_date if now_date is not None else date.today()  # LOCAL date (trap #3)
    wall_now = now_epoch if now_epoch is not None else time.time()
    start = monotonic()

    errors: list[str] = []
    daily_gzipped = daily_bytes = 0
    launchd_truncated = launchd_bytes = 0
    oversized_skipped = 0
    oversized_files: list[str] = []
    deadline_hit = False

    entries = scan_entries(log_dir, stat=stat, now_epoch=wall_now)
    daily_plan = plan_daily(today, entries, max_files_per_run)
    # skip_launchd HOLDS the whole lane (empty plan) — the watchdog-domain incident gate.
    launchd_plan = [] if skip_launchd else plan_launchd(entries, max_files_per_run)
    daily_pending = max(
        0,
        sum(
            1
            for e in entries
            if e.kind is LogKind.DAILY
            and e.daily_date is not None
            and (today - e.daily_date).days > defaults.DAILY_GZIP_AGE_DAYS
        )
        - len(daily_plan),
    )
    launchd_eligible = sum(
        1 for e in entries if e.kind is LogKind.LAUNCHD_OUT and e.size_bytes > 0
    )
    # When the lane is held, EVERY eligible launchd file is deferred — surface them as
    # pending so the note names the deferral, never a silent zero.
    launchd_pending = (
        launchd_eligible if skip_launchd else max(0, launchd_eligible - len(launchd_plan))
    )

    stamp = today.strftime("%Y%m%d") + "-" + time.strftime("%H%M%S", time.localtime(wall_now))

    def _over_deadline() -> bool:
        return (monotonic() - start) >= deadline_seconds

    def _oversized(entry: LogEntry) -> bool:
        # F4: gate BEFORE any read — a runaway is skipped, never streamed.
        if entry.size_bytes > max_file_bytes:
            nonlocal oversized_skipped
            oversized_skipped += 1
            oversized_files.append(entry.path.name)
            return True
        return False

    # DAILY lane first (bounded, non-launchd, safe).
    for entry in daily_plan:
        if _over_deadline():
            deadline_hit = True
            break
        if _oversized(entry):
            continue
        if dry_run:
            daily_gzipped += 1
            daily_bytes += entry.size_bytes
            continue
        try:
            daily_bytes += gzip_in_place(entry.path)
            daily_gzipped += 1
        except (OSError, ValueError) as exc:
            errors.append(f"daily {entry.path.name}: {exc!r}")

    # LAUNCHD lane — UNCONDITIONAL truncate for every planned file (trap #2, F1: no
    # per-file mtime skip). The whole lane is already held (empty plan) under an
    # open-CRITICAL incident; there is no second per-file incident gate.
    for entry in launchd_plan:
        if _over_deadline():
            deadline_hit = True
            break
        if _oversized(entry):
            continue
        if dry_run:
            launchd_truncated += 1
            launchd_bytes += entry.size_bytes
            continue
        try:
            launchd_bytes += archive_and_truncate(entry.path, stamp=stamp)
            launchd_truncated += 1
        except (OSError, ValueError) as exc:
            errors.append(f"launchd {entry.path.name}: {exc!r}")

    return RotationOutcome(
        daily_gzipped=daily_gzipped,
        daily_bytes_reclaimed=daily_bytes,
        daily_pending=daily_pending,
        launchd_truncated=launchd_truncated,
        launchd_bytes_reclaimed=launchd_bytes,
        launchd_pending=launchd_pending,
        oversized_skipped=oversized_skipped,
        oversized_files=tuple(oversized_files),
        deadline_hit=deadline_hit,
        dry_run=dry_run,
        errors=tuple(errors),
    )
