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

    PR-L (manual fallback): `render_blank_fillable(definition)` is the BLANK, fillable-
    AcroForm sibling of `render_submission_pdf` — same layout/branding/footer, empty
    fields where the submission renderer draws values. `render_cover_sheet()` renders
    the one manual-fallback instructions cover page. Both are pure bytes (no Box / send);
    `scripts/generate_form_archive.py` is the only thing that uploads them to Box.
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
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
            # malformed/dropped signature — never let it vanish silently. Log the
            # LENGTH only, never the path content: a signature is PII (and logging
            # it trips CodeQL clear-text-logging), so the debug signal is the size.
            logger.warning("form_pdf: signature value (len=%d) produced no drawable strokes — rendering blank",
                           len(path_d))

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


# ── form-definition loader ──────────────────────────────────────────────────────
# The Phase-4 definitions are the single source of truth shared with the TS display
# runtime; they live as JSON under safety_portal/forms/<form_code>.json. The TS side
# loads them via a Vite glob (src/forms/registry.ts); this is the Python side's loader
# for the Phase-5 intake portal branch (`form_code` arrives in the HMAC-verified payload).
_FORMS_DIR = Path(__file__).resolve().parent.parent / "safety_portal" / "forms"
# Form codes are lowercase-kebab + version (e.g. "jha-v1", "toolbox-talk-ppe-v1").
# The strict charset is also the path-traversal guard: `form_code` originates in the
# portal payload, so no "/" / "." / ".." can reach the filesystem path.
_FORM_CODE_RE = re.compile(r"[a-z0-9-]+")


def load_definition(form_code: str) -> dict | None:
    """Load the Phase-4 form definition for `form_code`, or None if unresolvable.

    Returns None (never raises, never returns a partial) on: an unsafe/empty
    form_code, a missing file, malformed JSON, or a non-object top level. The
    caller (intake portal branch) treats None as "unknown/invalid form → flag to
    the Review Queue, do NOT render" — a blank form must never be filed silently.
    """
    if not form_code or not _FORM_CODE_RE.fullmatch(form_code):
        return None
    path = _FORMS_DIR / f"{form_code}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


# ════════════════════════════════════════════════════════════════════════════════
# Blank fillable-form renderer (PR-L — manual fallback archive)
# ════════════════════════════════════════════════════════════════════════════════
# WHY a SIBLING, not a flag through the submission helpers: the submission renderer
# draws *values*; the blank renderer draws *empty AcroForm fields* at the same layout
# positions. Those are different per-cell operations, so a single helper would branch
# on `blank` in every cell — more churn, more risk to the live filing path. Instead we
# add blank-mode sibling section-builders that reuse the EXACT same _styles(), branding
# constants, page geometry, section order, and footer. The two text-only sections that
# MUST stay byte-identical between digital and manual — `static_text` (legal/mandatory
# wording) and `content_blocks` (the toolbox-talk body) — are rendered by delegating to
# the *existing* `_section_flowables` (with empty values), so they CANNOT diverge from
# the submission renderer. Preservation-over-refactor (Op Stds §14): render_submission_pdf
# and its helpers are untouched.
#
# WHY custom Flowables for fields: reportlab emits AcroForm widgets only via
# `canvas.acroForm.{textfield,checkbox,choice}`, which need an absolute canvas position.
# A platypus Flowable gets its canvas + origin at draw() time, so wrapping each field in
# a tiny Flowable lets the normal frame/table layout place it. Field NAMES must be unique
# per document (a repeated name makes one shared field), so a per-document counter
# (_FieldNamer) guarantees uniqueness.

# AcroForm field geometry. Kept modest so fields sit inside table cells / header rows.
_FIELD_H = 14.0  # single-line text/choice field height (pt)
_CHECK_SZ = 10.0  # checkbox side (pt)
# Leading dropdown option / initial value (see _ChoiceFieldFlowable for why ASCII + non-falsy).
_SELECT_PROMPT = "(select)"


class _FieldNamer:
    """Per-document monotonic field-name source — AcroForm names must be unique."""

    def __init__(self) -> None:
        self._n = 0

    def next(self, base: str) -> str:
        self._n += 1
        # Sanitize to a safe AcroForm name; the counter guarantees uniqueness even
        # when two cells share a base (e.g. repeated "Other:" rows).
        safe = re.sub(r"[^A-Za-z0-9_]", "_", base)[:40]
        return f"f{self._n}_{safe}"


class _TextFieldFlowable(Flowable):
    """An empty AcroForm text field laid out as a flowable (single- or multi-line)."""

    def __init__(self, namer: _FieldNamer, base: str, width: float,
                 *, multiline: bool = False, height: float | None = None) -> None:
        super().__init__()
        self._namer = namer
        self._base = base
        self.width = width
        # A multiline field is given a taller box so handwriting / typed notes fit.
        self.height = height if height is not None else (_FIELD_H * 2.4 if multiline else _FIELD_H)
        self._multiline = multiline

    def draw(self) -> None:
        af = self.canv.acroForm
        af.textfield(
            name=self._namer.next(self._base),
            x=0, y=0, width=self.width, height=self.height,
            borderWidth=0.5, borderColor=_LINE, fillColor=None,
            fontName="Helvetica", fontSize=8.5,
            fieldFlags="multiline" if self._multiline else "",
        )


class _ChoiceFieldFlowable(Flowable):
    """An empty AcroForm dropdown (choice) field carrying the definition's options."""

    def __init__(self, namer: _FieldNamer, base: str, width: float, options: list[str]) -> None:
        super().__init__()
        self._namer = namer
        self._base = base
        self.width = width
        self.height = _FIELD_H
        # An ASCII "(select)" leading option so the dropdown starts on a clearly-empty
        # prompt rather than pre-selecting a real value. reportlab's acroform.choice
        # raises UnboundLocalError on a FALSY initial `value` (a library bug), so the
        # initial value is this non-falsy ASCII sentinel — NOT a non-ASCII placeholder
        # (an em-dash trips reportlab's Helvetica encoding with KeyError).
        self._options = [_SELECT_PROMPT] + [str(o) for o in options]

    def draw(self) -> None:
        af = self.canv.acroForm
        af.choice(
            name=self._namer.next(self._base),
            x=0, y=0, width=self.width, height=self.height,
            options=self._options, value=_SELECT_PROMPT,
            borderWidth=0.5, borderColor=_LINE, fillColor=None,
            fontName="Helvetica", fontSize=8.5,
        )


class _CheckboxFieldFlowable(Flowable):
    """A row of one or more labelled AcroForm checkboxes (e.g. a rating scale)."""

    def __init__(self, namer: _FieldNamer, base: str, labels: list[str]) -> None:
        super().__init__()
        self._namer = namer
        self._base = base
        self._labels = [str(label) for label in labels]
        self._gap = 6.0
        self._label_pad = 3.0
        self._font = "Helvetica"
        self._fs = 8.0
        from reportlab.pdfbase.pdfmetrics import stringWidth
        self._w = stringWidth
        self.height = max(_CHECK_SZ, self._fs + 2)
        self.width = sum(
            _CHECK_SZ + self._label_pad + self._w(label, self._font, self._fs) + self._gap
            for label in self._labels
        )

    def draw(self) -> None:
        af = self.canv.acroForm
        c = self.canv
        x = 0.0
        y_box = (self.height - _CHECK_SZ) / 2
        for label in self._labels:
            af.checkbox(
                name=self._namer.next(f"{self._base}_{label}"),
                x=x, y=y_box, size=_CHECK_SZ,
                borderWidth=0.5, borderColor=_LINE, fillColor=None, checked=False,
            )
            x += _CHECK_SZ + self._label_pad
            c.setFont(self._font, self._fs)
            c.setFillColor(colors.black)
            c.drawString(x, (self.height - self._fs) / 2 + 1, label)
            x += self._w(label, self._font, self._fs) + self._gap


def _blank_field_cell(field: dict, namer: _FieldNamer, width: float) -> Flowable:
    """Map one Field (header col / table col) onto its fillable widget by `input`.

    text→text · textarea→multiline · date→labelled text · time→labelled text(HH:MM) ·
    number→text · select→dropdown(choice w/ options) · signature→sign-by-hand LINE.
    The label hints (e.g. "(MM/DD/YYYY)") are placed BEFORE the field so the printed
    or on-screen form tells the filler the expected format.
    """
    inp = field.get("input", "text")
    base = field.get("key", "field")
    if inp == "signature":
        # No typed signature on a manual form — a sign-by-hand baseline (matches the
        # submission renderer's signature baseline) rather than an AcroForm field.
        return SignatureDrawing("", width=min(width, 200), height=28)
    if inp == "select":
        return _ChoiceFieldFlowable(namer, base, width, field.get("options", []) or [])
    if inp == "textarea":
        return _TextFieldFlowable(namer, base, width, multiline=True)
    # text / date / time / number all become single-line text fields. date/time carry
    # a format hint in the label rendered by the caller; the field itself is free text.
    return _TextFieldFlowable(namer, base, width)


def _format_hint(inp: str) -> str:
    """A short, human format hint appended to date/time/number field labels."""
    return {"date": "  (MM/DD/YYYY)", "time": "  (HH:MM)", "number": "  (#)"}.get(inp, "")


def _blank_header_section(fields: list[dict], st: dict, namer: _FieldNamer) -> list[Flowable]:
    """Header Field[] as fillable meta rows. Manual fallback = NO system-resolved
    job/PM/date, so the envelope keys (work_date/job) ARE shown as fillable here
    (unlike the submission header, which hides them because intake fills them)."""
    rows: list[list[Any]] = []
    for f in fields:
        label = f.get("label", "") + _format_hint(f.get("input", "text"))
        rows.append([_p(label, st["cellb"]), _blank_field_cell(f, namer, 4.0 * inch)])
    if not rows:
        return []
    t = Table(rows, colWidths=[2.2 * inch, 4.3 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [t]


def _blank_table_section(section: dict, st: dict, namer: _FieldNamer) -> list[Flowable]:
    """repeating_table / signature_table → header row + EXACTLY min_rows blank rows of
    fillable fields. NO add/delete affordance (a static paper form). Per-column width
    is the available width split evenly across the columns."""
    cols = section["columns"]
    n_rows = _min_rows(section)
    avail = 7.0 * inch  # letter minus 0.6in margins ≈ 7.3in; leave slack for borders
    col_w = avail / max(len(cols), 1)
    head = [_p(c["label"] + _format_hint(c.get("input", "text")), st["cellb"]) for c in cols]
    body: list[list[Any]] = [head]
    for _ in range(n_rows):
        body.append([_blank_field_cell(c, namer, col_w - 8) for c in cols])
    t = Table(body, colWidths=[col_w] * len(cols), repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ee")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    out: list[Flowable] = []
    if section.get("title"):
        out.append(_p(section["title"], st["section"]))
    out.append(t)
    return out


def _blank_checklist_item_response(it: dict, group: dict, namer: _FieldNamer,
                                   st: dict) -> Flowable:
    """The fillable response widget for one checklist Item, by Item.kind.

    rated (default) → one checkbox per group/item scale value (N/A is an explicit box,
        DISTINCT from blank — matches the submission renderer's N/A-vs-blank rule);
    circle_one → checkbox set over the item's own options/scale (print-and-circle);
    numeric → a number text field;
    text → a text field.
    """
    kind = it.get("kind", "rated")
    base = it.get("key", "item")
    if kind == "numeric":
        return _TextFieldFlowable(namer, base, 0.9 * inch)
    if kind == "text":
        return _TextFieldFlowable(namer, base, 1.7 * inch)
    if kind == "circle_one":
        opts = it.get("options") or it.get("scale") or group.get("scale", [])
        return _CheckboxFieldFlowable(namer, base, opts)
    # rated (default): the group's scale (item may override its own scale).
    scale = it.get("scale") or group.get("scale", [])
    return _CheckboxFieldFlowable(namer, base, scale)


def _blank_checklist_section(section: dict, st: dict, namer: _FieldNamer) -> list[Flowable]:
    """checklist → render EVERY group/item as a fixed list (not a row table), each item
    with its kind-appropriate fillable response + (when comment/comment_per_item) a
    comment text field. Matches the submission checklist's per-group table shape."""
    out: list[Flowable] = []
    if section.get("title"):
        out.append(_p(section["title"], st["section"]))
    for g in section["groups"]:
        want_comment = bool(g.get("comment_per_item")) or any(
            it.get("comment") for it in g["items"]
        )
        head = [_p("Item", st["cellb"]), _p("Response", st["cellb"])]
        col_w = [3.6 * inch, 1.6 * inch] if not want_comment else [3.0 * inch, 1.6 * inch, 1.9 * inch]
        if want_comment:
            head.append(_p("Comments", st["cellb"]))
        rows: list[list[Any]] = [head]
        for it in g["items"]:
            # Per-item override: an explicit "comment": false suppresses the comment
            # field for that one item even in a comment_per_item group.
            item_comment = want_comment and it.get("comment", True)
            row: list[Any] = [
                _p(it["label"], st["cell"]),
                _blank_checklist_item_response(it, g, namer, st),
            ]
            if want_comment:
                row.append(
                    _TextFieldFlowable(namer, f"{it.get('key', 'item')}_comment", 1.8 * inch)
                    if item_comment else _p("", st["cell"])
                )
            rows.append(row)
        t = Table(rows, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, _LINE),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ee")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        out.append(_p(g["label"] + f"   (response: {' / '.join(g['scale'])})", st["group"]))
        out.append(t)
    return out


def _min_rows(section: dict) -> int:
    """The number of blank rows to emit for a static row table.

    Reads `min_rows` from the definition (the DEFINITION wins). Falls back to the SPA
    repeating-table component's default of 1 visible row when a table omits `min_rows`,
    rather than guessing a number. No add/delete affordance is rendered either way.
    """
    mr = section.get("min_rows")
    return int(mr) if isinstance(mr, int) and mr > 0 else 1


def _blank_section_flowables(section: dict, st: dict, namer: _FieldNamer) -> list[Flowable]:
    """Blank-mode dispatcher. The two text-only sections (`static_text`,
    `content_blocks`) delegate to the EXISTING submission `_section_flowables` (empty
    values) so legal/mandatory wording and the toolbox-talk body are byte-identical to
    the digital form — they cannot diverge."""
    typ = section["type"]
    if typ == "header":
        return _blank_header_section(section["fields"], st, namer)
    if typ in ("repeating_table", "signature_table"):
        return _blank_table_section(section, st, namer)
    if typ == "checklist":
        return _blank_checklist_section(section, st, namer)
    if typ == "freeform":
        multiline = section.get("input", "textarea") == "textarea"
        return [_p(section["label"], st["heading"]),
                _TextFieldFlowable(namer, section.get("key", "freeform"), 6.8 * inch,
                                   multiline=multiline, height=_FIELD_H * 3 if multiline else None)]
    if typ in ("static_text", "content_blocks"):
        # VERBATIM via the submission path — guarantees no divergence.
        return _section_flowables(section, {}, st)
    logger.warning("form_pdf: unknown section type %r — skipped (blank)", typ)
    return []


def render_blank_fillable(definition: dict) -> bytes:
    """Render a BLANK, fillable-AcroForm PDF of `definition` to bytes (PR-L).

    The manual-fallback sibling of `render_submission_pdf`: same platypus layout,
    section order, branding, footer, and page geometry, but every place the submission
    renderer draws a value, this emits an empty AcroForm field (text / checkbox /
    choice) at the layout position. `static_text` and `content_blocks` render through
    the SAME code path as the submission renderer (verbatim — no divergence).

    Deterministic; NO AI, NO network, NO Smartsheet/Box/Graph — pure data → bytes,
    same as `render_submission_pdf`. The Box upload of these blanks lives only in
    `scripts/generate_form_archive.py`, NOT here, so this module stays send-free and
    outside the network-capability gate (tests/test_capability_gating.py).
    """
    st = _styles()
    namer = _FieldNamer()
    title = definition.get("branding", {}).get("title") or definition.get("form_name", "")
    flow: list[Flowable] = [
        _p("EVERGREEN RENEWABLES", st["brand"]),
        _p(title, st["title"]),
    ]
    if definition.get("variant_label"):
        flow.append(_p(f"Type: {definition['variant_label']}", st["meta"]))
    # Manual-fallback hint line: this is the BLANK form, fill by hand or on screen.
    flow.append(_p("BLANK FORM — manual fallback. Fill on screen or print and complete by hand.",
                   st["legal"]))
    flow.append(Spacer(1, 6))

    for section in definition.get("sections", []):
        try:
            flow.extend(_blank_section_flowables(section, st, namer))
        except Exception:  # one bad section must not abort the whole document
            logger.exception("form_pdf: blank section render failed (type=%s) — skipped",
                             section.get("type"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            title=f"{definition.get('form_name', 'Safety form')} (fillable)",
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    doc.build(flow)
    return buf.getvalue()


# The manual-fallback instructions, one numbered step per line. Kept as data so the
# wording is reviewable in one place; the steps describe the SEND-FREE manual path
# (email the completed PDF to the job's Safety-Reports contact) — ITS does not send it.
_COVER_STEPS: list[str] = [
    "1. The portal is the normal way to file a safety report. Use this archive only "
    "when the portal is unavailable.",
    "2. Open the blank form you need (in this same Box folder). Each form name ends "
    "with “(fillable).pdf”.",
    "3. Fill it in — either on screen in a PDF reader, or print it and complete it "
    "by hand. Always include the job name, the date, and your name.",
    "4. Email the completed PDF to the job’s Safety-Reports contact and CC the "
    "office. Look the contact up in ITS_Active_Jobs (it stays up even when the "
    "portal is down).",
    "5. The emailed PDF is the official record — there is NO need to re-enter it "
    "into the portal once the portal is back.",
    "6. If a paper form runs out of rows, continue on an additional copy of the same "
    "form and staple them together.",
]


def render_cover_sheet() -> bytes:
    """Render the ONE manual-fallback cover/instructions sheet (PR-L) to bytes.

    Lives once in the archive's 00_Form_Archive folder (not per-form). Plain steps for
    a field PM when the portal is unavailable. Pure bytes — NO AI, NO network, NO send.
    """
    st = _styles()
    flow: list[Flowable] = [
        _p("EVERGREEN RENEWABLES", st["brand"]),
        _p("Safety Forms — Manual Fallback (portal unavailable)", st["title"]),
        _p("Use these blank fillable forms only when the Safety Portal is down. "
           "Normal filing is through the portal.", st["body"]),
        Spacer(1, 8),
        _p("What to do", st["section"]),
    ]
    for step in _COVER_STEPS:
        flow.append(_p(step, st["body"]))
    flow.append(Spacer(1, 10))
    flow.append(_p("The manual record stands. No re-entry when the portal returns.", st["legal"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="Safety Forms — Manual Fallback",
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch)
    doc.build(flow)
    return buf.getvalue()
