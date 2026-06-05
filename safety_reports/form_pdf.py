"""Render-parity PDF renderer for Safety Portal submissions (Phase 4 PR 3, Option B).

Purpose
-------
    Turn a structured portal submission + its form definition
    (`safety_portal/forms/<form_code>.json`) into a PDF that looks like the source
    paper form. The definition is the SINGLE source of truth shared with the TS
    display runtime (`safety_portal/src/forms/`), so the two renderers cannot drift.

Invariants
----------
    * Deterministic. NO AI, NO network, NO Smartsheet/Box/Graph — pure data → bytes.
    * Mandatory/legal text (`static_text`) is rendered verbatim from the definition,
      never user-editable (JHA "IF CONDITIONS CHANGE…"; equipment lock/tag-out).
    * N/A is rendered DISTINCT from blank (amendment 2026-06-05): a checklist item
      answered "N/A" prints "N/A"; an unanswered item prints an empty cell. Blank =
      not-yet-inspected (incomplete); N/A = deliberately-not-applicable (complete).
    * Obvious source typos are already corrected in the definitions (e.g. JHA
      "Crem"→"Crew", Skid Steer "WEARE"→"WEAR") — the renderer just prints the labels.

Failure modes
-------------
    An unknown section type or a malformed signature path is logged and skipped — the
    renderer never raises mid-document, so the rest of the PDF still renders. The
    caller (intake.py portal branch, Phase 5) is responsible for surfacing a
    render-degraded signal; `incomplete_checklist_items()` lets it flag blanks.

Consumers
---------
    Phase 5 intake renders on portal-submission arrival, then stores the PDF in Box
    ([Job]/[week of …]/). `weekly_generate` merges the per-submission PDFs Sat→Fri
    into the compiled weekly packet.
"""
from __future__ import annotations

import io
import logging
from typing import Any
from xml.sax.saxutils import escape as _esc

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# Signature-pad source coordinate space (SignaturePad.tsx viewBox 0 0 600 180).
_SIG_W, _SIG_H = 600.0, 180.0
_BRG = colors.HexColor("#3a5a40")  # Evergreen brand green
_GOLD = colors.HexColor("#b8860b")
_LINE = colors.HexColor("#cfcfcf")


# ── signatures ────────────────────────────────────────────────────────────────
def _parse_ml_path(d: str) -> list[list[tuple[float, float]]]:
    """Parse 'M x y L x y L x y M x y …' (the pad only emits M/L) into strokes."""
    strokes: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    toks = d.replace(",", " ").split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("M", "L"):
            try:
                x, y = float(toks[i + 1]), float(toks[i + 2])
            except (IndexError, ValueError):
                break
            if t == "M":
                if cur:
                    strokes.append(cur)
                cur = [(x, y)]
            else:
                cur.append((x, y))
            i += 3
        else:
            i += 1
    if cur:
        strokes.append(cur)
    return strokes


class SignatureDrawing(Flowable):
    """Draw SVG-path signature data scaled into a target box (Y flipped for PDF)."""

    def __init__(self, path_d: str, width: float = 200, height: float = 60) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self._strokes = _parse_ml_path(path_d) if path_d else []
        if path_d and not any(len(s) >= 2 for s in self._strokes):
            # A non-empty signature value that yields no drawable stroke is a
            # malformed/dropped signature — never let it vanish silently.
            logger.warning("form_pdf: signature path %r produced no drawable strokes — rendering blank",
                           path_d[:40])

    def draw(self) -> None:
        c = self.canv
        c.setStrokeColor(_LINE)
        c.setLineWidth(0.5)
        c.line(0, 0, self.width, 0)  # signature baseline
        if not self._strokes:
            return
        sx, sy = self.width / _SIG_W, self.height / _SIG_H
        c.setStrokeColor(colors.black)
        c.setLineWidth(1.2)
        for stroke in self._strokes:
            if len(stroke) < 2:
                continue
            p = c.beginPath()
            x0, y0 = stroke[0]
            p.moveTo(x0 * sx, self.height - y0 * sy)
            for x, y in stroke[1:]:
                p.lineTo(x * sx, self.height - y * sy)
            c.drawPath(p)


# ── styles ──────────────────────────────────────────────────────────────────
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s = {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=14, textColor=_BRG, spaceAfter=2),
        "title": ParagraphStyle("title", parent=base["Normal"], fontName="Helvetica-Bold",
                                 fontSize=12, spaceAfter=8),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName="Helvetica-Bold",
                                   fontSize=11, textColor=_BRG, spaceBefore=10, spaceAfter=4),
        "group": ParagraphStyle("group", parent=base["Normal"], fontName="Helvetica-Bold",
                                 fontSize=10, spaceBefore=6, spaceAfter=2),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", parent=base["Normal"], fontName="Helvetica-Bold",
                                 fontSize=8.5, leading=11),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontSize=9.5, leading=13),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12, spaceAfter=4),
        "legal": ParagraphStyle("legal", parent=base["Normal"], fontName="Helvetica-Bold",
                                 fontSize=9, leading=12, textColor=colors.HexColor("#5a4500")),
        "heading": ParagraphStyle("heading", parent=base["Normal"], fontName="Helvetica-Bold",
                                   fontSize=10, spaceBefore=6, spaceAfter=2),
    }
    return s


_ENVELOPE_KEYS = frozenset({"work_date", "job"})


def _p(text: str, st: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(str(text)), st)


# ── section flowables ─────────────────────────────────────────────────────────
def _header_section(fields: list[dict], values: dict, st: dict) -> list[Flowable]:
    rows: list[list[Any]] = []
    for f in fields:
        if f["key"] in _ENVELOPE_KEYS:
            continue
        val = values.get(f["key"], "")
        if f["input"] == "signature":
            cell: Any = SignatureDrawing(str(val)) if val else _p("", st["cell"])
        else:
            cell = _p(val, st["cell"])
        rows.append([_p(f["label"], st["cellb"]), cell])
    if not rows:
        return []
    t = Table(rows, colWidths=[2.2 * inch, 4.3 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [t]


def _table_section(section: dict, values: dict, st: dict) -> list[Flowable]:
    cols = section["columns"]
    head = [_p(c["label"], st["cellb"]) for c in cols]
    body: list[list[Any]] = [head]
    for row in values.get(section["key"], []) or []:
        cells: list[Any] = []
        for c in cols:
            v = row.get(c["key"], "")
            cells.append(SignatureDrawing(str(v), width=140, height=44)
                         if c["input"] == "signature" and v else _p(v, st["cell"]))
        body.append(cells)
    t = Table(body, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    out: list[Flowable] = []
    if section.get("title"):
        out.append(_p(section["title"], st["section"]))
    out.append(t)
    return out


def _checklist_section(section: dict, values: dict, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    if section.get("title"):
        out.append(_p(section["title"], st["section"]))
    cl = values.get(section["key"], {}) or {}
    for g in section["groups"]:
        rows: list[list[Any]] = [[_p("Item", st["cellb"]), _p("Response", st["cellb"]),
                                  _p("Comments", st["cellb"])]]
        for it in g["items"]:
            cur = cl.get(it["key"], {}) if isinstance(cl, dict) else {}
            resp = cur.get("response", "")
            # N/A prints "N/A"; blank prints an EMPTY cell — the two are distinct.
            rows.append([
                _p(it["label"], st["cell"]),
                _p(resp, st["cellb"]) if resp else _p("", st["cell"]),
                _p(cur.get("comment", ""), st["cell"]),
            ])
        t = Table(rows, colWidths=[3.7 * inch, 1.0 * inch, 1.8 * inch], repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, _LINE),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ee")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        out.append(_p(g["label"] + f"   (response: {' / '.join(g['scale'])})", st["group"]))
        out.append(t)
    return out


def _section_flowables(section: dict, values: dict, st: dict) -> list[Flowable]:
    typ = section["type"]
    if typ == "header":
        return _header_section(section["fields"], values, st)
    if typ in ("repeating_table", "signature_table"):
        return _table_section(section, values, st)
    if typ == "checklist":
        return _checklist_section(section, values, st)
    if typ == "freeform":
        return [_p(f"{section['label']}", st["heading"]),
                _p(values.get(section["key"], "") or "", st["body"])]
    if typ == "static_text":
        style = st["legal"] if section.get("emphasis") in ("legal", "footer") else st["heading"]
        return [Spacer(1, 4), _p(section["text"], style)]
    if typ == "content_blocks":
        out: list[Flowable] = []
        if section.get("title"):
            out.append(_p(section["title"], st["section"]))
        for b in section["blocks"]:
            if b.get("heading"):
                out.append(_p(b["heading"], st["heading"]))
            out.append(_p(b.get("body", ""), st["body"]))
        return out
    logger.warning("form_pdf: unknown section type %r — skipped", typ)
    return []


# ── public API ────────────────────────────────────────────────────────────────
def render_submission_pdf(definition: dict, submission: dict) -> bytes:
    """Render one submission to PDF bytes.

    submission keys: job_name (resolved by intake), work_date, values (the portal
    fill state). Deterministic; raises only on a totally malformed definition.
    """
    st = _styles()
    values = submission.get("values", {}) or {}
    flow: list[Flowable] = [
        _p("EVERGREEN RENEWABLES", st["brand"]),
        _p(definition.get("branding", {}).get("title") or definition.get("form_name", ""), st["title"]),
    ]
    meta = [("Job / Project", submission.get("job_name", "")), ("Date", submission.get("work_date", ""))]
    if definition.get("variant_label"):
        meta.insert(1, ("Type", definition["variant_label"]))
    # values escaped inside; <b>/&nbsp; are intentional markup, so build the
    # Paragraph directly rather than via _p() (which would escape the markup).
    flow.append(Paragraph("&nbsp;&nbsp;".join(f"<b>{_esc(k)}:</b> {_esc(str(v))}" for k, v in meta), st["meta"]))
    flow.append(Spacer(1, 6))

    for section in definition.get("sections", []):
        try:
            flow.extend(_section_flowables(section, values, st))
        except Exception:  # one bad section must not abort the whole document
            logger.exception("form_pdf: section render failed (type=%s) — skipped",
                             section.get("type"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=definition.get("form_name", "Safety form"),
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    doc.build(flow)
    return buf.getvalue()


def merge_pdfs(pdfs: list[bytes]) -> bytes:
    """Concatenate per-submission PDFs into one weekly packet, in the given order.

    The caller (weekly_generate, Phase 5) orders `pdfs` oldest-first — Sat→Fri
    ascending by work-date, intra-date by submission time. Deterministic; no AI /
    network. Raises ValueError on an empty list (an empty week takes the
    'no submissions this week' rollup path, never a zero-page merge).
    """
    from pypdf import (  # runtime dep; lazy so the renderer alone needn't load it
        PdfReader,
        PdfWriter,
    )

    if not pdfs:
        raise ValueError("merge_pdfs: no PDFs to merge")
    writer = PdfWriter()
    for b in pdfs:
        writer.append(PdfReader(io.BytesIO(b)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def incomplete_checklist_items(definition: dict, submission: dict) -> list[tuple[str, str, str]]:
    """Checklist items left BLANK (not-yet-inspected). N/A is a complete answer and is
    NOT returned. Phase-5 intake uses this to flag an incomplete submission — blank
    must never be silently treated as answered.
    """
    values = submission.get("values", {}) or {}
    blanks: list[tuple[str, str, str]] = []
    for section in definition.get("sections", []):
        if section.get("type") != "checklist":
            continue
        cl = values.get(section["key"], {}) or {}
        for g in section["groups"]:
            for it in g["items"]:
                resp = (cl.get(it["key"], {}) if isinstance(cl, dict) else {}).get("response", "")
                if resp == "" or resp is None:
                    blanks.append((section["key"], it["key"], it["label"]))
    return blanks
