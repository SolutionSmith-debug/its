import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob, seedPersonnel } from "./helpers";
import { materializeDueInstances, generateRecurringChecklists, type RecurrenceRow } from "../worker/fieldops_recurrence";

// ─────────────────────────────────────────────────────────────────────────────
// Recurring checklists per job (#16) — the D1 generation ENGINE + the Worker routes.
//   - materializeDueInstances / generateRecurringChecklists spawn kind='inspection'
//     instances on cadence (idempotent via the existing UNIQUE key + watermark).
//   - POST /checklist/assign (recurring branch) DEFINES a recurrence — DARK-gated by
//     RECURRING_CHECKLISTS_ENABLED; cap.checklist.manage.
//   - GET /checklist/recurrences + POST /recurrence/:id/deactivate (flag-independent
//     admin visibility + stop).
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0040 auto-apply).
// The feature flag is a Worker var: set on env for the enabled tests (default "false").
// ─────────────────────────────────────────────────────────────────────────────

function setFlag(v: boolean) {
  (env as unknown as { RECURRING_CHECKLISTS_ENABLED: string }).RECURRING_CHECKLISTS_ENABLED = v ? "true" : "false";
}

async function createTemplate(cookie: string, title: string): Promise<number> {
  const res = await post(cookie, "/api/fieldops/checklist/inspection", { title });
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
}
async function addItem(cookie: string, tplId: number, item: Record<string, unknown>): Promise<void> {
  const res = await post(cookie, `/api/fieldops/checklist/inspection/${tplId}/item`, item);
  expect(res.status, await res.clone().text()).toBe(201);
}

async function seedRecurrence(
  templateId: number,
  assigneeId: number,
  jobId: string,
  cadence: string,
  anchor: string,
  lastGen: string | null = null,
  title = "Rec",
  active = 1,
): Promise<number> {
  await env.DB.prepare(
    "INSERT INTO checklist_recurrences (template_id, assignee_personnel_id, job_id, cadence, anchor_date, active, last_generated_date, template_title) VALUES (?,?,?,?,?,?,?,?)",
  )
    .bind(templateId, assigneeId, jobId, cadence, anchor, active, lastGen, title)
    .run();
  return (await env.DB.prepare("SELECT id FROM checklist_recurrences ORDER BY id DESC LIMIT 1").first<{ id: number }>())!.id;
}
async function loadRec(id: number): Promise<RecurrenceRow> {
  return (await env.DB
    .prepare(
      "SELECT id, template_id, assignee_personnel_id, job_id, cadence, anchor_date, active, last_generated_date, template_title FROM checklist_recurrences WHERE id=?",
    )
    .bind(id)
    .first<RecurrenceRow>())!;
}
const countInstances = async (personId: number): Promise<number> =>
  (await env.DB.prepare("SELECT COUNT(*) c FROM checklist_instances WHERE kind='inspection' AND assignee_personnel_id=?").bind(personId).first<{ c: number }>())!.c;

let admin: string, submitter: string;
let personId: number, templateId: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_recurrences"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind='generic_inspection')"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind='generic_inspection'"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  await seedJob("JOB-CLOSED", { active: 0 });
  personId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
  templateId = await createTemplate(admin, "Site walk");
  await addItem(admin, templateId, { item_type: "manual_attest", label: "Walk the site" });
  await addItem(admin, templateId, { item_type: "count", label: "Extinguishers", target_count: 2 });
  setFlag(true); // most tests exercise the live feature; the dark-default test flips it off.
});

describe("engine — materializeDueInstances", () => {
  it("spawns an instance (with snapshotted item-states) for each on-cadence date, and advances the watermark", async () => {
    const recId = await seedRecurrence(templateId, personId, "JOB-A", "daily", "2026-07-01", null, "Site walk");
    const res = await materializeDueInstances(env.DB, await loadRec(recId), "2026-07-03");
    expect(res.created).toBe(3); // 07-01, 07-02, 07-03
    expect(await countInstances(personId)).toBe(3);
    // Each instance snapshotted the template's 2 items → 6 states.
    expect((await env.DB.prepare("SELECT COUNT(*) c FROM checklist_item_states").first<{ c: number }>())!.c).toBe(6);
    // Watermark advanced through today.
    expect((await env.DB.prepare("SELECT last_generated_date d FROM checklist_recurrences WHERE id=?").bind(recId).first<{ d: string }>())!.d).toBe("2026-07-03");
    // Each spawn is audited.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_recurrence_generate'").first<{ n: number }>())!.n).toBe(3);
  });

  it("is IDEMPOTENT — re-running the same day creates nothing new (no double-spawn)", async () => {
    const recId = await seedRecurrence(templateId, personId, "JOB-A", "daily", "2026-07-01", null);
    await materializeDueInstances(env.DB, await loadRec(recId), "2026-07-03");
    const again = await materializeDueInstances(env.DB, await loadRec(recId), "2026-07-03");
    expect(again.created).toBe(0);
    expect(await countInstances(personId)).toBe(3);
  });

  it("only spawns dates AFTER the watermark on a later day (catch-up window)", async () => {
    const recId = await seedRecurrence(templateId, personId, "JOB-A", "daily", "2026-07-01", "2026-07-03");
    const res = await materializeDueInstances(env.DB, await loadRec(recId), "2026-07-05");
    expect(res.created).toBe(2); // 07-04, 07-05 only
    expect(await countInstances(personId)).toBe(2);
  });
});

describe("engine — generateRecurringChecklists (the cron pass)", () => {
  it("materializes every active recurrence and reports a summary", async () => {
    await seedRecurrence(templateId, personId, "JOB-A", "weekly", "2026-07-01", null);
    const summary = await generateRecurringChecklists(env.DB, Date.UTC(2026, 6, 20, 16, 0, 0)); // 2026-07-20 Pacific
    expect(summary.recurrences).toBe(1);
    expect(summary.instances_created).toBe(3); // 07-01, 07-08, 07-15
    expect(summary.errors).toBe(0);
    expect(await countInstances(personId)).toBe(3);
  });

  it("auto-STOPS a recurrence whose job has closed (job inactive → active=0 + audit)", async () => {
    const recId = await seedRecurrence(templateId, personId, "JOB-CLOSED", "daily", "2026-07-01", null);
    const summary = await generateRecurringChecklists(env.DB, Date.UTC(2026, 6, 5, 16, 0, 0));
    expect(summary.autostopped).toBe(1);
    expect(summary.instances_created).toBe(0);
    expect((await env.DB.prepare("SELECT active FROM checklist_recurrences WHERE id=?").bind(recId).first<{ active: number }>())!.active).toBe(0);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_recurrence_autostop'").first<{ n: number }>())!.n).toBe(1);
  });
});

describe("route — POST /checklist/assign (recurring branch)", () => {
  it("DARK by default: a recurrence block is refused 400 recurring_disabled + no row written", async () => {
    setFlag(false);
    const res = await post(admin, "/api/fieldops/checklist/assign", {
      template_id: templateId,
      assignee_personnel_id: personId,
      job_id: "JOB-A",
      recurrence: { cadence: "daily", anchor_date: "2026-07-01" },
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("recurring_disabled");
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_recurrences").first<{ n: number }>())!.n).toBe(0);
  });

  it("flag ON: defines a per-job recurrence (201) with the right cadence/anchor + audit", async () => {
    const res = await post(admin, "/api/fieldops/checklist/assign", {
      template_id: templateId,
      assignee_personnel_id: personId,
      job_id: "JOB-A",
      recurrence: { cadence: "weekly", anchor_date: "2026-07-01" },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const body = (await res.json()) as { ok: boolean; recurrence_id: number; instances_created: number };
    expect(body.recurrence_id).toBeGreaterThan(0);
    const row = await env.DB
      .prepare("SELECT cadence, anchor_date, job_id, active, template_title FROM checklist_recurrences WHERE id=?")
      .bind(body.recurrence_id)
      .first<{ cadence: string; anchor_date: string; job_id: string; active: number; template_title: string }>();
    expect(row).toMatchObject({ cadence: "weekly", anchor_date: "2026-07-01", job_id: "JOB-A", active: 1, template_title: "Site walk" });
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_recurrence_define'").first<{ n: number }>())!.n).toBe(1);
  });

  it("flag ON: re-defining the same (template, person, job) UPSERTS (no duplicate) + reactivates", async () => {
    const first = await post(admin, "/api/fieldops/checklist/assign", {
      template_id: templateId, assignee_personnel_id: personId, job_id: "JOB-A",
      recurrence: { cadence: "daily", anchor_date: "2026-07-01" },
    });
    const id1 = ((await first.json()) as { recurrence_id: number }).recurrence_id;
    const second = await post(admin, "/api/fieldops/checklist/assign", {
      template_id: templateId, assignee_personnel_id: personId, job_id: "JOB-A",
      recurrence: { cadence: "monthly", anchor_date: "2026-08-01" },
    });
    const id2 = ((await second.json()) as { recurrence_id: number }).recurrence_id;
    expect(id2).toBe(id1); // same row, upserted
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_recurrences").first<{ n: number }>())!.n).toBe(1);
    const row = await env.DB.prepare("SELECT cadence, anchor_date FROM checklist_recurrences WHERE id=?").bind(id1).first<{ cadence: string; anchor_date: string }>();
    expect(row).toMatchObject({ cadence: "monthly", anchor_date: "2026-08-01" });
  });

  it("flag ON: validates cadence, anchor format, and required job", async () => {
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: templateId, assignee_personnel_id: personId, job_id: "JOB-A", recurrence: { cadence: "hourly", anchor_date: "2026-07-01" } })).status).toBe(400);
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: templateId, assignee_personnel_id: personId, job_id: "JOB-A", recurrence: { cadence: "daily", anchor_date: "07/01/2026" } })).status).toBe(400);
    // job_id is REQUIRED for a recurrence.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: templateId, assignee_personnel_id: personId, recurrence: { cadence: "daily", anchor_date: "2026-07-01" } })).status).toBe(422);
    // a non-recurring assign still works unchanged (no recurrence block).
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: templateId, assignee_personnel_id: personId })).status).toBe(201);
  });
});

describe("route — GET /recurrences + POST /recurrence/:id/deactivate (flag-independent)", () => {
  it("lists active defs (cap-gated), and deactivate STOPS one (idempotent 404 after)", async () => {
    const recId = await seedRecurrence(templateId, personId, "JOB-A", "daily", "2026-07-01", "2026-07-04", "Site walk");
    // cap gating — submitter lacks cap.checklist.manage.
    expect((await get(submitter, "/api/fieldops/checklist/recurrences")).status).toBe(403);
    expect((await post(submitter, `/api/fieldops/checklist/recurrence/${recId}/deactivate`)).status).toBe(403);

    const list = (await (await get(admin, "/api/fieldops/checklist/recurrences")).json()) as {
      recurrences: { id: number; cadence: string; assignee_name: string; project_name: string }[];
    };
    expect(list.recurrences).toHaveLength(1);
    expect(list.recurrences[0]).toMatchObject({ id: recId, cadence: "daily", assignee_name: "Sam Sub", project_name: "Project JOB-A" });

    expect((await post(admin, `/api/fieldops/checklist/recurrence/${recId}/deactivate`)).status).toBe(200);
    expect(((await (await get(admin, "/api/fieldops/checklist/recurrences")).json()) as { recurrences: unknown[] }).recurrences).toHaveLength(0);
    // Second deactivate → 404 (already inactive).
    expect((await post(admin, `/api/fieldops/checklist/recurrence/${recId}/deactivate`)).status).toBe(404);
  });
});
