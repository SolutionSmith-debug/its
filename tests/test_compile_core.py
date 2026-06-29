"""Tests for safety_reports/compile_core.py — the shared hardened compile core (Stage-0 A6).

Covers the three reusable primitives both the safety + future progress compiles instantiate:
the per-job SIGALRM wall-clock budget, the pre-merge memory guard, and the per-job loop with
its timeout / infra / unexpected error fences.
"""
from __future__ import annotations

import signal
import time

import pytest

from safety_reports import compile_core

# ---- job_time_budget -----------------------------------------------------


def test_job_time_budget_noop_when_disabled():
    # seconds <= 0 → no alarm armed; the block runs to completion, no raise.
    ran = False
    with compile_core.job_time_budget(0):
        ran = True
    assert ran


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM unavailable on this platform")
def test_job_time_budget_raises_on_overrun():
    with pytest.raises(compile_core.CompileJobTimeoutError):
        with compile_core.job_time_budget(1):
            time.sleep(5)  # the 1s alarm fires first and interrupts the sleep


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM unavailable on this platform")
def test_job_time_budget_cancels_alarm_on_normal_exit():
    # A fast (normal) compile must leave NO alarm armed, or it would fire mid next-job.
    with compile_core.job_time_budget(100):
        pass
    assert signal.alarm(0) == 0  # no time remaining on any armed alarm


# ---- enforce_memory_budget ----------------------------------------------


def test_enforce_memory_budget_under_ceiling_returns_total():
    assert compile_core.enforce_memory_budget([10, 20, 30], 1000) == 60


def test_enforce_memory_budget_over_ceiling_raises():
    with pytest.raises(compile_core.CompileMemoryExceededError):
        compile_core.enforce_memory_budget([600, 600], 1000)


def test_enforce_memory_budget_zero_ceiling_disables_guard():
    assert compile_core.enforce_memory_budget([10**9], 0) == 10**9


# ---- run_per_job ---------------------------------------------------------


def _fences(timeouts, infra, unexpected, *, infra_errors=(ValueError,)):
    return compile_core.JobFences(
        on_timeout=lambda j, k, e: timeouts.append((j, k)),
        on_infra_error=lambda j, k, e: infra.append((j, k)),
        on_unexpected=lambda j, k, e: unexpected.append((j, k)),
        infra_errors=infra_errors,
    )


def test_run_per_job_happy_path_starts_each_and_fences_none():
    started, compiled, t, i, u = [], [], [], [], []
    compile_core.run_per_job(
        ["a", "b"],
        lambda j: compiled.append(j),
        fences=_fences(t, i, u),
        job_timeout_seconds=0,
        on_job_start=lambda j: started.append(j),
    )
    assert started == ["a", "b"]
    assert compiled == ["a", "b"]
    assert not (t or i or u)


def test_run_per_job_routes_infra_error_and_continues():
    compiled, t, i, u = [], [], [], []

    def compile_one(j):
        if j == "bad":
            raise ValueError("boom")
        compiled.append(j)

    compile_core.run_per_job(
        ["bad", "good"],
        compile_one,
        fences=_fences(t, i, u, infra_errors=(ValueError,)),
        job_timeout_seconds=0,
    )
    assert i == [("bad", "ValueError")]  # routed to the infra fence
    assert compiled == ["good"]          # one bad job never tears down the run
    assert not (t or u)


def test_run_per_job_routes_unexpected_error():
    t, i, u = [], [], []
    compile_core.run_per_job(
        ["x"],
        lambda j: (_ for _ in ()).throw(KeyError("nope")),
        fences=_fences(t, i, u, infra_errors=(ValueError,)),
        job_timeout_seconds=0,
    )
    assert u == [("x", "KeyError")]
    assert not (t or i)


def test_run_per_job_routes_memory_exceeded_to_unexpected():
    # CompileMemoryExceededError is not an infra error → routed to on_unexpected (Review Queue).
    t, i, u = [], [], []
    compile_core.run_per_job(
        ["x"],
        lambda j: (_ for _ in ()).throw(compile_core.CompileMemoryExceededError("too big")),
        fences=_fences(t, i, u, infra_errors=(ValueError,)),
        job_timeout_seconds=0,
    )
    assert u == [("x", "CompileMemoryExceededError")]
    assert not (t or i)


def test_run_per_job_routes_timeout():
    t, i, u = [], [], []
    compile_core.run_per_job(
        ["x"],
        lambda j: (_ for _ in ()).throw(compile_core.CompileJobTimeoutError("hung")),
        fences=_fences(t, i, u),
        job_timeout_seconds=0,
    )
    assert t == [("x", "CompileJobTimeoutError")]
    assert not (i or u)
