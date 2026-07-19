"""macOS Vision OCR tier for SCANNED vendor estimates (ADR-0004 E5).

Purpose
-------
~20% of the vendor-quote corpus is scanned images (the Nassau invoice, the Apricus
AP ledger) — no native text layer, so Tier-1 cannot read them. `ocr_pages` recovers
per-page text LOCALLY: the Quartz page render already built for the disposition
previews (`estimate_preview.render_page_pngs`, which runs inside the killable
rlimited `estimate_sandbox` child and Pillow-RE-ENCODES every PNG before trusting
it) feeds `ocrmac` (Apple Vision `VNRecognizeTextRequest`, `accurate` level) per
page. The OCR text then rides the SAME downstream path as native text (doc-type
gate / Tier-2 local LLM) — nothing leaves the machine.

ONLY invoked when `estimate_parse.parse_native` reports `is_scanned` — a native
text layer is always preferred (cheaper, exact).

Isolation posture
-----------------
The hostile-BYTE decode happens in the sandbox child (Quartz render) and every PNG
is structurally re-proven by the Pillow re-encode in `estimate_preview` before this
module sees it — Vision here consumes only those laundered PNGs, in-process.

Failure modes — degrade, never raise:
* `ocrmac` / Vision unavailable (non-mac dev host, missing dep) → [].
* Whole-document render failure (sandbox timeout / unrenderable PDF) → [].
* A single page failing OCR → "" for that page; the rest survive.
The caller treats [] as "no text recovered" → Tier-3 manual review.

Consumers
---------
* `po_materials/estimate_poll.py` — the E5 ladder wiring (sibling slice).
"""
from __future__ import annotations

import io

from po_materials import estimate_preview

DEFAULT_MAX_PAGES = 8


def ocr_pages(data: bytes, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[str]:
    """OCR up to `max_pages` pages of a scanned estimate PDF, locally.

    Returns page-ordered text (one string per rendered page, '' for a page OCR
    could not read), or [] when nothing could be rendered / OCR is unavailable.
    Never raises on hostile input.
    """
    try:
        from ocrmac import ocrmac  # noqa: PLC0415 — lazy: macOS-only optional capability
    except Exception:  # noqa: BLE001 — ImportError or a broken Vision bridge → degrade
        return []
    try:
        from PIL import Image  # noqa: PLC0415 — lazy alongside ocrmac
    except Exception:  # noqa: BLE001
        return []

    pngs = estimate_preview.render_page_pngs(data, max_pages=max_pages)
    if not pngs:
        return []

    pages: list[str] = []
    for png in pngs:
        text = ""
        try:
            with Image.open(io.BytesIO(png)) as img:
                img.load()
                annotations = ocrmac.OCR(img, recognition_level="accurate").recognize()
            text = "\n".join(
                str(entry[0])
                for entry in annotations
                if isinstance(entry, list | tuple) and entry and entry[0]
            )
        except Exception:  # noqa: BLE001 — one bad page degrades to ""
            text = ""
        pages.append(text)
    return pages
