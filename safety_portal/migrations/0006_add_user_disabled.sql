-- Phase 7 — operator-controlled session revocation.
-- Adds a `disabled` flag to `users`. The cookie-derived session (HMAC + iat +
-- 90-day expiry) carries no server-side revocation on its own; requireSession now
-- reads this column per request and 401s a disabled (or deleted) user immediately.
-- Operator-provisioned via the bearer-gated /api/internal/admin/* routes
-- (portal_admin CLI): disable=1 locks the user out, enable=0 restores access.

-- ORDER DEPENDENCY (activation): apply this migration to the live D1 BEFORE the
-- Worker code that reads `disabled` deploys — otherwise the requireSession lookup
-- errors and (fail-closed by design) 401s every session until the column exists.
ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0;
