-- PO workstream S2 (Aug-7 delivery program WS1) — cap.po.manage, the Purchase-Order browser
-- capability (D11: any portal ADMIN drafts POs; approval authority is NOT a portal capability —
-- it lives on the ITS — Purchase Orders workspace share list, §46, enforced Mac-side by F22).
--
-- Exact 0023/0025/0027 pattern: 0013's admin grant was a seed-time catch-all
-- (`SELECT key FROM capabilities`), so it does NOT auto-include a capability added after 0013 —
-- the admin grant here must be EXPLICIT. submitter/manager deliberately get nothing: PO
-- drafting is an office-admin surface (D11).
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the /api/po/* Worker deploys —
-- resolveCapabilities is fail-closed, so the routes would 403 every admin until this lands
-- (the 0013 activation rule). INSERT OR IGNORE keeps a re-apply a no-op.

INSERT OR IGNORE INTO capabilities (key, label, description) VALUES
  ('cap.po.manage', 'Purchase Orders',
   'Draft / generate / supersede / cancel purchase orders and manage the vendor cache. Office-admin surface (D11); send approval stays Mac-side (F22 + the ITS — Purchase Orders workspace share list, §46).');

INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('admin', 'cap.po.manage');
