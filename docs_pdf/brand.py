"""Evergreen brand primitives for the docs → PDF pipeline (WS3 / D2-1).

Palette, logo, brand header, and the per-page footer canvas that stamps
``title · version · git-SHA · page N of M`` on every manual page.

§14 preservation-over-refactor note
-----------------------------------
    The palette below is a DELIBERATE DUPLICATION of the brand constants in
    ``safety_reports/form_pdf.py`` (the live, load-bearing safety-PDF renderer). It is
    NOT imported from there and ``form_pdf.py`` is NOT refactored to share it: that
    renderer files the legal document of record on the live intake path, and a shared
    brand module would couple the docs pipeline's churn to it for no real reuse benefit
    (two consumers, not the ≥4 the refactor bar wants). The two modules intentionally
    carry the SAME hex values so every ITS artifact — safety packet + manual — reads as
    one visual system; if the brand ever changes, update BOTH sites (grep the hexes).

Purity
------
    Pure functions + a Canvas subclass. The ONLY I/O is reading the committed logo PNG
    (shared with ``form_pdf.py``); a missing/unreadable asset degrades to a styled text
    wordmark so a PDF still renders. NO network, NO subprocess — the caller resolves the
    git SHA and passes it in (keeps this module deterministic and unit-testable).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as _pdfcanvas
from reportlab.platypus import Flowable, Image, Paragraph, Spacer, Table, TableStyle

# ── brand palette — DUPLICATED from safety_reports/form_pdf.py (see module docstring) ──
EVERGREEN = colors.HexColor("#1f4d2e")       # deep evergreen — headlines / H1 / rules
EVERGREEN_SOFT = colors.HexColor("#3a5a40")  # softer green — sub-headings / labels
GOLD = colors.HexColor("#b8860b")            # divider rule + accents (operator-requested)
INK = colors.HexColor("#1f2421")             # near-black body text (softer than pure black)
LINE = colors.HexColor("#d7d9d6")            # light grid / hairline rules
TINT = colors.HexColor("#e8f0e9")            # table header fill (pale green)
ZEBRA = colors.HexColor("#f5f8f5")           # alternating table-row fill
FOOT = colors.HexColor("#7a7f7b")            # footer text (muted grey-green)
CALLOUT_BG = colors.HexColor("#fbf6e9")      # gold-edged callout box fill (blockquotes)
CALLOUT_INK = colors.HexColor("#5a4500")     # deep-amber callout text (matches form_pdf legal)
CODE_BG = colors.HexColor("#f2f4f2")         # code-block / inline-code fill
WHITE = colors.white                         # zebra-row base

BRAND_NAME = "EVERGREEN RENEWABLES"

# Page geometry — Letter minus 0.75in side margins. Section rules + tables span exactly
# CONTENT_W so everything lines up (mirrors form_pdf's MARGIN/CONTENT_W discipline).
MARGIN = 0.75 * inch
CONTENT_W = letter[0] - 2 * MARGIN

# The committed brand logo is OWNED by safety_reports/assets (built once from the SVG via
# scripts/rasterize_logo.py). We reference the same file rather than committing a second
# copy — one asset, one visual system. REPO_ROOT is docs_pdf/..; the path resolves under
# the editable (source-in-place) install this repo always uses.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGO_PATH = _REPO_ROOT / "safety_reports" / "assets" / "evergreen-logo.png"
_LOGO_CACHE: list[Any] = []  # [ImageReader|None] memo; [] = not yet probed


def _logo_reader() -> Any | None:
    """Memoized ImageReader for the brand logo, or None if the asset is unusable."""
    if not _LOGO_CACHE:
        try:
            _LOGO_CACHE.append(ImageReader(str(_LOGO_PATH)) if _LOGO_PATH.is_file() else None)
        except Exception:  # noqa: BLE001 — a bad asset must degrade to text, never raise
            _LOGO_CACHE.append(None)
    return _LOGO_CACHE[0]


def _logo_flowable(max_w: float = 2.3 * inch, max_h: float = 0.52 * inch) -> Flowable | None:
    """Brand logo scaled to fit (max_w, max_h), aspect preserved, LEFT-aligned as a
    letterhead. None when the asset is unusable (caller falls back to the text wordmark)."""
    reader = _logo_reader()
    if reader is None:
        return None
    try:
        iw, ih = reader.getSize()
        if not iw or not ih:
            return None
        scale = min(max_w / iw, max_h / ih)
        img = Image(str(_LOGO_PATH), width=iw * scale, height=ih * scale, lazy=0)
        img.hAlign = "LEFT"
        return img
    except Exception:  # noqa: BLE001 — never let a logo glitch abort a document
        return None


def brand_styles() -> dict[str, ParagraphStyle]:
    """The document title / subtitle paragraph styles used by the masthead."""
    base = getSampleStyleSheet()["Normal"]
    return {
        "brand": ParagraphStyle("brand", parent=base, fontName="Helvetica-Bold",
                                fontSize=15, textColor=EVERGREEN, leading=17, spaceAfter=1),
        "doc_title": ParagraphStyle("doc_title", parent=base, fontName="Helvetica-Bold",
                                    fontSize=20, textColor=EVERGREEN, leading=24,
                                    spaceBefore=2, spaceAfter=2),
        "doc_subtitle": ParagraphStyle("doc_subtitle", parent=base, fontSize=10.5,
                                       textColor=FOOT, leading=14, spaceAfter=2),
    }


def brand_header(title: str, *, subtitle: str = "") -> list[Flowable]:
    """The top-of-document brand band: logo (or wordmark fallback) + document title +
    optional subtitle, closed by a thick gold rule spanning the content width. Shared by
    every rendered manual so they all open identically (mirrors form_pdf._brand_header)."""
    st = brand_styles()
    logo = _logo_flowable()
    head: list[Flowable] = [logo] if logo is not None else [Paragraph(BRAND_NAME, st["brand"])]
    head.append(Spacer(1, 6))
    if title:
        head.append(Paragraph(title, st["doc_title"]))
    if subtitle:
        head.append(Paragraph(subtitle, st["doc_subtitle"]))
    rule = Table([[""]], colWidths=[CONTENT_W], rowHeights=[0.5])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 2.0, GOLD),
                              ("TOPPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    head.append(Spacer(1, 5))
    head.append(rule)
    head.append(Spacer(1, 10))
    return head


# ── footer (brand · title · version · git-SHA · Page X of Y) ────────────────────────────
class FooterCanvas(_pdfcanvas.Canvas):
    """Canvas that stamps a footer on every page: the brand wordmark (LEFT — keeps the
    company name in the text layer of every page), a centred ``title · version · sha``
    provenance line, and ``Page X of Y`` (RIGHT). Two-pass save() defers drawing until
    the total page count is known.
    """

    def __init__(self, *args: Any, footer_title: str = "", version: str = "",
                 git_sha: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []
        self._footer_title = footer_title
        self._version = version
        self._git_sha = git_sha

    def showPage(self) -> None:  # noqa: N802 — overrides reportlab Canvas.showPage (camelCase API)
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for i, state in enumerate(self._saved, start=1):
            self.__dict__.update(state)
            self._draw_footer(i, total)
            super().showPage()
        super().save()

    def _provenance(self) -> str:
        """The centred ``title · version · git-SHA`` line — omits empty parts cleanly."""
        parts = [p for p in (self._footer_title, self._version,
                             (self._git_sha and f"rev {self._git_sha}")) if p]
        line = " · ".join(parts)
        return line if len(line) <= 96 else line[:93] + "…"

    def _draw_footer(self, page: int, total: int) -> None:
        w = self._pagesize[0]
        y = 0.42 * inch
        self.saveState()
        self.setStrokeColor(LINE)
        self.setLineWidth(0.5)
        self.line(MARGIN, y + 9, w - MARGIN, y + 9)
        self.setFillColor(FOOT)
        self.setFont("Helvetica-Bold", 7)
        self.drawString(MARGIN, y, BRAND_NAME)
        prov = self._provenance()
        if prov:
            self.setFont("Helvetica", 7)
            self.drawCentredString(w / 2.0, y, prov)
        self.setFont("Helvetica", 7)
        self.drawRightString(w - MARGIN, y, f"Page {page} of {total}")
        self.restoreState()


def canvas_maker(title: str, *, version: str = "", git_sha: str = "") -> Any:
    """A canvasmaker bound to the footer provenance, for SimpleDocTemplate.build(canvasmaker=)."""
    def make(*args: Any, **kwargs: Any) -> FooterCanvas:
        return FooterCanvas(*args, footer_title=title, version=version,
                            git_sha=git_sha, **kwargs)
    return make
