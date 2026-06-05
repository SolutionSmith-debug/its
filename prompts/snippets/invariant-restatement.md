---
type: reference
date: 2026-06-05
status: active
workstream: safety_portal
tags: [invariants, foundation-mission, pr-boilerplate, external-ingest]
---

# Foundation Invariant Restatement (PR boilerplate)

Paste this block verbatim into the description of **every PR that touches
external-ingest code** (anything that fetches, polls, parses, renders, or files
content originating outside the operating tenant). It keeps both Foundation
Mission v11 invariants in front of the reviewer and forces the PR to say *how*
the change satisfies each.

---

## Invariant 1 — External Send Gate (permanent)

No external transmission without explicit human approval. **Permanent, not
time-bounded.** Two-process model: generation scripts (which call the Anthropic
API and/or process untrusted input) have **zero** send capability; send scripts
(which transmit) have **zero** AI step. A successful prompt injection at the AI
layer cannot cause external transmission — the AI is in a different process from
the transmitter. Enforced in code by `tests/test_capability_gating.py`
(`GATED_SCRIPTS` / `SEND_SCRIPTS` + the F02 network-egress allowlist).

## Invariant 2 — Adversarial Input Handling

All content originating outside the operating customer tenant is **untrusted
data**. Six-layer defense, of which Layer 5 is a post-hoc detection tripwire (not
a co-equal barrier — FM v9 / audit F13); prevention is Layers 1–4 + the
two-process Send Gate:

1. **Sender allowlist + scope + header-forgery detection** — or, on the portal
   pull path, the **authenticated field-PM session + per-row HMAC** (only the
   Worker holding `HMAC_PAYLOAD_SECRET` can mint a row that verifies; a row whose
   HMAC fails is rejected + anomaly-logged + Review-Queue-flagged, never filed).
2. **Untrusted-content tagging** (`shared.untrusted_content.wrap()` +
   system boilerplate) on every Anthropic call over external content. N/A to a
   deterministic, LLM-free render path — but any portal field later fed to an LLM
   MUST be wrapped.
3. **Capability gating** — the AI / ingest process has no send permission
   (Invariant 1).
4. **Structured-output enforcement** — Anthropic tool-use JSON-schema, or, on the
   portal path, payload validation against the Phase-4 form definition before
   render.
5. **Anomaly logging** (`shared.anomaly_logger.check()`) — a tripwire, not a
   barrier.
6. **Attachment screening** (Op Stds §34) — N/A for safety reports (the portal's
   form-fill replaces arbitrary file attachment); load-bearing surface is Email
   Triage.

Residual risk: prompt injection is unsolved. The architecture assumes injection
might succeed at the AI layer and bounds the damage ceiling to "extracted data is
wrong," never "data exfiltrated" or "external action taken."

---

### How this PR satisfies them

> _(Fill in per PR — map the change to each invariant; do not delete the prompts.)_
>
> - **Invariant 1:** …
> - **Invariant 2 (per layer touched):** …
