"""Safety Reports weekly compile — the SAFETY instantiation of the shared `generate_core`.

Generation half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1) for the Safety Portal pull flow. **Zero send capability. Zero AI.**

P4 (parameterize-not-clone, Op Stds §14): the deterministic compile engine — gather the
week sheet's per-submission PDFs → merge into one branded packet → file to an `ITS`-prefixed
Box week folder (append-only, version-numbered) → dual-write a Rollup snapshot row + a
PENDING `WSR_human_review` row — now lives ONCE in `safety_reports.generate_core`, driven by
a `GenerateConfig`. This module is the thin SAFETY binding (`SAFETY_GENERATE_CONFIG`) carrying
the EXACT prior values, so the safety compile is **byte-identical** to its pre-extraction self
(guarded by `tests/test_weekly_generate.py`). The progress twin is
`progress_reports.progress_weekly_generate` (same core, the progress config).

Trigger + idempotency (unchanged — implemented in generate_core)
---------------------------------------------------------------
- Friday 14:00 local via launchd `StartCalendarInterval` (watchdog Check-I catch-up re-runs a
  missed Friday via `_run_pipeline` below). `--week-start` backfills a specific week.
- SKIP-if-already-compiled-and-no-new-docs (unless `Compile Now`); NEVER closes the week
  (a later submission + recompile APPENDS a fresh packet/Rollup/PENDING WSR row); empty week →
  STILL appends a Rollup + WSR row (the WSR row carries no packet, so `weekly_send` HELDs it).

Capability gating (Invariant 1)
-------------------------------
No send, no AI. `tests/test_capability_gating.py::GATED_SCRIPTS` forbids `anthropic` /
`anthropic_client` AND `graph_client` / `send_mail` / `resend` / `smtplib` / `email.mime` on
this module (and on generate_core). Box egress is the audited `shared.box_client`.

Consumers of the safety-bound aliases below: `safety_reports.compile_now_poll`
(`_compile_job_week` with a `selection` narrowing, `RunSummary`, `_safe_review_queue`) and
`scripts/watchdog.py` Check-I (`_run_pipeline`). They call the safety compile WITHOUT changing
their own code — the aliases bind `SAFETY_GENERATE_CONFIG` for them.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from safety_reports import generate_core, safety_naming, wsr_review
from safety_reports.week_sheet import SAFETY_WEEK_SHEET_CONFIG
from shared import active_jobs, review_queue, safety_week
from shared.active_jobs import ActiveJob
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.weekly_generate"

# The SAFETY binding: every value is the EXACT pre-P4 constant, so the safety compile is
# byte-identical. `add_review_row` is wsr_review.add_wsr_row with the WSR sheet id bound +
# workstream defaulting to "safety"; `box_legacy_fallback=True` keeps safety's project_routing
# fallback when the portal Box root is unset.
SAFETY_GENERATE_CONFIG = generate_core.GenerateConfig(
    script_name=SCRIPT_NAME,
    workstream="safety_reports",
    week_sheet_config=SAFETY_WEEK_SHEET_CONFIG,
    active_jobs_config=active_jobs.SAFETY_ACTIVE_JOBS_CONFIG,
    review_sheet_id=wsr_review.SHEET_ID,
    # Deferred lookup (NOT functools.partial): resolve wsr_review.add_wsr_row by name at CALL
    # time so a test patching wsr_review.add_wsr_row is honored. Production-identical.
    add_review_row=lambda **kw: wsr_review.add_wsr_row(wsr_review.SHEET_ID, **kw),
    email_body_template=wsr_review.email_body_template,
    box_root_setting_key=safety_naming.CFG_BOX_PORTAL_ROOT,
    box_legacy_fallback=True,
    compile_mutex_role="safety",
    watchdog_slug="safety_weekly_generate",
    sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
    cfg_job_timeout="safety_reports.weekly_generate.job_timeout_seconds",
    default_job_timeout=600,  # 10 min/job — one hung Box/Smartsheet call can't block the run
    cfg_memory_ceiling="safety_reports.weekly_generate.merge_memory_ceiling_bytes",
    default_memory_ceiling=256 * 1024 * 1024,  # 256 MiB of gathered source PDFs before merge
    cfg_evergreen_contact="safety_reports.evergreen_contact_name",
    default_evergreen_contact="the Evergreen Renewables office",
)

# ── Safety-bound aliases (preservation): compile_now_poll + watchdog call these unchanged ──

RunSummary = generate_core.RunSummary


def _run_pipeline(*, week_start_override: date | None) -> dict[str, Any]:
    """The undecorated safety compile pipeline (watchdog Check-I calls this directly to bypass
    @require_active / @its_error_log)."""
    return generate_core.run_generate(
        SAFETY_GENERATE_CONFIG, week_start_override=week_start_override
    )


def _compile_job_week(
    job: ActiveJob, week: safety_week.SafetyWeek, summary: generate_core.RunSummary,
    correlation_id: str, *, selection: set[int] | None = None, memory_ceiling: int = 0,
) -> None:
    """Safety-bound per-(job, week) compile — compile_now_poll reuses this for the on-demand
    `selection`-narrowed packet (never a second compile path)."""
    generate_core._compile_job_week(
        SAFETY_GENERATE_CONFIG, job, week, summary, correlation_id,
        selection=selection, memory_ceiling=memory_ceiling,
    )


def _safe_review_queue(
    job: ActiveJob, week: safety_week.SafetyWeek, error_class: str, correlation_id: str,
    summary: generate_core.RunSummary,
) -> None:
    """Safety-bound per-job failure → Review Queue (compile_now_poll reuses this)."""
    generate_core._safe_review_queue(
        SAFETY_GENERATE_CONFIG, job, week, error_class, correlation_id, summary
    )


@its_error_log(SCRIPT_NAME)
@require_active
def main(week_start_override: date | None = None) -> dict[str, Any]:
    """Compile weekly safety packets + dual-write Rollup/WSR for each Active job.

    Args:
        week_start_override: any date inside the target Sat→Fri week (backfill). Defaults to
            the week containing today (Friday run → the just-closed week).
    """
    return generate_core.run_generate(
        SAFETY_GENERATE_CONFIG, week_start_override=week_start_override
    )


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safety_reports.weekly_generate",
        description="Compile weekly safety packets + dual-write Rollup/WSR for each Active job.",
    )
    parser.add_argument(
        "--week-start", type=lambda s: date.fromisoformat(s), default=None,
        help="Any date inside the target Sat→Fri week (backfill). Defaults to the week containing today.",
    )
    args = parser.parse_args(argv)
    main(week_start_override=args.week_start)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
