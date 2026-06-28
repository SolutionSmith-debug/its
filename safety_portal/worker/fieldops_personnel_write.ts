import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt, isUniqueViolation } from "./audit";
import { hashPassword, normalizeUsername, parseRole } from "./auth";

// Task #22 — PERSONNEL CRUD (create / update / link / unlink / retire). cap.personnel.manage
// (admin-only). `personnel` is the job-site roster; `users` is the login directory. The two are a
// SOFT-linked "two-headed roster": personnel.username is a plain string pointing at users.username
// with NO foreign key (schema 0014). A person is either roster-only (username NULL) or linked to a
// login account. Two create modes share ONE route:
//   • roster-only  → INSERT personnel (username NULL)
//   • with-account → create the users row AND the personnel row ATOMICALLY in one D1 batch
// Retire is a SOFT-delete (active=0) so history (time_entries.personnel_id) keeps its target. Each
// mutation + its audit_log row(s) land in ONE D1 batch (W4). Send-free (D1 only).
//
// §42 — DEFENSE-IN-DEPTH on the account-creating branch: it ALSO requires the actor's role to be
// 'admin' (c.get("role"), set by requireSession), mirroring the requireRole("admin") boundary on
// /api/admin/users. cap.personnel.manage is admin-only TODAY so this never rejects a legitimate
// caller — but it keeps "mint a credential + assign a role" pinned to admin even if a future
// migration grants cap.personnel.manage to a non-admin role. Linking/unlinking an EXISTING account
// is plain cap.personnel.manage (it mints no credential and assigns no role).

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;
const MAX_NAME = 128;
const MAX_SHORT = 64;
const MIN_PASSWORD = 8; // mirror /api/admin/users
const MAX_PASSWORD = 256;

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

export function registerPersonnelWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/personnel — add a person to the roster (optionally WITH a login account).
  app.post(
    "/api/fieldops/personnel",
    gates.requireSession,
    gates.requireCapability("cap.personnel.manage"),
    async (c) => {
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);

      const name = typeof body.name === "string" ? body.name.trim() : "";
      const trade = typeof body.trade === "string" && body.trade.trim() !== "" ? body.trade.trim() : null;
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (trade !== null && trade.length > MAX_SHORT) return c.json({ error: "invalid_trade" }, 400);

      const actor = c.get("session").username;
      const account = body.account;

      // ── roster-only (no login account) ──────────────────────────────────────────
      if (account === undefined || account === null) {
        const res = await c.env.DB.batch([
          c.env.DB
            .prepare("INSERT INTO personnel (name, trade, username) VALUES (?1, ?2, NULL) RETURNING id")
            .bind(name, trade),
          auditStmt(c, actor, "personnel_create", name, { name, trade, account: false }),
        ]);
        const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
        return c.json({ ok: true, id: newId }, 201);
      }

      // ── with-account (creates a credentialed login) ─────────────────────────────
      // (see module header §42) minting a credential + assigning a role is admin-only.
      if (c.get("role") !== "admin") return c.json({ error: "forbidden" }, 403);
      if (typeof account !== "object" || Array.isArray(account)) return c.json({ error: "bad_request" }, 400);
      const acct = account as Record<string, unknown>;
      const username = normalizeUsername(typeof acct.username === "string" ? acct.username : "");
      const password = typeof acct.password === "string" ? acct.password : "";
      const role = parseRole(acct.role); // default 'submitter'; admin must be explicit
      if (!username) return c.json({ error: "invalid_username" }, 400);
      if (password.length < MIN_PASSWORD || password.length > MAX_PASSWORD) return c.json({ error: "invalid_password" }, 400);
      if (role === null) return c.json({ error: "invalid_role" }, 400);

      const exists = await c.env.DB.prepare("SELECT 1 FROM users WHERE username = ?").bind(username).first();
      if (exists) return c.json({ error: "exists" }, 409);
      const password_hash = await hashPassword(password); // plaintext never stored/logged
      try {
        // Personnel row carries the RETURNING (index 0, the proven pattern); users + audits follow.
        // The batch is ONE transaction — if the users INSERT loses the username UNIQUE race, the
        // whole batch rolls back, so no orphan personnel row is left behind.
        const res = await c.env.DB.batch([
          c.env.DB.prepare("INSERT INTO personnel (name, trade, username) VALUES (?1, ?2, ?3) RETURNING id").bind(name, trade, username),
          c.env.DB.prepare("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)").bind(username, password_hash, role),
          auditStmt(c, actor, "user_create", username, { role, via: "personnel" }),
          auditStmt(c, actor, "personnel_create", name, { name, trade, account: true, username }),
        ]);
        const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
        return c.json({ ok: true, id: newId, username, role }, 201);
      } catch (e) {
        // Lost the check-then-act race on users.username (UNIQUE) → 409, not a bubbled 500
        // (mirror /api/admin/users). The pre-check above is the cheap path; this is the backstop.
        if (isUniqueViolation(e)) return c.json({ error: "exists" }, 409);
        throw e;
      }
    },
  );

  // POST /api/fieldops/personnel/:id/update — edit name/trade (NOT the account link).
  app.post(
    "/api/fieldops/personnel/:id/update",
    gates.requireSession,
    gates.requireCapability("cap.personnel.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);
      const name = typeof body.name === "string" ? body.name.trim() : "";
      const trade = typeof body.trade === "string" && body.trade.trim() !== "" ? body.trade.trim() : null;
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (trade !== null && trade.length > MAX_SHORT) return c.json({ error: "invalid_trade" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE personnel SET name = ?2, trade = ?3 WHERE id = ?1 AND active = 1").bind(id, name, trade),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "personnel_update", String(id), JSON.stringify({ personnel_id: id, name })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // POST /api/fieldops/personnel/:id/link — link the roster row to an EXISTING login account.
  app.post(
    "/api/fieldops/personnel/:id/link",
    gates.requireSession,
    gates.requireCapability("cap.personnel.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);
      const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
      if (!username) return c.json({ error: "invalid_username" }, 400);

      // The link is a SOFT string (schema 0014: no FK). Per the operator decision, REJECT a link to
      // a non-existent account so we never persist a dangling reference. The account-existence test
      // lives INSIDE the UPDATE (… AND EXISTS(SELECT 1 FROM users …)) so check + write are ONE atomic
      // statement — an account deleted concurrently can't leave a dangling link (closes the W5 race).
      // On 0 changes we disambiguate deterministically: personnel row still present → it's the account
      // that's missing (422); no personnel row → bad id (404). 422 = well-formed username, no such
      // account (vs 400 = malformed username, handled above).
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("UPDATE personnel SET username = ?2 WHERE id = ?1 AND active = 1 AND EXISTS (SELECT 1 FROM users WHERE username = ?2)")
          .bind(id, username),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "personnel_link", String(id), JSON.stringify({ personnel_id: id, username })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        const row = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1 AND active = 1").bind(id).first();
        return row ? c.json({ error: "unknown_account" }, 422) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id, username }, 200);
    },
  );

  // POST /api/fieldops/personnel/:id/unlink — drop the account link (username → NULL). Idempotent
  // on an already-unlinked active row (the UPDATE still matches the row → 200).
  app.post(
    "/api/fieldops/personnel/:id/unlink",
    gates.requireSession,
    gates.requireCapability("cap.personnel.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE personnel SET username = NULL WHERE id = ?1 AND active = 1").bind(id),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "personnel_unlink", String(id), JSON.stringify({ personnel_id: id })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // POST /api/fieldops/personnel/:id/retire — SOFT-retire (active=0). Idempotent; preserves history
  // (time_entries.personnel_id keeps its target). Mirrors the equipment roster retire.
  app.post(
    "/api/fieldops/personnel/:id/retire",
    gates.requireSession,
    gates.requireCapability("cap.personnel.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?1 AND active = 1").bind(id),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "personnel_retire", String(id), JSON.stringify({ personnel_id: id })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // 0 changes = unknown id (404) or already-retired (idempotent 200).
        const row = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1").bind(id).first();
        return row ? c.json({ ok: true, id, already_retired: true }, 200) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id }, 200);
    },
  );
}
