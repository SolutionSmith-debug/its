"""Purchase-Order send — the PO instantiation of the shared send engine (WS1 S5b).

Purpose
-------
The PO twin of ``safety_reports.weekly_send`` / ``progress_reports.progress_send``: the
SAME dispatch logic (``safety_reports.weekly_send.send_one_row``), a different
``SendConfig``. This module is the thin PO binding over the S5a seam
(``recipient_lookup`` + ``envelope_builder``); it transmits one human-approved
``PO_Pending_Review`` row to a VENDOR via Microsoft Graph, from ``procurement@``. Invoked
per row by ``po_materials/po_send_poll.py`` (the launchd poller), which runs the F22
approval-attestation gate against the **ITS — Purchase Orders** workspace (§46 —
membership = PO approval authority, decision D11), then calls the bound sender. It is the
send half of the External Send Gate two-process model (Foundation Mission v11 Invariant 1)
for a NEW external audience: the vendor.

Invariants (§42 — why a binding, not a clone)
---------------------------------------------
- **Invariant 1 (External Send Gate):** zero AI capability — ``anthropic_client`` /
  ``anthropic`` AST-forbidden via ``tests/test_capability_gating.py::SEND_SCRIPTS``. No
  Graph-send call of its own; it delegates to the one transmitter
  (``weekly_send.send_one_row``). The generation side (``po_poll``/``po_generate``) is a
  SEPARATE process with zero send capability.
- **No cross-workstream mix-up:** ``workstream_tag="po_materials"`` is the contamination-
  guard expected value (a ``PO_Pending_Review`` row whose ``Workstream`` cell is not
  ``po_materials`` is HARD-HELD before any send; a ``po_materials`` tag on WSR/WPR is the
  same signal there). Recipients come ONLY from ``ITS_Vendors`` (the vendor SoR) — never an
  Active-Jobs sheet — via the ``_VendorRecipientLookup`` binding; the Vendor Key rides the
  ``COL_JOB_ID`` protocol slot (the S1 schema-twin contract).
- **Recipient resolved at SEND time (never the display columns):** TO = the vendor's
  ``Contact Email`` read LIVE from ``ITS_Vendors`` by Vendor Key at dispatch (a procurement-
  contact edit lands on the next send); an unknown key or a blank email → the engine HELDs
  (``held_no_recipient``), never a PO to nobody. CC = the versioned invoice-routing list
  (``purchaser.json``) so the internal Evergreen distribution (procurement / PM / permitting)
  sees every outbound PO — the corpus's invoice-routing convention.
- **Envelope carries the contractual number:** subject
  ``"Purchase Order {po_number} — {project} — {entity}"`` + the job-prefixed attachment
  ``<Job>_PO_{po_number}.pdf`` (via ``po_naming.po_pdf_filename`` — the same canonical name
  the Box file + Smartsheet attachment carry; blank job falls back to ``PO {po_number}.pdf``).
  ``po_number`` is read from the review row's Notes (``po_poll`` seeds it via
  ``po_review.notes_for_review_row``). A row that lost the tag → the envelope returns
  ``None`` → the engine HELDs (``held_missing_envelope``), mirroring the recipient_lookup
  None→HELD convention: an operator-visible HELD row (never re-dispatched) rather than a
  numberless PO to a vendor. The HELD is set BEFORE the write-ahead SENDING marker, so the
  row is never double-sent.
- **Inherited unchanged from the shared engine (§42 there):** the SENT/HELD idempotency
  gates, the write-ahead ``SENDING`` marker (no double-send), the oversized-packet HELD,
  the inline-vs-upload-session transport switch, the Notes-encoded retry state, the
  contamination guard, and the error fences.

Failure modes
-------------
``send_one_row`` returns a typed ``SendResult`` and HELDs (never transmits a half-formed
packet) on: unknown Vendor Key / blank vendor email (``held_no_recipient``), missing
compiled PDF (``held_missing_pdf``), an over-ceiling packet (``held_oversized_packet``), or
a wrong-``Workstream`` row (``held_workstream_mismatch`` + CRITICAL). A review row with no
parseable ``po_number`` is fenced per-row by the poller (never sent). Full successor-
remediation fault tree: ``docs/runbooks/po_send.md`` (Op Stds §43).

Consumers
---------
- launchd daemon ``org.solutionsmith.its.po-send`` via ``po_send_poll`` (the approved-PO
  dispatcher).
- ``main()`` / the CLI — operator manual rerun of one approved row (debugging).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from po_materials import po_naming, po_review, vendors
from po_materials import terms as terms_lib
from safety_reports import weekly_send
from safety_reports.weekly_send import EnvelopeContext, SendConfig, SendResult, _ReviewModule
from shared.error_log import its_error_log
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

SCRIPT_NAME = "po_materials.po_send"
WORKSTREAM = "po_materials"

CFG_FROM_MAILBOX = "po_materials.po_send.from_mailbox"
DEFAULT_FROM_MAILBOX = "procurement@evergreenmirror.com"


@dataclass(frozen=True)
class _VendorRecipientLookup:
    """Resolve the PO recipient from ``ITS_Vendors`` (the SoR) at send time.

    The engine passes the ``COL_JOB_ID`` cell value — for POs the **Vendor Key**
    (``VEN-######``, the S1 schema-twin protocol slot). Returns ``(vendor_email,
    invoice_cc)`` or ``None`` when the key is unknown or the vendor's Contact Email is
    blank → the engine HELDs (``held_no_recipient``), fail toward not-sending. The vendor
    is read LIVE each dispatch; the internal invoice-routing CC is versioned config
    (``purchaser.json``), read at send time so a config change lands the next cycle
    without a daemon reload. Frozen + no captured state (matching the S5a binding
    contract), so ``vendors.get_vendor_by_key`` stays patchable and the config re-reads
    fresh."""

    def __call__(self, vendor_key: str) -> tuple[str, Sequence[str]] | None:
        vendor = vendors.get_vendor_by_key((vendor_key or "").strip())
        if vendor is None:
            return None
        to_addr = str(vendor.get(vendors.COL_CONTACT_EMAIL) or "").strip()
        if not to_addr:
            return None
        routing = terms_lib.load_purchaser_config().get("invoice_routing") or {}
        invoice_cc = tuple(str(c) for c in routing.get("cc", []))
        return to_addr, invoice_cc


@dataclass(frozen=True)
class _PoEnvelope:
    """Subject + attachment name for a PO send. ``po_number`` is read from the review
    row's Notes (``po_poll`` seeds it via ``po_review.notes_for_review_row``); the
    purchaser entity is the versioned ``purchaser.json`` value, read at send time. A row
    with no parseable ``po_number`` returns ``None`` — a PO email must carry its number,
    so the engine HELDs (``held_missing_envelope``, mirroring the recipient_lookup
    None→HELD convention): an operator-visible HELD row (skipped by the SENT/HELD gate,
    never re-dispatched), NOT a numberless PO to a vendor."""

    def __call__(self, ctx: EnvelopeContext) -> tuple[str, str] | None:
        po_number = po_review.row_po_number(ctx.row)
        if not po_number:
            return None
        entity = str(terms_lib.load_purchaser_config().get("entity") or "Evergreen Renewables")
        subject = f"Purchase Order {po_number} — {ctx.project_name} — {entity}"
        return subject, po_naming.po_pdf_filename(po_number, ctx.project_name)


CONFIG = SendConfig(
    script_name=SCRIPT_NAME,
    workstream_tag="po_materials",
    config_workstream=WORKSTREAM,
    # cast: a module doesn't structurally match a Protocol in mypy, but po_review DOES
    # satisfy _ReviewModule's surface (it re-exports the WSR/WPR shared schema; locked by
    # tests/test_po_review.py — same pattern as safety's wsr_review + progress's wpr_review).
    review=cast(_ReviewModule, po_review),
    recipient_lookup=_VendorRecipientLookup(),
    envelope_builder=_PoEnvelope(),
    from_mailbox_cfg_key=CFG_FROM_MAILBOX,
    from_mailbox_default=DEFAULT_FROM_MAILBOX,
    max_send_retries=weekly_send.MAX_SEND_RETRIES,
    upload_session_threshold_bytes=weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES,
)

# #336 — the ONE ITS_Config key send_one_row resolves at RUNTIME: the from_mailbox, read
# under CONFIG.config_workstream ('po_materials'). Declared for the startup observability pass.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CONFIG.from_mailbox_cfg_key, CONFIG.config_workstream, CONFIG.from_mailbox_default, "str"),
]


def send_one_row(row_id: int) -> SendResult:
    """Send (or HELD / FAIL) one approved PO_Pending_Review row via the PO ``CONFIG``.

    Thin wrapper over ``weekly_send.send_one_row`` — the binding is the value, the
    dispatch logic is shared. The poller dispatches through this entry."""
    return weekly_send.send_one_row(row_id, CONFIG)


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved PO_Pending_Review row via CLI (operator debugging)."""
    # #336 startup observability (after @require_active, fail-open). Additive (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    if row_id_override is None:
        raise SystemExit("usage: python -m po_materials.po_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id, "status": result.status,
        "project_name": result.project_name, "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="po_materials.po_send",
        description="Manually send (or HELD) one approved PO_Pending_Review row. Production sends fire via po_send_poll.",
    )
    parser.add_argument("row_id", type=int, help="PO_Pending_Review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
