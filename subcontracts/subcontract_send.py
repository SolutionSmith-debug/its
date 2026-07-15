"""Subcontract send — the subcontract instantiation of the shared send engine (SC-S4).

Purpose
-------
The subcontract twin of ``po_materials.po_send`` / ``safety_reports.weekly_send``: the
SAME dispatch logic (``safety_reports.weekly_send.send_one_row``), a different
``SendConfig``. This module is the thin subcontract binding over the S5a seam
(``recipient_lookup`` + ``envelope_builder``); it transmits one human-approved
``Subcontract_Pending_Review`` row to a SUBCONTRACTOR via Microsoft Graph, from
``procurement@`` (decision D10 — reuses PO's mailbox; the 2026-07-15 operator decision).
Invoked per row by ``subcontracts/subcontract_send_poll.py`` (the launchd poller), which
runs the F22 approval-attestation gate against the **ITS — Subcontracts** workspace (§46 —
membership = subcontract approval authority), then calls the bound sender. It is the send
half of the External Send Gate two-process model (Foundation Mission v11 Invariant 1) for a
NEW external audience: the subcontractor.

Invariants (§42 — why a binding, not a clone)
---------------------------------------------
- **Invariant 1 (External Send Gate):** zero AI capability — ``anthropic_client`` /
  ``anthropic`` AST-forbidden via ``tests/test_capability_gating.py::SEND_SCRIPTS``. No
  Graph-send call of its own; it delegates to the one transmitter
  (``weekly_send.send_one_row``). The generation side (``subcontract_poll`` /
  ``subcontract_generate`` / ``subcontract_docx``) is a SEPARATE process with zero send
  capability.
- **No cross-workstream mix-up:** ``workstream_tag="subcontracts"`` is the contamination-
  guard expected value (a ``Subcontract_Pending_Review`` row whose ``Workstream`` cell is
  not ``subcontracts`` is HARD-HELD before any send; a ``subcontracts`` tag on WSR/WPR/PO is
  the same signal there). Recipients come ONLY from ``ITS_Subcontractors`` (the subcontractor
  SoR) via the ``_SubcontractorRecipientLookup`` binding; the Sub Key rides the ``COL_JOB_ID``
  protocol slot (the S1 schema-twin contract), exactly as the Vendor Key does for PO.
- **Recipient resolved at SEND time (never the display columns):** TO = the subcontractor's
  ``Contact Email`` read LIVE from ``ITS_Subcontractors`` by Sub Key at dispatch (a contact
  edit lands on the next send); an unknown key or a blank email → the engine HELDs
  (``held_no_recipient``), never a subcontract to nobody. **CC is EMPTY by design** — a
  subcontract has a single external recipient (the sub); there is no subcontract distribution
  list (contrast PO's invoice-routing CC). This is the as-built intent
  (``subcontract_poll`` seeds the review row's CC blank) and the 2026-07-15 operator decision.
- **Envelope carries the contractual number + the whole signable package:** subject
  ``"Subcontract {sc_number} — {project} — {entity}"`` + the job-prefixed ZIP attachment
  ``<Job>_Subcontract Package_{sc_number}.zip`` (via ``subcontract_naming.sc_package_zip_filename``).
  The package ZIP (Subcontract body ``.docx`` + Exhibit A ``.docx`` + Annex C SoV ``.xlsx``)
  is built + filed to Box by ``subcontract_poll`` and linked in the review row's "Compiled
  PDF" slot; the engine attaches THAT single Box file with the ``.zip`` content-type (2026-07-15
  operator decision — a combined package, no shared-engine multi-attachment change).
  ``sc_number`` is read from the review row's Notes (``subcontract_poll`` seeds it via
  ``subcontract_review.notes_for_review_row``); a row that lost the tag → the envelope returns
  ``None`` → the engine HELDs (``held_missing_envelope``): an operator-visible HELD row, NOT a
  numberless subcontract. The HELD is set BEFORE the write-ahead SENDING marker → never
  double-sent.
- **Inherited unchanged from the shared engine (§42 there):** the SENT/HELD idempotency
  gates, the write-ahead ``SENDING`` marker (no double-send), the oversized-packet HELD, the
  inline-vs-upload-session transport switch, the attachment content-type (derived from the
  filename — ``.zip`` here), the Notes-encoded retry state, the contamination guard, and the
  error fences.

executed / wet signature (SC vs PO)
-----------------------------------
A subcontract's terminal state is ``executed`` (the returned wet-signature copy), AFTER
``sent``. This send half only takes the row to SENT (the shared engine stamps it); the
``executed`` flip is an operator action mirrored back to D1 by ``subcontract_poll``'s status
pass — this module never touches it.

Failure modes
-------------
``send_one_row`` returns a typed ``SendResult`` and HELDs (never transmits a half-formed
package) on: unknown Sub Key / blank subcontractor email (``held_no_recipient``), missing
compiled package (``held_missing_pdf``), an over-ceiling package (``held_oversized_packet``),
or a wrong-``Workstream`` row (``held_workstream_mismatch`` + CRITICAL). A review row with no
parseable ``sc_number`` is HELD (``held_missing_envelope``). Full successor-remediation fault
tree: ``docs/runbooks/subcontract_send.md`` (Op Stds §43).

Consumers
---------
- launchd daemon ``org.solutionsmith.its.subcontract-send`` via ``subcontract_send_poll`` (the
  approved-subcontract dispatcher).
- ``main()`` / the CLI — operator manual rerun of one approved row (debugging).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from safety_reports import weekly_send
from safety_reports.weekly_send import EnvelopeContext, SendConfig, SendResult, _ReviewModule
from shared.error_log import its_error_log
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log
from subcontracts import subcontract_naming, subcontract_review, subcontractors
from subcontracts import terms as terms_lib

SCRIPT_NAME = "subcontracts.subcontract_send"
WORKSTREAM = "subcontracts"

CFG_FROM_MAILBOX = "subcontracts.subcontract_send.from_mailbox"
# Reuses PO's procurement@ mailbox (2026-07-15 operator decision — no dedicated subcontracts
# mailbox). The mirror value; the production cutover repoints it (cutover_checklist).
DEFAULT_FROM_MAILBOX = "procurement@evergreenmirror.com"


@dataclass(frozen=True)
class _SubcontractorRecipientLookup:
    """Resolve the subcontract recipient from ``ITS_Subcontractors`` (the SoR) at send time.

    The engine passes the ``COL_JOB_ID`` cell value — for subcontracts the **Sub Key**
    (``SUB-######``, the S1 schema-twin protocol slot). Returns ``(sub_email, ())`` — an
    EMPTY CC by design (a subcontract has one external recipient; there is no subcontract
    distribution list, unlike PO's invoice-routing CC) — or ``None`` when the key is unknown
    or the subcontractor's Contact Email is blank → the engine HELDs (``held_no_recipient``),
    fail toward not-sending. The subcontractor is read LIVE each dispatch (a contact edit
    lands next cycle). Frozen + no captured state (matching the S5a binding contract), so
    ``subcontractors.get_subcontractor_by_key`` stays patchable."""

    def __call__(self, sub_key: str) -> tuple[str, Sequence[str]] | None:
        subcontractor = subcontractors.get_subcontractor_by_key((sub_key or "").strip())
        if subcontractor is None:
            return None
        to_addr = str(subcontractor.get(subcontractors.COL_CONTACT_EMAIL) or "").strip()
        if not to_addr:
            return None
        return to_addr, ()  # empty CC — a subcontract has a single external recipient


@dataclass(frozen=True)
class _SubcontractEnvelope:
    """Subject + ZIP attachment name for a subcontract send. ``sc_number`` is read from the
    review row's Notes (``subcontract_poll`` seeds it via
    ``subcontract_review.notes_for_review_row``); the contractor entity is the versioned
    ``contractor.json`` value, read at send time. A row with no parseable ``sc_number``
    returns ``None`` — a subcontract email must carry its number, so the engine HELDs
    (``held_missing_envelope``, mirroring the recipient_lookup None→HELD convention): an
    operator-visible HELD row (skipped by the SENT/HELD gate, never re-dispatched), NOT a
    numberless subcontract. The attachment name is the ``.zip`` package name — its extension
    drives the engine's content-type (``application/zip``)."""

    def __call__(self, ctx: EnvelopeContext) -> tuple[str, str] | None:
        sc_number = subcontract_review.row_sc_number(ctx.row)
        if not sc_number:
            return None
        entity = str(terms_lib.load_contractor_config().get("entity") or "Evergreen Renewables")
        subject = f"Subcontract {sc_number} — {ctx.project_name} — {entity}"
        return subject, subcontract_naming.sc_package_zip_filename(sc_number, ctx.project_name)


CONFIG = SendConfig(
    script_name=SCRIPT_NAME,
    workstream_tag="subcontracts",
    config_workstream=WORKSTREAM,
    # cast: a module doesn't structurally match a Protocol in mypy, but subcontract_review
    # DOES satisfy _ReviewModule's surface (it re-exports the WSR/WPR shared schema; locked by
    # tests/test_subcontract_review.py — same pattern as safety's wsr_review + po's po_review).
    review=cast(_ReviewModule, subcontract_review),
    recipient_lookup=_SubcontractorRecipientLookup(),
    envelope_builder=_SubcontractEnvelope(),
    from_mailbox_cfg_key=CFG_FROM_MAILBOX,
    from_mailbox_default=DEFAULT_FROM_MAILBOX,
    max_send_retries=weekly_send.MAX_SEND_RETRIES,
    upload_session_threshold_bytes=weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES,
)

# #336 — the ONE ITS_Config key send_one_row resolves at RUNTIME: the from_mailbox, read
# under CONFIG.config_workstream ('subcontracts'). Declared for the startup observability pass.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CONFIG.from_mailbox_cfg_key, CONFIG.config_workstream, CONFIG.from_mailbox_default, "str"),
]


def send_one_row(row_id: int) -> SendResult:
    """Send (or HELD / FAIL) one approved Subcontract_Pending_Review row via the subcontract
    ``CONFIG``.

    Thin wrapper over ``weekly_send.send_one_row`` — the binding is the value, the dispatch
    logic is shared. The poller dispatches through this entry."""
    return weekly_send.send_one_row(row_id, CONFIG)


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved Subcontract_Pending_Review row via CLI (operator debugging)."""
    # #336 startup observability (after @require_active, fail-open). Additive (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    if row_id_override is None:
        raise SystemExit("usage: python -m subcontracts.subcontract_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id, "status": result.status,
        "project_name": result.project_name, "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="subcontracts.subcontract_send",
        description="Manually send (or HELD) one approved Subcontract_Pending_Review row. "
        "Production sends fire via subcontract_send_poll.",
    )
    parser.add_argument("row_id", type=int, help="Subcontract_Pending_Review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
