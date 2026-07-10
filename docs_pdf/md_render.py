"""markdown-it-py TOKEN STREAM → reportlab Platypus flowables (WS3 / D2-1).

Design
------
    We parse markdown to markdown-it-py's flat token stream and translate it directly to
    reportlab flowables — NOT to HTML. reportlab's ``Paragraph`` speaks a tiny HTML-ish
    mini-markup (``<b>``/``<i>``/``<font>``/``<a>``/``<br/>``), so inline formatting maps
    cleanly; block structure (headings, lists, tables, blockquotes, code, rules) becomes
    distinct flowables styled from the Evergreen palette in ``brand.py``.

Supported constructs
    * headings h1–h4 (h5/h6 fold to the h4 style — the enablement guides never go deeper)
    * paragraphs with inline em / strong / inline-code / links / strikethrough / breaks
    * bullet + ordered lists, nested (indent scales with depth; ordered items renumber)
    * GFM pipe tables → a branded grid (pale-green header, gold under-rule, zebra rows)
    * blockquote → a gold-edged callout box (recursively renders its inner blocks)
    * fenced + indented code blocks → a monospace tinted box
    * thematic breaks (``---``) → a hairline rule

Pre-processing (before tokenizing)
    * YAML frontmatter (a leading ``---\\n … \\n---`` block) is STRIPPED — it is doc
      metadata, not body content, and must never print onto a manual page.
    * HTML comments (``<!-- … -->``) are STRIPPED — the enablement guides carry
      operator TODO comments (e.g. the §6a manifest-registration reminders) that are
      maintainer notes, not reader content.

Adversarial-input posture (Invariant 2, defence-in-depth)
    The enablement guides are operator-authored in-repo, not untrusted external input, so
    this is not a trust boundary in the Invariant-2 sense. Even so, EVERY text run is
    reportlab-escaped (``xml.sax.saxutils.escape``) before it enters the mini-markup, so a
    stray ``<`` / ``&`` in a guide can neither break the Paragraph parser nor inject markup.

Purity
    Pure functions — parse + layout only. NO AI, NO network, NO file I/O (the caller
    supplies the markdown text and the title/version/sha metadata). Deterministic.
"""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape as _esc

from markdown_it import MarkdownIt
from markdown_it.token import Token
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from docs_pdf import brand

# Single CommonMark parser with the GFM table rule enabled. linkify-it-py is deliberately
# NOT required (we never auto-linkify bare URLs) — keeps the dep surface to markdown-it-py.
_MD = MarkdownIt("commonmark").enable("table")

# HTML comments — DOTALL so multi-line ``<!-- … -->`` reminder blocks are stripped whole.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ── pre-processing ──────────────────────────────────────────────────────────────────────
def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (``---\\n … \\n---``). Only a block at the
    very top is removed; a ``---`` used later as a thematic break is untouched."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return text
    m = re.search(r"^---\s*$", text[4:], flags=re.MULTILINE)
    if m is None:
        return text
    return text[4 + m.end():].lstrip("\n")


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


# ── styles ──────────────────────────────────────────────────────────────────────────────
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=17,
                             textColor=brand.EVERGREEN, leading=21, spaceBefore=12, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=13.5,
                             textColor=brand.EVERGREEN, leading=17, spaceBefore=12, spaceAfter=3),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=11.5,
                             textColor=brand.EVERGREEN_SOFT, leading=15, spaceBefore=9, spaceAfter=2),
        "h4": ParagraphStyle("h4", parent=base, fontName="Helvetica-Bold", fontSize=10,
                             textColor=brand.EVERGREEN_SOFT, leading=13, spaceBefore=7, spaceAfter=2),
        "body": ParagraphStyle("body", parent=base, fontSize=9.5, leading=13.5,
                               textColor=brand.INK, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", parent=base, fontSize=9.5, leading=13.5,
                                 textColor=brand.INK, spaceAfter=2),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11.5, textColor=brand.INK),
        "colhead": ParagraphStyle("colhead", parent=base, fontName="Helvetica-Bold",
                                  fontSize=8.5, leading=11.5, textColor=brand.EVERGREEN),
        "code": ParagraphStyle("code", parent=base, fontName="Courier", fontSize=8.2,
                               leading=11, textColor=brand.INK),
    }


# ── inline (children) → reportlab mini-markup ─────────────────────────────────────────────
def _inline_markup(token: Token | None) -> str:
    """Translate an ``inline`` token's children into reportlab Paragraph mini-markup.

    Every text run is XML-escaped; only the whitelisted formatting tokens emit markup, so
    a guide's stray ``<``/``&`` is inert. Unknown inline token types degrade to their
    escaped text content (never dropped silently, never raw)."""
    if token is None or not token.children:
        return ""
    out: list[str] = []
    # Track per-link whether we emitted a real <a> anchor: only EXTERNAL http(s)/mailto
    # links become clickable anchors. Internal anchors (``#sec``) and relative doc links
    # would make reportlab raise "undefined destination target" at save time (the manual
    # is standalone — those targets don't exist in the PDF), so they render as styled text
    # only. Links can't nest in CommonMark, so a single-element stack suffices.
    link_is_anchor: list[bool] = []
    for c in token.children:
        t = c.type
        if t == "text":
            out.append(_esc(c.content))
        elif t == "code_inline":
            out.append(f'<font face="Courier" size="8.5">{_esc(c.content)}</font>')
        elif t == "strong_open":
            out.append("<b>")
        elif t == "strong_close":
            out.append("</b>")
        elif t == "em_open":
            out.append("<i>")
        elif t == "em_close":
            out.append("</i>")
        elif t in ("s_open",):
            out.append("<strike>")
        elif t in ("s_close",):
            out.append("</strike>")
        elif t == "link_open":
            href = str(c.attrs.get("href", ""))
            is_ext = href.lower().startswith(("http://", "https://", "mailto:"))
            link_is_anchor.append(is_ext)
            if is_ext:
                out.append(f'<a href="{_esc(href)}"><font color="#1f4d2e"><u>')
            else:
                out.append('<font color="#1f4d2e">')
        elif t == "link_close":
            was_anchor = link_is_anchor.pop() if link_is_anchor else False
            out.append("</u></font></a>" if was_anchor else "</font>")
        elif t == "softbreak":
            out.append(" ")
        elif t == "hardbreak":
            out.append("<br/>")
        elif t == "image":
            # No image embedding in the manuals — render the alt text (child text content).
            out.append(_esc(_inline_plain(c)))
        else:
            if c.content:
                out.append(_esc(c.content))
    return "".join(out)


def _inline_plain(token: Token) -> str:
    """The plain (unformatted) text of an inline token, joining its text children."""
    if not token.children:
        return token.content
    return "".join(ch.content for ch in token.children if ch.type == "text")


# ── block builders ────────────────────────────────────────────────────────────────────────
def _find_close(tokens: list[Token], open_idx: int) -> int:
    """Index of the token that closes the block opened at ``open_idx``, found by walking
    the cumulative nesting counter back to zero. Block open/close tokens are matched pairs
    (+1 / -1); inline formatting lives in ``children`` and never affects this stream."""
    depth = 0
    for j in range(open_idx, len(tokens)):
        depth += tokens[j].nesting
        if depth == 0:
            return j
    return len(tokens) - 1


def _heading(level: int, inline: Token | None, st: dict[str, ParagraphStyle]) -> Flowable:
    key = f"h{min(max(level, 1), 4)}"
    return Paragraph(_inline_markup(inline), st[key])


def _code_block(content: str, st: dict[str, ParagraphStyle]) -> Flowable:
    """A fenced / indented code block → a tinted monospace box. Each source line becomes a
    reportlab Paragraph so long lines wrap inside the box rather than overflow the page."""
    lines = content.rstrip("\n").split("\n") or [""]
    rows = [[Paragraph(_esc(ln).replace(" ", "&nbsp;") or "&nbsp;", st["code"])] for ln in lines]
    box = Table(rows, colWidths=[brand.CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand.CODE_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2.0, brand.EVERGREEN_SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return KeepTogether([Spacer(1, 3), box, Spacer(1, 6)])


def _table(tokens: list[Token], st: dict[str, ParagraphStyle]) -> Flowable:
    """A GFM pipe table (token slice ``table_open … table_close``) → a branded grid."""
    header: list[Paragraph] = []
    body: list[list[Paragraph]] = []
    in_head = False
    current: list[Paragraph] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "thead_open":
            in_head = True
        elif t.type == "thead_close":
            in_head = False
        elif t.type == "tr_open":
            current = []
        elif t.type == "tr_close":
            if in_head:
                header = current
            else:
                body.append(current)
        elif t.type in ("th_open", "td_open"):
            inline = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1].type == "inline" else None
            cell_style = st["colhead"] if t.type == "th_open" else st["cell"]
            current.append(Paragraph(_inline_markup(inline), cell_style))
        i += 1

    grid_rows: list[list[Paragraph]] = ([header] if header else []) + body
    if not grid_rows:
        return Spacer(1, 0)
    ncols = max(len(r) for r in grid_rows)
    for r in grid_rows:  # pad ragged rows so the Table is rectangular
        while len(r) < ncols:
            r.append(Paragraph("", st["cell"]))
    col_w = brand.CONTENT_W / ncols
    table = Table(grid_rows, colWidths=[col_w] * ncols, repeatRows=1 if header else 0)
    tstyle: list[tuple[object, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, brand.LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.3, brand.LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, brand.LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        tstyle += [
            ("BACKGROUND", (0, 0), (-1, 0), brand.TINT),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, brand.GOLD),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [brand.WHITE, brand.ZEBRA]),
        ]
    else:
        tstyle += [("ROWBACKGROUNDS", (0, 0), (-1, -1), [brand.WHITE, brand.ZEBRA])]
    table.setStyle(TableStyle(tstyle))
    return KeepTogether([Spacer(1, 3), table, Spacer(1, 8)])


def _callout(inner: list[Flowable]) -> Flowable:
    """A blockquote → a gold-edged callout box wrapping its recursively-rendered blocks
    (mirrors form_pdf's legal/guidance callout — the operator's requested accent box)."""
    box = Table([[inner]], colWidths=[brand.CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand.CALLOUT_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, brand.GOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return KeepTogether([Spacer(1, 4), box, Spacer(1, 8)])


def _list(tokens: list[Token], ordered: bool, depth: int,
          st: dict[str, ParagraphStyle]) -> list[Flowable]:
    """Render a list token slice (``*_list_open … *_list_close``). Ordered items renumber
    from the list's first ``list_item_open`` info; nested lists recurse at ``depth+1``."""
    out: list[Flowable] = []
    i = 1  # skip the opening list token
    end = len(tokens) - 1  # skip the closing list token
    counter = 1
    while i < end:
        if tokens[i].type == "list_item_open":
            close = _find_close(tokens, i)
            marker = f"{counter}." if ordered else "•"
            out.extend(_list_item(tokens[i + 1:close], marker, depth, st))
            counter += 1
            i = close + 1
        else:
            i += 1
    return out


def _list_item(tokens: list[Token], marker: str, depth: int,
               st: dict[str, ParagraphStyle]) -> list[Flowable]:
    """One list item's blocks: the first paragraph carries the bullet marker (hanging
    indent by depth); nested lists recurse; extra paragraphs indent under the marker."""
    indent = 14 + depth * 16
    bullet_style = ParagraphStyle(f"bullet_d{depth}", parent=st["bullet"],
                                  leftIndent=indent, bulletIndent=indent - 12)
    body_style = ParagraphStyle(f"ibody_d{depth}", parent=st["body"], leftIndent=indent)
    out: list[Flowable] = []
    first_para_done = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "paragraph_open":
            close = _find_close(tokens, i)
            inline = tokens[i + 1] if i + 1 <= close else None
            markup = _inline_markup(inline)
            if not first_para_done:
                out.append(Paragraph(markup, bullet_style, bulletText=marker))
                first_para_done = True
            else:
                out.append(Paragraph(markup, body_style))
            i = close + 1
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            close = _find_close(tokens, i)
            out.extend(_list(tokens[i:close + 1], t.type == "ordered_list_open", depth + 1, st))
            i = close + 1
        elif t.type in ("fence", "code_block"):
            out.append(_code_block(t.content, st))
            i += 1
        else:
            i += 1
    return out


def _render_blocks(tokens: list[Token], st: dict[str, ParagraphStyle],
                   depth: int = 0) -> list[Flowable]:
    """Walk a block-level token list into flowables. Recurses for blockquotes."""
    out: list[Flowable] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        typ = t.type
        if typ == "heading_open":
            close = _find_close(tokens, i)
            level = int(t.tag[1]) if t.tag[1:].isdigit() else 4
            out.append(_heading(level, tokens[i + 1] if i + 1 <= close else None, st))
            i = close + 1
        elif typ == "paragraph_open":
            close = _find_close(tokens, i)
            out.append(Paragraph(_inline_markup(tokens[i + 1] if i + 1 <= close else None), st["body"]))
            i = close + 1
        elif typ in ("bullet_list_open", "ordered_list_open"):
            close = _find_close(tokens, i)
            out.extend(_list(tokens[i:close + 1], typ == "ordered_list_open", depth, st))
            i = close + 1
        elif typ == "blockquote_open":
            close = _find_close(tokens, i)
            inner = _render_blocks(tokens[i + 1:close], st, depth + 1)
            out.append(_callout(inner))
            i = close + 1
        elif typ == "table_open":
            close = _find_close(tokens, i)
            out.append(_table(tokens[i:close + 1], st))
            i = close + 1
        elif typ in ("fence", "code_block"):
            out.append(_code_block(t.content, st))
            i += 1
        elif typ == "hr":
            out.append(Spacer(1, 4))
            out.append(HRFlowable(width="100%", thickness=0.8, color=brand.GOLD,
                                  spaceBefore=2, spaceAfter=6))
            i += 1
        else:
            i += 1
    return out


# ── public API ────────────────────────────────────────────────────────────────────────────
def render_markdown_to_flowables(md_text: str) -> list[Flowable]:
    """Parse ``md_text`` (frontmatter + HTML comments stripped) into reportlab flowables.

    Pure: no I/O, no network. Returns a possibly-empty list for empty/blank input."""
    body = _strip_html_comments(_strip_frontmatter(md_text))
    tokens = _MD.parse(body)
    return _render_blocks(tokens, _styles())


def render_markdown_to_pdf_bytes(md_text: str, *, title: str, version: str,
                                 git_sha: str) -> bytes:
    """Render ``md_text`` to a branded PDF: Evergreen masthead (title + version subtitle),
    the translated body, and the ``title · version · git-SHA · page N of M`` footer.

    Pure data → bytes — NO AI, NO network, NO Smartsheet/Box/Graph. Deterministic apart
    from the (caller-supplied) ``git_sha``.
    """
    subtitle_bits = [b for b in (version, (git_sha and f"rev {git_sha}")) if b]
    subtitle = "  ·  ".join(subtitle_bits)
    flow: list[Flowable] = brand.brand_header(title, subtitle=subtitle)
    flow.extend(render_markdown_to_flowables(md_text))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title=title or "ITS manual",
        leftMargin=brand.MARGIN, rightMargin=brand.MARGIN,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    )
    doc.build(flow, canvasmaker=brand.canvas_maker(title, version=version, git_sha=git_sha))
    return buf.getvalue()
