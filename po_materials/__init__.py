"""po_materials — the Purchase-Order workstream (WS1, Aug-7 delivery program).

S3 lands the terms library + versioned purchaser/tax config (this package's pure,
side-effect-free foundation). The generation daemon (po_poll, S4) and send side
(po_send / po_send_poll, S5) land as separate GATED modules per Invariant 1 — when
they do, they enroll in tests/test_capability_gating.py in the same PR.
"""
