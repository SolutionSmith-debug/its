"""po_materials.po_naming — the canonical PO PDF name/title helpers (2026-07 job-name
convention). One source for all four surfaces (Box file, Smartsheet attachment, emailed
attachment, internal /Title) so they cannot drift."""
from __future__ import annotations

from po_materials import po_naming


class TestPoPdfFilename:
    def test_job_prefixed(self):
        assert (
            po_naming.po_pdf_filename("2026.001.352.0.0", "2023.126 Kendall Solar")
            == "2023.126 Kendall Solar_PO_2026.001.352.0.0.pdf"
        )

    def test_blank_job_falls_back_to_number_only(self):
        assert po_naming.po_pdf_filename("2026.001.352.0.0", "") == "PO 2026.001.352.0.0.pdf"
        assert po_naming.po_pdf_filename("2026.001.352.0.0", None) == "PO 2026.001.352.0.0.pdf"

    def test_whitespace_only_job_falls_back(self):
        assert po_naming.po_pdf_filename("42", "   ") == "PO 42.pdf"

    def test_slash_in_job_is_sanitised(self):
        # safety_naming.job_folder_name turns a path-like '/' into '-' (no nested Box/Smartsheet path)
        assert po_naming.po_pdf_filename("42", "A/B Job") == "A-B Job_PO_42.pdf"


class TestPoPdfTitle:
    def test_job_appended(self):
        assert (
            po_naming.po_pdf_title("2026.001.352.0.0", "2023.126 Kendall Solar")
            == "Purchase Order 2026.001.352.0.0 — 2023.126 Kendall Solar"
        )

    def test_blank_job_falls_back(self):
        assert po_naming.po_pdf_title("42", "") == "Purchase Order 42"
        assert po_naming.po_pdf_title("42", None) == "Purchase Order 42"
