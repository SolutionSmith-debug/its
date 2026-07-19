"""Page-preview PNG renderer for uploaded vendor estimates (ADR-0004 decision 3).

Purpose
-------
The disposition screen's fidelity control is the human side-by-side accept: an
extracted line may be accepted ONLY with the source page's preview loaded. This
module produces those previews — one PNG per page of the (potentially hostile)
estimate PDF — for `estimate_poll` to post into the Worker's `estimate_previews`
D1 cache.

Isolation + hygiene
-------------------
* The Quartz (CoreGraphics) render runs INSIDE the killable, rlimited child
  (`po_materials.estimate_sandbox`, red-team #5): a wedging/OOMing PDF kills the
  CHILD; this function returns [] and the doc degrades (no previews — the SPA then
  forces the explicit no-preview acknowledgment path).
* Every child-produced PNG is RE-ENCODED through Pillow in the parent (decode-cap
  guarded) before it is trusted: the re-encode is structural proof the PNG is a
  real image and destroys anything the render path might have carried through.
  A page failing the re-encode is dropped, never posted.
* Each preview is size-capped to the Worker's 1_000_000-byte decoded limit —
  oversized pages are progressively downscaled, then dropped if still over.

Failure modes: any per-page failure drops that page; any whole-document failure
returns [] — the caller logs WARN and continues (previews are best-effort, filing
is not blocked on them).
"""
from __future__ import annotations

import base64
import binascii
import io
import json

from PIL import Image

from po_materials import estimate_sandbox

# The Worker's decoded per-preview cap (POST .../preview rejects above this).
MAX_PREVIEW_BYTES = 1_000_000
# Decompression cap on a child-produced PNG before Pillow re-encode (same posture
# as po_attach_screen.MAX_IMAGE_PIXELS).
MAX_PREVIEW_PIXELS = 24_000_000
# Progressive downscale steps applied when a re-encoded PNG exceeds the byte cap.
_DOWNSCALE_STEPS = (0.75, 0.5, 0.35)


def render_page_pngs(data: bytes, *, max_pages: int) -> list[bytes]:
    """Render up to `max_pages` preview PNGs for one estimate PDF.

    Returns Pillow-re-encoded PNG bytes, page-ordered (index 0 = page 1), each
    ≤ MAX_PREVIEW_BYTES. Returns [] on any whole-document failure (sandbox timeout /
    crash / unrenderable PDF / Quartz unavailable). Never raises on hostile input.
    """
    out = estimate_sandbox.run_sandboxed(
        "render_page_pngs",
        data,
        timeout_s=estimate_sandbox.PREVIEW_TIMEOUT_S,
        args=(str(max_pages),),
    )
    if out is None:
        return []
    try:
        parsed = json.loads(out)
    except (ValueError, UnicodeDecodeError):
        return []
    raw_list = parsed.get("pngs") if isinstance(parsed, dict) else None
    if not isinstance(raw_list, list):
        return []

    previews: list[bytes] = []
    for entry in raw_list[:max_pages]:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            raw_png = base64.b64decode(entry, validate=True)
        except (binascii.Error, ValueError):
            continue
        reencoded = _reencode_png(raw_png)
        if reencoded is not None:
            previews.append(reencoded)
    return previews


def _reencode_png(raw_png: bytes) -> bytes | None:
    """Pillow re-encode one child-produced PNG (structural proof + size cap).

    None on any failure — unreadable, over the pixel cap, or still over the byte
    cap after the downscale ladder. The caller drops the page.
    """
    try:
        with Image.open(io.BytesIO(raw_png)) as img:
            if img.width * img.height > MAX_PREVIEW_PIXELS:
                return None
            img.load()
            base = img.convert("RGB")
    except Exception:  # noqa: BLE001 — hostile-adjacent input; a bad page is dropped
        return None

    for factor in (1.0, *_DOWNSCALE_STEPS):
        try:
            if factor == 1.0:
                candidate = base
            else:
                new_size = (
                    max(1, int(base.width * factor)),
                    max(1, int(base.height * factor)),
                )
                candidate = base.resize(new_size)
            buf = io.BytesIO()
            candidate.save(buf, format="PNG", optimize=True)
        except Exception:  # noqa: BLE001 — encode failure drops the page
            return None
        encoded = buf.getvalue()
        if len(encoded) <= MAX_PREVIEW_BYTES:
            return encoded
    return None
