import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { get, json, provision, login, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// CS4 #12 (waterfall collapse) — GET /api/fieldops/tasks/mine now carries the caller's OWN
// placement (`viewer_placement`: job_id + project_name + personnel_id + name), so the Daily tab
// no longer downloads a full Job Tracker list page to learn where its viewer is placed.
//
// SECURITY CONTRACT under test (the route's header note): cap.tasks.own returns
// SELF-INFORMATION ONLY — the viewer's own roster row and own placement, resolved strictly from
// the session username. Nothing about any other account/person/job may ride the shape.
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

interface MinePayload {
  tasks: unknown[];
  linked: boolean;
  viewer_placement: {
    job_id: string;
    project_name: string | null;
    personnel_id: number;
    name: string;
  } | null;
}

const MINE = "/api/fieldops/tasks/mine";

let manager: string, submitter: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("manager.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  manager = await login("manager.mo", "password123");
  submitter = await login("sub.sam", "password123");
  await seedJob("JOB-A", { projectName: "Alpha" });
  await seedJob("JOB-B", { projectName: "Bravo" });
});

describe("GET /api/fieldops/tasks/mine — viewer_placement (CS4 #12)", () => {
  it("a linked+placed viewer gets their OWN placement: job, project name, own roster id + name", async () => {
    const pid = await seedPersonnel("Mo Manager", "manager.mo", "JOB-A");
    const res = await get(manager, MINE);
    expect(res.status).toBe(200);
    const body = await json<MinePayload>(res);
    expect(body.linked).toBe(true);
    expect(body.viewer_placement).toEqual({
      job_id: "JOB-A",
      project_name: "Alpha",
      personnel_id: pid,
      name: "Mo Manager",
    });
  });

  it("linked but UNPLACED → linked:true with viewer_placement null (the two are distinct signals)", async () => {
    await seedPersonnel("Mo Manager", "manager.mo", null);
    const body = await json<MinePayload>(await get(manager, MINE));
    expect(body.linked).toBe(true);
    expect(body.viewer_placement).toBeNull();
  });

  it("an UNLINKED session → linked:false + viewer_placement null (not an error)", async () => {
    const res = await get(manager, MINE);
    expect(res.status).toBe(200);
    const body = await json<MinePayload>(res);
    expect(body.linked).toBe(false);
    expect(body.viewer_placement).toBeNull();
  });

  it("a RETIRED (active=0) roster link does not count: linked:false, no placement leak", async () => {
    await seedPersonnel("Mo Manager", "manager.mo", "JOB-A", { active: 0 });
    const body = await json<MinePayload>(await get(manager, MINE));
    expect(body.linked).toBe(false);
    expect(body.viewer_placement).toBeNull();
  });

  it("SELF-ONLY: another person's placement never rides the response (no cross-user exposure)", async () => {
    await seedPersonnel("Mo Manager", "manager.mo", "JOB-A");
    await seedPersonnel("Sam Sub", "sub.sam", "JOB-B");
    const mgrBody = await json<MinePayload>(await get(manager, MINE));
    expect(mgrBody.viewer_placement?.job_id).toBe("JOB-A");
    expect(mgrBody.viewer_placement?.name).toBe("Mo Manager");
    const subBody = await json<MinePayload>(await get(submitter, MINE));
    expect(subBody.viewer_placement?.job_id).toBe("JOB-B");
    expect(subBody.viewer_placement?.name).toBe("Sam Sub");
    // Neither response mentions the OTHER viewer's row anywhere.
    expect(JSON.stringify(mgrBody)).not.toContain("Sam Sub");
    expect(JSON.stringify(subBody)).not.toContain("Mo Manager");
  });

  it("a placement naming a VANISHED job (soft ref) still serves job_id, with project_name null", async () => {
    await seedPersonnel("Mo Manager", "manager.mo", "JOB-GONE");
    const body = await json<MinePayload>(await get(manager, MINE));
    expect(body.viewer_placement?.job_id).toBe("JOB-GONE");
    expect(body.viewer_placement?.project_name).toBeNull();
  });

  it("two active rows carrying the username resolve DETERMINISTICALLY to the lowest id (the resolveActorPersonnel pick)", async () => {
    const first = await seedPersonnel("Mo Manager", "manager.mo", "JOB-A");
    await seedPersonnel("Mo Duplicate", "manager.mo", "JOB-B");
    const body = await json<MinePayload>(await get(manager, MINE));
    expect(body.viewer_placement?.personnel_id).toBe(first);
    expect(body.viewer_placement?.job_id).toBe("JOB-A");
  });
});
