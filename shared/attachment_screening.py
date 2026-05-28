"""Attachment screening (Foundation Mission v8 Invariant 2, Layer 6) — STUB, NOT WIRED.

DOCTRINE-ONLY as of 2026-05-28. This module exists to make the Layer 6 gap
explicit and to pin the intended public signature; it is **not implemented** and
**not imported** by the intake pipeline. Building it is blocked on:

  1. An operator decision — Option A (build the four sub-layers) vs Option B
     (file a dated doctrine exception in its-blueprint and run Safety Reports
     without Layer 6 under stated conditions).
  2. An external prerequisite for sub-layer (c) — a running ``clamd`` socket on
     the host (ClamAV via ``pyclamd``).

If Option B is chosen, DELETE this file (the signature should not outlive the
decision to not build). See ``docs/audits/2026-05-28_forensic-evaluation.md``
(HIGH-2) and the tech_debt entry "Invariant 2 Layer 6 (attachment screening)
is doctrine-only".

Doctrine (FM v8 Invariant 2, Layer 6 / Op Stds v11 §34): every attachment passes
four sub-layers before it is uploaded to Box or referenced in any AI call —
  (a) static signature / magic-number / size,
  (b) format-aware structural inspection (PDF JS / embedded files, Office
      macros, EXIF anomalies),
  (c) ClamAV via a ``clamd`` socket,
  (d) optional VirusTotal hash (Phase 2+).
Disposition: malicious -> ITS_Quarantine + CRITICAL triple-fire + sender
DISABLED in ITS_Trusted_Contacts; suspicious -> ITS_Review_Queue; clean ->
proceed.

Do NOT import this module from the intake pipeline until the decision lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScreenDisposition(StrEnum):
    """Outcome of screening a single attachment."""

    CLEAN = "CLEAN"
    SUSPICIOUS = "SUSPICIOUS"
    MALICIOUS = "MALICIOUS"


@dataclass(frozen=True)
class ScreenVerdict:
    """Verdict for one screened attachment.

    Attributes:
        disposition: CLEAN / SUSPICIOUS / MALICIOUS.
        reason: Short reason code + human note for routing and audit
            (e.g. "clamav:Eicar-Test-Signature", "pdf:embedded-js",
            "mime-mismatch:declared=application/pdf,sniffed=application/zip").
    """

    disposition: ScreenDisposition
    reason: str


def screen(filename: str, content: bytes, mime_type: str) -> ScreenVerdict:
    """Screen one attachment through the Layer 6 sub-layers. NOT IMPLEMENTED.

    Intended contract (the audit's Option A acceptance criteria): run sub-layers
    (a)-(d) and return a verdict the intake pipeline routes on — malicious to
    ITS_Quarantine (+ CRITICAL triple-fire + sender DISABLED), suspicious to
    ITS_Review_Queue, clean proceeds. The verdict must be produced BEFORE the
    attachment reaches Box or any AI call.

    ``mime_type`` is the Microsoft-Graph-declared content type and is therefore
    attacker-controlled — a real implementation MUST sniff ``content`` bytes
    (magic number) rather than trust the declared type.

    Args:
        filename: Original attachment filename (for logging / disposition).
        content: Raw attachment bytes as downloaded from Microsoft Graph.
        mime_type: Declared MIME type (untrusted — verify against magic bytes).

    Returns:
        A :class:`ScreenVerdict`.

    Raises:
        NotImplementedError: always — Layer 6 is doctrine-only pending the
            Option A/B decision and the ``clamd`` prerequisite (see module
            docstring). This stub deliberately fails closed rather than
            silently returning CLEAN.
    """
    raise NotImplementedError(
        "Attachment screening (FM v8 Invariant 2, Layer 6) is not implemented — "
        "blocked on the operator's Option A (build) vs Option B (documented "
        "exception) decision and the clamd prerequisite. See "
        "docs/audits/2026-05-28_forensic-evaluation.md (HIGH-2). Do not wire "
        "into intake until the decision lands."
    )
