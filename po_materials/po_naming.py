"""Canonical PO PDF naming — ONE source for the job-prefixed document name + title.

Every surface a PO PDF's name lives on consumes THESE helpers so the four cannot drift
(the multi-surface fan-out lesson): the Box file name + the Smartsheet row attachment
(`po_poll`), the emailed attachment (`po_send`), and the PDF's internal ``/Title``
metadata (`po_generate`). Mirrors the Safety convention (`safety_naming`): the job name
prefixes the document so the same PO number on different jobs never shares a name, and a
reviewer / recipient sees the job at a glance. Reuses `safety_naming.job_folder_name` for
identical sanitisation (already the PO Box-folder sanitiser in `po_poll`).

Pure naming — no I/O, no external send. A blank job name falls back to the pre-existing
number-only name so a numberless/jobless edge case never crashes.
"""
from __future__ import annotations

from safety_reports import safety_naming


def po_pdf_filename(po_number: str, job_name: str | None) -> str:
    """The PO PDF file name: ``<Job>_PO_<po_number>.pdf`` (job-prefixed, matching the
    Safety ``<job>_<...>.pdf`` file style). Falls back to ``PO <po_number>.pdf`` when
    the job name is empty (the pre-2026-07 name)."""
    job = safety_naming.job_folder_name(job_name or "").strip()
    return f"{job}_PO_{po_number}.pdf" if job else f"PO {po_number}.pdf"


def po_pdf_title(po_number: str, job_name: str | None) -> str:
    """The PDF's internal ``/Title`` metadata: ``Purchase Order <po_number> — <Job>``
    (job appended). Falls back to ``Purchase Order <po_number>`` when the job name is
    empty."""
    job = (job_name or "").strip()
    return f"Purchase Order {po_number} — {job}" if job else f"Purchase Order {po_number}"
