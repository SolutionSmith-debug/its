"""§34 document-attachment screening for PO portal uploads (Feature B).

DETERMINISTIC, SEND-FREE, LLM-FREE. This module is the Mac-side trust boundary for
document attachments uploaded through the authenticated PO builder (Foundation
Mission Invariant 2, Layer 6 — attachment screening). It is the FIRST real
DOC-attachment instantiation of Op Stds §34: `safety_reports/photo_screen.py` is the
image-only sibling (its own header notes §34 Layer 2 "was authored for PDF/Office
attachments" — THIS module is that layer, realized). Enforced HERE, before
`po_poll.py` lets an attachment reach Box or a PO_Log row:

  L1  static signature checks   — magic-number vs the DECLARED MIME vs the file
                                  extension (all three must agree) + decoded-size
                                  sanity. Re-checked at this trust boundary even
                                  though the Worker sniffed at upload (the Worker
                                  gate could be bypassed by a direct internal call).
  L2  structural inspection     — format-aware:
                                  * PDF: bounded byte-scan for ACTIVE-CONTENT
                                    markers (/JavaScript /JS /OpenAction /AA
                                    /Launch /EmbeddedFile /RichMedia) → suspicious.
                                    We cannot sanitize a PDF the way a photo
                                    re-encode sanitizes pixels, so active content
                                    is refused-to-review, never auto-filed.
                                  * OpenXML (.docx/.xlsx): bounded IN-MEMORY zip
                                    walk — entry-count cap + total-decompressed cap
                                    (zip bomb → malicious), macro payload
                                    (vbaProject.bin → malicious), nested executable
                                    extensions → malicious, container/extension
                                    mismatch (a .docx carrying xl/) → suspicious.
                                  * images (JPEG/PNG): Pillow verify + the
                                    photo_screen decompression-bomb cap + a forced
                                    re-encode as STRUCTURAL PROOF (full decode must
                                    succeed). Unlike the photo path the re-encoded
                                    bytes are DISCARDED and the ORIGINAL bytes are
                                    filed on clean (operator decision: attachments
                                    are the operator's own specs/drawings bound for
                                    the operator's own Box — resolution fidelity
                                    wins; the metadata-egress rationale of the
                                    customer-facing photo path does not apply).
  L3  ClamAV (optional)         — `clamd.scan_stream` on the ORIGINAL bytes,
                                  config-gated (`po_materials.po_attach_screen.
                                  clamav_enabled`, default OFF — seeded false by
                                  scripts/migrations/seed_po_materials_config.py).
                                  pyclamd is an OPTIONAL operator-installed dep,
                                  lazily imported; enabled-but-unavailable →
                                  suspicious (never a blind pass).
  L4  VirusTotal                — explicitly skipped (Op Stds §34 Layer 4: "defer
                                  to Phase 2+"); not wired here.

Disposition ladder (mirrors photo_screen):
  * malicious  — active attack: macro payload, nested executable, zip bomb,
                 decompression bomb, or a ClamAV signature. The caller fires
                 CRITICAL NAMING THE UPLOADING ACCOUNT + a security-flagged
                 Review-Queue row and refuses the file before filing.
  * suspicious — refused-but-unproven: size/magic/extension inconsistency, PDF
                 active-content markers, unreadable structure, or clamd
                 required-but-unavailable. Review-Queue row; never filed.
  * clean      — survived every enabled layer; the ORIGINAL bytes are safe to file.

This module performs NO network egress except the optional, config-gated clamd
socket (pyclamd, allowlisted in tests/test_capability_gating.py) and NO
Smartsheet/Box I/O — all disposition + filing is the caller's job
(`po_poll._service_po_attachments`). Kept pure so the security logic is
unit-testable without live infra (tests/test_po_attach_screen.py).
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from typing import Literal

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

Disposition = Literal["clean", "suspicious", "malicious"]

# ── L1 — static signature constants ─────────────────────────────────────────────
# Decoded-bytes ceiling per attachment. Mirrors the Worker's ATTACHMENT_MAX_BYTES
# (po_attachments.ts) as defense-in-depth: a file reaching here larger than the
# Worker would have accepted means the Worker was bypassed → anomalous → suspicious.
MAX_ATTACHMENT_BYTES = 10_000_000

_PDF_MAGIC = b"%PDF-"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ZIP_MAGIC = b"PK\x03\x04"

# declared_mime → (allowed extensions, magic prefix checker key). MUST stay in
# lockstep with the Worker's MIME_ALLOWLIST (po_attachments.ts) — operator decision
# 2026-07-13: PDF + JPEG/PNG + OpenXML docx/xlsx ONLY (no legacy OLE, no CAD).
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ALLOWED_MIME: dict[str, tuple[tuple[str, ...], bytes]] = {
    "application/pdf": ((".pdf",), _PDF_MAGIC),
    "image/jpeg": ((".jpg", ".jpeg"), _JPEG_MAGIC),
    "image/png": ((".png",), _PNG_MAGIC),
    MIME_DOCX: ((".docx",), _ZIP_MAGIC),
    MIME_XLSX: ((".xlsx",), _ZIP_MAGIC),
}

# ── L2 — structural constants ───────────────────────────────────────────────────
# PDF active-content markers (name tokens). Presence anywhere in the raw bytes is
# treated as suspicious — a legitimate spec/drawing PDF has no reason to carry
# JavaScript, auto-exec actions, launch actions, or embedded files. Byte-scan is
# deliberately cruder than a full PDF object parse: a parser can be steered around
# (object streams, encodings) while a marker that never appears cannot execute via
# the standard dictionaries; anything obfuscated ALSO trips ClamAV/L1 elsewhere and
# the disposition is review, not silent pass.
PDF_ACTIVE_MARKERS: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/OpenAction",
    b"/AA",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
)

# OpenXML zip-walk caps (the zip-bomb gate): total decompressed bytes across all
# entries, and entry count. A real docx/xlsx spec sheet sits far below both.
MAX_ZIP_TOTAL_UNCOMPRESSED = 100_000_000  # ~100MB decompressed ceiling
MAX_ZIP_ENTRIES = 1_000

# Nested-executable extensions inside an OpenXML container → malicious (an
# executable payload has no legitimate place inside a spec document).
ZIP_EXECUTABLE_EXTS: tuple[str, ...] = (
    ".exe", ".dll", ".com", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".msi", ".jar", ".apk", ".sh",
)

# Image caps — mirror safety_reports/photo_screen (the image-class sibling): the
# same decompression-bomb pixel ceiling; the verify+re-encode is mirrored below
# (not imported — those helpers are module-private there; see _verify_image).
MAX_IMAGE_PIXELS = 24_000_000

MAX_FILENAME = 120  # mirror the Worker's MAX_ATTACHMENT_FILENAME


class _DecompressionBombError(Exception):
    """Pixel/byte expansion exceeds the cap — an active resource-exhaustion attack
    (malicious), distinct from a merely-unreadable file (suspicious)."""


@dataclass(frozen=True)
class ScreenResult:
    """Verdict for one attachment. On `clean` the caller files the ORIGINAL bytes
    (see the module docstring's image note — the re-encode is proof, not product)."""

    disposition: Disposition
    layer: str   # "L1" | "L2" | "L3" — which layer produced the verdict
    detail: str  # machine reason; NEVER contains file bytes or PII


def _extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot > 0 else ""


# ── L2 helpers ───────────────────────────────────────────────────────────────────


def _scan_pdf(raw: bytes) -> ScreenResult | None:
    """PDF structural pass: header/EOF sanity + the active-content marker scan.
    None = passed; a ScreenResult = the verdict (short-circuit)."""
    if b"%%EOF" not in raw[-4096:]:
        # No trailer in the tail — truncated or not really a document-shaped PDF.
        return ScreenResult("suspicious", "L2", "pdf_no_eof_trailer")
    for marker in PDF_ACTIVE_MARKERS:
        if marker in raw:
            # Active content in a spec/drawing PDF → refuse to review (structural
            # active content — the caller security-flags the Review-Queue row).
            return ScreenResult(
                "suspicious", "L2", f"pdf_active_content:{marker.decode('ascii')[1:]}"
            )
    return None


def _scan_openxml(raw: bytes, declared_mime: str) -> ScreenResult | None:
    """OpenXML structural pass: bounded in-memory zip walk. None = passed.

    Order matters: the bomb caps run on DECLARED sizes (zip central directory)
    BEFORE any decompression, so a bomb is refused without expanding it.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return ScreenResult("suspicious", "L2", "openxml_unreadable_zip")
    if len(infos) > MAX_ZIP_ENTRIES:
        return ScreenResult("malicious", "L2", f"zip_entry_bomb:{len(infos)}")
    total_uncompressed = 0
    names: list[str] = []
    for info in infos:
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED:
            return ScreenResult("malicious", "L2", f"zip_decompression_bomb:{total_uncompressed}")
        names.append(info.filename)
    lower_names = [n.lower() for n in names]
    for name in lower_names:
        if name.endswith("vbaproject.bin"):
            # A macro payload inside a "macro-free" .docx/.xlsx extension — active
            # content masquerading as a document (the .docm/.xlsm class smuggled
            # under the wrong extension) → malicious.
            return ScreenResult("malicious", "L2", "openxml_macro_payload")
        ext = _extension_of(name)
        if ext in ZIP_EXECUTABLE_EXTS:
            return ScreenResult("malicious", "L2", f"openxml_nested_executable:{ext}")
    if "[content_types].xml" not in lower_names:
        # Every real OpenXML container carries [Content_Types].xml at the root.
        return ScreenResult("suspicious", "L2", "openxml_missing_content_types")
    # Container/extension consistency: a docx carries word/, an xlsx carries xl/.
    # A container whose payload tree contradicts its declared type is disguised.
    expected_dir = "word/" if declared_mime == MIME_DOCX else "xl/"
    if not any(n.startswith(expected_dir) for n in lower_names):
        return ScreenResult("suspicious", "L2", f"openxml_container_mismatch:{expected_dir[:-1]}")
    return None


def _verify_image(raw: bytes) -> None:
    """Image structural pass — MIRRORS safety_reports/photo_screen._verify_and_reencode
    (module-private there, hence the documented copy, §42): header pixel-count cap →
    Pillow verify() → full decode → forced re-encode. The re-encode output is
    DISCARDED — here it is the structural PROOF that the full pixel pipeline decodes
    (a truncation/polyglot that survives verify() fails the decode); the ORIGINAL
    bytes are what the caller files on clean (module docstring, image note).

    Raises `_DecompressionBombError` on a pixel bomb (caller → malicious) and the
    usual Pillow decode errors on a structurally-bad image (caller → suspicious).
    """
    with Image.open(io.BytesIO(raw)) as probe:
        width, height = probe.size
        if width * height > MAX_IMAGE_PIXELS:
            raise _DecompressionBombError(f"{width}x{height}")
        probe.verify()  # detects truncation / CRC corruption; leaves image unusable
    with Image.open(io.BytesIO(raw)) as img:
        img.load()                # force full decode (raises on hidden truncation)
        rgb = img.convert("RGB")  # flatten palette/alpha — the full-pipeline proof
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=85)  # proof only; output discarded


def _clamav_scan(raw: bytes) -> tuple[str, str | None]:
    """L3: stream the ORIGINAL bytes to the local clamd daemon via pyclamd.

    MIRRORS safety_reports/photo_screen._clamav_scan (module-private there — a
    documented copy, §42). Returns ("OK", None) | ("FOUND", signature) |
    ("ERROR", detail). NEVER raises: a missing dep / unreachable daemon / unexpected
    result all degrade to ("ERROR", …) so the caller routes the attachment to review
    rather than crashing the pass. The ORIGINAL bytes are scanned (nothing here
    re-encodes documents, and for images a re-encode would strip a payload before
    clamd could see it).
    """
    try:
        import pyclamd  # noqa: PLC0415 — lazy + optional; operator-installed when clamav on
    except ImportError:
        return ("ERROR", "pyclamd_unavailable")
    try:
        cd = pyclamd.ClamdUnixSocket()
        result = cd.scan_stream(raw)  # None if clean; {"stream": ("FOUND", sig)} on a hit
    except Exception as exc:  # noqa: BLE001 — any clamd/socket failure → ERROR, never crash
        return ("ERROR", type(exc).__name__)
    if not result:
        return ("OK", None)
    status = result.get("stream") if isinstance(result, dict) else None
    if isinstance(status, (tuple, list)) and status and status[0] == "FOUND":
        return ("FOUND", str(status[1]) if len(status) > 1 else "unknown")
    return ("ERROR", "unexpected_clamd_result")


def screen_attachment(
    filename: str, declared_mime: str, data: bytes, *, clamav_enabled: bool = False
) -> ScreenResult:
    """Run the §34 sub-layers on ONE decoded attachment and return the verdict.

    Layers short-circuit cost-ordered (L1 cheap → L2 format-aware → L3 ClamAV).
    The caller has already verified the po-att:v1 HMAC + sha256 (transport
    integrity); this function judges the CONTENT. On `clean` the caller files the
    ORIGINAL `data` bytes (image re-encode is proof, not product — see module
    docstring).
    """
    # L1 — static signature checks: size, allowlist, magic⇄mime⇄extension agreement.
    if not data:
        return ScreenResult("suspicious", "L1", "empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        return ScreenResult("suspicious", "L1", f"oversize:{len(data)}")
    if not filename or len(filename) > MAX_FILENAME:
        return ScreenResult("suspicious", "L1", "bad_filename")
    entry = ALLOWED_MIME.get(declared_mime)
    if entry is None:
        return ScreenResult("suspicious", "L1", f"mime_not_allowed:{declared_mime[:64]}")
    exts, magic = entry
    if _extension_of(filename) not in exts:
        return ScreenResult("suspicious", "L1", "extension_mime_mismatch")
    if not data.startswith(magic):
        return ScreenResult("suspicious", "L1", "magic_mismatch")

    # L2 — format-aware structural inspection.
    if declared_mime == "application/pdf":
        verdict = _scan_pdf(data)
        if verdict is not None:
            return verdict
    elif declared_mime in (MIME_DOCX, MIME_XLSX):
        verdict = _scan_openxml(data, declared_mime)
        if verdict is not None:
            return verdict
    else:  # image/jpeg | image/png (the ALLOWED_MIME residue)
        try:
            _verify_image(data)
        except _DecompressionBombError as exc:
            return ScreenResult("malicious", "L2", f"decompression_bomb:{exc}")
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            return ScreenResult("suspicious", "L2", f"unreadable:{type(exc).__name__}")

    # L3 — ClamAV (optional, config-gated). Scans the ORIGINAL bytes.
    if clamav_enabled:
        verdict_str, sig = _clamav_scan(data)
        if verdict_str == "FOUND":
            return ScreenResult("malicious", "L3", f"clamav:{sig}")
        if verdict_str == "ERROR":
            # Scanner required but unavailable → do NOT pass blindly; route to review.
            return ScreenResult("suspicious", "L3", f"clamav_unavailable:{sig}")

    return ScreenResult("clean", "L3" if clamav_enabled else "L2", "ok")


def is_structural_active_content(result: ScreenResult) -> bool:
    """True when a SUSPICIOUS verdict is specifically STRUCTURAL ACTIVE CONTENT
    (PDF JavaScript/auto-exec/embedded-file markers) — the caller security-flags
    that Review-Queue row (Invariant 2 posture) while plainer inconsistencies
    (size/magic/extension/unreadable) stay ordinary review items."""
    return result.disposition == "suspicious" and result.detail.startswith("pdf_active_content:")
