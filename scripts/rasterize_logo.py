"""Regenerate the committed brand-logo PNG used by the PDF renderer.

WHY this exists (build-time only, NOT a runtime dependency)
-----------------------------------------------------------
`safety_reports/form_pdf.py` embeds a committed raster PNG
(`safety_reports/assets/evergreen-logo.png`) in every form's masthead. The source of
truth is the VECTOR logo `safety_portal/public/evergreen-logo.svg`, but the renderer
must stay deterministic + dependency-light: it cannot rasterize SVG at runtime
(`svglib`/`cairosvg` are not installed, and the logo uses linear gradients that the
bundled PyMuPDF SVG path drops to solid black). So we rasterize ONCE, here, and commit
the PNG. The renderer just loads the committed PNG — no SVG, no network, deterministic.

This step is macOS-only (it uses `qlmanage`, the system QuickLook rasterizer, whose
CoreGraphics SVG renderer honours the gradients). It is run by the developer when the
logo changes — it is never invoked by any daemon or test.

Pipeline: qlmanage SVG→PNG (high-res) → content-bbox autocrop (drop the square
letterbox) → small transparent-free white pad → downscale to ~1000px wide (crisp at the
~2.3in masthead width, lean file). Output overwrites the committed asset.

Usage:  python scripts/rasterize_logo.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

_REPO = Path(__file__).resolve().parent.parent
_SVG = _REPO / "safety_portal" / "public" / "evergreen-logo.svg"
_OUT = _REPO / "safety_reports" / "assets" / "evergreen-logo.png"
_RASTER_SIZE = 3000   # qlmanage thumbnail box (px); generous so gradients stay smooth
_TARGET_W = 1000      # committed width (px)


def main() -> int:
    if sys.platform != "darwin":
        print("rasterize_logo: macOS-only (needs qlmanage). Skipping.", file=sys.stderr)
        return 1
    if not _SVG.is_file():
        print(f"rasterize_logo: source SVG missing: {_SVG}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["qlmanage", "-t", "-s", str(_RASTER_SIZE), "-o", td, str(_SVG)],
                       check=True, capture_output=True)
        raw = next(Path(td).glob("*.png"), None)
        if raw is None:
            print("rasterize_logo: qlmanage produced no thumbnail", file=sys.stderr)
            return 1
        im = Image.open(raw).convert("RGB")
        # autocrop: bbox of everything differing from the (white) corner background
        bg = Image.new("RGB", im.size, im.getpixel((0, 0)))
        bbox = ImageChops.difference(im, bg).getbbox()
        cropped = im.crop(bbox) if bbox else im
        pad = max(6, cropped.height // 12)
        canvas = Image.new("RGB", (cropped.width + 2 * pad, cropped.height + 2 * pad), (255, 255, 255))
        canvas.paste(cropped, (pad, pad))
        h = round(canvas.height * _TARGET_W / canvas.width)
        canvas.resize((_TARGET_W, h), Image.Resampling.LANCZOS).save(_OUT, optimize=True)
    print(f"rasterize_logo: wrote {_OUT} ({_OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
