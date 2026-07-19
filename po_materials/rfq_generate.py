"""Deterministic RFQ (Request for Quote) PDF render — the PRICE-FREE PO sibling (R2).

`render_rfq_pdf` lays out the outbound Evergreen request-for-quote in the ITS house
style (ADR-0004 Lane 2): brand masthead, DATE / RFQ NUMBER / QUOTES DUE band,
purchaser identity, the TO-vendor block, the job block, the **price-free** line grid
(# / Part / Description / Qty / Unit / Notes — a request for quote carries NO money
columns anywhere, by design), the scope/request text, and the submit-your-quote
footer block ('on the attached form or your own letterhead'). It REUSES
`safety_reports.form_pdf`'s brand primitives and `po_generate`'s block helpers
(§14 parameterize-not-clone) so every ITS document shares ONE visual system.

DETERMINISTIC + capability-gated (the generation side of Invariant 1): no AI (cloud
or local), no network, no clock reads — the caller supplies `rfq_date` + `due_date`;
`invariant=1` pins reportlab's CreationDate + document ID so identical inputs yield
identical bytes, which is what makes §47 version-on-conflict Box filing idempotent
across crash-retries. Enrolled in tests/test_capability_gating.py GATED_SCRIPTS.

Escaping (red-team #11): every untrusted/operator string rendered here flows through
`form_pdf._p` / `form_pdf._rich_body`, whose `xml.sax.saxutils.escape` neutralises
reportlab paragraph markup — a hostile ``<b>``/``<font>``/broken-tag description
renders as literal text, never as markup (and never crashes the paraparser). The
RED test renders deliberately-malformed markup and asserts the render survives.

Vendor snapshot (the #494 posture, inherited from po_generate): the vendor identity
embedded in the PDF is the ITS_Vendors SoR row `rfq_poll` resolves at render time
via `vendors.get_vendor_by_key` — never the D1 cache, never a client-supplied field.
The purchaser identity comes from the SAME versioned config artifacts po_generate
uses (`terms.load_purchaser_config`, read-only reuse — D5, never hard-coded here).
"""
from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from reportlab.lib.pagesizes import letter  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Flowable,
    KeepTogether,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from po_materials import rfq_naming
from po_materials import vendors as vendors_mod
from po_materials.po_generate import _address_lines, _kv_block, _qty_display
from safety_reports.form_pdf import (
    _CONTENT_W,
    _GOLD,
    _LINE,
    _MARGIN,
    _TINT,
    _brand_header,
    _canvas_maker,
    _p,
    _rich_body,
    _section_header,
    _styles,
)

# The fixed vendor-facing instruction block (rendered verbatim below the scope; the
# RFQ number is interpolated through the escaping path). The 'attached form' is the
# per-vendor fillable .xlsx quote form (R3 attaches it beside this PDF at send time).
_SUBMIT_FOOTER = (
    "Please submit your quote on the attached quote form or on your own letterhead, "
    "referencing RFQ {rfq_number} and quoting the due date above. Itemize pricing "
    "per line where applicable and note lead times, freight, and any substitutions. "
    "This request for quote is NOT a purchase order and is not a commitment to "
    "purchase; any resulting order will be placed by written purchase order."
)


def _rfq_line_items_table(lines: Sequence[Mapping[str, Any]], st: dict) -> Table:
    """The PRICE-FREE line grid: # / Part # / Description / Qty / Unit / Notes.

    Deliberately NO money columns (no unit cost, no extended, no totals block) —
    the vendor supplies pricing on the quote form; a money value has no legitimate
    path into this document. Every cell renders through `_p` (escaped)."""
    head = ["#", "Part # / SKU", "Description", "Qty", "Unit", "Notes"]
    widths = [0.05, 0.16, 0.39, 0.09, 0.09, 0.22]
    data: list[list[Any]] = [[_p(h, st["colhead"]) for h in head]]
    for line in lines:
        data.append([
            _p(str(line.get("position") or ""), st["cell"]),
            _p(str(line.get("part_number") or ""), st["cell"]),
            _p(str(line.get("description") or ""), st["cell"]),
            _p(_qty_display(line.get("qty")), st["cell"]),
            _p(str(line.get("unit") or ""), st["cell"]),
            _p(str(line.get("line_note") or ""), st["cell"]),
        ])
    t = Table(data, colWidths=[w * _CONTENT_W for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _GOLD),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return t


def render_rfq_pdf(
    rfq: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    vendor: Mapping[str, Any],
    purchaser: Mapping[str, Any],
    *,
    rfq_date: date,
    due_date: date | None,
) -> bytes:
    """Render one price-free RFQ → PDF bytes. DETERMINISTIC for fixed inputs.

    Args:
        rfq: the HMAC-VERIFIED /pending row (rfq_number, job_no/job_id/job_name,
            scope_text — this function renders, it does not re-police; the verify
            already happened in rfq_poll pass ①).
        lines: the row's `line_items`, position-ordered, PRICE-FREE.
        vendor: the ITS_Vendors SoR row from `vendors.get_vendor_by_key` — the
            render-time snapshot addressed by this per-vendor copy (#494 posture).
        purchaser: `terms.load_purchaser_config()` — entity, address_lines, phone
            (D5 versioned config; NEVER hard-coded here).
        rfq_date: the DATE printed on the RFQ (the caller's filing date — passed in
            so the render itself stays clock-free/deterministic).
        due_date: the quotes-due date, or None (nullable by the Worker contract —
            "quote at your earliest convenience"); printed in the meta band and
            quoted in the footer when present.

    `invariant=1` pins reportlab's CreationDate + document ID so identical inputs
    yield identical bytes (§47 idempotent crash-retry filing).
    """
    st = _styles()
    entity = str(purchaser.get("entity") or "")
    rfq_number = str(rfq.get("rfq_number") or "")
    vendor_name = str(vendor.get(vendors_mod.COL_VENDOR_NAME) or "")
    flow: list[Flowable] = _brand_header("REQUEST FOR QUOTE", st)

    # Purchaser identity under the masthead (D5 versioned config).
    for line in [entity, *purchaser.get("address_lines", []),
                 f"PH {purchaser.get('phone')}" if purchaser.get("phone") else ""]:
        if line:
            flow.append(_p(line, st["meta"]))
    flow.append(Spacer(1, 6))

    # DATE / RFQ NUMBER / QUOTES DUE band (the PO meta band + the due-date slot).
    due_display = due_date.strftime("%-m/%-d/%Y") if due_date is not None else "—"
    meta = Table(
        [[_p("DATE", st["colhead"]), _p(rfq_date.strftime("%-m/%-d/%Y"), st["cellb"]),
          _p("RFQ NUMBER", st["colhead"]), _p(rfq_number, st["cellb"]),
          _p("QUOTES DUE", st["colhead"]), _p(due_display, st["cellb"])]],
        colWidths=[_CONTENT_W * 0.10, _CONTENT_W * 0.16, _CONTENT_W * 0.16,
                   _CONTENT_W * 0.26, _CONTENT_W * 0.14, _CONTENT_W * 0.18],
    )
    meta.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.8, _GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    flow.append(meta)
    flow.append(Spacer(1, 8))

    # TO (the addressed vendor — render-time SoR snapshot) + PROJECT side-by-side.
    to_rows = [
        vendor_name,
        *_address_lines(str(vendor.get(vendors_mod.COL_ADDRESS) or "")),
    ]
    contact_bits = [
        str(vendor.get(col) or "").strip()
        for col in (vendors_mod.COL_CONTACT_NAME, vendors_mod.COL_CONTACT_PHONE,
                    vendors_mod.COL_CONTACT_EMAIL)
    ]
    contact_line = " · ".join(bit for bit in contact_bits if bit)
    if contact_line:
        to_rows.append(contact_line)
    project_rows = [str(v) for v in (
        rfq.get("job_name"),
        f"Job # {rfq.get('job_no')}" if rfq.get("job_no") else "",
    ) if v]
    pair = Table(
        [[_kv_block("TO — VENDOR", [r for r in to_rows if r], st),
          _kv_block("PROJECT", project_rows, st)]],
        colWidths=[_CONTENT_W / 2, _CONTENT_W / 2],
    )
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    flow.append(pair)
    flow.append(Spacer(1, 8))

    # The price-free line grid.
    flow.append(_rfq_line_items_table(lines, st))
    flow.append(Spacer(1, 6))

    # Scope / request details (escaped rich body — operator free text).
    if str(rfq.get("scope_text") or "").strip():
        flow.append(_section_header("SCOPE / REQUEST DETAILS", st))
        flow.extend(_rich_body(str(rfq.get("scope_text")), st))

    # The submit-your-quote footer block (fixed language; number escaped via _p).
    flow.append(_section_header("HOW TO SUBMIT YOUR QUOTE", st))
    flow.append(Spacer(1, 2))
    footer = KeepTogether([
        _p(_SUBMIT_FOOTER.format(rfq_number=rfq_number), st["legal"]),
        Spacer(1, 4),
        _p(
            (
                f"Quotes are due by {due_display} to {entity}."
                if due_date is not None
                else f"Please return your quote to {entity} at your earliest convenience."
            )
            + (f" Direct questions to {purchaser.get('phone')}." if purchaser.get("phone") else ""),
            st["legal"],
        ),
    ])
    flow.append(footer)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        title=rfq_naming.rfq_pdf_title(rfq_number, vendor_name),
        leftMargin=_MARGIN, rightMargin=_MARGIN,
        topMargin=_MARGIN, bottomMargin=0.7 * 72,
        # Byte-determinism: pins CreationDate + the document /ID (see docstring).
        invariant=1,
    )
    doc.build(flow, canvasmaker=_canvas_maker(f"Request for Quote {rfq_number}"))
    return buf.getvalue()
