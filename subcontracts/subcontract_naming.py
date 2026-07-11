"""Canonical Subcontract PDF naming — ONE source for the job-prefixed document name + title.

Every surface a Subcontract PDF's name lives on consumes THESE helpers so the four cannot
drift (the multi-surface fan-out lesson): the Box file name + the Smartsheet row attachment
(`subcontract_poll`), the emailed attachment (`subcontract_send`), and the PDF's internal
``/Title`` metadata (`subcontract_generate`). Mirrors the Safety convention (`safety_naming`):
the job name prefixes the document so the same subcontract number on different jobs never
shares a name, and a reviewer / recipient sees the job at a glance. Reuses
`safety_naming.job_folder_name` for identical sanitisation (already the Subcontract Box-folder
sanitiser in `subcontract_poll`).

Pure naming — no I/O, no external send. A blank job name falls back to the number-only name so
a numberless/jobless edge case never crashes.
"""
from __future__ import annotations

from safety_reports import safety_naming


def sc_pdf_filename(sc_number: str, job_name: str | None) -> str:
    """The Subcontract PDF file name: ``<Job>_Subcontract_<sc_number>.pdf`` (job-prefixed,
    matching the Safety ``<job>_<...>.pdf`` file style). Falls back to
    ``Subcontract <sc_number>.pdf`` when the job name is empty (the number-only name)."""
    job = safety_naming.job_folder_name(job_name or "").strip()
    return f"{job}_Subcontract_{sc_number}.pdf" if job else f"Subcontract {sc_number}.pdf"


def sc_pdf_title(sc_number: str, job_name: str | None) -> str:
    """The PDF's internal ``/Title`` metadata: ``Subcontract <sc_number> — <Job>``
    (job appended). Falls back to ``Subcontract <sc_number>`` when the job name is
    empty."""
    job = (job_name or "").strip()
    return f"Subcontract {sc_number} — {job}" if job else f"Subcontract {sc_number}"
