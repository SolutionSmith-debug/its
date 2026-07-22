"""Observable ITS_Config resolution — every daemon's startup config ledger (§42).

Purpose
-------
Close issue #336: a daemon that silently falls back to a hardcoded default on a
missing / malformed ITS_Config row hides a real misconfiguration. The 2026-07-05
pain that motivated this: the operator went hunting for a config row to flip and
found NONE existed — a boolean gate read via ``_read_bool_setting(default=False)``
treats a MISSING row identically to a row set to ``false``, so a capability that
"ships dark" has no visible switch at all. This module makes every runtime
ITS_Config key a daemon resolves **observable at startup**: each key is resolved
once, logged with its resolved value AND its source (``ITS_Config`` vs
``default``), and a MISSING declared row WARNs LOUD and DISTINCTLY from a row that
merely holds the default (``error_code="config_row_missing"``).

Invariants
----------
- **Additive, not a replacement** (Op Stds v20 §14): ``resolve_and_log`` is a
  startup observability pass. It does NOT replace the per-key runtime
  ``_read_*_setting`` reads each daemon already does — both read; the startup log
  is the observability, the runtime read is the behavior. The existing wrappers
  are UNCHANGED.
- **Fail-open, always** ("never silent" ≠ "never continue"): a config-observability
  failure must NEVER raise or crash a daemon. Every per-key resolution is fenced;
  on any error the key resolves to its declared default and the loop continues.
- **A missing row is distinct** from a present-but-blank row and from a transient
  read error — three distinguishable log lines (modelled on ``kill_switch``'s
  three fail-open WARN branches) so the morning ITS_Errors scan reveals exactly
  which state each key is in.
- **Deterministic + no network beyond ``get_setting``**: one Smartsheet read per
  key, no side effects other than the log lines.

Failure modes
-------------
- Row present, non-blank Value  → collected into the per-pass INFO summary
  (``source ITS_Config``, value coerced)
- Row present, blank Value (get_setting returns None) → collected into the per-pass
  INFO summary (``source default``)
- After the loop, ONE INFO summary line names every successfully-resolved key with
  its value + source (``config resolved N key(s): setting[workstream]=value(source);
  ...``); emitted only when ≥1 key resolved (§5 "log each resolved setting with its
  source" — one line naming them all).
- Row MISSING (SmartsheetNotFoundError) → WARN ``config_row_missing`` (→ default),
  kept PER-KEY (each is an individually actionable "seed this row")
- Any other SmartsheetError (transient / circuit-open / auth) → collected and
  summarized into ONE ``config_read_error`` WARN per pass (→ default this cycle,
  fail-open)

Consumers
---------
Every polling / scheduled daemon entry point declares a module-level
``REQUIRED_CONFIG: list[ConfigKey]`` enumerating the (Setting, Workstream) pairs
it resolves at runtime, and calls ``resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)``
at the top of its decorated entry function (after ``@require_active`` — a PAUSED
daemon never logs). Current consumers: ``field_ops.fieldops_sync``,
``safety_reports.portal_poll`` / ``intake`` / ``compile_now_poll`` /
``weekly_generate`` / ``weekly_send`` / ``weekly_send_poll`` / ``publish_daemon``,
``progress_reports.progress_weekly_generate`` / ``progress_send`` /
``progress_send_poll``, ``scripts.watchdog``, and ``scripts.run_picklist_sync``.

Scoping — what a daemon declares vs what it doesn't
---------------------------------------------------
A daemon's ``REQUIRED_CONFIG`` enumerates the keys IT resolves directly, plus its
own per-daemon transitive keys (e.g. ``fieldops_sync`` declares the three
``progress_reports.*.row_cap_warn_threshold`` keys its row-cap monitors read). It
does NOT re-declare keys owned by a SHARED sub-helper every caller invokes — those
belong to the sub-helper's own observability at its own boundary, not duplicated
across N callers (which would be unbounded):
  - ``system.state`` — ``kill_switch.check_system_state`` already emits its own
    three-branch fail-open WARN (the model this module follows).
  - ``smartsheet.sheet_count_ceiling`` / ``sheet_count_margin`` — read by the
    shared ``sheet_capacity`` helper on the sheet-create path (bounded follow-up:
    wire the helper's own observability once at its boundary, not per caller).
  - ``alerting.*`` — read by ``alert_dedupe``.
``scripts.audit_picklist_drift`` reads NO ITS_Config at runtime — nothing to
declare (a visibly-complete roster, not a silent omission).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import smartsheet_client
from .error_log import Severity, log
from .smartsheet_client import SmartsheetError, SmartsheetNotFoundError

_VALID_KINDS = ("str", "bool", "int", "float")
_TRUTHY = ("true", "1", "yes", "on")


@dataclass(frozen=True)
class ConfigKey:
    """One declared ITS_Config key a daemon resolves at runtime.

    ``setting`` and ``workstream`` are the (Setting, Workstream) row-key pair
    ``smartsheet_client.get_setting`` matches on — BOTH matter, because the same
    Setting name can be read under different Workstream cells (e.g. the shared
    ``safety_reports.portal.worker_base_url`` is read under ``field_ops`` and
    ``progress_reports`` too, and the progress-intake gate
    ``progress_reports.intake_enabled`` is read under ``safety_reports`` — the
    footgun documented at ``safety_reports.intake``).

    ``default`` is the value used when the row is missing / blank / unreadable.
    ``kind`` selects the coercion applied to a resolved string value.
    """

    setting: str
    workstream: str
    default: object
    kind: str = "str"
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"ConfigKey kind must be one of {_VALID_KINDS}, got {self.kind!r} "
                f"for setting {self.setting!r}"
            )


def _coerce(raw: str, key: ConfigKey) -> object:
    """Coerce a resolved string per ``key.kind``; fall back to the default on a
    malformed int/float (never raises)."""
    if key.kind == "bool":
        return raw.strip().lower() in _TRUTHY
    if key.kind == "int":
        try:
            return int(raw.strip())
        except (TypeError, ValueError):
            return key.default
    if key.kind == "float":
        try:
            return float(raw.strip())
        except (TypeError, ValueError):
            return key.default
    return raw  # "str"


def resolve_and_log(
    script_name: str, keys: Sequence[ConfigKey]
) -> dict[str, object]:
    """Resolve every declared ITS_Config key once and log each with its source.

    Returns ``{setting: resolved_value}``. The return value is a convenience for
    callers/tests — the load-bearing effect is the per-key log line. Fail-open:
    a per-key failure resolves to the declared default and never propagates, so
    this can be called unconditionally at daemon startup without risking the run.
    """
    resolved: dict[str, object] = {}
    # Successfully-resolved keys (present-non-blank AND present-but-blank) are COLLECTED and
    # summarized into ONE INFO line per pass rather than logged per key. Rationale: these
    # one-shot-per-StartInterval daemons re-run their startup config pass every cycle, so a
    # daemon with N declared keys emitting N INFO lines *per cycle* (and each written twice —
    # daily log + launchd stdout) dominated the log corpus (44.6%). One summary line that
    # NAMES every key with its value + source still satisfies HOUSE_REFLEXES §5 ("log each
    # resolved setting with its source") while cutting the volume. Order is the input key
    # order (deterministic + greppable).
    resolved_summary: list[str] = []
    # Transient read failures (circuit-open / timeout / 5xx / auth) are COLLECTED and
    # summarized into ONE WARN per pass rather than logged per key. Rationale: during a
    # Smartsheet outage the breaker short-circuits every get_setting, so a daemon with N
    # declared keys would otherwise emit N `config_read_error` WARNs *per cycle* — a flood
    # across daemons. The missing-ROW WARN stays per-key (each is an individually actionable
    # "seed this row"); only the transient case is summarized. Fail-open is unchanged (each
    # failed key still resolves to its default).
    transient_failures: list[tuple[str, str]] = []
    for key in keys:
        try:
            raw = smartsheet_client.get_setting(
                key.setting, workstream=key.workstream
            )
        except SmartsheetNotFoundError:
            # THE #336 distinct case: NO ROW at all. WARN loud so the operator
            # can see the switch that does not yet exist and seed it.
            log(
                Severity.WARN,
                script_name,
                f"config {key.setting} [{key.workstream}]: NO ROW in ITS_Config "
                f"— using default {key.default!r}. Seed a row "
                f"(Setting={key.setting}, Workstream={key.workstream}) to make "
                f"this observable + flippable.",
                error_code="config_row_missing",
            )
            resolved[key.setting] = key.default
            continue
        except SmartsheetError as exc:
            # Transient / circuit-open / auth — read failed this cycle. Fail-open to
            # the default; COLLECT for the one-per-pass summary (not a per-key WARN).
            transient_failures.append((key.setting, type(exc).__name__))
            resolved[key.setting] = key.default
            continue
        except Exception as exc:  # noqa: BLE001 — observability must never crash a daemon
            transient_failures.append((key.setting, type(exc).__name__))
            resolved[key.setting] = key.default
            continue

        if raw is None:
            # Row present but Value cell blank. NOT a misconfig to page on — the
            # operator seeded the row and left it default — but still surfaced (in the
            # per-pass summary line below; source "default").
            resolved_summary.append(
                f"{key.setting}[{key.workstream}]={key.default!r}(default)"
            )
            resolved[key.setting] = key.default
            continue

        value = _coerce(raw, key)
        resolved_summary.append(
            f"{key.setting}[{key.workstream}]={value!r}(ITS_Config)"
        )
        resolved[key.setting] = value

    if resolved_summary:
        # ONE INFO summary line for every successfully-resolved key this pass, naming each
        # key with its value + source (§5). Emitted only when ≥1 key resolved — a daemon
        # that declared zero keys, or whose every key missed/errored, writes no summary.
        log(
            Severity.INFO,
            script_name,
            f"config resolved {len(resolved_summary)} key(s): "
            f"{'; '.join(resolved_summary)}",
        )

    if transient_failures:
        # ONE summarized WARN for the whole pass (alert-hygiene): during a breaker-open /
        # transient-Smartsheet window this replaces the former per-key flood. error_code
        # stays `config_read_error` so existing rotation/filters/runbooks are unchanged.
        settings = [s for s, _ in transient_failures]
        exc_types = sorted({t for _, t in transient_failures})
        log(
            Severity.WARN,
            script_name,
            f"config read failed for {len(transient_failures)} of {len(keys)} key(s) this "
            f"cycle — using defaults (fail-open): {', '.join(exc_types)}. Keys: "
            f"{', '.join(settings)}",
            error_code="config_read_error",
        )

    return resolved
