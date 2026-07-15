"""verify_cutover — the §53 mechanical production-cutover gate (read-only).

Op Stds v20 §53 (sandbox-masks-production): a cutover claim is narrative until
it is mechanically verified. This script IS that verification — the Aug-3
tenant cutover (and the Aug-7 on-site re-run) is **not done** until it exits 0.
Companion docs:

- ``docs/operations/cutover_checklist.md`` (v2) — every checklist item that a
  machine can check cross-references a ``VC-NN`` id below.
- ``docs/operations/host_migration_runbook.md`` — Phase B runs a subset via
  ``--only`` before the tenant cutover exists.
- ``docs/operations/production_rollback.md`` — re-run after any rollback leg.

Design contract:

- **Read-only.** Keychain reads, ``launchctl list``, Smartsheet sheet reads,
  ``git status``/``rev-parse``/``ls-remote``, ``wrangler d1 migrations list``.
  No writes, no sends, no AI — this script must never need enrollment in the
  send/generation lists of ``tests/test_capability_gating.py``.
- **No ``@require_active``.** The cutover runs under ``system.state=MAINTENANCE``
  (and a rollback may run under PAUSED); the gate must execute in both.
- **Never silent.** Every check prints ``[PASS]``/``[FAIL] VC-NN slug`` with a
  one-line summary; failures carry details. A partial run (``--only``/``--skip``)
  prints a loud PARTIAL-RUN banner — a partial run is NOT a cutover verdict.
- **Secrets never printed.** The keychain check reports presence + length only
  (§54 discipline).

Checks (each independently selectable via ``--only`` / ``--skip``):

====== ============== ==============================================================
id     slug           what it proves
====== ============== ==============================================================
VC-01  keychain       all required Keychain secrets present (18: 11 non-Box + Box
                      triplet + ``ITS_PORTAL_PO_TOKEN`` + the config-actuator /
                      subcontract-poll daemon bearers + the operator-dashboard PIN)
VC-02  launchd        every shipped ``org.solutionsmith.its.*`` plist loaded EXCEPT the
                      dark-unloaded send daemons (``po-send`` — send-gate), which must
                      NOT be loaded (no missing, no orphans, no dark send daemon running)
VC-03  config         load-bearing ITS_Config rows present + non-default
                      (worker_base_url, from_mailbox rows, scheduled_send_local,
                      the polling/sync/intake gates, ``system.operator_email``,
                      the subcontract-poll gate rows) and — unless
                      ``--allow-sandbox`` — free of the mirror domain
VC-04  daemon-health  every Enabled ITS_Daemon_Health row has a heartbeat fresher
                      than 2 x its Interval Seconds (the schema's documented
                      staleness threshold)
VC-05  review-queue   ITS_Review_Queue reachable (read of pending rows succeeds)
VC-06  alerting       ITS_SENTRY_DSN + ITS_RESEND_API_KEY present and shape-valid
VC-07  git            repo on ``main``, working tree clean, HEAD == origin/main
VC-08  d1-migrations  ``wrangler d1 migrations list <db> --remote`` reports none
                      pending (one retry on transient Cloudflare 7403)
VC-09  heartbeat-url  ``system.heartbeat_url`` (UptimeRobot) configured, https
====== ============== ==============================================================

PO enrollment note (WS1): ``po_send`` has LANDED (PR #500, ships dark via a seeded
``po_materials.po_send.polling_enabled=false`` row), so its production-address surface is
now enrolled below — ``po_materials.po_send.from_mailbox`` (VC-03 sandbox-scanned) plus the
two previously-unscanned ``worker_base_url`` copies (the ``progress_reports`` + ``po_materials``
Workstream rows of ``safety_reports.portal.worker_base_url``), closing the mechanical gap the
manual CL-14 grep used to backstop. The keychain check already requires the
``ITS_PORTAL_PO_TOKEN`` bearer. DEFERRED (NOT enrolled): ``po_send.polling_enabled`` /
``scheduled_send_local`` — enrolling ``polling_enabled`` as ``"true"`` would DEMAND PO send be
live at cutover, and first-enabling a send path is a FIXED high-capability External-Send-Gate
decision (Seth). Enroll them only once PO send is confirmed in the Aug-7 send scope.

Dark-daemon-bearer + dashboard-PIN enrollment (WS2 / config editor / subcontracts,
operator directive 2026-07-12): three more Keychain secrets are now cutover-required
even though their consumers ship dark — same "provision-even-while-dark" rationale as
``ITS_PORTAL_PO_TOKEN``. ``ITS_PORTAL_CONFIG_TOKEN`` (the §50 ``config_actuator`` daemon
bearer) and ``ITS_PORTAL_SUB_TOKEN`` (the ``subcontract_poll`` daemon bearer) both back
LOADED-but-runtime-gated daemons, so their tokens must be present at cutover for the
activation cell-flip to work. ``ITS_OPERATOR_PIN`` gates the operator dashboard
(``operator_dashboard/auth.py``, manual-start, no plist yet); it is enrolled so the
operator ACT surface is usable at cutover. VC-03 additionally now scans
``system.operator_email`` (the last-resort Resend page recipient, must be off the mirror
domain — CO-3) and asserts the three ``subcontracts.subcontract_poll.*`` gate rows are
seeded present (``non_empty``, NOT forced ``true`` — the dark-ship reflex: a missing gate
row leaves the operator no switch to flip). DEFERRED (NOT enrolled) until the SC-S4
subcontract SEND half is built: the subcontract ``from_mailbox`` / ``scheduled_send_local`` /
send ``polling_enabled`` rows — those daemons + rows do not exist yet.

Daemon-gate seed enrollment (2026-07-13 config-WARN-storm incident): VC-03 also asserts
the three previously-unenrolled rows from ``seed_daemon_gate_config.py`` are seeded
present — ``safety_reports.photo_screen.clamav_enabled`` and both
``<workstream>.compile_now_poll.polling_enabled`` copies (``non_empty``, NOT forced
``true`` — clamav is a dark security gate and the compile-now passes are operator
choices). Their ABSENCE caused the per-cycle ``config_row_missing`` WARN storm that
filled ITS_Errors to the 20k cap. The seed set's two ``progress_send`` rows were already
enrolled.

Usage::

    python -m scripts.verify_cutover                 # full gate (the cutover verdict)
    python -m scripts.verify_cutover --list          # enumerate checks
    python -m scripts.verify_cutover --only keychain,launchd
    python -m scripts.verify_cutover --skip d1-migrations
    python -m scripts.verify_cutover --allow-sandbox # mirror dress-rehearsal mode
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shared import keychain, review_queue, sheet_ids, smartsheet_client
from shared.smartsheet_client import SmartsheetNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---- constants -----------------------------------------------------------

LAUNCHD_PLIST_DIR = REPO_ROOT / "scripts" / "launchd"
LABEL_PREFIX = "org.solutionsmith.its."

# Dark-unloaded SEND daemons: their plist ships in scripts/launchd/ but they stay
# launchd-UNLOADED at cutover so a dark external-send path is not even running
# (send-gate defense-in-depth; operator decision 2026-07-12). VC-02 therefore does NOT
# require them loaded, and FAILS if one IS loaded — a send daemon live at cutover is a
# FIXED high-class External-Send-Gate event (Seth). A future subcontract-send goes here
# too. First-enabling a send path = remove its label here + load its plist.
DARK_UNLOADED_LABELS = frozenset({"org.solutionsmith.its.po-send"})

# The mirror-tenant marker. Any load-bearing config value still containing this
# after cutover means a daemon is pointed at the sandbox (§53 sandbox-masks-
# production). `--allow-sandbox` relaxes this for mirror dress rehearsals.
SANDBOX_DOMAIN_MARKER = "evergreenmirror"

# safety_portal/wrangler.jsonc `database_name` — wrangler keys `--local`/`--remote`
# migration state off the name, not the id.
D1_DATABASE_NAME = "its-safety-portal-db"
# Transient Cloudflare API error code observed on `d1 migrations list --remote`;
# tolerated with exactly ONE retry (a second occurrence is a real outage → FAIL).
D1_TRANSIENT_ERROR_MARKER = "7403"
WRANGLER_TIMEOUT_SECONDS = 180

# ITS_Daemon_Health staleness threshold multiplier — the schema's documented
# design intent ("feeds the stale-heartbeat threshold (2 x Interval Seconds)",
# blueprint references/daemon-health-schema.md).
DAEMON_HEALTH_STALE_MULTIPLIER = 2.0

# Keychain service names, verified against live HEAD (shared/*, safety_reports/*,
# progress_reports/*, field_ops/fieldops_sync.py, po_materials/*, subcontracts/*,
# operator_dashboard/auth.py, safety_portal/worker/*.ts). Presence-only check —
# values are NEVER printed (§54).
NON_BOX_SECRETS: tuple[str, ...] = (
    "ITS_SMARTSHEET_TOKEN",
    "ITS_ANTHROPIC_KEY",
    "ITS_RESEND_API_KEY",
    "ITS_SENTRY_DSN",
    "ITS_MS_TENANT_ID",
    "ITS_MS_CLIENT_ID",
    "ITS_MS_CLIENT_SECRET",
    "ITS_PORTAL_INTERNAL_TOKEN",
    "ITS_PORTAL_HMAC_SECRET",
    "ITS_PORTAL_ADMIN_TOKEN",
    "ITS_PORTAL_FIELDOPS_TOKEN",
)
# Box triplet: single-consumer refresh-token rotation — seeded ONLY in host-
# migration Phase B, on exactly one host (docs/operations/host_migration_runbook.md).
BOX_SECRETS: tuple[str, ...] = (
    "ITS_BOX_CLIENT_ID",
    "ITS_BOX_CLIENT_SECRET",
    "ITS_BOX_REFRESH_TOKEN",
)
# PO internal-tier bearer (Worker `requirePoToken`, safety_portal/worker/po.ts).
# Provisioned with WS1 S2; REQUIRED at cutover. Pre-provisioning runs of the
# keychain check will FAIL naming it — that is correct, not noise.
PO_SECRETS: tuple[str, ...] = ("ITS_PORTAL_PO_TOKEN",)
# Dark-but-loaded daemon bearers (config-actuator §50 + subcontract-poll). Same
# provision-even-while-dark rationale as ITS_PORTAL_PO_TOKEN: both daemons are LOADED
# and runtime-gated false, so their bearer tokens must be present at cutover for the
# activation cell-flip to work. ITS_PORTAL_CONFIG_TOKEN = po_materials/config_actuator.py
# KC_BEARER; ITS_PORTAL_SUB_TOKEN = subcontracts/subcontract_poll.py KC_SUB_TOKEN.
DARK_BEARER_SECRETS: tuple[str, ...] = (
    "ITS_PORTAL_CONFIG_TOKEN",
    "ITS_PORTAL_SUB_TOKEN",
)
# Operator-dashboard PIN (operator_dashboard/auth.py PIN_KEYCHAIN_KEY). The dashboard
# ships dark + manual-start (no launchd plist), but the PIN is REQUIRED at cutover so the
# operator ACT surface is usable (operator directive 2026-07-12). Not a Worker bearer.
OPERATOR_SECRETS: tuple[str, ...] = ("ITS_OPERATOR_PIN",)

REQUIRED_SECRETS: tuple[str, ...] = (
    NON_BOX_SECRETS + BOX_SECRETS + PO_SECRETS + DARK_BEARER_SECRETS + OPERATOR_SECRETS
)


@dataclass(frozen=True)
class ConfigRow:
    """One load-bearing ITS_Config row VC-03 asserts.

    requirement:
        ``non_empty`` — row exists and Value is a non-blank string.
        ``true``      — row exists and Value is the string ``true`` (the
                        boolean-gate convention; a MISSING row reads as false
                        in daemon code, so presence is part of the check).
    sandbox_scan:
        when True, the value must not contain ``SANDBOX_DOMAIN_MARKER``
        (skipped under ``--allow-sandbox``).
    """

    key: str
    workstream: str
    requirement: str
    sandbox_scan: bool = False


# Verified against live HEAD constants (each daemon's CFG_* names). NOTE the
# documented footgun: `progress_reports.intake_enabled` is read under
# Workstream=safety_reports (intake's own workstream), not progress_reports.
CONFIG_ROWS: tuple[ConfigRow, ...] = (
    ConfigRow(
        "safety_reports.portal.worker_base_url", "safety_reports", "non_empty",
        sandbox_scan=True,
    ),
    ConfigRow(
        "safety_reports.weekly_send.from_mailbox", "safety_reports", "non_empty",
        sandbox_scan=True,
    ),
    ConfigRow(
        "progress_reports.progress_send.from_mailbox", "progress_reports", "non_empty",
        sandbox_scan=True,
    ),
    # The two previously-unscanned worker_base_url copies. `safety_reports.portal.worker_base_url`
    # is ONE Setting name read under THREE Workstream cells = 3 physical ITS_Config rows
    # (registry.py) — the safety_reports copy is scanned above; these are the progress_reports copy
    # (progress_weekly_generate.py) + the po_materials copy (config_actuator.py). All three MUST be
    # the production custom domain at cutover; enrolling them replaces the manual CL-14 grep backstop
    # with a mechanical sandbox scan.
    ConfigRow(
        "safety_reports.portal.worker_base_url", "progress_reports", "non_empty",
        sandbox_scan=True,
    ),
    ConfigRow(
        "safety_reports.portal.worker_base_url", "po_materials", "non_empty",
        sandbox_scan=True,
    ),
    # po_send LANDED (PR #500, dark). Its FROM address must be production regardless of whether
    # sending is enabled at cutover — enroll it (sandbox-scanned) so a mirror procurement@ residue
    # is caught. NOT enrolling po_send.polling_enabled / scheduled_send_local: demanding
    # polling_enabled="true" would force a send-enable (a high-class External-Send-Gate decision —
    # Seth); add them only once PO send is confirmed in the Aug-7 send scope. (docstring PO note.)
    ConfigRow(
        "po_materials.po_send.from_mailbox", "po_materials", "non_empty",
        sandbox_scan=True,
    ),
    # Feature B (PO document attachments): the §34 screener's ClamAV gate must be
    # SEEDED PRESENT (non_empty, NOT forced 'true' — dark-ship reflex: it ships false
    # and stays false until clamd + pyclamd exist on the Mac; the deterministic L1/L2
    # layers run regardless). seed_po_materials_config.py seeds it.
    ConfigRow("po_materials.po_attach_screen.clamav_enabled", "po_materials", "non_empty"),
    ConfigRow("safety_reports.weekly_send.scheduled_send_local", "safety_reports", "non_empty"),
    ConfigRow("progress_reports.progress_send.scheduled_send_local", "progress_reports", "non_empty"),
    ConfigRow("safety_reports.portal_poll.polling_enabled", "safety_reports", "true"),
    ConfigRow("safety_reports.weekly_send.polling_enabled", "safety_reports", "true"),
    ConfigRow("progress_reports.progress_send.polling_enabled", "progress_reports", "true"),
    ConfigRow("progress_reports.intake_enabled", "safety_reports", "true"),
    ConfigRow("field_ops.fieldops_sync.sync_enabled", "field_ops", "true"),
    # system.operator_email (CO-3): the last-resort Resend page recipient
    # (shared/resend_client.py) resolved when ITS_Config can't be read. Must be a
    # production address at cutover, so sandbox-scanned — a mirror residue
    # (seths@evergreenmirror.com) fails the gate. Global workstream. Closes the CL-12
    # manual-grep backstop with a mechanical scan.
    ConfigRow("system.operator_email", "global", "non_empty", sandbox_scan=True),
    # Subcontracts (operator scoped fully-in incl. send, 2026-07-12). subcontract_poll
    # reuses the safety_reports.portal.worker_base_url row (scanned above), so no new
    # worker_base_url row here. Assert the three subcontract_poll gate rows are SEEDED
    # PRESENT (non_empty, NOT forced 'true' — dark-ship reflex: a missing gate row leaves
    # no switch to flip; seed_subcontracts_config.py must have run). The gates ship false;
    # activation is a later operator cell-flip once the SC-S3c live smoke passes.
    ConfigRow("subcontracts.subcontract_poll.polling_enabled", "subcontracts", "non_empty"),
    ConfigRow("subcontracts.subcontract_poll.subcontractors_sync_enabled", "subcontracts", "non_empty"),
    ConfigRow("subcontracts.subcontract_poll.status_sync_enabled", "subcontracts", "non_empty"),
    # 2026-07-13 config-WARN-storm seeds (scripts/migrations/seed_daemon_gate_config.py):
    # the ABSENCE of these rows made daemons WARN config_row_missing per-cycle and filled
    # ITS_Errors to the 20k cap (the Check O storm-mode incident). Assert them SEEDED
    # PRESENT (non_empty, NOT forced 'true' — same dark-ship reflex as the subcontract
    # gates above): clamav_enabled is a dark security gate (stays 'false' until ClamAV is
    # installed on the Mac) and the compile_now_poll passes are operator-toggleable, so
    # demanding 'true' would pin an operator choice. The two progress_send rows from the
    # same seed set were already enrolled above.
    ConfigRow("safety_reports.photo_screen.clamav_enabled", "safety_reports", "non_empty"),
    ConfigRow("safety_reports.compile_now_poll.polling_enabled", "safety_reports", "non_empty"),
    ConfigRow("progress_reports.compile_now_poll.polling_enabled", "progress_reports", "non_empty"),
    # Subcontract SEND half (SC-S4, built 2026-07-15). The from_mailbox is production-address
    # surface (VC-03 sandbox-scanned — it holds the evergreenmirror.com mirror value, flagged
    # to repoint at cutover), enrolled exactly like po_send.from_mailbox. The send gate +
    # scheduled window are asserted SEEDED PRESENT (non_empty, NOT forced 'true' — the
    # dark-ship reflex: seed_subcontracts_send_config.py must have run so there is a switch to
    # flip). polling_enabled is deliberately NOT forced 'true': turning the subcontract send
    # gate on is a FIXED high-capability-class External-Send-Gate decision (Seth), same posture
    # as po_send's polling_enabled.
    ConfigRow(
        "subcontracts.subcontract_send.from_mailbox", "subcontracts", "non_empty",
        sandbox_scan=True,
    ),
    ConfigRow("subcontracts.subcontract_send.polling_enabled", "subcontracts", "non_empty"),
    ConfigRow("subcontracts.subcontract_send.scheduled_send_local", "subcontracts", "non_empty"),
)


# ---- result plumbing (watchdog.py CheckResult style, PASS/FAIL binary) ----


@dataclass(frozen=True)
class Options:
    """Cross-check options resolved from the CLI."""

    allow_sandbox: bool = False


@dataclass(frozen=True)
class CheckOutcome:
    passed: bool
    summary: str
    details: str = ""


@dataclass(frozen=True)
class CheckSpec:
    check_id: str  # "VC-01" — the id the cutover checklist cross-references
    slug: str      # "keychain" — the --only/--skip handle
    description: str
    fn: Callable[[Options], CheckOutcome]


# ---- VC-01 keychain -------------------------------------------------------


def _check_keychain(opts: Options) -> CheckOutcome:
    """All required Keychain secrets present (presence + length only, §54)."""
    missing: list[str] = []
    present: list[str] = []
    for name in REQUIRED_SECRETS:
        try:
            value = keychain.get_secret(name)
        except keychain.KeychainError:
            missing.append(name)
            continue
        if value:
            present.append(f"{name} (len={len(value)})")
        else:
            missing.append(f"{name} (empty)")
    if missing:
        return CheckOutcome(
            passed=False,
            summary=f"{len(missing)} of {len(REQUIRED_SECRETS)} required secrets missing/empty.",
            details="missing: " + ", ".join(missing),
        )
    return CheckOutcome(
        passed=True,
        summary=f"{len(present)}/{len(REQUIRED_SECRETS)} required Keychain secrets present.",
    )


# ---- VC-02 launchd --------------------------------------------------------


def _expected_labels() -> set[str]:
    """The labels that MUST be loaded at cutover — every shipped plist MINUS the
    dark-unloaded send daemons (DARK_UNLOADED_LABELS). Derived, so a new daemon plist
    auto-enrolls in this check with no edit here."""
    shipped = {p.stem for p in LAUNCHD_PLIST_DIR.glob(f"{LABEL_PREFIX}*.plist")}
    return shipped - DARK_UNLOADED_LABELS


def _launchctl_list() -> str:
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True, check=True, timeout=30
    )
    return result.stdout


def _check_launchd(opts: Options) -> CheckOutcome:
    """`launchctl list` shows exactly the must-be-loaded ITS label set: every shipped
    plist loaded EXCEPT the dark-unloaded send daemons, which must NOT be loaded."""
    expected = _expected_labels()
    loaded: set[str] = set()
    for line in _launchctl_list().splitlines():
        parts = line.split()
        if parts and parts[-1].startswith(LABEL_PREFIX):
            loaded.add(parts[-1])
    missing = sorted(expected - loaded)
    orphans = loaded - expected
    dark_loaded = sorted(orphans & DARK_UNLOADED_LABELS)   # send daemon running = send-gate violation
    true_orphans = sorted(orphans - DARK_UNLOADED_LABELS)  # loaded but not shipped at all
    if missing or dark_loaded or true_orphans:
        details: list[str] = []
        if missing:
            details.append("not loaded: " + ", ".join(missing))
        if dark_loaded:
            details.append(
                "dark-unloaded SEND daemon IS loaded (send-gate violation): "
                + ", ".join(dark_loaded)
            )
        if true_orphans:
            details.append("loaded but not shipped (orphan): " + ", ".join(true_orphans))
        return CheckOutcome(
            passed=False,
            summary=(
                f"launchd label set mismatch ({len(missing)} missing, "
                f"{len(dark_loaded)} dark-loaded, {len(true_orphans)} orphan)."
            ),
            details="; ".join(details),
        )
    return CheckOutcome(
        passed=True,
        summary=(
            f"all {len(expected)} must-load ITS labels loaded; "
            f"{len(DARK_UNLOADED_LABELS)} send daemon(s) correctly unloaded (send-gate)."
        ),
    )


# ---- VC-03 config ---------------------------------------------------------


def _check_config(opts: Options) -> CheckOutcome:
    """Load-bearing ITS_Config rows present + non-default (+ sandbox-free)."""
    problems: list[str] = []
    for row in CONFIG_ROWS:
        try:
            value = smartsheet_client.get_setting(row.key, workstream=row.workstream)
        except SmartsheetNotFoundError:
            problems.append(f"{row.key} [{row.workstream}]: row MISSING")
            continue
        text = (value or "").strip()
        if not text:
            problems.append(f"{row.key} [{row.workstream}]: blank Value")
            continue
        if row.requirement == "true" and text.lower() != "true":
            problems.append(f"{row.key} [{row.workstream}]: expected 'true', got {text!r}")
            continue
        if (
            row.sandbox_scan
            and not opts.allow_sandbox
            and SANDBOX_DOMAIN_MARKER in text.lower()
        ):
            problems.append(
                f"{row.key} [{row.workstream}]: still points at the sandbox "
                f"({SANDBOX_DOMAIN_MARKER!r} in value)"
            )
    if problems:
        return CheckOutcome(
            passed=False,
            summary=f"{len(problems)} of {len(CONFIG_ROWS)} load-bearing config rows failed.",
            details="; ".join(problems),
        )
    suffix = " (sandbox values allowed)" if opts.allow_sandbox else ""
    return CheckOutcome(
        passed=True,
        summary=f"all {len(CONFIG_ROWS)} load-bearing ITS_Config rows present + non-default{suffix}.",
    )


# ---- VC-04 daemon-health --------------------------------------------------


def _parse_heartbeat(raw: object) -> datetime | None:
    """Parse the Last Heartbeat cell (ISO-8601 UTC per the schema). Naive
    strings are assumed UTC (defensive — the writer emits offset-aware)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _check_daemon_health(opts: Options) -> CheckOutcome:
    """Every Enabled ITS_Daemon_Health row fresh within 2 x Interval Seconds.

    Enabled=false rows are ignored — per the schema, Enabled marks "expected
    to write heartbeats" (report-filter metadata; the runtime gate is the
    ITS_Config row, which VC-03 covers).
    """
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_DAEMON_HEALTH)
    now = datetime.now(UTC)
    problems: list[str] = []
    enabled_count = 0
    for row in rows:
        if not row.get("Enabled"):
            continue
        enabled_count += 1
        name = str(row.get("Daemon Name") or f"row {row.get('_row_id')}")
        heartbeat = _parse_heartbeat(row.get("Last Heartbeat"))
        if heartbeat is None:
            problems.append(f"{name}: no parseable Last Heartbeat")
            continue
        try:
            interval = float(str(row.get("Interval Seconds")))
        except (TypeError, ValueError):
            problems.append(f"{name}: no numeric Interval Seconds")
            continue
        age = (now - heartbeat).total_seconds()
        limit = DAEMON_HEALTH_STALE_MULTIPLIER * interval
        if age > limit:
            problems.append(f"{name}: heartbeat {age:.0f}s old (limit {limit:.0f}s)")
    if enabled_count == 0:
        return CheckOutcome(
            passed=False,
            summary="ITS_Daemon_Health has zero Enabled rows — nothing is heartbeating.",
        )
    if problems:
        return CheckOutcome(
            passed=False,
            summary=f"{len(problems)} of {enabled_count} enabled daemon rows stale/unparseable.",
            details="; ".join(problems),
        )
    return CheckOutcome(
        passed=True,
        summary=f"all {enabled_count} enabled daemon-health rows fresh (< 2x interval).",
    )


# ---- VC-05 review-queue ---------------------------------------------------


def _check_review_queue(opts: Options) -> CheckOutcome:
    """ITS_Review_Queue reachable (a read failure at cutover blinds triage)."""
    pending = review_queue.get_pending()
    return CheckOutcome(
        passed=True,
        summary=f"ITS_Review_Queue reachable ({len(pending)} pending row(s)).",
    )


# ---- VC-06 alerting -------------------------------------------------------


def _check_alerting(opts: Options) -> CheckOutcome:
    """Sentry DSN + Resend key present and shape-valid (values never printed)."""
    problems: list[str] = []
    try:
        dsn = keychain.get_secret("ITS_SENTRY_DSN")
        if not dsn.startswith("https://"):
            problems.append("ITS_SENTRY_DSN does not look like an https DSN")
    except keychain.KeychainError:
        problems.append("ITS_SENTRY_DSN missing from Keychain")
    try:
        resend_key = keychain.get_secret("ITS_RESEND_API_KEY")
        if not resend_key.startswith("re_"):
            problems.append("ITS_RESEND_API_KEY does not start with 're_'")
    except keychain.KeychainError:
        problems.append("ITS_RESEND_API_KEY missing from Keychain")
    if problems:
        return CheckOutcome(
            passed=False,
            summary="alerting credentials missing or malformed.",
            details="; ".join(problems),
        )
    return CheckOutcome(passed=True, summary="Sentry DSN + Resend key present and shape-valid.")


# ---- VC-07 git ------------------------------------------------------------


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def _check_git(opts: Options) -> CheckOutcome:
    """On main, clean tree, HEAD == origin/main (read-only: ls-remote, no fetch)."""
    problems: list[str] = []
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        problems.append(f"on branch {branch!r}, expected 'main'")
    if _git("status", "--porcelain"):
        problems.append("working tree not clean")
    local_head = _git("rev-parse", "HEAD")
    ls_remote = _git("ls-remote", "origin", "refs/heads/main")
    remote_head = ls_remote.split()[0] if ls_remote else ""
    if not remote_head:
        problems.append("could not resolve origin/main via ls-remote")
    elif local_head != remote_head:
        problems.append(
            f"HEAD {local_head[:9]} != origin/main {remote_head[:9]} (stale checkout)"
        )
    if problems:
        return CheckOutcome(
            passed=False,
            summary="git tree is not a clean origin/main checkout.",
            details="; ".join(problems),
        )
    return CheckOutcome(
        passed=True, summary=f"on main @ {local_head[:9]}, clean, matches origin/main."
    )


# ---- VC-08 d1-migrations --------------------------------------------------


def _run_wrangler_migrations_list() -> subprocess.CompletedProcess[str]:
    """One `wrangler d1 migrations list --remote` invocation (read-only).

    Runs from safety_portal/ so wrangler resolves wrangler.jsonc + the local
    node_modules install. NEVER run this from a stale checkout — the migrations
    folder IS the comparison baseline (forensic class #2); VC-07 enforces that.
    """
    return subprocess.run(
        ["npx", "wrangler", "d1", "migrations", "list", D1_DATABASE_NAME, "--remote"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT / "safety_portal",
        timeout=WRANGLER_TIMEOUT_SECONDS,
    )


def _check_d1_migrations(opts: Options) -> CheckOutcome:
    """Remote D1 has no pending migrations; one retry on transient 7403."""
    attempts = 0
    result = _run_wrangler_migrations_list()
    attempts += 1
    combined = (result.stdout or "") + (result.stderr or "")
    if D1_TRANSIENT_ERROR_MARKER in combined:
        result = _run_wrangler_migrations_list()
        attempts += 1
        combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0 and "no migrations to apply" in combined.lower():
        return CheckOutcome(
            passed=True,
            summary=f"remote D1 ({D1_DATABASE_NAME}) has no pending migrations"
            + (f" (after {attempts} attempts)" if attempts > 1 else "")
            + ".",
        )
    tail = "\n".join(combined.strip().splitlines()[-8:])
    return CheckOutcome(
        passed=False,
        summary=f"pending migrations or wrangler failure (rc={result.returncode}, "
        f"{attempts} attempt(s)).",
        details=tail,
    )


# ---- VC-09 heartbeat-url --------------------------------------------------


def _check_heartbeat_url(opts: Options) -> CheckOutcome:
    """UptimeRobot heartbeat URL configured (watchdog's external dead-man ping)."""
    try:
        value = smartsheet_client.get_setting("system.heartbeat_url", workstream="global")
    except SmartsheetNotFoundError:
        return CheckOutcome(
            passed=False,
            summary="ITS_Config row system.heartbeat_url [global] MISSING.",
        )
    text = (value or "").strip()
    if not text.startswith("https://"):
        return CheckOutcome(
            passed=False,
            summary="system.heartbeat_url is blank or not an https URL.",
        )
    return CheckOutcome(passed=True, summary="system.heartbeat_url configured (https).")


# ---- registry + harness ---------------------------------------------------

CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec("VC-01", "keychain", "required Keychain secrets present", _check_keychain),
    CheckSpec("VC-02", "launchd", "loaded launchd label set matches shipped plists", _check_launchd),
    CheckSpec("VC-03", "config", "load-bearing ITS_Config rows present + non-default", _check_config),
    CheckSpec("VC-04", "daemon-health", "enabled daemon-health rows fresh (< 2x interval)", _check_daemon_health),
    CheckSpec("VC-05", "review-queue", "ITS_Review_Queue reachable", _check_review_queue),
    CheckSpec("VC-06", "alerting", "Sentry DSN + Resend key present, shape-valid", _check_alerting),
    CheckSpec("VC-07", "git", "repo on main, clean, matches origin/main", _check_git),
    CheckSpec("VC-08", "d1-migrations", "remote D1 has no pending migrations", _check_d1_migrations),
    CheckSpec("VC-09", "heartbeat-url", "UptimeRobot heartbeat URL configured", _check_heartbeat_url),
)


def _resolve_selection(only: str | None, skip: str | None) -> list[CheckSpec]:
    """Filter CHECKS by --only/--skip (comma-separated slugs or VC ids).

    Unknown names raise ValueError — a typo silently skipping a gate check
    would be a fail-open misconfig.
    """
    by_handle = {spec.slug: spec for spec in CHECKS} | {spec.check_id: spec for spec in CHECKS}

    def parse(csv: str) -> list[CheckSpec]:
        specs: list[CheckSpec] = []
        for token in (t.strip() for t in csv.split(",")):
            if not token:
                continue
            if token not in by_handle:
                raise ValueError(
                    f"unknown check {token!r} — valid handles: "
                    + ", ".join(s.slug for s in CHECKS)
                )
            specs.append(by_handle[token])
        return specs

    if only:
        selected = parse(only)
    else:
        selected = list(CHECKS)
    if skip:
        skipped_ids = {spec.check_id for spec in parse(skip)}
        selected = [spec for spec in selected if spec.check_id not in skipped_ids]
    # Preserve canonical order + dedupe.
    seen: set[str] = set()
    ordered: list[CheckSpec] = []
    for spec in CHECKS:
        if spec.check_id in {s.check_id for s in selected} and spec.check_id not in seen:
            seen.add(spec.check_id)
            ordered.append(spec)
    return ordered


def _run_one(spec: CheckSpec, opts: Options) -> CheckOutcome:
    """Failure isolation: an exception inside a check is a FAIL for that check,
    not a crash of the gate (the remaining checks still run)."""
    try:
        return spec.fn(opts)
    except Exception as exc:  # noqa: BLE001 — deliberate harness-level catch
        return CheckOutcome(
            passed=False,
            summary=f"check raised {type(exc).__name__} (treated as FAIL).",
            details=str(exc),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_cutover",
        description="§53 mechanical cutover gate — read-only; exits 0 only when all selected checks pass.",
    )
    parser.add_argument("--only", help="comma-separated check slugs/ids to run (default: all)")
    parser.add_argument("--skip", help="comma-separated check slugs/ids to skip")
    parser.add_argument("--list", action="store_true", help="list checks and exit")
    parser.add_argument(
        "--allow-sandbox",
        action="store_true",
        help="permit evergreenmirror.com values in VC-03 (mirror dress-rehearsal mode)",
    )
    args = parser.parse_args(argv)

    if args.list:
        for spec in CHECKS:
            print(f"{spec.check_id}  {spec.slug:<14} {spec.description}")
        return 0

    try:
        selected = _resolve_selection(args.only, args.skip)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not selected:
        print("error: selection resolves to zero checks", file=sys.stderr)
        return 2

    opts = Options(allow_sandbox=bool(args.allow_sandbox))
    partial = len(selected) != len(CHECKS)
    if partial:
        print("== PARTIAL RUN — not a cutover verdict (some checks not selected) ==")
    if opts.allow_sandbox:
        print("== --allow-sandbox — mirror values permitted; NOT a production verdict ==")

    failures = 0
    for spec in selected:
        outcome = _run_one(spec, opts)
        marker = "PASS" if outcome.passed else "FAIL"
        print(f"[{marker}] {spec.check_id} {spec.slug} — {outcome.summary}")
        if outcome.details and not outcome.passed:
            for line in outcome.details.splitlines():
                print(f"        {line}")
        if not outcome.passed:
            failures += 1

    skipped = len(CHECKS) - len(selected)
    print(
        f"verify_cutover: {len(selected) - failures} passed, {failures} failed, "
        f"{skipped} skipped."
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
