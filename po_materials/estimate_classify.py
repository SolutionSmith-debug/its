"""Deterministic doc-type classifier for uploaded vendor estimates (ADR-0004 stage 1).

Purpose
-------
The Evergreen vendor-quote corpus is doc-type-MIXED: quotes, estimates, proposals,
INVOICES, and a scanned QuickBooks AP report all live in one folder. An invoice or
AP report must be classified and REFUSED from the PO path — never parsed as new
line items (ADR-0004 decision 6, the doc-type gate). This module is the PR-A
minimal deterministic classifier: keyword/regex scoring over per-page text, no AI,
no network.

Isolation
---------
`extract_pages_text` is a thin parent-side wrapper: the actual pdfplumber parse
runs INSIDE the killable, rlimited child (`po_materials.estimate_sandbox` — the
red-team #5 contract). A hostile PDF that wedges/OOMs the parser kills the CHILD;
this function returns [] and the caller degrades the document (doc_type 'other'
→ needs_review). `classify_doc_type` itself is pure string scoring — safe on the
already-extracted text.

Failure modes
-------------
* Sandbox timeout / crash / malformed child output → `extract_pages_text` → [].
* Empty page list → `classify_doc_type` → ('other', 0.0) — the honest "I could not
  read it" verdict; the daemon routes the doc to needs_review, never refuses on it.
"""
from __future__ import annotations

import json
import re

from po_materials import estimate_sandbox

# The classifier vocabulary (the ONLY doc types this lane knows). invoice/ap_report
# are the REFUSED types; everything else proceeds to filing + review.
DOC_TYPES = ("quote", "estimate", "proposal", "invoice", "ap_report", "other")
REFUSED_DOC_TYPES = frozenset({"invoice", "ap_report"})

DEFAULT_MAX_PAGES = 8

# ---- Scoring rules --------------------------------------------------------------
# Each rule: (doc_type, weight, compiled regex). Scores accumulate per type over the
# first pages' text (case-insensitive). Derived from the corpus survey (ADR-0004):
#  * invoice — "INVOICE #"/invoice-number headers, remit-to blocks, net-terms lines,
#    "invoice total"/"amount due" (billing language a quote never carries).
#  * ap_report — "bills for"/aging buckets/"A/P"/open-balance ledger table language
#    (the scanned QuickBooks Apricus report class).
#  * quote/estimate/proposal — their own headers ("QUOTATION", "ESTIMATE",
#    "PROPOSAL"), "quote #", "valid until"/"pricing valid", "prices quoted".
_RULES: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    # invoice signals
    ("invoice", 3, re.compile(r"\binvoice\s*(?:#|no\.?|number)\b", re.I)),
    ("invoice", 3, re.compile(r"\binvoice\s+(?:total|date)\b", re.I)),
    ("invoice", 3, re.compile(r"\bremit(?:\s+payment)?\s+to\b", re.I)),
    ("invoice", 2, re.compile(r"\bamount\s+due\b", re.I)),
    ("invoice", 2, re.compile(r"\bnet\s*(?:10|15|30|45|60)\b", re.I)),
    ("invoice", 2, re.compile(r"\bdue\s+(?:date|upon\s+receipt)\b", re.I)),
    ("invoice", 1, re.compile(r"^\s*invoice\b", re.I | re.M)),
    # AP-report signals (aging / ledger table language)
    ("ap_report", 4, re.compile(r"\ba/?p\s+aging\b", re.I)),
    ("ap_report", 3, re.compile(r"\baccounts\s+payable\b", re.I)),
    ("ap_report", 3, re.compile(r"\bbills?\s+for\b", re.I)),
    ("ap_report", 3, re.compile(r"\b(?:1\s*-\s*30|31\s*-\s*60|61\s*-\s*90|>\s*90)\s*(?:days)?\b", re.I)),
    ("ap_report", 2, re.compile(r"\bopen\s+balance\b", re.I)),
    ("ap_report", 2, re.compile(r"\bunpaid\s+bills\b", re.I)),
    # quote signals
    ("quote", 3, re.compile(r"\bquot(?:e|ation)\s*(?:#|no\.?|number)\b", re.I)),
    ("quote", 3, re.compile(r"^\s*quot(?:e|ation)\b", re.I | re.M)),
    ("quote", 2, re.compile(r"\b(?:pricing|prices?|quote)\s+valid\b", re.I)),
    ("quote", 2, re.compile(r"\bvalid\s+(?:until|through|for)\b", re.I)),
    ("quote", 1, re.compile(r"\bprices?\s+quoted\b", re.I)),
    # estimate signals
    ("estimate", 3, re.compile(r"\bestimate\s*(?:#|no\.?|number)\b", re.I)),
    ("estimate", 3, re.compile(r"^\s*estimate\b", re.I | re.M)),
    # proposal signals
    ("proposal", 3, re.compile(r"^\s*proposal\b", re.I | re.M)),
    ("proposal", 2, re.compile(r"\bwe\s+propose\b", re.I)),
    ("proposal", 2, re.compile(r"\bscope\s+of\s+work\b", re.I)),
    ("proposal", 1, re.compile(r"\bnot[\s-]+to[\s-]+exceed\b", re.I)),
)

# A doc must clear this score to earn a positive type; below it → 'other'.
_MIN_SCORE = 3


def extract_pages_text(data: bytes, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[str]:
    """Per-page text of a (potentially hostile) PDF, via the sandboxed child.

    Returns up to `max_pages` strings (one per page, '' for an unreadable page), or
    [] when the child timed out / crashed / produced malformed output — the caller's
    degrade signal (doc_type 'other' → needs_review). Never raises on hostile input.
    """
    out = estimate_sandbox.run_sandboxed(
        "extract_pages_text",
        data,
        timeout_s=estimate_sandbox.TEXT_TIMEOUT_S,
        args=(str(max_pages),),
    )
    if out is None:
        return []
    try:
        parsed = json.loads(out)
    except (ValueError, UnicodeDecodeError):
        return []
    pages = parsed.get("pages") if isinstance(parsed, dict) else None
    if not isinstance(pages, list):
        return []
    return [p for p in pages if isinstance(p, str)][:max_pages]


def classify_doc_type(pages: list[str]) -> tuple[str, float]:
    """Score the extracted text against the deterministic rule set.

    Returns `(doc_type, confidence)` with doc_type ∈ DOC_TYPES. Confidence is a
    bounded heuristic (winning-score dominance, 0.0–1.0) — advisory display data,
    NOT a gate: the daemon refuses on the TYPE (invoice/ap_report), never on the
    confidence. Empty/whitespace-only text → ('other', 0.0).
    """
    text = "\n".join(pages).strip()
    if not text:
        return ("other", 0.0)

    scores: dict[str, int] = {t: 0 for t in DOC_TYPES}
    for doc_type, weight, pattern in _RULES:
        if pattern.search(text):
            scores[doc_type] += weight

    winner = max(scores, key=lambda t: scores[t])
    top = scores[winner]
    if top < _MIN_SCORE:
        return ("other", 0.0)
    total = sum(scores.values())
    confidence = round(min(1.0, top / total if total else 0.0), 3)
    return (winner, confidence)
