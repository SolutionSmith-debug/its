-- VALIDATION-ONLY seed user. Exists in local dev + the validation environment
-- (evergreenmirror) to prove the login path end-to-end. DO NOT apply to production
-- — production PMs are provisioned via the Phase 7 admin route.
--
-- The bcrypt hash below is cost 10. The plaintext is a throwaway dev credential
-- documented in safety_portal/README.md ("Local development"); it unlocks only a
-- local/validation D1 that does not exist in production and is intentionally NOT a
-- secret. Username follows the lastname.firstname convention.

INSERT OR IGNORE INTO users (username, password_hash) VALUES
  ('test.pm', '$2b$10$3tYpvbBx9R4BXguZivmv9uJAD2VqOSAdI22mqYcKQ1rEp7.52hQ.O');
