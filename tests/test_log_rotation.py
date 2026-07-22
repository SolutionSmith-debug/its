"""Tests for shared/log_rotation.py — the watchdog Check W pruner ENGINE.

Every test drives the pure engine against a TEMP logs dir (`tmp_path`), NEVER the
real `~/its/logs`. The engine is dependency-injected (`stat`, `monotonic`, `now_date`,
`now_epoch`) so every branch is reachable without a real launchd fd or a wall-clock wait.

The load-bearing safety controls this file proves (§55.2 — each has a paired inject-and-RED
proof recorded in the task report):
  * a launchd `.out.log` is ONLY ever truncated — never unlinked / renamed / moved, for
    EVERY orchestrator branch (routine / capped / deadline / incident-skip);
  * truncate preserves the O_APPEND child's inode + append semantics (no NUL padding);
  * an archive is verified before the source is removed/truncated — a failed gzip verify
    leaves the original intact and writes NO `.gz` (zero data loss);
  * the CURRENT LOCAL-date daily file is never selected (the UTC-vs-local midnight race);
  * the per-run cap + monotonic deadline each stop cleanly and NEVER raise.
"""
from __future__ import annotations

import gzip
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from shared import defaults, log_rotation
from shared.log_rotation import LogKind

# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #

_OLD_MTIME_OFFSET = 100_000.0  # ~27 h — well outside any incident-skip window


def _write(path: Path, data: bytes, *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _launchd_dir(log_dir: Path) -> Path:
    return log_dir / defaults.LOG_DIR_LAUNCHD_SUBDIR


def _out_log_path_inode_set(launchd_dir: Path) -> set[tuple[str, int]]:
    """The (name, inode) set of EXISTING `*.out.log` files (NOT `.gz`) under launchd/.

    A rename changes the name; an unlink drops the entry; a truncate changes NEITHER
    (only the size). So this set must be byte-identical before and after any engine run.
    """
    if not launchd_dir.is_dir():
        return set()
    return {
        (p.name, p.stat().st_ino)
        for p in launchd_dir.iterdir()
        if p.name.endswith(".out.log")  # excludes the .gz archives we create
    }


def _fake_monotonic(values: list[float]):
    it = iter(values)
    last = [0.0]

    def _m() -> float:
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    return _m


# --------------------------------------------------------------------------- #
# parse_daily_date                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2026-07-21.log", date(2026, 7, 21)),
        ("2000-01-01.log", date(2000, 1, 1)),
        ("foo.log", None),  # non-date stem
        ("2026-07-21.log.gz", None),  # already archived
        ("2026-13-01.log", None),  # invalid month
        ("2026-07-21", None),  # no .log suffix
        (".DS_Store", None),  # macOS turd that exists today
        ("2026-07-21.out.log", None),  # launchd-shaped, not a daily
    ],
)
def test_parse_daily_date(name: str, expected: date | None) -> None:
    assert log_rotation.parse_daily_date(name) == expected


# --------------------------------------------------------------------------- #
# classification                                                              #
# --------------------------------------------------------------------------- #


def test_classify_daily(tmp_path: Path) -> None:
    assert (
        log_rotation.classify_entry(tmp_path / "2026-07-01.log", log_dir=tmp_path)
        is LogKind.DAILY
    )


def test_classify_launchd_out(tmp_path: Path) -> None:
    p = _launchd_dir(tmp_path) / "org.solutionsmith.its.portal-poll.out.log"
    assert log_rotation.classify_entry(p, log_dir=tmp_path) is LogKind.LAUNCHD_OUT


@pytest.mark.parametrize(
    "relpath",
    [
        "foo.log",  # non-date daily name
        ".DS_Store",  # not a date, exists today
        "2026-07-01.log.gz",  # already archived
        "launchd/foo.err.log",  # the incident file — NEVER touched
        "launchd/foo.out.log.20260701-120000.gz",  # an existing launchd archive
        "migrations/2020-01-01.log",  # date-shaped but under the allowlist-exclude
        "migrations/box_build_output.txt",  # committed audit output
        "2026-07-01.out.log",  # launchd-shaped but NOT under launchd/
    ],
)
def test_classify_excluded(tmp_path: Path, relpath: str) -> None:
    assert (
        log_rotation.classify_entry(tmp_path / relpath, log_dir=tmp_path)
        is LogKind.EXCLUDED
    )


def test_classify_path_outside_log_dir_is_excluded(tmp_path: Path) -> None:
    # A path that is not under log_dir at all is never classified as actionable.
    other = tmp_path / "elsewhere" / "2026-07-01.log"
    assert log_rotation.classify_entry(other, log_dir=tmp_path / "logs") is LogKind.EXCLUDED


# --------------------------------------------------------------------------- #
# scan_entries — the walk boundary                                            #
# --------------------------------------------------------------------------- #


def test_scan_entries_selects_only_daily_and_launchd_out(tmp_path: Path) -> None:
    _write(tmp_path / "2026-07-01.log", b"daily")
    _write(tmp_path / "foo.log", b"non-date")
    _write(tmp_path / "2026-07-01.log.gz", b"archived")
    _write(tmp_path / ".DS_Store", b"turd")
    _write(_launchd_dir(tmp_path) / "d.out.log", b"out")
    _write(_launchd_dir(tmp_path) / "d.err.log", b"err")

    entries = log_rotation.scan_entries(tmp_path)
    names = {(e.path.name, e.kind) for e in entries}
    assert names == {
        ("2026-07-01.log", LogKind.DAILY),
        ("d.out.log", LogKind.LAUNCHD_OUT),
    }


def test_scan_entries_never_walks_migrations(tmp_path: Path) -> None:
    # A date-shaped file under migrations/ would look daily-eligible; it must NEVER appear.
    _write(tmp_path / "migrations" / "2000-01-01.log", b"committed audit output")
    _write(tmp_path / "migrations" / "box_build.txt", b"audit")
    _write(tmp_path / "2026-07-01.log", b"real daily")

    entries = log_rotation.scan_entries(tmp_path)
    assert all("migrations" not in e.path.parts for e in entries)
    assert {e.path.name for e in entries} == {"2026-07-01.log"}


def test_scan_entries_populates_daily_date(tmp_path: Path) -> None:
    _write(tmp_path / "2026-07-01.log", b"x")
    _write(_launchd_dir(tmp_path) / "d.out.log", b"y")
    entries = {e.path.name: e for e in log_rotation.scan_entries(tmp_path)}
    assert entries["2026-07-01.log"].daily_date == date(2026, 7, 1)
    assert entries["d.out.log"].daily_date is None


def test_scan_entries_empty_dir(tmp_path: Path) -> None:
    assert log_rotation.scan_entries(tmp_path / "does-not-exist") == []


# --------------------------------------------------------------------------- #
# plan_daily — the current-local-date guard (trap #3)                          #
# --------------------------------------------------------------------------- #


def _daily_entry(d: date, size: int = 100) -> log_rotation.LogEntry:
    return log_rotation.LogEntry(
        path=Path(f"/tmp/logs/{d.isoformat()}.log"),
        kind=LogKind.DAILY,
        size_bytes=size,
        mtime=0.0,
        daily_date=d,
    )


@pytest.mark.parametrize("today", [date(2026, 7, 21), date(2026, 1, 1), date(2024, 2, 29)])
def test_plan_daily_never_selects_current_local_date(today: date) -> None:
    """The file named for TODAY (LOCAL) is age 0 and must never be gzipped — even though
    the line stamps inside it are UTC and the host runs EDT (a UTC-keyed pruner would race
    the current file near midnight)."""
    entries = [
        _daily_entry(today),  # age 0
        _daily_entry(today - timedelta(days=1)),  # age 1
        _daily_entry(today - timedelta(days=14)),  # age 14 — exactly the threshold
        _daily_entry(today - timedelta(days=15)),  # age 15 — eligible
    ]
    plan = log_rotation.plan_daily(today, entries, max_files=100, age_days=14)
    picked = {e.daily_date for e in plan}
    assert today not in picked
    assert (today - timedelta(days=1)) not in picked
    assert (today - timedelta(days=14)) not in picked  # strict > : 14 is NOT eligible
    assert picked == {today - timedelta(days=15)}


def test_plan_daily_utc_local_off_by_one_near_midnight() -> None:
    """A file whose local name is 'today' is excluded even if a UTC clock has ticked into
    the next day — plan_daily keys on the LOCAL now_date passed in, never on UTC."""
    local_today = date(2026, 7, 21)
    entries = [_daily_entry(local_today)]
    # Even asked with the local date, the age-0 current file is not selected.
    assert log_rotation.plan_daily(local_today, entries, max_files=100) == []


def test_plan_daily_oldest_first_and_capped() -> None:
    today = date(2026, 7, 21)
    entries = [_daily_entry(today - timedelta(days=n)) for n in (30, 20, 40, 15, 100)]
    plan = log_rotation.plan_daily(today, entries, max_files=2, age_days=14)
    # oldest-first: the two most-aged (100d, 40d) picked before newer eligible ones.
    assert [e.daily_date for e in plan] == [
        today - timedelta(days=100),
        today - timedelta(days=40),
    ]


def test_plan_daily_ignores_non_daily_and_none_dates() -> None:
    today = date(2026, 7, 21)
    launchd = log_rotation.LogEntry(
        path=Path("/tmp/logs/launchd/d.out.log"),
        kind=LogKind.LAUNCHD_OUT,
        size_bytes=999,
        mtime=0.0,
    )
    assert log_rotation.plan_daily(today, [launchd], max_files=10) == []


# --------------------------------------------------------------------------- #
# plan_launchd                                                                #
# --------------------------------------------------------------------------- #


def _launchd_entry(name: str, size: int, mtime: float = 0.0) -> log_rotation.LogEntry:
    return log_rotation.LogEntry(
        path=Path(f"/tmp/logs/launchd/{name}"),
        kind=LogKind.LAUNCHD_OUT,
        size_bytes=size,
        mtime=mtime,
    )


def test_plan_launchd_largest_first_drops_empty_and_caps() -> None:
    entries = [
        _launchd_entry("a.out.log", 10),
        _launchd_entry("b.out.log", 5000),
        _launchd_entry("c.out.log", 0),  # zero-byte → dropped (nothing to archive)
        _launchd_entry("d.out.log", 300),
    ]
    plan = log_rotation.plan_launchd(entries, max_files=2)
    assert [e.path.name for e in plan] == ["b.out.log", "d.out.log"]  # largest-first, capped


def test_plan_launchd_ignores_daily_entries() -> None:
    assert log_rotation.plan_launchd([_daily_entry(date(2020, 1, 1))], max_files=10) == []


# --------------------------------------------------------------------------- #
# is_incident_recent                                                          #
# --------------------------------------------------------------------------- #


def test_is_incident_recent() -> None:
    now = 1_000_000.0
    assert log_rotation.is_incident_recent(now - 60, now, skip_minutes=30)  # 1 min ago
    assert log_rotation.is_incident_recent(now - 29 * 60, now, skip_minutes=30)
    assert not log_rotation.is_incident_recent(now - 31 * 60, now, skip_minutes=30)
    assert not log_rotation.is_incident_recent(now - 100_000, now, skip_minutes=30)


# --------------------------------------------------------------------------- #
# archive primitives — refuse-guards                                          #
# --------------------------------------------------------------------------- #


def test_gzip_in_place_refuses_launchd_path(tmp_path: Path) -> None:
    p = _write(_launchd_dir(tmp_path) / "d.out.log", b"bytes")
    with pytest.raises(ValueError, match="refuses a launchd path"):
        log_rotation.gzip_in_place(p)
    assert p.exists()  # untouched


def test_archive_and_truncate_refuses_non_launchd_path(tmp_path: Path) -> None:
    p = _write(tmp_path / "2020-01-01.log", b"bytes")
    with pytest.raises(ValueError, match="refuses a non-launchd path"):
        log_rotation.archive_and_truncate(p, stamp="20260721-000000")
    assert p.read_bytes() == b"bytes"  # untouched


# --------------------------------------------------------------------------- #
# gzip_in_place — daily archive, remove-after-verify                           #
# --------------------------------------------------------------------------- #


def test_gzip_in_place_archives_then_removes_original(tmp_path: Path) -> None:
    raw = b"one\ntwo\nthree\n" * 100
    p = _write(tmp_path / "2020-01-01.log", raw)
    reclaimed = log_rotation.gzip_in_place(p)
    assert reclaimed == len(raw)
    assert not p.exists()  # original removed
    gz = tmp_path / "2020-01-01.log.gz"
    assert gz.exists()
    with gzip.open(gz, "rb") as fh:  # bytes survive, verifiably
        assert fh.read() == raw
    # dashboard-safe: the archive name does NOT fnmatch *.log
    import fnmatch

    assert not fnmatch.fnmatch(gz.name, "*.log")


def test_gzip_in_place_verify_failure_leaves_original_and_no_gz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VERIFY-BEFORE-REPLACE: if the gzip round-trip verify fails, the original .log stays
    intact and NO .gz replaces it — zero data loss."""
    raw = b"forensic bytes that must not be lost\n" * 50
    p = _write(tmp_path / "2020-01-01.log", raw)

    real_open = gzip.open

    def _corrupt_open(path, mode="rb", *a, **k):  # type: ignore[no-untyped-def]
        if "w" in str(mode):
            return real_open(path, mode, *a, **k)  # write faithfully

        class _F:
            # The verify path now STREAMS: it calls read(chunk) in a loop until EOF. Return
            # corrupted bytes once, then b"" — so the running sha256/len mismatch is detected.
            _done = False

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *exc):  # type: ignore[no-untyped-def]
                return False

            def read(self, *_a):  # type: ignore[no-untyped-def]
                if self._done:
                    return b""
                self._done = True
                return b"CORRUPTED-DOES-NOT-MATCH"

        return _F()

    monkeypatch.setattr(log_rotation.gzip, "open", _corrupt_open)

    with pytest.raises(OSError, match="round-trip verification FAILED"):
        log_rotation.gzip_in_place(p)

    assert p.read_bytes() == raw  # original untouched
    assert not (tmp_path / "2020-01-01.log.gz").exists()  # no archive replaced it
    # no leftover temp files either
    assert not list(tmp_path.glob(".*.tmp.*.gz"))


# --------------------------------------------------------------------------- #
# archive_and_truncate — launchd: truncate-only, verify-before-truncate        #
# --------------------------------------------------------------------------- #


def test_archive_and_truncate_keeps_inode_and_zeroes_size(tmp_path: Path) -> None:
    p = _write(_launchd_dir(tmp_path) / "d.out.log", b"launchd output\n" * 200)
    ino_before = p.stat().st_ino
    raw = p.read_bytes()

    archived = log_rotation.archive_and_truncate(p, stamp="20260721-101010")

    assert archived == len(raw)
    assert p.exists()  # NEVER unlinked
    assert p.stat().st_ino == ino_before  # SAME inode — the O_APPEND fd stays bound
    assert p.stat().st_size == 0  # truncated in place
    gz = _launchd_dir(tmp_path) / "d.out.log.20260721-101010.gz"
    assert gz.exists()
    with gzip.open(gz, "rb") as fh:
        assert fh.read() == raw


def test_archive_and_truncate_preserves_o_append_semantics(tmp_path: Path) -> None:
    """Simulate the launchd child holding an O_APPEND fd ACROSS the truncate: after the
    engine truncates the file to 0, a write through the held fd must land cleanly at offset
    0 with NO NUL padding (an unlink/rename would strand this fd forever)."""
    p = _write(_launchd_dir(tmp_path) / "d.out.log", b"pre-rotation bytes\n" * 100)
    ino_before = p.stat().st_ino

    fd = os.open(p, os.O_WRONLY | os.O_APPEND)  # the child's open fd
    try:
        log_rotation.archive_and_truncate(p, stamp="20260721-101010")  # rotate underneath it
        os.write(fd, b"post-rotation line\n")  # child writes through the held fd
    finally:
        os.close(fd)

    assert p.stat().st_ino == ino_before
    content = p.read_bytes()
    assert content == b"post-rotation line\n"  # clean resume
    assert b"\x00" not in content  # zero NUL padding


def test_archive_and_truncate_verify_failure_leaves_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launchd no-data-loss guard: a failed gzip verify must NOT truncate the file."""
    raw = b"live incident output being tailed\n" * 40
    p = _write(_launchd_dir(tmp_path) / "d.out.log", raw)
    ino_before = p.stat().st_ino

    real_open = gzip.open

    def _corrupt_open(path, mode="rb", *a, **k):  # type: ignore[no-untyped-def]
        if "w" in str(mode):
            return real_open(path, mode, *a, **k)

        class _F:
            # STREAMED verify: read(chunk) until EOF. Yield corrupt bytes once, then b"".
            _done = False

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *exc):  # type: ignore[no-untyped-def]
                return False

            def read(self, *_a):  # type: ignore[no-untyped-def]
                if self._done:
                    return b""
                self._done = True
                return b"NOPE"

        return _F()

    monkeypatch.setattr(log_rotation.gzip, "open", _corrupt_open)

    with pytest.raises(OSError, match="round-trip verification FAILED"):
        log_rotation.archive_and_truncate(p, stamp="20260721-101010")

    assert p.read_bytes() == raw  # NOT truncated
    assert p.stat().st_ino == ino_before
    assert not list(_launchd_dir(tmp_path).glob("*.gz"))  # no archive


def test_archive_and_truncate_never_clobbers_an_existing_archive(tmp_path: Path) -> None:
    ld = _launchd_dir(tmp_path)
    p = _write(ld / "d.out.log", b"payload")
    # A same-second archive already exists → the engine picks a -NN sibling, never overwrites.
    _write(ld / "d.out.log.20260721-101010.gz", b"prior archive - must survive")
    log_rotation.archive_and_truncate(p, stamp="20260721-101010")
    assert (ld / "d.out.log.20260721-101010.gz").read_bytes() == b"prior archive - must survive"
    assert (ld / "d.out.log.20260721-101010-1.gz").exists()


# --------------------------------------------------------------------------- #
# run_log_rotation — orchestrator: never unlinks/renames a launchd path, for   #
# EVERY branch (routine / capped / deadline / incident-skip).                  #
# --------------------------------------------------------------------------- #


def _seed_launchd(tmp_path: Path, sizes: dict[str, int], *, mtime: float) -> None:
    for name, size in sizes.items():
        _write(_launchd_dir(tmp_path) / name, b"x" * size, mtime=mtime)


def test_run_never_unlinks_or_renames_launchd_routine(tmp_path: Path) -> None:
    old = time.time() - _OLD_MTIME_OFFSET
    _seed_launchd(tmp_path, {"a.out.log": 500, "b.out.log": 800}, mtime=old)
    before = _out_log_path_inode_set(_launchd_dir(tmp_path))

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=time.time()
    )

    after = _out_log_path_inode_set(_launchd_dir(tmp_path))
    assert after == before  # names + inodes IDENTICAL — only size changed + .gz added
    assert outcome.launchd_truncated == 2
    for name in ("a.out.log", "b.out.log"):
        assert (_launchd_dir(tmp_path) / name).stat().st_size == 0
        assert list(_launchd_dir(tmp_path).glob(f"{name}.*.gz"))


def test_run_never_unlinks_or_renames_launchd_capped(tmp_path: Path) -> None:
    old = time.time() - _OLD_MTIME_OFFSET
    _seed_launchd(tmp_path, {"a.out.log": 500, "b.out.log": 800, "c.out.log": 200}, mtime=old)
    before = _out_log_path_inode_set(_launchd_dir(tmp_path))

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path,
        now_date=date(2026, 7, 21),
        now_epoch=time.time(),
        max_files_per_run=1,  # only the largest is processed; rest deferred
    )

    after = _out_log_path_inode_set(_launchd_dir(tmp_path))
    assert after == before
    assert outcome.launchd_truncated == 1
    assert outcome.launchd_pending == 2  # deferred, not deleted


def test_run_never_unlinks_or_renames_launchd_deadline(tmp_path: Path) -> None:
    old = time.time() - _OLD_MTIME_OFFSET
    _seed_launchd(tmp_path, {"a.out.log": 500, "b.out.log": 800}, mtime=old)
    before = _out_log_path_inode_set(_launchd_dir(tmp_path))

    # monotonic: start=0.0, then jump past the 30s deadline so the very first loop-guard trips.
    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path,
        now_date=date(2026, 7, 21),
        now_epoch=time.time(),
        deadline_seconds=30.0,
        monotonic=_fake_monotonic([0.0, 100.0, 100.0, 100.0]),
    )

    after = _out_log_path_inode_set(_launchd_dir(tmp_path))
    assert after == before
    assert outcome.deadline_hit is True
    assert outcome.launchd_truncated == 0  # nothing processed after the fuse blew
    # sizes untouched — the files were never reached
    for name in ("a.out.log", "b.out.log"):
        assert (_launchd_dir(tmp_path) / name).stat().st_size > 0


def test_run_truncates_recently_written_launchd_unconditionally(tmp_path: Path) -> None:
    """F1 (2026-07-21, growth-hole fix): the per-file mtime SKIP is GONE. A `.out.log`
    written seconds ago is STILL copy-gz-truncated. The per-file recency skip fatally
    defeated the check — a fast always-on daemon (portal_poll writes its `.out.log` every
    60s and is the LARGEST target at ~36 MB) always has a recent mtime, so it was skipped
    on EVERY run and NEVER truncated, i.e. the exact unbounded growth the check exists to
    bound. Incident-safety now lives in the WHOLE-LANE `skip_launchd` hold, not a per-file
    gate. Still inode+size only: never unlinked / renamed."""
    now = time.time()
    _seed_launchd(tmp_path, {"a.out.log": 500, "b.out.log": 800}, mtime=now)  # freshly modified
    before = _out_log_path_inode_set(_launchd_dir(tmp_path))

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=now
    )

    after = _out_log_path_inode_set(_launchd_dir(tmp_path))
    assert after == before  # names+inodes IDENTICAL — truncate-in-place, never unlink/rename
    assert outcome.launchd_truncated == 2  # recent mtime did NOT defer them (F1)
    for name in ("a.out.log", "b.out.log"):  # truncated + archived, despite recent mtime
        assert (_launchd_dir(tmp_path) / name).stat().st_size == 0
        assert list(_launchd_dir(tmp_path).glob(f"{name}.*.gz"))


# --------------------------------------------------------------------------- #
# run_log_rotation — daily lane + local-date default                          #
# --------------------------------------------------------------------------- #


def test_run_uses_local_today_by_default_and_spares_current_file(tmp_path: Path) -> None:
    """No now_date passed → the engine uses date.today() (LOCAL). Today's file survives;
    an aged one is gzipped."""
    today = date.today()
    _write(tmp_path / f"{today.isoformat()}.log", b"current day - keep")
    aged = today - timedelta(days=30)
    _write(tmp_path / f"{aged.isoformat()}.log", b"old day - archive me")

    outcome = log_rotation.run_log_rotation(log_dir=tmp_path)  # default now_date

    assert (tmp_path / f"{today.isoformat()}.log").exists()  # spared
    assert not (tmp_path / f"{aged.isoformat()}.log").exists()  # gzipped away
    assert (tmp_path / f"{aged.isoformat()}.log.gz").exists()
    assert outcome.daily_gzipped == 1


def test_run_daily_cap_reports_pending_and_never_raises(tmp_path: Path) -> None:
    today = date(2026, 7, 21)
    for n in (20, 25, 30, 40, 50):
        d = today - timedelta(days=n)
        _write(tmp_path / f"{d.isoformat()}.log", b"aged")
    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=today, now_epoch=time.time(), max_files_per_run=2
    )
    assert outcome.daily_gzipped == 2
    assert outcome.daily_pending == 3  # deferred to next run


def test_run_dry_run_writes_nothing(tmp_path: Path) -> None:
    old = time.time() - _OLD_MTIME_OFFSET
    _write(tmp_path / "2020-01-01.log", b"aged daily", mtime=old)
    _seed_launchd(tmp_path, {"a.out.log": 500}, mtime=old)

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=time.time(), dry_run=True
    )

    assert outcome.dry_run is True
    assert outcome.daily_gzipped == 1  # would-do counts
    assert outcome.launchd_truncated == 1
    # ...but ZERO writes actually happened:
    assert (tmp_path / "2020-01-01.log").exists()
    assert not (tmp_path / "2020-01-01.log.gz").exists()
    assert (_launchd_dir(tmp_path) / "a.out.log").stat().st_size == 500
    assert "DRY RUN" in outcome.note()


def test_run_never_raises_on_per_file_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-file archive failure is caught into outcome.errors; the run never raises and
    keeps going."""
    old = time.time() - _OLD_MTIME_OFFSET
    _write(tmp_path / "2020-01-01.log", b"aged", mtime=old)
    _seed_launchd(tmp_path, {"a.out.log": 500}, mtime=old)

    def _boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(log_rotation, "gzip_in_place", _boom)
    monkeypatch.setattr(log_rotation, "archive_and_truncate", _boom)

    outcome = log_rotation.run_log_rotation(  # must NOT raise
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=time.time()
    )

    assert outcome.had_errors
    assert outcome.daily_gzipped == 0
    assert outcome.launchd_truncated == 0
    assert len(outcome.errors) == 2  # one daily + one launchd failure, both recorded
    assert "disk full" in outcome.note()


def test_rotation_outcome_note_routine_is_clean(tmp_path: Path) -> None:
    outcome = log_rotation.RotationOutcome(daily_gzipped=3, launchd_truncated=1)
    note = outcome.note()
    assert note.startswith("[log-dir-rotation] ")
    assert "gzip 3 daily" in note
    assert "truncate 1 launchd" in note


# --------------------------------------------------------------------------- #
# F4 — per-file SIZE CAP (skip a runaway, never read it) + STREAMING archive.  #
# --------------------------------------------------------------------------- #


def test_run_skips_oversized_launchd_over_size_cap_and_reports_it(tmp_path: Path) -> None:
    """F4: a candidate whose st_size exceeds max_file_bytes is SKIPPED (never read/truncated),
    the run is ABNORMAL (oversized_skipped>0), and the note NAMES the file so a runaway log
    (a daemon stuck in a print-loop) is operator-visible instead of hogging the run."""
    old = time.time() - _OLD_MTIME_OFFSET
    _seed_launchd(tmp_path, {"runaway.out.log": 5000, "normal.out.log": 400}, mtime=old)

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path,
        now_date=date(2026, 7, 21),
        now_epoch=time.time(),
        max_file_bytes=1000,  # runaway (5000 B) is over cap; normal (400 B) is under
    )

    assert outcome.oversized_skipped == 1
    assert "runaway.out.log" in outcome.oversized_files
    assert "runaway.out.log" in outcome.note()
    assert "SKIPPED over size cap" in outcome.note()
    # The runaway was NEVER read/truncated — its bytes are intact and no archive was written.
    runaway = _launchd_dir(tmp_path) / "runaway.out.log"
    assert runaway.stat().st_size == 5000
    assert not list(_launchd_dir(tmp_path).glob("runaway.out.log.*.gz"))
    # The under-cap file was processed normally.
    assert outcome.launchd_truncated == 1
    assert (_launchd_dir(tmp_path) / "normal.out.log").stat().st_size == 0


def test_run_skips_oversized_daily_over_size_cap(tmp_path: Path) -> None:
    """F4 (daily lane): an aged daily log over the cap is skipped, never gzipped."""
    old = time.time() - _OLD_MTIME_OFFSET
    _write(tmp_path / "2020-01-01.log", b"x" * 5000, mtime=old)

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=time.time(), max_file_bytes=1000
    )

    assert outcome.oversized_skipped == 1
    assert "2020-01-01.log" in outcome.oversized_files
    assert outcome.daily_gzipped == 0
    assert (tmp_path / "2020-01-01.log").exists()  # NOT gzipped
    assert not (tmp_path / "2020-01-01.log.gz").exists()


def test_gzip_streams_and_round_trips_a_multi_megabyte_file(tmp_path: Path) -> None:
    """F4 (streaming correctness proxy): a large-but-UNDER-cap file (~3 MiB, well past the
    1 MiB chunk) still archives + verifies + round-trips byte-for-byte. The engine streams
    in LOG_ROTATION_GZIP_CHUNK_BYTES chunks (never read_bytes() of the whole file) on both
    the compress and the verify pass, so memory is bounded regardless of size; a faithful
    round-trip across several chunks is the functional proof the streamed path is correct."""
    # ~3 MiB of non-trivial (non-constant) bytes so the sha256 verify is meaningful and it
    # spans multiple 1 MiB chunks on BOTH the compress and the decompress-verify pass.
    raw = (b"the quick brown fox jumps over the lazy dog 0123456789\n") * 60_000
    assert len(raw) > 3 * defaults.LOG_ROTATION_GZIP_CHUNK_BYTES
    p = _write(tmp_path / "2020-01-01.log", raw)

    reclaimed = log_rotation.gzip_in_place(p)  # under the 1 GiB default cap → archived

    assert reclaimed == len(raw)
    assert not p.exists()  # original removed only after a verified archive
    gz = tmp_path / "2020-01-01.log.gz"
    assert gz.exists()
    with gzip.open(gz, "rb") as fh:  # bytes survive across all chunks
        assert fh.read() == raw


# --------------------------------------------------------------------------- #
# F6 — the ONE orchestrator: skip_launchd holds the launchd lane while the      #
# daily lane still runs (the watchdog path and the public entry share it).      #
# --------------------------------------------------------------------------- #


def test_run_skip_launchd_leaves_launchd_untouched_but_gzips_daily(tmp_path: Path) -> None:
    """F6: `run_log_rotation(skip_launchd=True)` is the SINGLE loop the watchdog Check W
    delegates to. When the launchd lane is held (the watchdog passes skip_launchd='an open
    CRITICAL is present'), every eligible launchd `.out.log` is untouched — name + inode +
    size ALL invariant — while the always-safe daily `.gz` lane still archives. This proves
    incident-safety travels with the ONE orchestrator, not a re-implemented watchdog loop."""
    old = time.time() - _OLD_MTIME_OFFSET
    _seed_launchd(tmp_path, {"a.out.log": 500, "b.out.log": 800}, mtime=old)
    aged = date(2026, 7, 21) - timedelta(days=30)
    _write(tmp_path / f"{aged.isoformat()}.log", b"old day - archive me", mtime=old)

    ld = _launchd_dir(tmp_path)
    before_inode = _out_log_path_inode_set(ld)
    before_size = {name: (ld / name).stat().st_size for name in ("a.out.log", "b.out.log")}

    outcome = log_rotation.run_log_rotation(
        log_dir=tmp_path, now_date=date(2026, 7, 21), now_epoch=time.time(), skip_launchd=True
    )

    # launchd lane fully HELD: name + inode + size all invariant, nothing archived.
    assert _out_log_path_inode_set(ld) == before_inode
    for name in ("a.out.log", "b.out.log"):
        assert (ld / name).stat().st_size == before_size[name]  # NOT truncated
        assert not list(ld.glob(f"{name}.*.gz"))  # NOT archived
    assert outcome.launchd_truncated == 0
    assert outcome.launchd_pending == 2  # both eligible files deferred, surfaced (never silent)
    # ...but the daily lane still ran.
    assert outcome.daily_gzipped == 1
    assert not (tmp_path / f"{aged.isoformat()}.log").exists()
    assert (tmp_path / f"{aged.isoformat()}.log.gz").exists()


# --------------------------------------------------------------------------- #
# F7 — scan_entries reaps a STALE crash-orphaned temp, never a real archive.    #
# --------------------------------------------------------------------------- #


def test_scan_entries_reaps_stale_temp_archive_not_real_gz(tmp_path: Path) -> None:
    """F7: a crash between temp-create and os.replace orphans a `.<name>.tmp.<pid>.<rand>.gz`
    sibling. scan_entries opportunistically reaps such a temp once it is older than
    LOG_ROTATION_TEMP_ORPHAN_AGE_SECONDS (a daily run ⇒ >1h reliably means 'from a dead run').
    It must NEVER reap a finished `<name>.gz` archive (no leading dot, no `.tmp.`) or a fresh
    temp the CURRENT run may be mid-write on."""
    now = time.time()
    stale = now - defaults.LOG_ROTATION_TEMP_ORPHAN_AGE_SECONDS - 100  # older than the window
    fresh = now  # a temp the current run might be mid-write on

    stale_temp = _write(
        tmp_path / ".2020-01-01.log.tmp.12345.abcd.gz", b"orphan", mtime=stale
    )
    fresh_temp = _write(
        tmp_path / ".2020-01-02.log.tmp.99999.beef.gz", b"in-flight", mtime=fresh
    )
    real_archive = _write(tmp_path / "2020-01-01.log.gz", b"finished archive", mtime=stale)

    entries = log_rotation.scan_entries(tmp_path, now_epoch=now)

    # neither temp nor the archive is ever a returned candidate (all EXCLUDED / swept)
    assert entries == []
    # the STALE temp was reaped; the FRESH temp and the REAL archive both survive
    assert not stale_temp.exists()
    assert fresh_temp.exists()
    assert real_archive.exists()
