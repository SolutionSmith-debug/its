"""Deterministic subcontract render core (SC-S3a) — the record → filled contract-body TEXT transform,
with the money/legal/gate correctness wired in. NO AI (operator directive). The .docx/.xlsx rendering
(python-docx / openpyxl) layers on top in SC-S3b; the Worker + daemon pipeline in SC-S3c. Capability-
gated (Invariant 1): this module performs ZERO external transmission and no LLM step.

The transform, in order (any failure fences to Review — never files a wrong contract):
  1. shape-validate the subcontract record (required fields present);
  2. SOV-sums-to-price guard (money.sov_mismatches) — the Schedule of Values must reconcile to §2.1;
  3. load the Contractor identity config;
  4. load the body text via subcontracts.terms, branching on the profile KIND: a ``library`` profile
     loads the sha-verified + Layer-A-gated 27-article body (a pending body RAISES, fencing the
     subcontract until the operator make-currents it); an ``attach`` profile (negotiated MSA) loads
     the sha-pinned one-page reference body (no version gate) — its binding terms are the external MSA;
  5. build the 10 body tokens (parties/date + the num2words price clause + governing-law-from-state);
  6. STRICT token substitution → the filled body text (an unfilled contract blank RAISES).
"""
from __future__ import annotations

from typing import Any

from subcontracts import governing_law, money, terms

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}
_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]

# The subcontract-record fields the body preamble/§2.1/signature need. Snapshot fields frozen at draft.
_REQUIRED_FIELDS = (
    "subcontractor_entity", "project_name", "owner_entity", "governing_law_state",
    "contract_price_cents", "price_basis",
)


class SubcontractGenerateError(Exception):
    """The subcontract cannot be rendered from the record (bad shape, SOV mismatch, etc.). The daemon
    fences this to the Review Queue and NEVER files a contract whose numbers/clauses don't re-derive."""


def format_agreement_date(year: int, month: int, day: int) -> str:
    """A body-preamble date: '11th day of July 2026'. Ordinal day (11th/21st/2nd/3rd) + month + year."""
    if not (1 <= month <= 12) or not (1 <= day <= 31):
        raise SubcontractGenerateError(f"invalid agreement date {year}-{month}-{day}")
    # 11th/12th/13th are always -th; otherwise the last digit picks the suffix.
    suffix = "th" if 11 <= (day % 100) <= 13 else _ORDINAL_SUFFIX.get(day % 10, "th")
    return f"{day}{suffix} day of {_MONTHS[month]} {year}"


def build_body_tokens(subcontract: dict[str, Any], contractor: dict[str, Any]) -> dict[str, str]:
    """Assemble the 10 body {{tokens}} from the subcontract record + the Contractor config + the
    deterministic money/governing-law derivations. Raises SubcontractGenerateError / MoneyError /
    GoverningLawError on any bad value (never emits a blank/wrong contract field)."""
    missing = [f for f in _REQUIRED_FIELDS if not str(subcontract.get(f, "")).strip()
               and f not in ("contract_price_cents",)]
    if missing:
        raise SubcontractGenerateError(f"subcontract record missing required field(s): {missing}")

    price_cents = subcontract.get("contract_price_cents")
    if not isinstance(price_cents, int) or isinstance(price_cents, bool):
        raise SubcontractGenerateError(f"contract_price_cents must be an integer (got {price_cents!r})")
    price_basis = subcontract.get("price_basis") or "fixed"

    # agreement_date: the record's explicit (y,m,d) or a caller-provided one; never silently "today".
    ymd = subcontract.get("agreement_ymd")
    if not (isinstance(ymd, (list, tuple)) and len(ymd) == 3):
        raise SubcontractGenerateError("subcontract record missing agreement_ymd (year, month, day)")
    agreement_date = format_agreement_date(int(ymd[0]), int(ymd[1]), int(ymd[2]))

    prime = str(subcontract.get("prime_contractor") or contractor["prime_contractor_default"]).strip()
    law = governing_law.resolve(str(subcontract["governing_law_state"]),
                                subcontract.get("governing_law_venue"))

    return {
        "agreement_date": agreement_date,
        "contractor_entity": str(contractor["entity"]).strip(),
        "subcontractor_entity": str(subcontract["subcontractor_entity"]).strip(),
        "project_name": str(subcontract["project_name"]).strip(),
        "prime_contractor": prime,
        "owner_entity": str(subcontract["owner_entity"]).strip(),
        "contract_price_clause": money.contract_price_clause(price_cents, str(price_basis)),
        "governing_law_state_name": law["governing_law_state_name"],
        "governing_law_venue": law["governing_law_venue"],
        "signature_entity": str(contractor["signature_entity"]).strip(),
    }


def render_body_text(
    subcontract: dict[str, Any],
    sov_lines: list[dict[str, Any]],
    *,
    terms_profile_id: str = "standard_subcontract",
    terms_version: str | None = None,
) -> str:
    """The filled contract-body TEXT for a subcontract record — the deterministic core the .docx
    render (S3b) turns into a document. Runs the SOV guard, then branches on the terms profile KIND:
    a ``library`` profile renders the sha-verified + Layer-A-gated 27-article body; an ``attach``
    profile (a negotiated MSA) renders the one-page reference body (preamble + §2.1 price + the
    profile's manifest render_line + signature) INSTEAD of fencing. Both paths STRICT-fill tokens.
    Raises on any failure (the daemon fences it, never files)."""
    price_cents = subcontract.get("contract_price_cents")
    if not isinstance(price_cents, int) or isinstance(price_cents, bool):
        raise SubcontractGenerateError(f"contract_price_cents must be an integer (got {price_cents!r})")
    # SOV-sums-to-price guard FIRST — a money mismatch never renders a contract.
    problems = money.sov_mismatches(price_cents, sov_lines)
    if problems:
        raise SubcontractGenerateError("SOV does not reconcile to the Contract Price: " + "; ".join(problems))

    contractor = terms.load_contractor_config()
    tokens = build_body_tokens(subcontract, contractor)
    kind = terms.get_profile(terms_profile_id).get("kind")
    if kind == "attach":
        # Attach-kind (a negotiated MSA): render the one-page REFERENCE body — NOT the 27-article
        # library, and NO library-text load / Layer-A version gate (an attach profile carries no
        # versioned body language; the binding terms are the externally-negotiated MSA the render_line
        # points to). The reference body's {{render_line}} is filled from the manifest render_line;
        # Exhibit A (scope) + Annex C (SOV) still render as their own package files.
        tokens["render_line"] = terms.render_line(terms_profile_id)
        body = terms.load_attach_reference()
    else:
        # Library: sha-verified + Layer-A-gated body (a pending/un-cleared version RAISES here).
        body = terms.load_terms_text(terms_profile_id, terms_version)
    return terms.substitute_tokens(body, tokens)
