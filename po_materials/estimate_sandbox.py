"""Killable-subprocess isolation for hostile-document parsing (ADR-0004, red-team #5).

Purpose
-------
Every hostile-input parse stage of the vendor-estimate importer — pdfplumber text
extraction (`estimate_classify.extract_pages_text`) and the Quartz PDF→PNG preview
render (`estimate_preview.render_page_pngs`) — runs INSIDE a killable child process
with an `RLIMIT_AS` address-space cap and a wall-clock timeout. A wedged / OOM /
crashing parse of an attacker-crafted PDF is REAPED by the parent; the stage returns
None/[] and the document DEGRADES (no previews / doc_type 'other' → needs_review) —
the `estimate_poll` daemon NEVER dies from a hostile document.

Protocol
--------
`run_sandboxed(fn_name, data, ...)` re-invokes `python -m po_materials.estimate_sandbox
<fn_name> [args...]`, writes `data` to the child's stdin, and returns the child's raw
stdout bytes (a JSON document per the fn contract below) — or None on timeout / kill /
crash / nonzero exit / empty output. The parse libraries (pdfplumber, Quartz) are
imported lazily INSIDE the child only — the daemon process never loads them, so a
parser bug cannot corrupt daemon state even in-process.

Child fn contracts (stdout JSON):
  extract_pages_text [max_pages]  → {"pages": ["page 1 text", ...]}
  render_page_pngs   [max_pages]  → {"pngs": ["<base64 png>", ...]}
  (plus four harmless _test_* fns — spin / bounded-alloc / crash / echo — dispatched
  only by tests/test_estimate_sandbox.py to prove the reap contract on REAL children)

Invariants
----------
* The child attempts `resource.setrlimit(RLIMIT_AS, cap)` via preexec_fn BEFORE
  exec — an allocation bomb hits the rlimit and dies in the child, not the daemon.
  HONESTY (live-probed 2026-07-19 on the exec host): the Darwin kernel REJECTS
  lowering RLIMIT_AS/RLIMIT_DATA (EINVAL), so on macOS the AS cap is best-effort
  and the ENFORCED bounds are `RLIMIT_CPU` (set to the wall-clock budget — kills a
  CPU-spinning parse) + the parent-side `subprocess.run(..., timeout=)` kill. The
  isolation guarantee that matters — a wedged/OOM parse dies in the CHILD, never
  the daemon — holds either way. Every setrlimit is try/except-wrapped: a raising
  preexec_fn would otherwise abort the spawn itself.
  (preexec_fn is documented thread-unsafe; the ITS daemons are single-threaded
  one-shot launchd processes, so the constraint holds by construction.)
* `subprocess.run(..., timeout=)` kills the child on wall-clock overrun.
* NEVER raises on hostile input: every failure mode collapses to None. A None is
  the caller's degrade signal, not an error.
* No AI, no network, no sends — pure local parsing (capability-gated with the
  importer: the estimate lane is AI-free by ADR-0004 decision 1).

Consumers
---------
`po_materials/estimate_classify.py` (text extraction → doc-type classifier) and
`po_materials/estimate_preview.py` (page-preview PNG render), both driven by
`po_materials/estimate_poll.py`.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import resource
import subprocess
import sys
from collections.abc import Sequence
from typing import Any, NoReturn

# Address-space cap for a parse child: generous for a legitimate 10 MB estimate,
# fatal for an allocation bomb. 2 GiB.
DEFAULT_RLIMIT_BYTES = 2 * 1024 * 1024 * 1024
# Wall-clock budgets (the caller passes these; constants here so both callers and
# tests share one source).
TEXT_TIMEOUT_S = 60
PREVIEW_TIMEOUT_S = 120
# Sanity cap on child stdout — a preview batch of a dozen page PNGs sits far below
# this; anything larger is a runaway child, treated as a failure.
MAX_CHILD_STDOUT_BYTES = 64 * 1024 * 1024

# Preview render geometry (child-side): target on-screen readable width in pixels,
# hard pixel-area cap per page (matches po_attach_screen.MAX_IMAGE_PIXELS posture).
PREVIEW_TARGET_WIDTH_PX = 1100
PREVIEW_MAX_PIXELS = 24_000_000

# TEST-SUPPORT child fns (tests/test_estimate_sandbox.py — the REAL-child-process
# suite proving the reap/rlimit contract without a hostile document). Deliberately
# ungated: each is harmless — local CPU/memory inside a child the parent reaps
# (spin / bounded alloc / crash / echo); nothing in the daemon dispatches them,
# and invoking one by hand just burns a few seconds of local CPU.
_TEST_FNS = ("_test_spin", "_test_alloc", "_test_crash", "_test_echo")
_ALLOWED_FNS = ("extract_pages_text", "render_page_pngs", *_TEST_FNS)


def run_sandboxed(
    fn_name: str,
    data: bytes,
    *,
    timeout_s: int,
    rlimit_bytes: int = DEFAULT_RLIMIT_BYTES,
    args: Sequence[str] = (),
) -> bytes | None:
    """Run one child-side parse fn over `data` in an rlimited, timeout-bounded child.

    Returns the child's raw stdout bytes (JSON per the fn contract) or None on ANY
    failure — timeout (child killed), crash/OOM (nonzero exit or signal), unknown
    fn, spawn failure, or empty/oversized output. Never raises on hostile input.
    """
    if fn_name not in _ALLOWED_FNS:
        return None

    def _limit_child() -> None:  # pragma: no cover — runs in the child pre-exec
        # BEST-EFFORT address-space cap: Darwin rejects lowering RLIMIT_AS (EINVAL;
        # see the module docstring's honesty note) — never let a raising preexec_fn
        # abort the spawn itself.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (rlimit_bytes, rlimit_bytes))
        except (ValueError, OSError):
            pass
        # ENFORCED CPU-time cap (works on Darwin): a CPU-spinning parse dies at the
        # wall-clock budget even if the parent were gone; the parent timeout kills
        # sleep/IO wedges.
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s + 5))
        except (ValueError, OSError):
            pass

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter/module
            [sys.executable, "-m", "po_materials.estimate_sandbox", fn_name, *args],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            preexec_fn=_limit_child,  # noqa: PLW1509 — single-threaded daemon (see docstring)
        )
    except subprocess.TimeoutExpired:
        return None
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout
    if not out or len(out) > MAX_CHILD_STDOUT_BYTES:
        return None
    return out


# ---- Child-side workers (run ONLY inside the rlimited child) ------------------------
#
# Hostile-input parsing lives below this line. These functions are invoked via the
# __main__ dispatch; the parse libraries are imported lazily HERE so the daemon
# process never loads them. Any exception escaping a worker exits the child nonzero
# — the parent maps that to None (degrade), never a daemon death.


def _child_extract_pages_text(data: bytes, max_pages: int) -> dict[str, Any]:
    """pdfplumber per-page text extraction (child-side). Empty string per unreadable
    page; a completely unparseable document raises (→ nonzero exit → parent None)."""
    import pdfplumber  # noqa: PLC0415 — lazy child-only import by design

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — one bad page degrades to ""
                pages.append("")
    return {"pages": pages}


def _child_render_page_pngs(data: bytes, max_pages: int) -> dict[str, Any]:
    """Quartz (CoreGraphics) PDF→PNG page render (child-side).

    CGPDFDocumentCreateWithProvider → per page CGBitmapContext draw →
    CGImageDestination PNG encode. A page that fails to draw/encode is skipped; a
    document Quartz cannot open yields {"pngs": []} (the parent treats an empty
    list as no-previews, a degrade — the disposition screen then forces the
    no-preview path per ADR-0004 decision 3). If the Quartz bridge is unavailable
    at runtime, returns {"pngs": []} gracefully (contract: never a hard dependency).
    """
    try:
        import Quartz  # noqa: PLC0415 — lazy child-only import by design
    except ImportError:
        return {"pngs": []}

    cf_data = Quartz.CFDataCreate(None, data, len(data))
    provider = Quartz.CGDataProviderCreateWithCFData(cf_data)
    doc = Quartz.CGPDFDocumentCreateWithProvider(provider)
    if doc is None:
        return {"pngs": []}
    page_count = min(int(Quartz.CGPDFDocumentGetNumberOfPages(doc)), max_pages)
    pngs: list[str] = []
    for page_no in range(1, page_count + 1):
        try:
            png = _render_one_page(Quartz, doc, page_no)
        except Exception:  # noqa: BLE001 — one bad page is skipped, not fatal
            png = None
        if png:
            pngs.append(base64.b64encode(png).decode("ascii"))
    return {"pngs": pngs}


def _render_one_page(quartz: Any, doc: Any, page_no: int) -> bytes | None:
    """Render one PDF page to PNG bytes via a white-backed RGB bitmap context."""
    page = quartz.CGPDFDocumentGetPage(doc, page_no)
    if page is None:
        return None
    box = quartz.CGPDFPageGetBoxRect(page, quartz.kCGPDFMediaBox)
    width_pts = float(box.size.width)
    height_pts = float(box.size.height)
    if width_pts <= 0 or height_pts <= 0:
        return None
    scale = PREVIEW_TARGET_WIDTH_PX / width_pts
    scale = max(0.1, min(scale, 3.0))
    if width_pts * scale * height_pts * scale > PREVIEW_MAX_PIXELS:
        # Absurd page geometry (a decompression-bomb-shaped MediaBox) — shrink to cap.
        scale = (PREVIEW_MAX_PIXELS / (width_pts * height_pts)) ** 0.5
    width = max(1, int(width_pts * scale))
    height = max(1, int(height_pts * scale))

    color_space = quartz.CGColorSpaceCreateDeviceRGB()
    ctx = quartz.CGBitmapContextCreate(
        None, width, height, 8, 0, color_space, quartz.kCGImageAlphaPremultipliedLast
    )
    if ctx is None:
        return None
    quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    quartz.CGContextFillRect(ctx, quartz.CGRectMake(0, 0, width, height))
    quartz.CGContextScaleCTM(ctx, scale, scale)
    quartz.CGContextTranslateCTM(ctx, -float(box.origin.x), -float(box.origin.y))
    quartz.CGContextDrawPDFPage(ctx, page)
    image = quartz.CGBitmapContextCreateImage(ctx)
    if image is None:
        return None
    out_data = quartz.CFDataCreateMutable(None, 0)
    dest = quartz.CGImageDestinationCreateWithData(out_data, "public.png", 1, None)
    if dest is None:
        return None
    quartz.CGImageDestinationAddImage(dest, image, None)
    if not quartz.CGImageDestinationFinalize(dest):
        return None
    return bytes(out_data)


# ---- Test-support child fns (REAL-kill proof; see tests/test_estimate_sandbox.py) --
#
# _test_alloc BOUNDS its own allocation (_TEST_ALLOC_CAP_BYTES) and then spins:
# on a platform that enforces a lowered RLIMIT_AS (Linux) it dies mid-allocation;
# on Darwin (which REJECTS lowering RLIMIT_AS — the module docstring's honesty
# note) the bound keeps host memory safe until the CPU/wall-clock reap kills it.

_TEST_ALLOC_BLOCK_BYTES = 64 * 1024 * 1024
_TEST_ALLOC_CAP_BYTES = 512 * 1024 * 1024


def _child_test_spin() -> NoReturn:  # pragma: no cover — runs in the child, reaped
    """CPU-spin forever — killed by RLIMIT_CPU or the parent wall-clock timeout."""
    while True:
        pass


def _child_test_alloc() -> NoReturn:  # pragma: no cover — runs in the child, reaped
    """Allocate up to the cap (dies to RLIMIT_AS where enforced), then spin for
    the reap — never exits cleanly, so the parent always maps this to None."""
    blocks: list[bytearray] = []
    total = 0
    while total < _TEST_ALLOC_CAP_BYTES:
        blocks.append(bytearray(_TEST_ALLOC_BLOCK_BYTES))  # zero-fill commits pages
        total += _TEST_ALLOC_BLOCK_BYTES
    while True:  # cap reached without an enforceable RLIMIT_AS: await the reap
        pass


def _child_test_echo(data: bytes) -> dict[str, Any]:
    """Happy-path round-trip probe: prove stdin bytes reached the child intact
    and the JSON-on-stdout contract works end-to-end."""
    return {"echo_len": len(data), "echo_sha256": hashlib.sha256(data).hexdigest()}


def _child_main(argv: list[str]) -> int:
    """Child entry: dispatch `<fn_name> [max_pages]`, data on stdin, JSON on stdout."""
    if len(argv) < 1 or argv[0] not in _ALLOWED_FNS:
        return 2
    fn_name = argv[0]
    try:
        max_pages = int(argv[1]) if len(argv) > 1 else 8
    except ValueError:
        return 2
    max_pages = max(1, min(max_pages, 50))
    data = sys.stdin.buffer.read()
    if fn_name == "extract_pages_text":
        result = _child_extract_pages_text(data, max_pages)
    elif fn_name == "render_page_pngs":
        result = _child_render_page_pngs(data, max_pages)
    elif fn_name == "_test_echo":
        result = _child_test_echo(data)
    elif fn_name == "_test_crash":  # pragma: no cover — child exits nonzero
        raise RuntimeError("deliberate child crash (_test_crash, test-support)")
    elif fn_name == "_test_spin":  # pragma: no cover — child never returns
        _child_test_spin()
    else:  # pragma: no cover — _test_alloc, the only remaining allowed name
        _child_test_alloc()
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via run_sandboxed
    sys.exit(_child_main(sys.argv[1:]))
