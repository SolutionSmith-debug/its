"""Tests for the host-level compile mutex (shared/compile_mutex.py).

flock contention is per-open-file-description, so a second fd to the same sidecar
contends even within one process — that is how the contended path is exercised
without a second process.
"""
from __future__ import annotations

import fcntl

import pytest

from shared import compile_mutex


@pytest.fixture
def tmp_anchor(tmp_path, monkeypatch):
    """Point the host-compile lock anchor at a tmp dir (never touch ~/its/state)."""
    anchor = tmp_path / "host_compile"
    monkeypatch.setattr(compile_mutex, "_HOST_COMPILE_LOCK_ANCHOR", anchor)
    return anchor


def _sidecar(anchor):
    return anchor.with_name(anchor.name + ".lock")


def test_hold_yields_true_when_free(tmp_anchor):
    with compile_mutex.hold(role="safety") as acquired:
        assert acquired is True


def test_hold_yields_false_and_warns_when_contended(tmp_anchor, mocker):
    warn = mocker.patch.object(compile_mutex, "log")
    # Another compile holds the sidecar flock on a separate fd.
    sidecar = _sidecar(tmp_anchor)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    holder = sidecar.open("a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with compile_mutex.hold(role="safety") as acquired:
            assert acquired is False  # body still runs — caller decides
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    # exactly one WARN, stable error_code, role named in the message.
    assert warn.call_count == 1
    args, kwargs = warn.call_args
    assert kwargs["error_code"] == "compile_mutex.contended"
    assert "role='safety'" in args[2]


def test_hold_releases_on_normal_exit(tmp_anchor):
    with compile_mutex.hold(role="safety") as first:
        assert first is True
    # released → a second acquire succeeds (proves the lock is not stranded).
    with compile_mutex.hold(role="progress") as second:
        assert second is True


def test_hold_releases_on_body_exception(tmp_anchor):
    with pytest.raises(ValueError):
        with compile_mutex.hold(role="safety"):
            raise ValueError("boom")
    # released despite the body raising → re-acquire succeeds.
    with compile_mutex.hold(role="safety") as acquired:
        assert acquired is True


def test_external_holder_release_frees_the_lock(tmp_anchor):
    """fd-close by the other holder frees the lock (the flock auto-release seam)."""
    sidecar = _sidecar(tmp_anchor)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    holder = sidecar.open("a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with compile_mutex.hold(role="safety") as contended:
        assert contended is False
    holder.close()  # the other compile dies / releases (fd close releases the flock)
    with compile_mutex.hold(role="safety") as acquired:
        assert acquired is True
