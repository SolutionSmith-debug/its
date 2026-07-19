"""Tier-2 LOCAL-LLM vendor-estimate extraction (ADR-0004 E5 — the ladder's last
automated tier).

Purpose
-------
When the deterministic Tier-1 parse (template + generic table) cannot read a
document, this tier asks a LOCAL Ollama model (schema-constrained decoding) for
the corpus-union extraction. `extract(...)` returns the rich `ExtractionResult`
(the `estimate_poll` ladder contract — the daemon converts an accepted result to
the Worker body and stamps tier=2; `estimate_parse.to_worker_payload` is the
shared converter for other consumers such as the eval harness). LOCAL-ONLY by ADR-0004 decision 1 —
vendor pricing never leaves the machine; the "sole live Anthropic consumer is
`intake.py`" invariant holds (this module can never import `anthropic*`, enforced
by GATED_SCRIPTS).

Defense stack (Invariant 2, red-team #6):
* EVERY page is wrapped in `untrusted_content.wrap(source='vendor-estimate')`;
  the system prompt leads with `untrusted_content.system_boilerplate()`.
* `schemas/vendor_estimate_extraction.json` v1.0.0 (pinned via
  `shared.schema_loader`) drives BOTH Ollama `format=` constrained decoding AND
  post-hoc `jsonschema.validate` inside `shared.ollama_client` — explicit numeric
  maxima make the schema a real VALUE gate.
* `estimate_parse.check_math` re-verifies every line + doc total deterministically.
* `anomaly_logger.check` runs on STRING FIELDS ONLY (mirroring intake's
  `collect_anomalies`) — NEVER the cents integers, which would trip its >1000
  numeric sentinel and burn the tripwire (red-team #6). It is a post-hoc tripwire,
  NOT a price-manipulation control: the automated gates verify internal
  CONSISTENCY, not fidelity — the human side-by-side accept is the fidelity
  control (decision 3).

Failure modes
-------------
* ANY inference failure (`OllamaClientError`: transport / non-local URL /
  nonconforming reply) or a payload the converter cannot shape → None — the
  caller's Tier-3 signal (Review Queue → manual disposition entry). The caller
  logs; this module stays pure.
* `SchemaLoaderError` / `SchemaVersionError` PROPAGATE — a schema-pin mismatch is
  a code/config bug that must surface loudly (never-silent), not degrade every
  document to Tier-3 forever.
* Model-reported `confidence` below the threshold, any math flag, or any anomaly
  flag → the result is RETURNED flagged `needs_review=True` (the caller routes);
  extraction data is advisory either way (decision 2).

Consumers
---------
* `po_materials/estimate_poll.py` — the E5 ladder wiring (sibling slice).
"""
from __future__ import annotations

import dataclasses
from typing import Any

from po_materials import estimate_parse
from po_materials.estimate_parse import ExtractionResult, LineItem
from shared import anomaly_logger, ollama_client, untrusted_content
from shared.schema_loader import load_schema

SCHEMA_NAME = "vendor_estimate_extraction"
SCHEMA_VERSION = "1.0.0"

# House default (CLAUDE.md "Confidence scoring on extractions"): below this the
# result is flagged needs_review. The caller may override from ITS_Config.
DEFAULT_CONFIDENCE_THRESHOLD = 0.85

_EXTRACTION_INSTRUCTION = (
    "You are extracting structured purchasing data from ONE vendor quote/estimate "
    "document, provided page by page inside <untrusted_content> tags. Fill the "
    "JSON schema exactly:\n"
    "- All *_cents fields are INTEGER US cents (e.g. $1,098.90 -> 109890). Never dollars.\n"
    "- line_items: one entry per priced line row. Section headings (rows with no "
    "quantity or unit price) go in the 'section' field of the lines under them — "
    "NEVER emit them as line items, never invent $0 lines.\n"
    "- unit is the unit-of-measure token exactly as printed (FT, EA, M, ...).\n"
    "- If pricing is per-thousand ('M'), extended = qty / 1000 x unit price.\n"
    "- A lump-sum / not-to-exceed document is ONE line item plus "
    "not_to_exceed_cap_cents.\n"
    "- Copy values only from the document. Use null for anything not present — "
    "never guess or invent.\n"
    "- confidence is your honest 0-1 estimate that every extracted value matches "
    "the document."
)


def extract(
    pages: list[str],
    *,
    model: str,
    base_url: str,
    timeout_s: int = ollama_client.DEFAULT_TIMEOUT_S,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    filename: str = "",
) -> ExtractionResult | None:
    """Run the Tier-2 local-LLM extraction over per-page document text.

    Returns an `ExtractionResult` (tier='tier2_llm', possibly flagged
    `needs_review`) or None on any inference failure — the caller's Tier-3
    signal. Raises only on a schema-pin mismatch (config bug, never silent).
    The `estimate_poll` ladder converts an accepted result to the Worker body
    itself; `estimate_parse.to_worker_payload` is the shared converter for other
    consumers (the eval harness). `filename` is tolerated for older call sites
    but UNUSED by design (identity is body-derived — filenames lie, decision 7).
    """
    del filename  # interface compat only — see docstring
    if not pages or not any(p.strip() for p in pages):
        return None
    schema_doc = load_schema(SCHEMA_NAME, expected_version=SCHEMA_VERSION)
    schema: dict[str, Any] = schema_doc["json_schema"]

    wrapped_pages = "\n\n".join(
        untrusted_content.wrap(page, source="vendor-estimate") for page in pages
    )
    system = untrusted_content.system_boilerplate() + "\n\n" + _EXTRACTION_INSTRUCTION
    prompt = (
        "Extract the vendor estimate below into the required JSON.\n\n" + wrapped_pages
    )
    try:
        payload = ollama_client.generate_structured(
            prompt=prompt,
            schema=schema,
            model=model,
            system=system,
            base_url=base_url,
            timeout_s=timeout_s,
        )
    except ollama_client.OllamaClientError:
        return None

    result = _result_from_payload(payload)
    if result is None:
        return None
    result = estimate_parse.check_math(result)

    # Layer-5 tripwire on STRING FIELDS ONLY (red-team #6): mirror intake's
    # collect_anomalies — the cents integers must never reach the >1000 numeric
    # sentinel.
    flags = anomaly_logger.check(_string_fields(payload))
    needs_review = (
        bool(flags)
        or not result.math_ok
        or result.confidence < confidence_threshold
    )
    return dataclasses.replace(
        result, anomaly_flags=list(flags), needs_review=needs_review
    )


def _string_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the extraction payload down to its STRING fields (structure kept).

    Numeric fields (the cents ints, qty, confidence) are dropped so
    `anomaly_logger.check`'s >1000 numeric sentinel never fires on legitimate
    money — the schema's explicit maxima are the numeric gate (red-team #6).
    """
    out: dict[str, Any] = {
        k: v for k, v in payload.items() if isinstance(v, str)
    }
    notes = payload.get("notes")
    if isinstance(notes, list):
        out["notes"] = [n for n in notes if isinstance(n, str)]
    line_items = payload.get("line_items")
    if isinstance(line_items, list):
        out["line_items"] = [
            {k: v for k, v in li.items() if isinstance(v, str)}
            for li in line_items
            if isinstance(li, dict)
        ]
    return out


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _result_from_payload(payload: dict[str, Any]) -> ExtractionResult | None:
    """Shape the schema-validated payload into an ExtractionResult.

    Belt-and-braces on top of jsonschema (the schema already validated): any
    surprise shape returns None rather than raising into the daemon.
    """
    try:
        doc_type = payload["doc_type"]
        confidence = float(payload["confidence"])
        raw_lines = payload["line_items"]
        if not isinstance(doc_type, str) or not isinstance(raw_lines, list):
            return None
        lines: list[LineItem] = []
        for raw in raw_lines:
            if not isinstance(raw, dict):
                return None
            description = raw.get("description")
            if not isinstance(description, str) or not description:
                return None
            qty = raw.get("qty")
            lines.append(
                LineItem(
                    description=description,
                    section=_opt_str(raw.get("section")),
                    part_number=_opt_str(raw.get("part_number")),
                    qty=float(qty) if isinstance(qty, int | float) and not isinstance(qty, bool) else None,
                    unit=_opt_str(raw.get("unit")),
                    unit_cost_cents=_opt_int(raw.get("unit_cost_cents")),
                    extended_cents=_opt_int(raw.get("extended_cents")),
                    line_note=_opt_str(raw.get("line_note")),
                )
            )
        notes = payload.get("notes")
        return ExtractionResult(
            doc_type=doc_type,
            confidence=confidence,
            line_items=lines,
            vendor_name=_opt_str(payload.get("vendor_name")),
            quote_number=_opt_str(payload.get("quote_number")),
            revision_label=_opt_str(payload.get("revision_label")),
            quote_date=_opt_str(payload.get("quote_date")),
            valid_until=_opt_str(payload.get("valid_until")),
            subtotal_cents=_opt_int(payload.get("subtotal_cents")),
            tax_cents=_opt_int(payload.get("tax_cents")),
            freight_cents=_opt_int(payload.get("freight_cents")),
            misc_cents=_opt_int(payload.get("misc_cents")),
            grand_total_cents=_opt_int(payload.get("grand_total_cents")),
            not_to_exceed_cap_cents=_opt_int(payload.get("not_to_exceed_cap_cents")),
            payment_terms=_opt_str(payload.get("payment_terms")),
            notes=[n for n in notes if isinstance(n, str)] if isinstance(notes, list) else [],
            tier="tier2_llm",
        )
    except (KeyError, TypeError, ValueError):
        return None
