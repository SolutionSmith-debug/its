import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S4 — loop-closure + count / inspection completion.
//   - form_linked / inspection items AUTO-CHECK on GET /checklist/mine when a submission exists for
//     (instance.job_id, the item's form-code FAMILY, instance.instance_date) — a submission whose
//     form_code EQUALS the parent OR is a versioned variant (`parent || '-v%'`). Stays open otherwise.
//   - count items complete via /complete { value_num } iff value_num >= target_count (else 400
//     'below_target', value recorded, item open). manual_attest still works. A manual complete on a
//     form_linked/inspection item is refused (400 'auto_close_only').
//   - The instance flips to 'complete' when EVERY item — including auto-checked form_linked — is done.
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0026 auto-apply). Storage is
// isolated per-test (vitest-pool-workers) so the migration-0026 daily_default seed is fresh each test.
// ─────────────────────────────────────────────────────────────────────────────


// Insert a submission for the day (the durable record is Smartsheet/Box; this is the portal cache).
async function seedSubmission(jobId: string, formCode: string, workDate: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json) VALUES (?,?,?,?,?)",
  ).bind(`sub-${jobId}-${formCode}-${workDate}-${Math.random()}`, jobId, formCode, workDate, "{}").run();
}
// Add a job_override item on JOB-A so it appears in the placed manager's snapshot (without mutating the
// shared daily_default seed). Returns nothing — the item surfaces via the merge on the next mine().
async function addJobItem(jobId: string, type: string, label: string, formCode: string | null, targetCount: number | null): Promise<void> {
  await env.DB.prepare("INSERT OR IGNORE INTO checklist_templates (kind, job_id, active) VALUES ('job_override',?,1)").bind(jobId).run();
  const ot = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='job_override' AND job_id=?").bind(jobId).first<{ id: number }>())!.id;
  await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count) VALUES (?,999,?,?,?,?)")
    .bind(ot, type, label, formCode, targetCount).run();
}

interface ItemState { id: number; item_type: string; label: string | null; form_code: string | null; target_count: number | null; status: string; value_num: number | null; completed_by: string | null; }
interface MineResp { instance: { id: number; job_id: string; instance_date: string; status: string } | null; items: ItemState[]; }
async function mine(cookie: string): Promise<MineResp> {
  const res = await get(cookie, "/api/fieldops/checklist/mine");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as MineResp;
}

let manager: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override')"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind='job_override'"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("mgr.mo", "password123", "manager");
  manager = await login("mgr.mo", "password123");
  await seedJob("JOB-A");
  await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
});

describe("checklist S4 — form_linked loop-closure (auto-check on a matching submission)", () => {
  it("auto-checks the seeded form_linked item when a PARENT form_code submission exists (same job+date)", async () => {
    // First read materializes the instance; the seeded form_linked item (form_code 'daily-report') is open.
    const before = await mine(manager);
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    expect(fl.status).toBe("open");
    // File a submission for the exact parent form_code on the instance's job + date.
    await seedSubmission("JOB-A", "daily-report", before.instance!.instance_date);
    const after = await mine(manager);
    const fl2 = after.items.find((i) => i.id === fl.id)!;
    expect(fl2.status).toBe("done");
    expect(fl2.completed_by).toBe("(auto)");
  });

  it("auto-checks on a VERSIONED VARIANT submission (form_code 'daily-report-v1' matches parent 'daily-report')", async () => {
    const before = await mine(manager);
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    await seedSubmission("JOB-A", "daily-report-v1", before.instance!.instance_date);
    const after = await mine(manager);
    expect(after.items.find((i) => i.id === fl.id)!.status).toBe("done");
  });

  it("stays OPEN with no submission, a WRONG-date submission, or a WRONG-job submission", async () => {
    const before = await mine(manager);
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    // Wrong date + wrong job + an unrelated form family — none should close the item.
    await seedSubmission("JOB-A", "daily-report", "1999-01-01");
    await seedSubmission("JOB-B", "daily-report", before.instance!.instance_date);
    await seedSubmission("JOB-A", "jha-v3", before.instance!.instance_date);
    const after = await mine(manager);
    expect(after.items.find((i) => i.id === fl.id)!.status).toBe("open");
  });

  it("does NOT false-match a sibling family with the same prefix but no '-v' anchor", async () => {
    // A form_linked item for parent 'daily-report' must not be closed by a 'daily-report-extra' submission.
    const before = await mine(manager);
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    await seedSubmission("JOB-A", "daily-report-extra", before.instance!.instance_date);
    const after = await mine(manager);
    expect(after.items.find((i) => i.id === fl.id)!.status).toBe("open");
  });

  it("auto-checks an INSPECTION item the same way (inspection items are submissions too)", async () => {
    await addJobItem("JOB-A", "inspection", "Skid-steer pre-inspection", "equipment-skid-steer", null);
    const before = await mine(manager);
    const insp = before.items.find((i) => i.item_type === "inspection")!;
    expect(insp.status).toBe("open");
    await seedSubmission("JOB-A", "equipment-skid-steer-v1", before.instance!.instance_date);
    const after = await mine(manager);
    expect(after.items.find((i) => i.id === insp.id)!.status).toBe("done");
    expect(after.items.find((i) => i.id === insp.id)!.completed_by).toBe("(auto)");
  });

  it("the instance flips to COMPLETE once every item (incl. the auto-checked form_linked) is done", async () => {
    // Complete every seeded item WITHOUT mutating the shared daily_default seed (0028 SOP content):
    // manually complete each manual_attest item, meet each count item's target, then file the jha +
    // daily-report submissions that auto-check the two form_linked items.
    const before = await mine(manager);
    expect(before.instance!.status).toBe("open");
    for (const it of before.items.filter((i) => i.item_type === "manual_attest")) {
      expect((await post(manager, `/api/fieldops/checklist/item-state/${it.id}/complete`)).status).toBe(200);
    }
    for (const it of before.items.filter((i) => i.item_type === "count")) {
      expect((await post(manager, `/api/fieldops/checklist/item-state/${it.id}/complete`, { value_num: it.target_count ?? 1 })).status).toBe(200);
    }
    await seedSubmission("JOB-A", "jha-v3", before.instance!.instance_date);
    // Still open — the daily-report form_linked item hasn't closed yet.
    expect((await mine(manager)).instance!.status).toBe("open");
    await seedSubmission("JOB-A", "daily-report-v1", before.instance!.instance_date);
    const after = await mine(manager);
    expect(after.items.filter((i) => i.item_type === "form_linked").every((i) => i.status === "done")).toBe(true);
    expect(after.instance!.status).toBe("complete");
  });
});

describe("checklist S4 — count completion (value ≥ target)", () => {
  async function countItemId(target: number): Promise<{ id: number; instanceStatus: string }> {
    await addJobItem("JOB-A", "count", "Log deliveries", null, target);
    const body = await mine(manager);
    // Find OUR job-added count item by label — the 0028 daily_default seed carries its own count
    // items (photos target 50, check-ins target 2), so "first count item" is no longer ours.
    const it = body.items.find((i) => i.item_type === "count" && i.label === "Log deliveries")!;
    return { id: it.id, instanceStatus: body.instance!.status };
  }

  it("completes at value_num >= target_count and stores value_num", async () => {
    const { id } = await countItemId(3);
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { value_num: 5 });
    expect(res.status, await res.clone().text()).toBe(200);
    const j = (await res.json()) as { status: string; value_num: number };
    expect(j.status).toBe("done");
    expect(j.value_num).toBe(5);
    const after = await mine(manager);
    const it = after.items.find((i) => i.id === id)!;
    expect(it.status).toBe("done");
    expect(it.value_num).toBe(5);
  });

  it("completes at exactly the target (>=)", async () => {
    const { id } = await countItemId(3);
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { value_num: 3 });
    expect(res.status).toBe(200);
    expect((await mine(manager)).items.find((i) => i.id === id)!.status).toBe("done");
  });

  it("REJECTS below target (400 'below_target'), records the value, leaves the item OPEN", async () => {
    const { id } = await countItemId(3);
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { value_num: 2 });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("below_target");
    const it = (await mine(manager)).items.find((i) => i.id === id)!;
    expect(it.status).toBe("open");
    expect(it.value_num).toBe(2);
    // (W4) the below-target value write IS audited — a repeated overwrite leaves a forensic trail.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_item_value_recorded'").first<{ n: number }>())!.n).toBe(1);
  });

  it("rejects a missing / non-numeric value_num (400 'invalid_value_num')", async () => {
    const { id } = await countItemId(3);
    expect((await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, {})).status).toBe(400);
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { value_num: "lots" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("invalid_value_num");
  });

  it("un-completes a done count item back to open (clears value_num)", async () => {
    const { id } = await countItemId(3);
    expect((await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { value_num: 4 })).status).toBe(200);
    expect((await post(manager, `/api/fieldops/checklist/item-state/${id}/uncomplete`)).status).toBe(200);
    const it = (await mine(manager)).items.find((i) => i.id === id)!;
    expect(it.status).toBe("open");
    expect(it.value_num).toBeNull();
  });
});

describe("checklist S4 — manual_attest still works; auto-close types reject manual complete/uncomplete", () => {
  it("manual_attest completes with a note (S3 behavior preserved)", async () => {
    const body = await mine(manager);
    const id = body.items.find((i) => i.item_type === "manual_attest")!.id;
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { note: "walked it" });
    expect(res.status, await res.clone().text()).toBe(200);
    expect((await mine(manager)).items.find((i) => i.id === id)!.status).toBe("done");
  });

  it("a manual complete on a form_linked item → 400 'auto_close_only'", async () => {
    const body = await mine(manager);
    const id = body.items.find((i) => i.item_type === "form_linked")!.id;
    const res = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { note: "x" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("auto_close_only");
  });

  it("an uncomplete on an auto-checked form_linked item → 400 'auto_close_only' (stays done)", async () => {
    const before = await mine(manager);
    // Pick the daily-report form_linked item explicitly — the 0028 seed also carries a jha one.
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    await seedSubmission("JOB-A", "daily-report-v1", before.instance!.instance_date);
    expect((await mine(manager)).items.find((i) => i.id === fl.id)!.status).toBe("done");
    const res = await post(manager, `/api/fieldops/checklist/item-state/${fl.id}/uncomplete`);
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("auto_close_only");
    expect((await mine(manager)).items.find((i) => i.id === fl.id)!.status).toBe("done");
  });
});
