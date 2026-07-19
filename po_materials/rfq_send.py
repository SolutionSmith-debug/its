"""RFQ send — the outbound-RFQ instantiation of the shared send engine (ADR-0004 R3).

Purpose
-------
The RFQ twin of ``po_materials.po_send`` / ``safety_reports.weekly_send``: the SAME
dispatch logic (``safety_reports.weekly_send.send_one_row``), a different ``SendConfig``.
This module is the thin RFQ binding over the S5a seam (``recipient_lookup`` +
``envelope_builder``) PLUS the R3 sequence-attachment seam (``extra_attachments``); it
transmits one human-approved ``RFQ_Pending_Review`` row to a VENDOR via Microsoft Graph,
from ``procurement@``. Invoked per row by ``po_materials/rfq_send_poll.py`` (the launchd
poller), which runs the F22 approval-attestation gate against the **ITS — Purchase
Orders** workspace (§46 — the SAME procurement approver set as POs), then calls the bound
sender. It is the send half of the External Send Gate two-process model (Foundation
Mission v11 Invariant 1) for the vendor audience — the outbound half of the RFQ round trip
(the inbound half is the estimate importer + R4 auto-bind).

Invariants (§42 — why a binding, not a clone)
---------------------------------------------
- **Invariant 1 (External Send Gate):** zero AI capability — ``anthropic_client`` /
  ``anthropic`` **and** ``ollama_client`` AST-forbidden via
  ``tests/test_capability_gating.py::SEND_SCRIPTS`` (ADR-0004 decision 12 — send scripts
  are local-AI-free too). No Graph-send call of its own; it delegates to the one
  transmitter (``weekly_send.send_one_row``). The generation side
  (``rfq_poll``/``rfq_generate``) is a SEPARATE process with zero send capability.
- **No cross-lane mix-up (ADR-0004 decision 12 / red-team #8):**
  ``workstream_tag="po_materials_rfq"`` (``rfq_review.WORKSTREAM_TAG``) is the
  contamination-guard expected value — the DISTINCT send-lane tag, NOT the parent
  ``po_materials``. Because every ``RFQ_Pending_Review`` row is hard-populated
  ``po_materials_rfq`` at creation and the tag is registered in
  ``shared.picklist_validation`` (``_RFQ_WORKSTREAM_VALUES``), ``po_send``'s Stage-2b guard
  HARD-HELDs any RFQ row it is ever handed (and vice versa: this sender HARD-HELDs a
  ``po_materials``/``safety``/``subcontracts`` row). Cross-lane dispatch is structurally
  impossible. Recipients come ONLY from ``ITS_Vendors`` (the vendor SoR) via the
  ``_VendorRecipientLookup`` binding; the Vendor Key rides the ``COL_JOB_ID`` protocol slot
  (the S1 schema-twin contract), exactly as for a PO.
- **Recipient resolved at SEND time (never the display columns):** TO = the vendor's
  ``Contact Email`` read LIVE from ``ITS_Vendors`` by Vendor Key at dispatch (a procurement-
  contact edit lands on the next send); an unknown key or a blank email → the engine HELDs
  (``held_no_recipient``), never an RFQ to nobody. CC = the versioned invoice-routing list
  (``purchaser.json``), so the internal Evergreen distribution sees each outbound RFQ,
  mirroring ``po_send`` (procurement visibility of the request, as with the PO).
- **TWO attachments (the R3 sequence-attachment seam):** the primary is the PRICE-FREE
  **RFQ PDF** (the engine downloads it from the review row's "Compiled PDF" Box link — the
  standard single-attachment fetch); the second is the vendor's fillable **``.xlsx`` quote
  form**, resolved by ``_RfqQuoteFormAttachment`` from the Box file id ``rfq_poll`` seeded
  in the review row's Notes (``rfq_review.row_form_box_file_id``). A row with no form box id
  (an RFQ that degraded to PDF-only) or a transient Box download failure → the send goes
  **PDF-only** (WARN, never HELD) — the form is a convenience; the RFQ PDF is the essential
  document, and the body invites a quote "on the attached form OR your own letterhead".
- **Envelope carries the contractual number:** subject
  ``"Request for Quote {rfq_number} — {job_name} — {entity}"`` + the number-only attachment
  ``RFQ {rfq_number}.pdf`` (``rfq_naming.rfq_pdf_filename`` fallback — the WSR-twin row
  carries no vendor name; the Box file keeps the vendor-suffixed name). ``rfq_number`` is
  read from the review row's Notes (``rfq_poll`` seeds it via
  ``rfq_review.notes_for_review_row``); a row that lost the tag → the envelope returns
  ``None`` → the engine HELDs (``held_missing_envelope``): an operator-visible HELD row,
  NOT a numberless RFQ. The HELD is set BEFORE the write-ahead SENDING marker → never
  double-sent.
- **Inherited unchanged from the shared engine (§42 there):** the SENT/HELD idempotency
  gates, the write-ahead ``SENDING`` marker (no double-send), the oversized-packet HELD,
  the inline-vs-upload-session transport switch, the ``.xlsx``/``.pdf`` content-type
  derivation, the Notes-encoded retry state, the contamination guard, and the error fences.

Failure modes
-------------
``send_one_row`` returns a typed ``SendResult`` and HELDs (never transmits a half-formed
packet) on: unknown Vendor Key / blank vendor email (``held_no_recipient``), missing
compiled RFQ PDF (``held_missing_pdf``), a wrong-``Workstream`` row
(``held_workstream_mismatch`` + CRITICAL), or a numberless row (``held_missing_envelope``).
A transient Box failure fetching the quote form degrades the send to PDF-only (WARN), never
HELD. Full successor-remediation fault tree: ``docs/runbooks/rfq_send.md`` (Op Stds §43).

Consumers
---------
- launchd daemon ``org.solutionsmith.its.rfq-send`` via ``rfq_send_poll`` (the approved-RFQ
  dispatcher). Ships **dark** — go-live is a FIXED high-class External-Send-Gate operator
  flip (flip ``po_materials.rfq_send.polling_enabled`` true + load the plist).
- ``main()`` / the CLI — operator manual rerun of one approved row (debugging).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from po_materials import rfq_naming, rfq_review, vendors
from po_materials import terms as terms_lib
from safety_reports import weekly_send
from safety_reports.weekly_send import EnvelopeContext, SendConfig, SendResult, _ReviewModule
from shared import box_client, error_log
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

SCRIPT_NAME = "po_materials.rfq_send"
WORKSTREAM = "po_materials"

CFG_FROM_MAILBOX = "po_materials.rfq_send.from_mailbox"
DEFAULT_FROM_MAILBOX = "procurement@evergreenmirror.com"


@dataclass(frozen=True)
class _VendorRecipientLookup:
    """Resolve the RFQ recipient from ``ITS_Vendors`` (the SoR) at send time.

    The engine passes the ``COL_JOB_ID`` cell value — for RFQs the **Vendor Key**
    (``VEN-######``, the S1 schema-twin protocol slot). Returns ``(vendor_email,
    invoice_cc)`` or ``None`` when the key is unknown or the vendor's Contact Email is
    blank → the engine HELDs (``held_no_recipient``), fail toward not-sending. The vendor
    is read LIVE each dispatch; the internal invoice-routing CC is versioned config
    (``purchaser.json``, the SAME artifact ``po_send`` uses), read at send time so a config
    change lands the next cycle without a daemon reload. Frozen + no captured state
    (matching the S5a binding contract), so ``vendors.get_vendor_by_key`` stays patchable
    and the config re-reads fresh."""

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
class _RfqEnvelope:
    """Subject + attachment name for an RFQ send. ``rfq_number`` is read from the review
    row's Notes (``rfq_poll`` seeds it via ``rfq_review.notes_for_review_row``); the
    purchaser entity is the versioned ``purchaser.json`` value, read at send time. A row
    with no parseable ``rfq_number`` returns ``None`` — an RFQ email must carry its number,
    so the engine HELDs (``held_missing_envelope``, mirroring the recipient_lookup
    None→HELD convention): an operator-visible HELD row (skipped by the SENT/HELD gate,
    never re-dispatched), NOT a numberless RFQ. The attachment name is the number-only
    ``RFQ {rfq_number}.pdf`` (the WSR-twin review row carries no vendor name; the Box file
    keeps the vendor-suffixed name)."""

    def __call__(self, ctx: EnvelopeContext) -> tuple[str, str] | None:
        rfq_number = rfq_review.row_rfq_number(ctx.row)
        if not rfq_number:
            return None
        entity = str(terms_lib.load_purchaser_config().get("entity") or "Evergreen Renewables")
        subject = f"Request for Quote {rfq_number} — {ctx.project_name} — {entity}"
        return subject, rfq_naming.rfq_pdf_filename(rfq_number, None)


@dataclass(frozen=True)
class _RfqQuoteFormAttachment:
    """The R3 sequence-attachment seam binding — the SECOND attachment: the vendor's
    fillable ``.xlsx`` quote form.

    Reads the Box file id ``rfq_poll`` seeded in the review row's Notes
    (``rfq_review.row_form_box_file_id``), downloads THAT file from Box, and returns a
    one-element ``[(filename, content_type, bytes)]`` list — the form filename is the
    number-only ``RFQ {rfq_number} - Quote Form.xlsx`` (``rfq_naming.rfq_form_filename``;
    the ``.xlsx`` extension drives the engine's content-type). Degrades to ``[]`` (the send
    goes PDF-only) when: the Notes carry no form box id (an RFQ that filed PDF-only), or the
    Box download fails transiently (WARN — the form is a convenience; the RFQ PDF is the
    essential document). Frozen + no captured state (matching the S5a/S5b binding contract),
    so ``box_client.download_file`` stays patchable."""

    def __call__(self, ctx: EnvelopeContext) -> Sequence[tuple[str, str, bytes]]:
        form_file_id = rfq_review.row_form_box_file_id(ctx.row)
        if not form_file_id:
            return []
        rfq_number = rfq_review.row_rfq_number(ctx.row) or ""
        filename = rfq_naming.rfq_form_filename(rfq_number)
        try:
            form_bytes = box_client.download_file(form_file_id)
        except box_client.BoxError as exc:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"RFQ {rfq_number or '?'}: could not download the quote form "
                f"(Box file {form_file_id}) — sending PDF-only: {exc!r}",
                error_code="rfq_send.quote_form_fetch_failed",
            )
            return []
        content_type = weekly_send._attachment_content_type(filename)
        return [(filename, content_type, form_bytes)]


CONFIG = SendConfig(
    script_name=SCRIPT_NAME,
    # The DISTINCT send-lane tag (rfq_review.WORKSTREAM_TAG = 'po_materials_rfq'), NOT the
    # parent 'po_materials' — this is what makes cross-lane dispatch impossible (module
    # docstring; ADR-0004 decision 12).
    workstream_tag=rfq_review.WORKSTREAM_TAG,
    config_workstream=WORKSTREAM,
    # cast: a module doesn't structurally match a Protocol in mypy, but rfq_review DOES
    # satisfy _ReviewModule's surface (it re-exports the WSR shared schema; locked by
    # tests/test_rfq_review.py — the same pattern as safety's wsr_review + po's po_review).
    review=cast(_ReviewModule, rfq_review),
    recipient_lookup=_VendorRecipientLookup(),
    envelope_builder=_RfqEnvelope(),
    from_mailbox_cfg_key=CFG_FROM_MAILBOX,
    from_mailbox_default=DEFAULT_FROM_MAILBOX,
    max_send_retries=weekly_send.MAX_SEND_RETRIES,
    upload_session_threshold_bytes=weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES,
    # R3 sequence-attachment seam: append the vendor's fillable xlsx quote form after the
    # primary RFQ PDF. The ONLY binding that sets this — every other SendConfig leaves it
    # None (single-attachment path, byte-identical).
    extra_attachments=_RfqQuoteFormAttachment(),
)

# #336 — the ONE ITS_Config key send_one_row resolves at RUNTIME: the from_mailbox, read
# under CONFIG.config_workstream ('po_materials'). Declared for the startup observability pass.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CONFIG.from_mailbox_cfg_key, CONFIG.config_workstream, CONFIG.from_mailbox_default, "str"),
]


def send_one_row(row_id: int) -> SendResult:
    """Send (or HELD / FAIL) one approved RFQ_Pending_Review row via the RFQ ``CONFIG``.

    Thin wrapper over ``weekly_send.send_one_row`` — the binding is the value, the
    dispatch logic is shared. The poller dispatches through this entry."""
    return weekly_send.send_one_row(row_id, CONFIG)


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved RFQ_Pending_Review row via CLI (operator debugging)."""
    # #336 startup observability (after @require_active, fail-open). Additive (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    if row_id_override is None:
        raise SystemExit("usage: python -m po_materials.rfq_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id, "status": result.status,
        "project_name": result.project_name, "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="po_materials.rfq_send",
        description="Manually send (or HELD) one approved RFQ_Pending_Review row. "
        "Production sends fire via rfq_send_poll.",
    )
    parser.add_argument("row_id", type=int, help="RFQ_Pending_Review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
