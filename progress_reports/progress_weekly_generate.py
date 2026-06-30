"""Progress Reports weekly compile — the PROGRESS instantiation of the shared `generate_core`.

Purpose
-------
The progress twin of `safety_reports.weekly_generate` — the SAME deterministic compile engine
(`safety_reports.generate_core.run_generate`), a different config. This module is the thin
PROGRESS binding (`PROGRESS_GENERATE_CONFIG`, P4 parameterize-not-clone, Op Stds §14): it
iterates the progress workspace's own `ITS_Active_Jobs_Progress` sheet, compiles each Active
job's Sat→Fri week of submitted progress-form PDFs into a Box packet, and dual-writes a Rollup
snapshot row + a PENDING `WPR_human_review` row (the progress twin of safety's WSR). The
progress send (P5) reads the human-approved WPR row and transmits.

Invariants
----------
- GENERATION half of the External Send Gate (Foundation Mission v11 Invariant 1): **Zero send
  capability. Zero AI.** `tests/test_capability_gating.py::GATED_SCRIPTS` AST-forbids
  `anthropic` / `anthropic_client` / `graph_client` / `send_mail` / `resend` / `smtplib` /
  `email.mime`. Box egress is the audited `shared.box_client` (via generate_core).
- NO cross-workstream mix-up (operator requirement): the workstream binding is the ONE
  `PROGRESS_GENERATE_CONFIG` — the review row goes to the WPR sheet via `wpr_review.add_wpr_row`
  (which tags `Workstream=progress`), recipients resolve ONLY from `ITS_Active_Jobs_Progress`,
  and the packet files under the progress Box root (`box_legacy_fallback=False` — no safety
  fallback). A progress compile can never write a safety review row or resolve a safety recipient.
- Deterministic over already-HMAC-verified PDFs + typed Smartsheet cells — NO LLM step, so
  Invariant-2 Layer-2 untrusted-content tagging is N/A here.
- Trigger/idempotency (in generate_core): Friday 14:30 local launchd `StartCalendarInterval`,
  staggered 30 min after safety's 14:00 (both hold the host compile mutex);
  SKIP-if-already-compiled-and-no-new-docs; NEVER closes the week; empty week → STILL appends a
  Rollup + WPR row.

Failure modes
-------------
Per-job timeout / memory fences route a single job-week to `ITS_Review_Queue` and continue (one
bad job never tears down the run); an unset progress Box root surfaces a config gap to the
per-job fence (no silent safety-tree fallback, `box_legacy_fallback=False`); a missed Friday run
is operator-recovered by a manual re-run. Full successor-remediation fault tree:
`docs/runbooks/progress_weekly_generate.md` (Op Stds §43).

Consumers
---------
- launchd daemon `org.solutionsmith.its.progress-generate` (the Friday 14:30 trigger).
- The progress send poll (P5) reads the human-approved `WPR_human_review` rows this writes.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from progress_reports import wpr_review
from safety_reports import generate_core
from safety_reports.week_sheet import PROGRESS_WEEK_SHEET_CONFIG
from shared import active_jobs, review_queue
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "progress_reports.progress_weekly_generate"

# The progress Box portal-root ITS_Config key (the progress twin of
# safety_naming.CFG_BOX_PORTAL_ROOT). Unset → no Box mirror tree yet; progress has NO legacy
# project_routing fallback (box_legacy_fallback=False), so an unset root surfaces a config gap
# to the per-job fence rather than silently filing into a safety/legacy tree.
CFG_BOX_PORTAL_ROOT = "progress_reports.box.portal_root_folder_id"

PROGRESS_GENERATE_CONFIG = generate_core.GenerateConfig(
    script_name=SCRIPT_NAME,
    workstream="progress_reports",
    week_sheet_config=PROGRESS_WEEK_SHEET_CONFIG,
    active_jobs_config=active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG,
    review_sheet_id=wpr_review.SHEET_ID,
    # Deferred lookup (mockable at call time, like the safety binding); add_wpr_row bakes in the
    # WPR sheet id + Workstream=progress.
    add_review_row=lambda **kw: wpr_review.add_wpr_row(**kw),
    email_body_template=wpr_review.email_body_template,  # the generic seed body (re-export)
    box_root_setting_key=CFG_BOX_PORTAL_ROOT,
    box_legacy_fallback=False,  # progress has no legacy Box tree — require the portal root
    compile_mutex_role="progress",
    watchdog_slug="progress_weekly_generate",
    sla_tier=review_queue.SlaTier.SAFETY_INTAKE,  # the 4h review window; Workstream tag distinguishes
    cfg_job_timeout="progress_reports.progress_weekly_generate.job_timeout_seconds",
    default_job_timeout=600,
    cfg_memory_ceiling="progress_reports.progress_weekly_generate.merge_memory_ceiling_bytes",
    default_memory_ceiling=256 * 1024 * 1024,
    cfg_evergreen_contact="progress_reports.evergreen_contact_name",
    default_evergreen_contact="the Evergreen Renewables office",
)


@its_error_log(SCRIPT_NAME)
@require_active
def main(week_start_override: date | None = None) -> dict[str, Any]:
    """Compile weekly progress packets + dual-write Rollup/WPR for each Active progress job.

    Args:
        week_start_override: any date inside the target Sat→Fri week (backfill). Defaults to
            the week containing today (Friday run → the just-closed week).
    """
    return generate_core.run_generate(
        PROGRESS_GENERATE_CONFIG, week_start_override=week_start_override
    )


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="progress_reports.progress_weekly_generate",
        description="Compile weekly progress packets + dual-write Rollup/WPR for each Active job.",
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
