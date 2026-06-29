"""Shared hardened compile core — reusable orchestration + robustness fences for the
deterministic compile pipelines (Op Stds §14 parameterize-not-clone; Stage-0 A6).

Both the safety weekly compile (`safety_reports.weekly_generate`) and the future progress
compile (P4-core) INSTANTIATE this core instead of re-cloning the serial loop, so the
single-host robustness fences are written ONCE and inherited by both:

  * ``job_time_budget``      — a per-job SIGALRM wall-clock fence, so one hung Box/Smartsheet
                               call cannot block the whole run.
  * ``enforce_memory_budget`` — a pre-``merge_pdfs`` total-bytes guard, so a pathological week
                               is routed to the Review Queue instead of OOMing the daemon.
  * ``run_per_job``          — the per-job loop wrapping both fences + a per-job error fence
                               (one bad job never tears down the run).

§42 — why these and not others: launchd has NO default ``ExitTimeOut``, so there is no silent
SIGKILL to defend against (do NOT add one). The binding single-host risks A6 closes are the
wall-clock hang and the merge-step memory spike — handled above. (These fences bind only where
``run_per_job`` is used — the scheduled ``weekly_generate`` + the future P4-core; the on-demand
``compile_now_poll`` single-job path is intentionally unfenced.) The loop is shared (not the
safety-vs-progress specifics) so the future progress compile does not "re-clone the serial
loop" (plan P4-core). This module deliberately depends on stdlib ONLY (no shared/* import), so
it carries no policy and is trivially capability-clean.

§43 (successor-remediation) — if the weekly/progress compile daemon:
  * appears HUNG / a run never finishes → each job is now SIGALRM-fenced; check ITS_Errors for
    ``*.compile_timeout`` and the Review Queue for the fenced ``(job, week)``. Low-class repair:
    re-run the compile — the Rollup watermark is written LAST (see weekly_generate), so a re-run
    resumes (skips a fully-compiled (job, week), recompiles a half-done one); a duplicate PENDING
    review row is caught at human approval, never an un-sent week.
    NOTE: these fences bind only to the SCHEDULED compile (``weekly_generate`` via ``run_per_job``)
    and the future P4-core compile — NOT ``compile_now_poll``'s on-demand single-job path (it calls
    ``_compile_job_week`` directly, no fence), so a hung Compile-Now is NOT auto-timed-out and needs
    a manual process kill (launchd has no ExitTimeOut).
  * fences a job as ``CompileMemoryExceededError`` → that week's gathered PDFs exceeded the configured
    ceiling; the job is in the Review Queue, NOT lost. Low-class repair: inspect that week's
    submissions; only raise the ``*.merge_memory_ceiling_bytes`` ITS_Config value with care.
  ESCALATE to the Developer-Operator (Seth) for anything needing a CODE change (a fixed
  high-capability-class category) — e.g. the fence logic itself misbehaving.

Capability gating (Invariant 1): enrolled in ``tests/test_capability_gating.py::GATED_SCRIPTS``
— imports NO send surface and NO LLM. It orchestrates already-rendered PDFs + typed cells:
there is nothing to send and nothing to reason over.
"""
from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class CompileJobTimeoutError(Exception):
    """A single ``(job, period)`` compile exceeded its per-job wall-clock budget (SIGALRM)."""


class CompileMemoryExceededError(Exception):
    """The gathered per-submission PDFs exceed the configured pre-merge byte ceiling."""


@contextmanager
def job_time_budget(seconds: int) -> Iterator[None]:
    """Per-job SIGALRM wall-clock fence: raise ``CompileJobTimeoutError`` if the wrapped block runs
    longer than ``seconds``.

    NO-OP when ``seconds <= 0``, when SIGALRM is unavailable, or when not on the main thread
    (signal handlers install only on the main thread) — so tests and non-main contexts stay
    benign and the happy path is unchanged. Restores the prior handler and cancels the timer in
    ``finally``, so a normal (fast) compile leaves no alarm armed.
    """
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _on_alarm(signum: int, frame: Any) -> None:
        raise CompileJobTimeoutError(f"compile exceeded its {seconds}s wall-clock budget")

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.alarm(seconds)
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def enforce_memory_budget(pdf_sizes: Iterable[int], ceiling_bytes: int) -> int:
    """Sum ``pdf_sizes`` (the gathered per-submission PDF byte lengths) and raise
    ``CompileMemoryExceededError`` if the total exceeds ``ceiling_bytes``.

    ``ceiling_bytes <= 0`` disables the guard. Returns the total. Call BEFORE ``merge_pdfs``
    (which ~doubles peak memory) so an oversized week is fenced to the Review Queue rather than
    OOMing the host.
    """
    total = sum(pdf_sizes)
    if ceiling_bytes > 0 and total > ceiling_bytes:
        raise CompileMemoryExceededError(
            f"gathered PDFs total {total} bytes, exceeding the {ceiling_bytes}-byte "
            "pre-merge ceiling"
        )
    return total


@dataclass
class JobFences:
    """Per-job exception → side-effect routing supplied by the concrete compile.

    Each callback receives ``(job, error_class_name, exc)`` and records/surfaces it (e.g. a
    Review-Queue row + an ITS_Errors log); ``run_per_job`` guarantees one bad job never tears
    down the run. ``infra_errors`` is the tuple of transient-infra exception types (e.g.
    ``SmartsheetError``, ``BoxError``) routed to ``on_infra_error``; everything else (incl.
    ``CompileMemoryExceededError``) routes to ``on_unexpected``.
    """

    on_timeout: Callable[[Any, str, BaseException], None]
    on_infra_error: Callable[[Any, str, BaseException], None]
    on_unexpected: Callable[[Any, str, BaseException], None]
    infra_errors: tuple[type[Exception], ...]


def run_per_job(
    jobs: Iterable[Any],
    compile_one: Callable[[Any], None],
    *,
    fences: JobFences,
    job_timeout_seconds: int,
    on_job_start: Callable[[Any], None] | None = None,
) -> None:
    """Iterate ``jobs``, compiling each under a per-job wall-clock budget (``job_time_budget``)
    plus a per-job error fence.

    ``on_job_start`` (if given) runs before each job (e.g. a processed counter). A timeout
    routes to ``fences.on_timeout``; a ``fences.infra_errors`` member to ``on_infra_error``;
    anything else (incl. ``CompileMemoryExceededError``) to ``on_unexpected``. The loop ALWAYS
    continues to the next job — one job's failure is recorded, never fatal.
    """
    for job in jobs:
        if on_job_start is not None:
            on_job_start(job)
        try:
            with job_time_budget(job_timeout_seconds):
                compile_one(job)
        except CompileJobTimeoutError as exc:
            fences.on_timeout(job, "CompileJobTimeoutError", exc)
        except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never kills the run
            if isinstance(exc, fences.infra_errors):
                fences.on_infra_error(job, type(exc).__name__, exc)
            else:
                fences.on_unexpected(job, type(exc).__name__, exc)
