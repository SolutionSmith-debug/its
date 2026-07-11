"""subcontracts — the Subcontract generation workstream (SC, ADR-0003).

A near-mirror of the Purchase-Order workstream (po_materials), built fully
DETERMINISTIC (no AI in the generation path — the operator authors Exhibit A
Article II from a trade template). S1 lands the data-layer foundation (this
package's pure party/numbering/log/naming modules) + the D1 tables (migrations
0049–0051) + the Smartsheet builders. The generation daemon (subcontract_poll,
S3) and send side (subcontract_send / subcontract_send_poll, S4) land as separate
GATED modules per Invariant 1 — when they do, they enroll in
tests/test_capability_gating.py in the same PR (the commented stubs are already
present).
"""
