import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob, seedPersonnel } from "./helpers";
import { buildSubmissionInsert } from "../worker/submission";

// ─────────────────────────────────────────────────────────────────────────────
// #17 (Seam A) — checklist/inspection completion → weekly progress report.
// POST /api/fieldops/checklist/instance/:id/submit: a COMPLETE assigned inspection's assignee signs
// off and the Worker synthesizes a category:'progress' `checklist-completion-v1` submission that
// rides the EXISTING intake → progress-week-sheet → weekly-compile pipeline (a standard submission,
// NOT a new §51 SoR write-route). Runs against the REAL worker with a Miniflare D1 (migrations incl.
// 0041 auto-apply). The feature flag is a Worker var — set on env for the live tests (default
// "false"). What this locks:
//   - DARK by default (flag off → 400 progress_logging_disabled, never-silent);
//   - flag ON + complete + owned + signed → 201, a submissions row with form_code
//     'checklist-completion-v1' + the right values shape + a VALID 5-field HMAC + emitted marker;
//   - idempotent (a second submit → 409, no duplicate submission);
//   - ownership 403; not-complete 400; job/date-missing 400; missing signature 400.
// ─────────────────────────────────────────────────────────────────────────────

const HMAC_SECRET = "test-hmac-payload-secret"; // == HMAC_PAYLOAD_SECRET in vitest.config.ts
const FORM_CODE = "checklist-completion-v1";

function setFlag(v: boolean) {
  (env as unknown as { CHECKLIST_PROGRESS_LOGGING_ENABLED: string }).CHECKLIST_PROGRESS_LOGGING_ENABLED = v
    ? "true"
    : "false";
}

/** Recompute the canonical submission HMAC exactly as buildSubmissionInsert does. */
async function canonicalHmac(p: {
  submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string;
}): Promise<string> {
  const message = [p.submission_uuid, p.job_id, p.form_code, p.work_date, p.payload_json].join("\n");
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(HMAC_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

let admin: string, sam: string, otto: string;
let personId: number;
let tplCounter = 0;

async function createTemplate(title: string): Promise<number> {
  const res = await post(admin, "/api/fieldops/checklist/inspection", { title });
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
}

/** Create a fresh single-item inspection template, assign it (optional job/date), and — unless
 *  `complete:false` — complete every item as the assignee so the instance reaches 'complete'. */
async function makeInstance(
  opts: { job?: string | null; date?: string | null; complete?: boolean; assignee?: number; assigneeCookie?: string } = {},
): Promise<number> {
  const job = opts.job === undefined ? "JOB-A" : opts.job;
  const date = opts.date === undefined ? "2026-07-01" : opts.date;
  const assignee = opts.assignee ?? personId;
  const cookie = opts.assigneeCookie ?? sam;
  const complete = opts.complete ?? true;

  const tplId = await createTemplate(`Site walk ${++tplCounter}`);
  const addRes = await post(admin, `/api/fieldops/checklist/inspection/${tplId}/item`, {
    item_type: "manual_attest",
    label: "Walk the site",
  });
  expect(addRes.status, await addRes.clone().text()).toBe(201);

  const assignRes = await post(admin, "/api/fieldops/checklist/assign", {
    template_id: tplId,
    assignee_personnel_id: assignee,
    ...(job ? { job_id: job } : {}),
    ...(date ? { due_date: date } : {}),
  });
  expect(assignRes.status, await assignRes.clone().text()).toBe(201);
  const instanceId = ((await assignRes.json()) as { instance_id: number }).instance_id;

  if (complete) {
    const assigned = (await (await get(cookie, "/api/fieldops/checklist/assigned")).json()) as {
      inspections: { instance: { id: number }; items: { id: number }[] }[];
    };
    const insp = assigned.inspections.find((i) => i.instance.id === instanceId)!;
    for (const it of insp.items) {
      const r = await post(cookie, `/api/fieldops/checklist/item-state/${it.id}/complete`, {});
      expect(r.status, await r.clone().text()).toBe(200);
    }
  }
  return instanceId;
}

beforeEach(async () => {
  tplCounter = 0; // deterministic template titles per test (each test's first makeInstance → "Site walk 1")
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM checklist_item_states"),
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
  await provision("sub.otto", "password123", "submitter");
  admin = await login("admin.one", "password123");
  sam = await login("sub.sam", "password123");
  otto = await login("sub.otto", "password123");
  await seedJob("JOB-A");
  personId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
  await seedPersonnel("Otto Other", "sub.otto", "JOB-A"); // a DIFFERENT assignee for the ownership test
  setFlag(true); // most tests exercise the live feature; the dark test flips it off.
});

describe("emit route — dark gate", () => {
  it("DARK by default: a valid signed complete inspection is refused 400 progress_logging_disabled, nothing filed", async () => {
    setFlag(false);
    const instanceId = await makeInstance();
    const res = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 5 5" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("progress_logging_disabled");
    // No submission was created, no marker stamped.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions").first<{ n: number }>())!.n).toBe(0);
    const inst = await env.DB.prepare("SELECT emitted_submission_uuid FROM checklist_instances WHERE id=?").bind(instanceId).first<{ emitted_submission_uuid: string | null }>();
    expect(inst!.emitted_submission_uuid).toBeNull();
  });
});

describe("emit route — happy path", () => {
  it("201: files a checklist-completion-v1 submission with the right values shape + a valid HMAC, stamps the marker", async () => {
    const instanceId = await makeInstance();
    const signature = "M 1 2 L 3 4 L 5 6";
    const res = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature });
    expect(res.status, await res.clone().text()).toBe(201);
    const body = (await res.json()) as { ok: boolean; submission_uuid: string };
    expect(body.ok).toBe(true);
    expect(body.submission_uuid).toBeTruthy();

    // Exactly one submission, under the emit form_code, attributed to the assignee.
    const row = await env.DB
      .prepare("SELECT submission_uuid, job_id, form_code, work_date, payload_json, hmac, box_verified, actor_username, submitted_as FROM submissions WHERE form_code=?")
      .bind(FORM_CODE)
      .first<{ submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string; hmac: string; box_verified: number; actor_username: string; submitted_as: string }>();
    expect(row!.submission_uuid).toBe(body.submission_uuid);
    expect(row!.job_id).toBe("JOB-A");
    expect(row!.work_date).toBe("2026-07-01");
    expect(row!.box_verified).toBe(0);
    expect(row!.actor_username).toBe("sub.sam");
    expect(row!.submitted_as).toBe("sub.sam");

    // Values shape: header + item roster + signature + signed_at + envelope work_date.
    const values = JSON.parse(row!.payload_json) as {
      checklist_title: string; assignee_name: string; job_id: string; work_date: string;
      items: { label: string; status: string; note: string | null }[]; signature: string; signed_at: number;
    };
    expect(values.checklist_title).toBe("Site walk 1");
    expect(values.assignee_name).toBe("Sam Sub");
    expect(values.job_id).toBe("JOB-A");
    expect(values.work_date).toBe("2026-07-01");
    expect(values.signature).toBe(signature);
    expect(typeof values.signed_at).toBe("number");
    expect(values.items).toEqual([{ label: "Walk the site", status: "done", note: null }]);

    // The stored HMAC is the canonical 5-field signature (verifiable Mac-side).
    const expected = await canonicalHmac({
      submission_uuid: row!.submission_uuid,
      job_id: row!.job_id,
      form_code: row!.form_code,
      work_date: row!.work_date,
      payload_json: row!.payload_json,
    });
    expect(row!.hmac).toBe(expected);

    // The one-shot marker + signature/time columns are stamped on the instance.
    const inst = await env.DB
      .prepare("SELECT emitted_submission_uuid, completion_signature, completion_signed_at FROM checklist_instances WHERE id=?")
      .bind(instanceId)
      .first<{ emitted_submission_uuid: string; completion_signature: string; completion_signed_at: number }>();
    expect(inst!.emitted_submission_uuid).toBe(body.submission_uuid);
    expect(inst!.completion_signature).toBe(signature);
    expect(inst!.completion_signed_at).toBeGreaterThan(0);

    // Audited.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_completion_submit'").first<{ n: number }>())!.n).toBe(1);
  });

  it("marks the instance progress_logged in the assignee's /checklist/assigned read", async () => {
    const instanceId = await makeInstance();
    await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 1 1" });
    const assigned = (await (await get(sam, "/api/fieldops/checklist/assigned")).json()) as {
      inspections: { instance: { id: number; progress_logged: boolean } }[];
    };
    const insp = assigned.inspections.find((i) => i.instance.id === instanceId)!;
    expect(insp.instance.progress_logged).toBe(true);
  });
});

describe("emit route — idempotency", () => {
  it("a second submit → 409 already_submitted, and NO duplicate submission", async () => {
    const instanceId = await makeInstance();
    const first = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 2 2" });
    expect(first.status).toBe(201);
    const firstUuid = ((await first.json()) as { submission_uuid: string }).submission_uuid;

    const second = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 9 9 L 8 8" });
    expect(second.status).toBe(409);
    const secondBody = (await second.json()) as { error: string; submission_uuid: string };
    expect(secondBody.error).toBe("already_submitted");
    expect(secondBody.submission_uuid).toBe(firstUuid); // reports the winner

    // Still exactly one submission for this form_code (no dup).
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions WHERE form_code=?").bind(FORM_CODE).first<{ n: number }>())!.n).toBe(1);
  });

  // The concurrency-strand fix (portal-worker-security BLOCK): the GUARDED buildSubmissionInsert must
  // write ZERO rows when the instance already emitted, so a race loser leaves no stranded duplicate.
  // Tested at the statement level because the local Miniflare harness serializes route dispatch (a
  // true cross-isolate race isn't reproducible), so the route-level idempotency test above only
  // exercises the sequential fast-path — this pins the batch guard's SQL directly.
  it("the GUARDED insert writes ZERO rows once the instance has emitted (no stranded duplicate on a lost race)", async () => {
    const instanceId = await makeInstance();
    const secret = "test-hmac-payload-secret";
    const mk = (uuid: string, x: number) =>
      buildSubmissionInsert(
        env.DB,
        secret,
        { submission_uuid: uuid, job_id: "JOB-A", form_code: FORM_CODE, work_date: "2026-07-01", values: { x }, actor: "sub.sam" },
        { guardInstanceNotEmitted: instanceId },
      );
    // marker NULL → the winner's guarded INSERT fires.
    expect(((await (await mk("u1", 1)).run()).meta.changes ?? 0)).toBe(1);
    // simulate the winner having stamped the marker.
    await env.DB.prepare("UPDATE checklist_instances SET emitted_submission_uuid='u1' WHERE id=?").bind(instanceId).run();
    // marker NOT NULL → the loser's guarded INSERT writes ZERO rows (the fix).
    expect(((await (await mk("u2", 2)).run()).meta.changes ?? 0)).toBe(0);
    // exactly one submission (u1) exists — never the loser's u2.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions WHERE form_code=?").bind(FORM_CODE).first<{ n: number }>())!.n).toBe(1);
  });
});

// The manual-forgery gate (form-definition-reviewer BLOCK): checklist-completion* is synthesized-only
// — a client must NOT be able to hand-file it through the general /api/submit flow (which does no
// ownership / complete / one-shot / dark-gate checks).
describe("/api/submit — synthesized-only rejection (#17)", () => {
  it("a client POSTing form_code=checklist-completion-v1 to /api/submit is 403 forbidden_synthesized, nothing filed", async () => {
    const res = await post(sam, "/api/submit", {
      job_id: "JOB-A",
      form_code: FORM_CODE,
      work_date: "2026-07-01",
      submission_uuid: crypto.randomUUID(),
      values: { checklist_title: "forged", assignee_name: "Sam Sub", items: [], signature: "forged" },
    });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_synthesized");
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions").first<{ n: number }>())!.n).toBe(0);
  });
});

describe("emit route — guards", () => {
  it("ownership: a DIFFERENT user cannot sign another's inspection (403)", async () => {
    const instanceId = await makeInstance({ assignee: personId, assigneeCookie: sam });
    const res = await post(otto, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 1 1" });
    expect(res.status).toBe(403);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions").first<{ n: number }>())!.n).toBe(0);
  });

  it("not-complete: an OPEN inspection is refused 400 not_complete", async () => {
    const instanceId = await makeInstance({ complete: false });
    const res = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 1 1" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("not_complete");
  });

  it("job/date missing: a complete inspection with no job+date is refused 400 job_and_date_required", async () => {
    const instanceId = await makeInstance({ job: null, date: null });
    const res = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "M 0 0 L 1 1" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("job_and_date_required");
  });

  it("missing/empty signature: 400 signature_required, nothing filed", async () => {
    const instanceId = await makeInstance();
    expect((await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, {})).status).toBe(400);
    const empty = await post(sam, `/api/fieldops/checklist/instance/${instanceId}/submit`, { signature: "" });
    expect(empty.status).toBe(400);
    expect(((await empty.json()) as { error: string }).error).toBe("signature_required");
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM submissions").first<{ n: number }>())!.n).toBe(0);
  });

  it("unknown / non-inspection id → 404", async () => {
    expect((await post(sam, "/api/fieldops/checklist/instance/999999/submit", { signature: "M 0 0 L 1 1" })).status).toBe(404);
  });
});
