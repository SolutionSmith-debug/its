import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt, auditStmtIfChanged } from "./audit";

// Assigned-Tasks (P4) Slice T — SUBCONTRACTOR scoped crew-create (cap.crew.create; migration 0027).
//
// A subcontractor (the 'submitter' tier, display-renamed "Subcontractor") may add a NON-LOGIN roster
// person and have them AUTO-PLACED on the subcontractor's OWN current job. This is a DELIBERATELY
// narrow capability, distinct from the two fuller personnel powers held by admin/manager:
//   • cap.personnel.manage — create (incl. login-mint, admin-only) / edit / link / unlink / retire
//     ANY personnel (fieldops_personnel_write.ts). A cap.crew.create-only actor holds NONE of that.
//   • cap.crew.assign      — place ANY person on ANY active job (fieldops_crew_assign.ts). A
//     cap.crew.create actor cannot choose the job — the new person lands on the ACTOR's own job.
//
// STRICT server enforcement (Invariant 2 — the SPA gate is convenience, this is the boundary):
//   1. NON-LOGIN only. Any account/login/password/role payload key is REJECTED (400) — minting a
//      credential + assigning a role stays admin-only on the personnel-create route. No users row.
//   2. created_by = the actor's session username (provenance the time-route scoping keys on).
//   3. current_job = the ACTOR's OWN current_job, resolved session → linked personnel → current_job.
//      A caller with NO linked personnel, or a linked personnel not placed on a job → 422 not_placed.
//      The subcontractor cannot place crew anywhere but where they themselves are placed.
// Mutation + its audit_log row land in ONE atomic D1 batch (W4). Bound params only. Send-free (D1 only).

const MAX_NAME = 128;
const MAX_SHORT = 64;
const MY_CREW_CAP = 500;

// Payload keys that would (attempt to) mint a login / assign a role. Their PRESENCE on this route is
// a category error — the scoped route creates non-login crew only. Reject rather than silently ignore
// so a client can't believe it minted an account here.
const LOGIN_KEYS = ["account", "username", "password", "role"] as const;

export function registerCrewWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/crew — a subcontractor adds a NON-LOGIN roster person, auto-placed on THEIR job.
  app.post(
    "/api/fieldops/crew",
    gates.requireSession,
    gates.requireCapability("cap.crew.create"),
    async (c) => {
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);

      // (1) NON-LOGIN ONLY — reject any credential/role payload. login-mint stays admin-only.
      for (const k of LOGIN_KEYS) {
        if (body[k] !== undefined) return c.json({ error: "login_not_allowed" }, 400);
      }

      const name = typeof body.name === "string" ? body.name.trim() : "";
      const trade = typeof body.trade === "string" && body.trade.trim() !== "" ? body.trade.trim() : null;
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (trade !== null && trade.length > MAX_SHORT) return c.json({ error: "invalid_trade" }, 400);

      const actor = c.get("session").username;

      // (3) Resolve the ACTOR's own current job: session → linked ACTIVE personnel → current_job.
      // No linked personnel OR unplaced (current_job NULL) → 422 not_placed. Bound param.
      // (W5, accepted) This read-then-INSERT is a benign staleness, not a privilege issue: current_job
      // is NEVER client-supplied, so the worst case is the new crew landing on the job the actor held a
      // moment earlier — no cross-job placement, no escalation. Left as-is per review.
      const me = await c.env.DB.prepare(
        "SELECT current_job FROM personnel WHERE username = ?1 AND active = 1",
      )
        .bind(actor)
        .first<{ current_job: string | null }>();
      if (!me || me.current_job === null || me.current_job === "") {
        return c.json({ error: "not_placed" }, 422);
      }
      const currentJob = me.current_job;

      // (2)+(4) INSERT the non-login person (username NULL) stamped with created_by + the actor's job,
      // and its audit row, in ONE atomic batch. RETURNING the new id (proven pattern, index 0).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO personnel (name, trade, username, current_job, created_by) VALUES (?1, ?2, NULL, ?3, ?4) RETURNING id",
          )
          .bind(name, trade, currentJob, actor),
        auditStmt(c, actor, "crew_create", name, { name, trade, current_job: currentJob, created_by: actor }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId, current_job: currentJob }, 201);
    },
  );

  // GET /api/fieldops/crew/mine — the crew a subcontractor may log time for: their OWN linked personnel
  // OR anyone they created (created_by = them), active only. Backs the time-log person picker so a
  // subcontractor is only offered people the time-route scoping will accept. cap.crew.create-gated.
  // G2.3: created_by_me (1/0) gates the SPA's Edit/Retire controls to rows the actor CREATED (their
  // own linked row gets none) WITHOUT exposing raw created_by usernames on the wire.
  app.get(
    "/api/fieldops/crew/mine",
    gates.requireSession,
    gates.requireCapability("cap.crew.create"),
    async (c) => {
      const actor = c.get("session").username;
      const res = await c.env.DB.prepare(
        "SELECT id, name, trade, current_job, CASE WHEN created_by = ?1 THEN 1 ELSE 0 END AS created_by_me FROM personnel WHERE active = 1 AND (username = ?1 OR created_by = ?1) ORDER BY name ASC LIMIT ?2",
      )
        .bind(actor, MY_CREW_CAP)
        .all<{ id: number; name: string; trade: string | null; current_job: string | null; created_by_me: number }>();
      return c.json({ personnel: res.results ?? [] }, 200);
    },
  );

  // ── G2.3 — POST /api/fieldops/crew/:id/update — scoped crew EDIT (name/trade) ───────────────────
  // A cap.crew.create holder fixes a typo on crew THEY created (created_by = actor) while the row
  // is still active. Managers/admins keep the unrestricted cap.personnel.manage route
  // (fieldops_personnel_write.ts /personnel/:id/update) — this route never widens their power.
  // Ownership is FOLDED INTO the UPDATE's WHERE (atomic — no check-then-act), and changes()=0
  // collapses unknown-id / retired / not-created-by-me into ONE 404 (no existence oracle: a
  // subcontractor can't probe the roster through this route). SPEC.md §2.1 / §4.1.
  app.post(
    "/api/fieldops/crew/:id/update",
    gates.requireSession,
    gates.requireCapability("cap.crew.create"),
    async (c) => {
      const id = parseInt(c.req.param("id") ?? "", 10);
      if (isNaN(id)) return c.json({ error: "invalid_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);
      // Bounded fields, create-route rules. LOGIN_KEYS stay rejected — the scoped tier can never
      // touch the account link; 'name'/'trade' are the ONLY writable columns here.
      for (const k of LOGIN_KEYS) {
        if (body[k] !== undefined) return c.json({ error: "login_not_allowed" }, 400);
      }
      const name = typeof body.name === "string" ? body.name.trim() : "";
      const trade = typeof body.trade === "string" && body.trade.trim() !== "" ? body.trade.trim() : null;
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (trade !== null && trade.length > MAX_SHORT) return c.json({ error: "invalid_trade" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("UPDATE personnel SET name = ?2, trade = ?3 WHERE id = ?1 AND active = 1 AND created_by = ?4")
          .bind(id, name, trade, actor),
        auditStmtIfChanged(c, actor, "crew_update", String(id), { personnel_id: id, name, trade }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // ── G2.3 — POST /api/fieldops/crew/:id/retire — scoped crew soft-RETIRE (active=0) ──────────────
  // The typo'd-duplicate escape hatch: a cap.crew.create holder may retire crew they created IFF
  //   (a) nobody ELSE has logged time against the person (the actor's OWN entries don't block — a
  //       duplicate the sub logged time on is still theirs to retire; history keeps its target,
  //       soft-delete semantics identical to the admin retire), and
  //   (b) the person isn't placed on a DIFFERENT job than the actor (someone the office moved
  //       elsewhere is a real worker — escalate to the office).
  // Both guards are FOLDED INTO the UPDATE (atomic); changes()=0 is then disambiguated read-back
  // (the personnel-link route's pattern) into 404 not_found (unknown / not mine — no oracle),
  // 200 already_retired (idempotent, admin-retire parity), 409 crew_has_foreign_time, or
  // 409 crew_on_other_job. Admin/manager retire (role==='admin'-gated) is UNCHANGED. SPEC.md §2.2/§4.2.
  app.post(
    "/api/fieldops/crew/:id/retire",
    gates.requireSession,
    gates.requireCapability("cap.crew.create"),
    async (c) => {
      const id = parseInt(c.req.param("id") ?? "", 10);
      if (isNaN(id)) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `UPDATE personnel SET active = 0
             WHERE id = ?1 AND active = 1 AND created_by = ?2
               AND NOT EXISTS (SELECT 1 FROM time_entries WHERE personnel_id = ?1 AND actor_username != ?2)
               AND (current_job IS NULL OR current_job =
                     (SELECT current_job FROM personnel WHERE username = ?2 AND active = 1 ORDER BY id ASC LIMIT 1))`,
          )
          .bind(id, actor),
        auditStmtIfChanged(c, actor, "crew_retire", String(id), { personnel_id: id }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // Disambiguate deterministically, narrowest first. Not-owned and unknown collapse to 404.
        const row = await c.env.DB.prepare("SELECT active, current_job FROM personnel WHERE id = ?1 AND created_by = ?2")
          .bind(id, actor)
          .first<{ active: number; current_job: string | null }>();
        if (!row) return c.json({ error: "not_found" }, 404);
        if (row.active === 0) return c.json({ ok: true, id, already_retired: true }, 200);
        const foreign = await c.env.DB.prepare(
          "SELECT 1 AS x FROM time_entries WHERE personnel_id = ?1 AND actor_username != ?2 LIMIT 1",
        )
          .bind(id, actor)
          .first();
        if (foreign) return c.json({ error: "crew_has_foreign_time" }, 409);
        return c.json({ error: "crew_on_other_job" }, 409);
      }
      return c.json({ ok: true, id }, 200);
    },
  );
}
