import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, get, post, seedJob, seedPersonnel, json } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// G1 Slice 1 — checklist item-photo CAPTURE + pending queue (Option D RATIFIED: record-only;
// no serving route ever; delete-on-screen).
//   POST /api/fieldops/checklist/item-state/:id/photo
//     • session + cap.tasks.own + assignee-ownership (loadOwnedItemState — 404/403 matrix)
//     • the VERBATIM validatePhotoValues per-photo bounds (magic / ≤400KB decoded / base64
//       shape / meta caps) behind the single derived body bound (413)
//     • ONE photo per item state: 409 while pending/clean; refused vacates the slot (retry)
//     • HMAC-signed like submissions (canonical: "item_photo:v1"\n<item_state_id>\n<photo_json>)
//     • W4: queue INSERT + photo_ref='pending:<id>' stamp + audit row in ONE atomic batch
//   GET /checklist/assigned now carries photo_status (pending|clean|refused|null) per item.
//   Instance cancel cascades item_photos (no orphaned photo bytes).
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply) — the same harness as
// fieldops-checklist-lifecycle.test.ts.
// ─────────────────────────────────────────────────────────────────────────────

// Test photo payloads — tiny REAL magic-numbered byte strings (the gate sniffs the first 4
// decoded bytes; it never fully decodes an image).
function b64(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes));
}
const JPEG_B64 = b64([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49]);
const PNG_B64 = b64([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]);
const BAD_MAGIC_B64 = b64([0x00, 0x01, 0x02, 0x03, 0x04, 0x05]);

function photo(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { data: JPEG_B64, name: "site.jpg", taken_at: "", gps: "", ...over };
}

const upload = (cookie: string, stateId: number | string, body: unknown): Promise<Response> =>
  post(cookie, `/api/fieldops/checklist/item-state/${stateId}/photo`, body);

// The Worker-side HMAC recompute (same WebCrypto path) so the stored signature is verified
// byte-for-byte against the documented canonical string.
async function hmacHexTest(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

interface PhotoRow {
  id: number;
  item_state_id: number;
  status: string;
  photo_json: string | null;
  hmac: string;
  box_file_id: string | null;
  created_at: number;
  screened_at: number | null;
}
async function photoRows(): Promise<PhotoRow[]> {
  const r = await env.DB.prepare("SELECT * FROM item_photos ORDER BY id").all<PhotoRow>();
  return r.results;
}
async function itemStateRow(id: number): Promise<{ photo_ref: string | null }> {
  return (await env.DB.prepare("SELECT photo_ref FROM checklist_item_states WHERE id=?1").bind(id).first<{ photo_ref: string | null }>())!;
}
async function auditRows(action: string): Promise<{ detail: string | null }[]> {
  const r = await env.DB.prepare("SELECT detail FROM audit_log WHERE action=?1 ORDER BY id").bind(action).all<{ detail: string | null }>();
  return r.results;
}

interface AssignedItem {
  id: number;
  item_type: string;
  label: string | null;
  status: string;
  photo_ref: string | null;
  photo_status: string | null;
}
interface AssignedResp {
  inspections: { instance: { id: number }; items: AssignedItem[] }[];
}
async function assignedItems(cookie: string): Promise<AssignedItem[]> {
  const res = await get(cookie, "/api/fieldops/checklist/assigned");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await json<AssignedResp>(res)).inspections.flatMap((i) => i.items);
}

let admin: string, manager: string, sub: string;
let subPersonId: number;
let instanceId: number;
let manualStateId: number;
let countStateId: number;
let linkedStateId: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM item_photos"),
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind IN ('job_override','generic_inspection'))"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind IN ('job_override','generic_inspection')"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("mgr.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("mgr.mo", "password123");
  sub = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  subPersonId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");

  // A generic_inspection template with all three completion classes, assigned to sub.sam.
  const tplRes = await post(admin, "/api/fieldops/checklist/inspection", { title: "Site photo checks" });
  expect(tplRes.status).toBe(201);
  const tplId = (await json<{ id: number }>(tplRes)).id;
  for (const item of [
    { item_type: "manual_attest", label: "Harness photo" },
    { item_type: "count", label: "Anchors", target_count: 2 },
    { item_type: "form_linked", label: "File the daily", form_code: "daily-report" },
  ]) {
    expect((await post(admin, `/api/fieldops/checklist/inspection/${tplId}/item`, item)).status).toBe(201);
  }
  const asgRes = await post(admin, "/api/fieldops/checklist/assign", {
    template_id: tplId, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2099-07-10",
  });
  expect(asgRes.status, await asgRes.clone().text()).toBe(201);
  instanceId = (await json<{ instance_id: number }>(asgRes)).instance_id;

  const items = await assignedItems(sub);
  manualStateId = items.find((i) => i.item_type === "manual_attest")!.id;
  countStateId = items.find((i) => i.item_type === "count")!.id;
  linkedStateId = items.find((i) => i.item_type === "form_linked")!.id;
});

describe("G1 photo upload — bounds matrix (the verbatim validatePhotoValues gate)", () => {
  it("accepts a JPEG (201) — and a PNG on a fresh item", async () => {
    const res = await upload(sub, manualStateId, { photo: photo() });
    expect(res.status, await res.clone().text()).toBe(201);
    const body = await json<{ ok: boolean; photo_id: number; photo_status: string; photo_ref: string }>(res);
    expect(body.ok).toBe(true);
    expect(body.photo_status).toBe("pending");
    expect(body.photo_ref).toBe(`pending:${body.photo_id}`);

    const png = await upload(sub, countStateId, { photo: photo({ data: PNG_B64, name: "site.png" }) });
    expect(png.status, await png.clone().text()).toBe(201);
  });

  it("rejects bad magic → 400 invalid_photo/photo_bad_magic", async () => {
    const res = await upload(sub, manualStateId, { photo: photo({ data: BAD_MAGIC_B64 }) });
    expect(res.status).toBe(400);
    expect(await json<{ error: string; detail: string }>(res)).toEqual({ error: "invalid_photo", detail: "photo_bad_magic" });
  });

  it("rejects >400,000 decoded bytes → 400 invalid_photo/photo_too_large", async () => {
    // 533,340 base64 chars → 400,005 decoded bytes (just over the cap), valid charset + %4.
    const res = await upload(sub, manualStateId, { photo: photo({ data: "A".repeat(533_340) }) });
    expect(res.status).toBe(400);
    expect((await json<{ detail: string }>(res)).detail).toBe("photo_too_large");
  });

  it("rejects non-base64 data → 400 invalid_photo/photo_not_base64 (charset AND length%4)", async () => {
    for (const bad of ["not-b64!", "abc"]) {
      const res = await upload(sub, manualStateId, { photo: photo({ data: bad }) });
      expect(res.status).toBe(400);
      expect((await json<{ detail: string }>(res)).detail).toBe("photo_not_base64");
    }
  });

  it("rejects oversized meta → 400 invalid_photo/photo_meta_too_long (name/taken_at/gps caps)", async () => {
    for (const over of [{ name: "n".repeat(101) }, { taken_at: "t".repeat(41) }, { gps: "g".repeat(65) }]) {
      const res = await upload(sub, manualStateId, { photo: photo(over) });
      expect(res.status).toBe(400);
      expect((await json<{ detail: string }>(res)).detail).toBe("photo_meta_too_long");
    }
  });

  it("rejects a malformed photo shape → 400 invalid_photo/invalid_photo_shape (missing/extra keys, non-object)", async () => {
    for (const bad of [
      { data: JPEG_B64, name: "x.jpg", taken_at: "" }, // missing gps
      photo({ extra: "key" }), // 5th key
      "just-a-string",
      [photo()], // array — one photo per request, never a list
      null,
    ]) {
      const res = await upload(sub, manualStateId, { photo: bad });
      expect(res.status, JSON.stringify(bad).slice(0, 40)).toBe(400);
      expect((await json<{ detail?: string; error: string }>(res)).error).toBe("invalid_photo");
    }
  });

  it("rejects a body over the single derived bound → 413 photo_upload_too_large (before JSON.parse)", async () => {
    const res = await upload(sub, manualStateId, { photo: photo({ data: "A".repeat(560_004) }) });
    expect(res.status).toBe(413);
    expect((await json<{ error: string }>(res)).error).toBe("photo_upload_too_large");
    expect(await photoRows()).toHaveLength(0); // nothing queued
  });

  it("rejects a form_linked item → 400 photo_not_supported (its evidence is the filed submission)", async () => {
    const res = await upload(sub, linkedStateId, { photo: photo() });
    expect(res.status).toBe(400);
    expect((await json<{ error: string }>(res)).error).toBe("photo_not_supported");
  });
});

describe("G1 photo upload — ownership matrix (loadOwnedItemState contract)", () => {
  it("401 with no session", async () => {
    const res = await post("", `/api/fieldops/checklist/item-state/${manualStateId}/photo`, { photo: photo() });
    expect(res.status).toBe(401);
  });

  it("403 for a linked user who is NOT the instance assignee", async () => {
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const res = await upload(manager, manualStateId, { photo: photo() });
    expect(res.status).toBe(403);
  });

  it("403 for a session with NO linked personnel (admin included — capability alone never grants)", async () => {
    const res = await upload(admin, manualStateId, { photo: photo() });
    expect(res.status).toBe(403);
  });

  it("404 for an unknown item state; 400 for a non-numeric id", async () => {
    expect((await upload(sub, 999_999, { photo: photo() })).status).toBe(404);
    expect((await upload(sub, "abc", { photo: photo() })).status).toBe(400);
  });

  it("bounds are checked BEFORE ownership resolves, but never leak existence (bad photo on a foreign item → 400)", async () => {
    // A bounds 400 on a foreign item is fine (no row data returned); the success path is what
    // must be ownership-gated — locked by the 403 cases above.
    const res = await upload(manager, manualStateId, { photo: photo({ data: BAD_MAGIC_B64 }) });
    expect(res.status).toBe(400);
  });
});

describe("G1 photo upload — the one-photo rule (pending/clean block; refused vacates)", () => {
  it("a second upload while PENDING → 409 photo_already_attached", async () => {
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    const res = await upload(sub, manualStateId, { photo: photo() });
    expect(res.status).toBe(409);
    expect((await json<{ error: string }>(res)).error).toBe("photo_already_attached");
    expect(await photoRows()).toHaveLength(1); // no second row
  });

  it("a second upload while CLEAN → 409 (the photo is on file — one photo per item)", async () => {
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    await env.DB.prepare("UPDATE item_photos SET status='clean', photo_json=NULL, screened_at=?1").bind(1_700_000_000).run();
    const res = await upload(sub, manualStateId, { photo: photo() });
    expect(res.status).toBe(409);
  });

  it("after REFUSED the slot is vacated → retry allowed (fresh pending row; refused marker kept)", async () => {
    const first = await upload(sub, manualStateId, { photo: photo() });
    const firstId = (await json<{ photo_id: number }>(first)).photo_id;
    await env.DB.prepare("UPDATE item_photos SET status='refused', photo_json=NULL, screened_at=?1 WHERE id=?2")
      .bind(1_700_000_000, firstId)
      .run();
    const retry = await upload(sub, manualStateId, { photo: photo({ name: "retake.jpg" }) });
    expect(retry.status, await retry.clone().text()).toBe(201);
    const retryId = (await json<{ photo_id: number }>(retry)).photo_id;
    expect(retryId).not.toBe(firstId);
    const rows = await photoRows();
    expect(rows.map((r) => [r.id, r.status])).toEqual([
      [firstId, "refused"],
      [retryId, "pending"],
    ]);
    // photo_ref re-stamped to the NEW pending photo.
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`pending:${retryId}`);
  });

  it("the partial unique index enforces the rule structurally (a direct second pending INSERT fails)", async () => {
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    await expect(
      env.DB.prepare("INSERT INTO item_photos (item_state_id, status, photo_json, hmac) VALUES (?1,'pending','{}','h')")
        .bind(manualStateId)
        .run(),
    ).rejects.toThrow(/UNIQUE/i);
  });
});

describe("G1 photo upload — W4 atomicity + HMAC + the stored record", () => {
  it("one batch: queue row + photo_ref stamp + audit — and the HMAC verifies against the documented canonical string", async () => {
    const res = await upload(sub, manualStateId, { photo: photo({ taken_at: "2026-07-03T10:00", gps: "33.5,-117.7" }) });
    expect(res.status, await res.clone().text()).toBe(201);
    const { photo_id } = await json<{ photo_id: number }>(res);

    // The queue row: pending, bytes + meta + AUTHENTICATED uploader in photo_json.
    const rows = await photoRows();
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe(photo_id);
    expect(rows[0].item_state_id).toBe(manualStateId);
    expect(rows[0].status).toBe("pending");
    expect(rows[0].box_file_id).toBeNull();
    expect(rows[0].screened_at).toBeNull();
    const stored = JSON.parse(rows[0].photo_json!) as Record<string, string>;
    expect(stored).toEqual({
      data: JPEG_B64, name: "site.jpg", taken_at: "2026-07-03T10:00", gps: "33.5,-117.7",
      uploaded_by: "sub.sam", // from the SESSION, not the client body
    });

    // The HMAC: "item_photo:v1" \n <item_state_id> \n <photo_json>, HMAC-SHA256 hex, same secret
    // as submissions (vitest binding HMAC_PAYLOAD_SECRET).
    const expected = await hmacHexTest(
      "test-hmac-payload-secret",
      `item_photo:v1\n${manualStateId}\n${rows[0].photo_json}`,
    );
    expect(rows[0].hmac).toBe(expected);

    // The same-batch photo_ref stamp.
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`pending:${photo_id}`);

    // The same-batch audit row — natural tuple + sizes, NEVER the bytes.
    const audits = await auditRows("checklist_item_photo_add");
    expect(audits).toHaveLength(1);
    const detail = JSON.parse(audits[0].detail!) as Record<string, unknown>;
    expect(detail.item_state_id).toBe(manualStateId);
    expect(detail.instance_id).toBe(instanceId);
    expect(detail.photo_name).toBe("site.jpg");
    expect(detail.decoded_bytes).toBeGreaterThan(0);
    expect(audits[0].detail).not.toContain(JPEG_B64);
  });

  it("a rejected upload leaves NO trace (no queue row, no ref, no audit) — the 409 path", async () => {
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    const before = await photoRows();
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(409);
    expect(await photoRows()).toEqual(before); // unchanged
    expect(await auditRows("checklist_item_photo_add")).toHaveLength(1); // still just the first
  });
});

describe("G1 — photo_status in the /checklist/assigned read", () => {
  it("carries the lifecycle: null → pending → refused → (retry) pending; clean reads clean", async () => {
    let items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBeNull();

    const up = await upload(sub, manualStateId, { photo: photo() });
    const { photo_id } = await json<{ photo_id: number }>(up);
    items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBe("pending");

    await env.DB.prepare("UPDATE item_photos SET status='refused', photo_json=NULL WHERE id=?1").bind(photo_id).run();
    items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBe("refused");

    // Retry: the LATEST row wins the read.
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBe("pending");

    await env.DB.prepare("UPDATE item_photos SET status='clean', photo_json=NULL WHERE status='pending'").run();
    items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBe("clean");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G1 Slice 2 — the internal screening-queue routes the Mac portal_poll
// _service_item_photos pass drives. Bearer gate = requireInternalToken (the SAME
// middleware instance as GET /api/internal/pending).
//   GET  /api/internal/item-photos/pending     — pending-only, oldest-first, photo_json + hmac
//   POST /api/internal/item-photos/:id/result  — ONE atomic batch (W4): photo_ref flip +
//        disposition + photo_json=NULL (DELETE-ON-SCREEN) + changes()-gated audit;
//        idempotent (found:false no-op); item COMPLETION untouched.
// ─────────────────────────────────────────────────────────────────────────────
const INTERNAL_BEARER = "test-internal-token"; // == PORTAL_INTERNAL_API_TOKEN

const pendingGet = (init: Parameters<typeof call>[1] = {}): Promise<Response> =>
  call("/api/internal/item-photos/pending", init);
const postResult = (
  id: number | string, body: unknown, bearer: string = INTERNAL_BEARER,
): Promise<Response> =>
  call(`/api/internal/item-photos/${id}/result`, {
    method: "POST",
    ...(bearer === "" ? {} : { bearer }),
    body: JSON.stringify(body),
  });

describe("G1 S2 — GET /api/internal/item-photos/pending", () => {
  it("401 without the internal bearer (and with a wrong one)", async () => {
    expect((await pendingGet()).status).toBe(401);
    expect((await pendingGet({ bearer: "wrong-token" })).status).toBe(401);
  });

  it("serves ONLY pending rows (photo_json + hmac riding), oldest-first", async () => {
    const up1 = await upload(sub, manualStateId, { photo: photo() });
    const id1 = (await json<{ photo_id: number }>(up1)).photo_id;
    const up2 = await upload(sub, countStateId, { photo: photo({ data: PNG_B64, name: "b.png" }) });
    const id2 = (await json<{ photo_id: number }>(up2)).photo_id;
    // Make row 1 older than row 2, then screen row 2 out of the queue.
    await env.DB.prepare("UPDATE item_photos SET created_at = created_at - 100 WHERE id=?1").bind(id1).run();
    await env.DB.prepare("UPDATE item_photos SET status='clean', photo_json=NULL WHERE id=?1").bind(id2).run();
    // A refused marker row is excluded too.
    await env.DB.prepare(
      "INSERT INTO item_photos (item_state_id, status, photo_json, hmac) VALUES (?1,'refused',NULL,'h')",
    ).bind(linkedStateId).run();

    const res = await pendingGet({ bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const { item_photos } = await json<{ item_photos: PhotoRow[] }>(res);
    expect(item_photos.map((r) => r.id)).toEqual([id1]);
    expect(item_photos[0].item_state_id).toBe(manualStateId);
    expect(typeof item_photos[0].photo_json).toBe("string");
    expect(item_photos[0].hmac).toMatch(/^[0-9a-f]{64}$/);
  });

  it("orders oldest-first and honors the limit param", async () => {
    for (const [i, sid] of [manualStateId, countStateId].entries()) {
      const up = await upload(sub, sid, { photo: photo({ name: `p${i}.jpg` }) });
      expect(up.status).toBe(201);
    }
    // Reverse the natural order via created_at: make the SECOND row older.
    const rows = await photoRows();
    await env.DB.prepare("UPDATE item_photos SET created_at = created_at - 500 WHERE id=?1")
      .bind(rows[1].id)
      .run();
    const res = await pendingGet({ bearer: INTERNAL_BEARER });
    const { item_photos } = await json<{ item_photos: PhotoRow[] }>(res);
    expect(item_photos.map((r) => r.id)).toEqual([rows[1].id, rows[0].id]);

    const limited = await call("/api/internal/item-photos/pending?limit=1", { bearer: INTERNAL_BEARER });
    const page = await json<{ item_photos: PhotoRow[] }>(limited);
    expect(page.item_photos.map((r) => r.id)).toEqual([rows[1].id]);
  });
});

describe("G1 S2 — POST /api/internal/item-photos/:id/result — validation matrix", () => {
  let photoId: number;
  beforeEach(async () => {
    const up = await upload(sub, manualStateId, { photo: photo() });
    photoId = (await json<{ photo_id: number }>(up)).photo_id;
  });

  it("401 without the internal bearer (and with a wrong one)", async () => {
    expect((await postResult(photoId, { status: "clean", box_file_id: "b1" }, "")).status).toBe(401);
    expect((await postResult(photoId, { status: "clean", box_file_id: "b1" }, "wrong-token")).status).toBe(401);
    expect((await photoRows())[0].status).toBe("pending"); // nothing applied
  });

  it("400 on a bad id / bad body / unknown status", async () => {
    expect((await postResult("abc", { status: "clean", box_file_id: "b1" })).status).toBe(400);
    expect((await postResult(0, { status: "clean", box_file_id: "b1" })).status).toBe(400);
    expect((await postResult(photoId, "not-an-object")).status).toBe(400);
    expect((await postResult(photoId, null)).status).toBe(400);
    for (const status of ["pending", "CLEAN", "", 7]) {
      const res = await postResult(photoId, { status });
      expect(res.status, String(status)).toBe(400);
      expect((await json<{ detail: string }>(res)).detail).toBe("status");
    }
  });

  it("400 clean-without-box_file_id; 400 refused-with-box_file_id (tight contract)", async () => {
    const noBox = await postResult(photoId, { status: "clean" });
    expect(noBox.status).toBe(400);
    expect((await json<{ detail: string }>(noBox)).detail).toBe("box_file_id_required");

    const withBox = await postResult(photoId, { status: "refused", box_file_id: "b1" });
    expect(withBox.status).toBe(400);
    expect((await json<{ detail: string }>(withBox)).detail).toBe("box_file_id_forbidden");

    // Nothing applied by either rejection.
    expect((await photoRows())[0].status).toBe("pending");
  });

  it("unknown id → { ok:true, found:false } (mark_filed's benign-no-op semantics)", async () => {
    const res = await postResult(999_999, { status: "clean", box_file_id: "b1" });
    expect(res.status).toBe(200);
    expect(await json<{ ok: boolean; found: boolean }>(res)).toMatchObject({ ok: true, found: false });
  });
});

describe("G1 S2 — result application (W4 batch, delete-on-screen, completion stands)", () => {
  it("CLEAN: one batch → status flip + photo_json IS NULL + box_file_id + screened_at + photo_ref + audit", async () => {
    const up = await upload(sub, manualStateId, { photo: photo() });
    const photoId = (await json<{ photo_id: number }>(up)).photo_id;

    const res = await postResult(photoId, { status: "clean", box_file_id: "box-42" });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await json<{ found: boolean }>(res)).toMatchObject({ ok: true, found: true });

    const row = (await photoRows())[0];
    expect(row.status).toBe("clean");
    expect(row.photo_json).toBeNull(); // DELETE-ON-SCREEN: the bytes left D1
    expect(row.box_file_id).toBe("box-42");
    expect(row.screened_at).not.toBeNull();
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`clean:${photoId}`);

    const audits = await auditRows("checklist_item_photo_result");
    expect(audits).toHaveLength(1);
    const detail = JSON.parse(audits[0].detail!) as Record<string, unknown>;
    expect(detail).toMatchObject({
      item_photo_id: photoId, item_state_id: manualStateId, status: "clean", box_file_id: "box-42",
    });
    // The tripwire byte-check: no photo bytes anywhere after disposition.
    expect(audits[0].detail).not.toContain(JPEG_B64);
  });

  it("REFUSED: photo_ref 'refused:<id>', bytes deleted, detail audited — and the item COMPLETION STANDS", async () => {
    // Complete the item FIRST (evidence refused ≠ work not done).
    const done = await post(sub, `/api/fieldops/checklist/item-state/${manualStateId}/complete`, { note: "done on site" });
    expect(done.status, await done.clone().text()).toBe(200);
    const up = await upload(sub, manualStateId, { photo: photo() });
    const photoId = (await json<{ photo_id: number }>(up)).photo_id;

    const res = await postResult(photoId, { status: "refused", detail: "L1:magic_mismatch" });
    expect(res.status).toBe(200);
    expect(await json<{ found: boolean }>(res)).toMatchObject({ ok: true, found: true });

    const row = (await photoRows())[0];
    expect(row.status).toBe("refused");
    expect(row.photo_json).toBeNull(); // delete-on-screen applies to refusals too
    expect(row.box_file_id).toBeNull();
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`refused:${photoId}`);

    // Completion untouched: still done, still the crew's completion record.
    const st = await env.DB.prepare(
      "SELECT status, completed_by, note FROM checklist_item_states WHERE id=?1",
    ).bind(manualStateId).first<{ status: string; completed_by: string | null; note: string | null }>();
    expect(st!.status).toBe("done");
    expect(st!.completed_by).toBe("sub.sam");
    expect(st!.note).toBe("done on site");

    const audits = await auditRows("checklist_item_photo_result");
    const detail = JSON.parse(audits[0].detail!) as Record<string, unknown>;
    expect(detail).toMatchObject({ status: "refused", detail: "L1:magic_mismatch" });

    // photo_status surfaces the refusal to the assignee (retry copy).
    const items = await assignedItems(sub);
    expect(items.find((i) => i.id === manualStateId)!.photo_status).toBe("refused");
  });

  it("idempotent re-post → found:false, NO second audit, row unchanged", async () => {
    const up = await upload(sub, manualStateId, { photo: photo() });
    const photoId = (await json<{ photo_id: number }>(up)).photo_id;
    expect((await postResult(photoId, { status: "clean", box_file_id: "box-42" })).status).toBe(200);
    const after = await photoRows();

    const again = await postResult(photoId, { status: "clean", box_file_id: "box-99" });
    expect(again.status).toBe(200);
    expect(await json<{ found: boolean; status: string }>(again)).toMatchObject({
      ok: true, found: false, status: "clean",
    });
    expect(await photoRows()).toEqual(after); // box_file_id NOT clobbered to box-99
    expect(await auditRows("checklist_item_photo_result")).toHaveLength(1);
  });

  it("a LATE refused re-post can never clobber a newer retry's pending ref", async () => {
    const first = await upload(sub, manualStateId, { photo: photo() });
    const firstId = (await json<{ photo_id: number }>(first)).photo_id;
    // Screening refuses the first photo (the real route, not a direct UPDATE).
    expect((await postResult(firstId, { status: "refused", detail: "L1:magic_mismatch" })).status).toBe(200);
    // The crew retries — a NEW pending photo now owns the slot + the ref.
    const retry = await upload(sub, manualStateId, { photo: photo({ name: "retake.jpg" }) });
    const retryId = (await json<{ photo_id: number }>(retry)).photo_id;
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`pending:${retryId}`);

    // A duplicate/late re-post for the OLD photo is a structural no-op.
    const late = await postResult(firstId, { status: "refused", detail: "late-duplicate" });
    expect(await json<{ found: boolean }>(late)).toMatchObject({ ok: true, found: false });
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`pending:${retryId}`); // NOT clobbered

    // And the retry lifecycle completes normally.
    expect((await postResult(retryId, { status: "clean", box_file_id: "box-7" })).status).toBe(200);
    expect((await itemStateRow(manualStateId)).photo_ref).toBe(`clean:${retryId}`);
  });
});

describe("G1 — instance-cancel cascade covers item_photos", () => {
  it("cancelling the instance deletes its item photos with the item states (no orphaned bytes)", async () => {
    expect((await upload(sub, manualStateId, { photo: photo() })).status).toBe(201);
    expect(await photoRows()).toHaveLength(1);

    const res = await post(admin, `/api/fieldops/checklist/instance/${instanceId}/cancel`);
    expect(res.status, await res.clone().text()).toBe(200);

    expect(await photoRows()).toHaveLength(0);
    const states = await env.DB.prepare("SELECT COUNT(*) n FROM checklist_item_states WHERE instance_id=?1").bind(instanceId).first<{ n: number }>();
    expect(states!.n).toBe(0);
  });
});
