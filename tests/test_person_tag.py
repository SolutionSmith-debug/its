"""Tests for box_migration/parse_job_v3.PERSON_TAG_IN_SUBJECT.

Added 2026-05-20 implementing Direction A from
docs/audits/person_tag_audit_2026-05-19.md — the third alternation
(`-\\s*[A-Z][a-z]+\\s*$`, "trailing-capitalized-word after dash") was
removed because audit measured a 60–70% false-positive rate against
~138 corpus occurrences. The first two alternations stay:

  * `\\bfor\\s+[A-Z]{3,}\\b`                                  (alt 1)
  * `^[A-Z][a-z]+\\s+(Organize|Cleanup|Notes|Files)\\b`       (alt 2)

Three test groups:
  A. Positive regression — alt 1 + alt 2 still fire on real TPs.
  B. Audit FP locks      — the 13 confirmed FPs from the audit must
                           no longer match. Prevents accidental
                           reintroduction of alt 3.
  C. Known TP losses     — acceptance lock. Removing alt 3 drops a
                           handful of real catches by design; the
                           audit doc records that as the tradeoff.
                           A future maintainer who thinks "we're
                           missing coverage" must read the audit
                           before reintroducing alt 3.

Mirrors the path-manipulation pattern from tests/test_parse_vendor_sub.py
— box_migration/ isn't a package, so we prepend the directory to
sys.path before importing.

Run with: pytest -q tests/test_person_tag.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOX_MIGRATION_DIR = Path(__file__).resolve().parent.parent / "box_migration"
if str(BOX_MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(BOX_MIGRATION_DIR))

import parse_job_v3 as p  # noqa: E402

# ===========================================================================
# Group A — Positive regression (alternations 1 + 2 still fire)
# ===========================================================================


@pytest.mark.parametrize(
    "raw,expected_match",
    [
        # Alt 1: "for <ALLCAPS>" with ≥3 caps
        ("11. EPC Contract Redlines for ZACK",  "for ZACK"),
        ("for ABC",                             "for ABC"),
        ("for XYZ",                             "for XYZ"),
    ],
)
def test_alt1_for_allcaps_still_matches(raw, expected_match):
    """Alt 1 (`\\bfor\\s+[A-Z]{3,}\\b`) must continue to fire."""
    m = p.PERSON_TAG_IN_SUBJECT.search(raw)
    assert m is not None, f"expected match on {raw!r}"
    assert m.group(0) == expected_match


@pytest.mark.parametrize(
    "raw,expected_match",
    [
        # Alt 2: "<First> <Verb>" across all four allowlist verbs.
        ("Teala Organize folder", "Teala Organize"),
        ("Teala Cleanup folder",  "Teala Cleanup"),
        ("Teala Notes folder",    "Teala Notes"),
        ("Teala Files folder",    "Teala Files"),
    ],
)
def test_alt2_first_plus_verb_still_matches(raw, expected_match):
    """Alt 2 (`^[A-Z][a-z]+\\s+(Organize|Cleanup|Notes|Files)\\b`) must continue
    to fire across all four allowlist verbs."""
    m = p.PERSON_TAG_IN_SUBJECT.search(raw)
    assert m is not None, f"expected match on {raw!r}"
    assert m.group(0) == expected_match


# ===========================================================================
# Group B — Audit FP negative locks
# ===========================================================================
# All 13 confirmed false positives from docs/audits/person_tag_audit_2026-05-19.md
# (rows #1–#12 from the 20-sample table, plus #19 `As-Built Lum Mark-Ups`).
# Every one hit the removed third alternation. None should match the
# refined two-alternation regex. If a future change reintroduces alt 3,
# these tests catch the regression.


AUDIT_FP_CASES = [
    # Document type ----------------------------------------------------------
    "9. Utility-Documents-Tracking",
    "7.11 As-Built",
    "T-Sheets",
    "11. AHJ & Utility Permits-Inspections",
    "As-Built Lum Mark-Ups",
    # Document state ---------------------------------------------------------
    "Bonacci 1 - OCO 001 - Final",
    # Project / location -----------------------------------------------------
    "Module Deliveries - Rockford",
    "Quick - Brimfield",
    "Re_ Final Golden Row Submittal - Steger",
    # Customer name ----------------------------------------------------------
    "Pull Tests- Forefront",
    # Vendor / equipment -----------------------------------------------------
    "CPS-Chint",
    # Discipline / abbreviation ---------------------------------------------
    "Geo-Tech",
    "2 - Environmental",
]


@pytest.mark.parametrize("raw", AUDIT_FP_CASES)
def test_audit_false_positives_no_longer_match(raw):
    """Audit-confirmed FPs must NOT fire after alt 3 removal."""
    m = p.PERSON_TAG_IN_SUBJECT.search(raw)
    assert m is None, (
        f"reintroduced FP: {raw!r} matched on {(m.group(0) if m else None)!r}; "
        "see docs/audits/person_tag_audit_2026-05-19.md"
    )


# ===========================================================================
# Group C — Known TP losses (acceptance lock)
# ===========================================================================
# Per docs/audits/person_tag_audit_2026-05-19.md: removing the third alternation
# drops these real-or-leaning-real person-tag catches by design. Operator
# triages visually in the folder tree. DO NOT "fix" by reintroducing alt 3 —
# the audit doc has the FP cost analysis (138 hits, ~95% noise) that makes
# this tradeoff the right call. Audit samples #15, #16, #17, #18, #20.

KNOWN_TP_LOSSES_NO_LONGER_FLAGGED = [
    "Structural - Bowman",        # audit #15
    "R. Bowman-Pungo",            # audit #16
    "R. 11.4.25 Ferc-Bowman",     # audit #17
    "V6. Maddox-Coker",           # audit #18
    "XFMR Re-build- Coker",       # audit #20
]


@pytest.mark.parametrize("raw", KNOWN_TP_LOSSES_NO_LONGER_FLAGGED)
def test_known_tp_losses_no_longer_flagged(raw):
    """Acceptance lock: these audit-doc TP / lean-TP cases lose their flag
    after Direction A. A future PR that "fixes" the missing coverage by
    reintroducing alt 3 will fail Group B, but this test makes the tradeoff
    explicit at the regex level too."""
    m = p.PERSON_TAG_IN_SUBJECT.search(raw)
    assert m is None, (
        f"unexpected match on known-TP-loss case {raw!r}: {m.group(0)!r}. "
        "Did alt 3 get reintroduced? See docs/audits/person_tag_audit_2026-05-19.md."
    )


# ===========================================================================
# Consumer-path integration — verifies detect_chaos still emits the
# person_tag_in_subject ChaosFlag for TPs and skips it for FPs.
# ===========================================================================


def _chaos_pattern_names(name: str) -> list[str]:
    return [f.pattern for f in p.detect_chaos(name)]


def test_detect_chaos_emits_person_tag_for_tp():
    """End-to-end: a real TP still surfaces the person_tag_in_subject flag."""
    assert "person_tag_in_subject" in _chaos_pattern_names(
        "11. EPC Contract Redlines for ZACK"
    )


def test_detect_chaos_skips_person_tag_for_audit_fp():
    """End-to-end: the most common audit FP (`-Tracking` suffix) no longer
    surfaces the person_tag_in_subject flag."""
    assert "person_tag_in_subject" not in _chaos_pattern_names(
        "9. Utility-Documents-Tracking"
    )
