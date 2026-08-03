import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, p as j, seedJob as seedJobRow } from "./helpers";
import { jobFolderKey } from "../worker/fieldops_job_write";

// ROADMAP Track 6 — POST /api/fieldops/job/:job_id/{archive,unarchive}.
//
// These routes record INTENT; the Mac-side pass performs the relocation and reports back. Every
// assertion below is about that separation holding: a refusal must write nothing, an in-flight
// request must not be reset by a second click, and the confirmation must be a real server-side
// control rather than a modal a second browser tab can walk around.

async function createOk(cookie: string, body: Record<string, unknown>): Promise<string> {
  const withCc = "safety_cc" in body ? body : { ...body, safety_cc: ["cc@x.com"] };
  const res = await j(cookie, "/api/fieldops/job", withCc);
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { job_id: string }).job_id;
}
async function jobRow(jobId: string) {
  return await env.DB.prepare("SELECT * FROM jobs WHERE job_id=?").bind(jobId).first<any>();
}
async function audits(action: string) {
  return (await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[];
}
/** Force a terminal / in-flight archive state without going through the daemon. */
async function setArchive(jobId: string, state: string, direction: string) {
  await env.DB
    .prepare("UPDATE jobs SET archive_state=?2, archive_direction=?3 WHERE job_id=?1")
    .bind(jobId, state, direction)
    .run();
}

const NAME = "Bradley Solar - Block C";
let admin: string, manager: string, submitter: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS job_counter (id INTEGER PRIMARY KEY CHECK (id = 1), last_value INTEGER NOT NULL)"),
    env.DB.prepare("INSERT OR REPLACE INTO job_counter (id, last_value) VALUES (1, 16)"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("manager.mia", "password123", "manager");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("manager.mia", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("job archive - the gate", () => {
  it("anon 401 / submitter 403 / MANAGER 403 / admin 200", async () => {
    const id = await createOk(admin, { project_name: NAME });
    const body = JSON.stringify({ confirm: NAME });
    expect((await call(`/api/fieldops/job/${id}/archive`, { method: "POST", body })).status).toBe(401);
    expect((await j(submitter, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(403);
    // A manager holds the day-to-day job caps but NOT cap.job.archive — the whole reason
    // archiving got its own capability instead of riding cap.jobtracker.manage.
    expect((await j(manager, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(403);
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(200);
  });

  it("THE GATE IS REAL - revoking the grant makes the same admin 403 immediately", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await env.DB
      .prepare("DELETE FROM role_capabilities WHERE role_key='admin' AND capability_key='cap.job.archive'")
      .run();
    // Capabilities resolve per-request from D1, so this bites without a re-login.
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(403);
    await env.DB
      .prepare("INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES ('admin','cap.job.archive')")
      .run();
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(200);
  });
});

describe("job archive - body shape (the 'audit #1' class)", () => {
  it("null / array / missing / non-string / oversize confirm all 400, and write NOTHING", async () => {
    const id = await createOk(admin, { project_name: NAME });
    const before = await jobRow(id);

    const bodies = ["null", "[]", "{}", JSON.stringify({ confirm: 123 }), JSON.stringify({ confirm: "x".repeat(300) })];
    for (const body of bodies) {
      const res = await call(`/api/fieldops/job/${id}/archive`, {
        method: "POST", cookie: admin, headers: { "content-type": "application/json" }, body,
      });
      expect(res.status, body).toBe(400);
    }
    const after = await jobRow(id);
    expect(after.archive_state).toBe(before.archive_state);
    expect(after.mirror_version).toBe(before.mirror_version);
    expect((await audits("job_archive")).length).toBe(0);
  });

  it("an unparseable body is 400 bad_request, not a bare 500", async () => {
    const id = await createOk(admin, { project_name: NAME });
    const res = await call(`/api/fieldops/job/${id}/archive`, {
      method: "POST", cookie: admin, headers: { "content-type": "application/json" }, body: "{oops",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("bad_request");
  });
});

describe("job archive - the typed confirmation is a SERVER-side control", () => {
  it("a wrong name is 409 confirm_mismatch and leaves D1 completely untouched", async () => {
    const id = await createOk(admin, { project_name: NAME });
    const before = await jobRow(id);

    const res = await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: "Some Other Job" });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("confirm_mismatch");

    const after = await jobRow(id);
    expect(after.lifecycle).toBe(before.lifecycle);
    expect(after.archive_state).toBe("none");
    expect(after.archive_direction).toBe("");
    expect(after.mirror_version).toBe(before.mirror_version);
    expect((await audits("job_archive")).length).toBe(0);
  });

  it("is case-SENSITIVE but tolerates surrounding whitespace", async () => {
    const id = await createOk(admin, { project_name: NAME });
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME.toLowerCase() })).status).toBe(409);
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: `  ${NAME}  ` })).status).toBe(200);
  });
});

describe("job archive - origin fence", () => {
  it("REFUSES a smartsheet-origin job even with the correct confirmation", async () => {
    await seedJobRow("SS-9", { status: "active", projectName: "Legacy Job" });
    const res = await j(admin, "/api/fieldops/job/SS-9/archive", { confirm: "Legacy Job" });
    expect(res.status).toBe(404);
    const row = await jobRow("SS-9");
    expect(row.archive_state).toBe("none"); // untouched - the down-sync owns this row
  });
});

describe("job archive - the state machine", () => {
  it("archive: none to requested, stamps the folder key, and audits ONCE", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await env.DB.prepare("UPDATE jobs SET sync_state='synced' WHERE job_id=?").bind(id).run();

    const res = await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME });
    expect(res.status).toBe(200);

    const row = await jobRow(id);
    expect(row.archive_state).toBe("requested");
    expect(row.archive_direction).toBe("archive");
    expect(row.archive_requested_at).toBeGreaterThan(0);
    expect(row.archive_completed_at).toBeNull();
    expect(row.archive_attempts).toBe(0);
    // Snapshotted so a later /contacts rename cannot strand the daemon.
    expect(row.archive_folder_key).toBe(jobFolderKey(NAME));
    expect(row.lifecycle).toBe("archived");
    expect(row.active).toBe(0);
    expect(row.sync_state).toBe("pending"); // re-dirtied so the Active-Jobs cell follows
    expect((await audits("job_archive")).length).toBe(1);
  });

  it("a double-click while REQUESTED is an idempotent 200 that does NOT re-stamp or re-audit", async () => {
    const id = await createOk(admin, { project_name: NAME });
    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(200);
    const first = await jobRow(id);

    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(200);
    const second = await jobRow(id);

    // Re-raising would reset attempts and re-stamp requested_at UNDER the daemon mid-relocation.
    expect(second.archive_requested_at).toBe(first.archive_requested_at);
    expect(second.mirror_version).toBe(first.mirror_version);
    expect((await audits("job_archive")).length).toBe(1);
  });

  it("the OPPOSITE direction while in flight is 409 archive_in_flight", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME });
    const res = await j(admin, `/api/fieldops/job/${id}/unarchive`, { confirm: NAME });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("archive_in_flight");
  });

  it("archiving an already-COMPLETE archive is 409 already_archived", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await setArchive(id, "complete", "archive");
    const res = await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("already_archived");
  });

  it("un-archiving a job that was never archived is 409 not_archived", async () => {
    const id = await createOk(admin, { project_name: NAME });
    const res = await j(admin, `/api/fieldops/job/${id}/unarchive`, { confirm: NAME });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("not_archived");
  });

  it("un-archive returns the job to INACTIVE, never active", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await setArchive(id, "complete", "archive");

    expect((await j(admin, `/api/fieldops/job/${id}/unarchive`, { confirm: NAME })).status).toBe(200);

    const row = await jobRow(id);
    expect(row.archive_state).toBe("requested");
    expect(row.archive_direction).toBe("unarchive");
    // Retrieving folders is NOT re-opening the job: auto-activating would silently re-add it to
    // every dropdown and both weekly compiles as a side effect of a folder move.
    expect(row.lifecycle).toBe("inactive");
    expect(row.active).toBe(0);
    expect((await audits("job_unarchive")).length).toBe(1);
  });

  it("a PARTIAL archive is retryable and clears the stale attempt counter + report", async () => {
    const id = await createOk(admin, { project_name: NAME });
    await env.DB
      .prepare("UPDATE jobs SET archive_state='partial', archive_direction='archive', archive_attempts=3, archive_detail='stale' WHERE job_id=?")
      .bind(id).run();

    expect((await j(admin, `/api/fieldops/job/${id}/archive`, { confirm: NAME })).status).toBe(200);

    const row = await jobRow(id);
    expect(row.archive_state).toBe("requested");
    expect(row.archive_attempts).toBe(0);  // the operator's "Try again" clears the streak
    expect(row.archive_detail).toBe("");   // and the stale per-container report
  });
});

describe("jobFolderKey - lockstep with safety_naming.job_folder_name", () => {
  it("maps '/' to '-', trims, and falls back when sanitizing empties the name", () => {
    expect(jobFolderKey("Bradley 1")).toBe("Bradley 1");
    expect(jobFolderKey("  Coker  ")).toBe("Coker");
    expect(jobFolderKey("A/B")).toBe("A-B");
    // A name that sanitizes to nothing falls back to the raw stripped input rather than
    // silently producing a key that would collide with every other empty-sanitizing name.
    expect(jobFolderKey("    ")).toBe("");
  });

  it("strips control characters the same way the Python side does", () => {
    const withControl = "Cok" + String.fromCharCode(7) + "er";
    expect(jobFolderKey(withControl)).toBe("Coker");
  });
});
