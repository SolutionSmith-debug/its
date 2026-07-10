"""docs_pdf — the repeatable markdown → branded-PDF documentation pipeline (WS3 / D2-1).

Turns the in-repo plain-markdown enablement guides (``docs/enablement/*.md``) into
polished, Evergreen-branded PDF manuals for the §6 / A8 documentation program — the
delivery-critical leave-behind set that ships before each cutover.

Three pure, side-effect-free modules plus one build script:

  * ``brand.py``     — the Evergreen palette (#1f4d2e / #b8860b), logo, and the page
    footer canvas (``title · version · git-SHA · page N of M``). The palette constants
    are a DELIBERATE §14 duplication of ``safety_reports/form_pdf.py`` (the live
    safety-PDF renderer) — see that module's cross-reference note; the two share ONE
    visual system without one importing the other's private internals.
  * ``md_render.py`` — markdown-it-py TOKEN STREAM → reportlab Platypus flowables
    (headings, paragraphs, lists, pipe tables, blockquote callouts, code, rules).
    ``render_markdown_to_flowables`` + ``render_markdown_to_pdf_bytes``.
  * ``manifest.py``  — loader for ``docs/enablement/manifest.yaml``, THE §6a enablement
    manifest: the single source of truth for the PDF build set.

The build orchestration (render each manifest doc, doc-currency SHA-256 check) lives in
``scripts/build_docs_pdfs.py``. The Box publish leg is D2-3 (not built here); the ITS
Owner's Manual / config-dictionary generated content is D2-2 (not built here).

No AI, no network, no Smartsheet/Box/Graph — pure data → bytes. Safe to import anywhere.
"""
from __future__ import annotations

from docs_pdf.md_render import (
    render_markdown_to_flowables,
    render_markdown_to_pdf_bytes,
)

__all__ = [
    "render_markdown_to_flowables",
    "render_markdown_to_pdf_bytes",
]
