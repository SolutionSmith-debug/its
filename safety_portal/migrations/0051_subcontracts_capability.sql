-- Subcontracts workstream S1 — grant cap.subcontracts.manage into the D1 capability tables. The
-- CONFIG_REGISTRY in worker/config.ts already NAMES cap.subcontracts.manage (the provisioned
-- placeholder), but resolveCapabilities is fail-closed against the capabilities/role_capabilities
-- tables — so the /api/subcontracts/* routes would 403 every admin until this grant lands. Exact
-- 0044_po_capability pattern: 0013's admin catch-all does NOT auto-include a capability added after
-- it, so the admin grant here must be EXPLICIT. submitter/manager get nothing (subcontract drafting is
-- an office-admin surface, D11); send/execute approval is NOT a portal capability — it lives on the
-- ITS — Subcontracts workspace share list (§46), enforced Mac-side by F22.
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the /api/subcontracts/* Worker deploys.
-- INSERT OR IGNORE keeps a re-apply a no-op.

INSERT OR IGNORE INTO capabilities (key, label, description) VALUES
  ('cap.subcontracts.manage', 'Subcontracts',
   'Draft / generate / supersede / cancel subcontracts and manage the subcontractor cache. Office-admin surface (D11); send + execution approval stays Mac-side (F22 + the ITS — Subcontracts workspace share list, §46).');

INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('admin', 'cap.subcontracts.manage');
