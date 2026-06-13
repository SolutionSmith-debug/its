import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { validateDefinition } from "../worker/publishValidation";
import metaSchema from "../forms/meta-schema.json";

// ─────────────────────────────────────────────────────────────────────────────
// Photo upload PR-1 (2026-06-12) — the Worker bounds/shape gate + the HMAC lock.
//  * /api/submit accepts exact-shape photo arrays ({data,name,taken_at,gps}) and
//    rejects oversize / bad-magic / over-count / mixed arrays — never logging bytes.
//  * Table-row arrays (Record<colKey,string>[]) are NEVER misread as photos.
//  * THE LOAD-BEARING LOCK: photos ride payload_json under the UNCHANGED canonical
//    HMAC (uuid\njob\nform\ndate\npayload_json) — recomputed here byte-for-byte.
//  * publishValidation: photo fields are header-level only, max_count bounded 1..4.
// Real workerd + Miniflare D1 (no mocks), same harness as hardening.test.ts.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token";
const INTERNAL_BEARER = "test-internal-token";

type Init = RequestInit & { cookie?: string; bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
function cookieFrom(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0];
}
async function provision(username: string, password: string) {
  const res = await call("/api/internal/admin/users", {
    method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role: "submitter" }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status).toBe(200);
  return cookieFrom(res);
}

// Valid base64 with real image magic bytes. "/9j/" decodes to FF D8 FF (JPEG SOI);
// "iVBORw0KGgo" decodes to 89 50 4E 47 0D 0A 1A 0A (PNG signature).
const JPEG_B64 = "/9j/" + "A".repeat(96);
const PNG_B64 = "iVBORw0KGgoA";
const OVERSIZE_B64 = "/9j/" + "A".repeat(533_340); // decodes to ~400,008 bytes (> 400,000 cap)
const BAD_MAGIC_B64 = btoa("hello world!"); // valid base64, not an image

function photo(over: Partial<{ data: string; name: string; taken_at: string; gps: string }> = {}) {
  return {
    data: JPEG_B64, name: "site.jpg",
    taken_at: "2026-06-12T10:30:00", gps: "27.950575,-82.457178",
    ...over,
  };
}
function submitBody(values: Record<string, unknown>, submission_uuid: string) {
  return JSON.stringify({ job_id: "J1", form_code: "jha-v1", work_date: "2026-06-12", submission_uuid, values });
}
function submit(cookie: string, values: Record<string, unknown>, uuid = crypto.randomUUID()) {
  return call("/api/submit", { method: "POST", cookie, body: submitBody(values, uuid) });
}
async function hmacHex(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

let cookie = "";
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("INSERT INTO jobs (job_id, project_name, active) VALUES ('J1','Photo Test',1)"),
  ]);
  await provision("crew.lead", "pw-123456"); // usernames are first.last (normalizeUsername)
  cookie = await login("crew.lead", "pw-123456");
});

describe("/api/submit — photo bounds/shape gate", () => {
  it("accepts a photo-bearing submission (JPEG + PNG) and queues it", async () => {
    const res = await submit(cookie, { site_photos: [photo(), photo({ data: PNG_B64, name: "p2.png" })] });
    expect(res.status, await res.clone().text()).toBe(200);
  });

  it("never misreads table-row arrays as photo arrays", async () => {
    const res = await submit(cookie, {
      site_photos: [photo()],
      crew: [{ name: "Bob", task: "dig" }, { name: "Ana", task: "rig" }],
    });
    expect(res.status, await res.clone().text()).toBe(200);
  });

  it("rejects non-image bytes (magic check) with the machine reason, never the bytes", async () => {
    const res = await submit(cookie, { site_photos: [photo({ data: BAD_MAGIC_B64 })] });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body).toEqual({ error: "invalid_photo", detail: "photo_bad_magic" });
  });

  it("rejects an oversize photo (decoded > 400KB)", async () => {
    const res = await submit(cookie, { site_photos: [photo({ data: OVERSIZE_B64 })] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("photo_too_large");
  });

  it("rejects malformed base64", async () => {
    const res = await submit(cookie, { site_photos: [photo({ data: "not base64!!" })] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("photo_not_base64");
  });

  it("rejects more than 4 photos in one field", async () => {
    const res = await submit(cookie, { site_photos: [photo(), photo(), photo(), photo(), photo()] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("too_many_photos_in_field");
  });

  it("rejects more than 8 photos per submission across fields", async () => {
    const four = () => [photo(), photo(), photo(), photo()];
    const res = await submit(cookie, { a: four(), b: four(), c: [photo()] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("too_many_photos");
  });

  it("rejects a mixed photo array (partial photo shapes)", async () => {
    const res = await submit(cookie, { site_photos: [photo(), { data: JPEG_B64 }] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("mixed_photo_array");
  });

  it("rejects over-long sidecar metadata", async () => {
    const res = await submit(cookie, { site_photos: [photo({ name: "x".repeat(101) })] });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toBe("photo_meta_too_long");
  });

  it("payload cap: 1.2MB now ACCEPTED (raised for photos), 1.9MB still 413", async () => {
    expect((await submit(cookie, { notes: "x".repeat(1_200_000) })).status).toBe(200);
    expect((await submit(cookie, { notes: "x".repeat(1_900_000) })).status).toBe(413);
  });
});

describe("photos ride payload_json under the UNCHANGED canonical HMAC", () => {
  it("pending row round-trips the photos verbatim and the stored hmac recomputes byte-for-byte", async () => {
    const uuid = crypto.randomUUID();
    const photos = [photo(), photo({ data: PNG_B64, name: "p2.png", gps: "" })];
    expect((await submit(cookie, { site_photos: photos, notes: "trench north wall" }, uuid)).status).toBe(200);

    const res = await call("/api/internal/pending", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const { pending } = (await res.json()) as {
      pending: Array<{ submission_uuid: string; payload_json: string; hmac: string }>;
    };
    const row = pending.find((r) => r.submission_uuid === uuid);
    expect(row).toBeDefined();

    // Verbatim round-trip: the photo objects come back exactly as submitted.
    const values = JSON.parse(row!.payload_json) as { site_photos: unknown };
    expect(values.site_photos).toEqual(photos);

    // The canonical is UNCHANGED (uuid\njob\nform\ndate\npayload_json) — the exact
    // invariant shared/portal_hmac.py recomputes on the Mac side. Photos inside
    // payload_json are therefore integrity-covered with zero signing changes.
    const canonical = [uuid, "J1", "jha-v1", "2026-06-12", row!.payload_json].join("\n");
    expect(row!.hmac).toBe(await hmacHex(env.HMAC_PAYLOAD_SECRET, canonical));
  });
});

describe("publishValidation — photo fields in form definitions", () => {
  const photoProbe = () => ({
    form_code: "photo-probe-v1",
    parent_form_code: "photo-probe",
    form_name: "Photo Probe",
    variant_label: null,
    version: 1,
    archetype: "rows_signatures",
    source_pdf: "probe.pdf",
    sections: [
      {
        type: "header",
        fields: [
          { key: "site_photos", label: "Site photos", input: "photo", max_count: 3 },
          { key: "supervisor_signature", label: "Supervisor signature", input: "signature" },
        ],
      },
    ],
  });
  const ctx = { identity: "photo-probe", parentFormCode: "photo-probe" };

  it("accepts a header-level photo field with a bounded max_count", () => {
    expect(validateDefinition(photoProbe(), ctx)).toEqual({ ok: true });
  });

  it("rejects a photo field as a table column (header-level only)", () => {
    const def = photoProbe();
    (def.sections as Record<string, unknown>[]).push({
      type: "repeating_table",
      key: "crew",
      columns: [{ key: "snap", label: "Snap", input: "photo" }],
    });
    const r = validateDefinition(def, ctx);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain("header-level only");
  });

  it("rejects an out-of-bounds max_count", () => {
    const def = photoProbe();
    ((def.sections[0] as Record<string, unknown>).fields as Record<string, unknown>[])[0].max_count = 9;
    expect(validateDefinition(def, ctx).ok).toBe(false);
  });

  it("rejects max_count on a non-photo field", () => {
    const def = photoProbe();
    const fields = (def.sections[0] as Record<string, unknown>).fields as Record<string, unknown>[];
    fields.push({ key: "foreman", label: "Foreman", input: "text", max_count: 2 });
    expect(validateDefinition(def, ctx).ok).toBe(false);
  });
});

// ── INPUTS / meta-schema parity (kills the three-copies drift class) ──────────────
// PR-1 (#271) added "photo" to the meta-schema enum + this Worker's INPUTS but NOT to the
// SPA's editorValidation copy (fixed: derived from FIELD_INPUTS). publishValidation.INPUTS is
// module-private, so this asserts the property behaviorally: every input the meta-schema
// (the JSON-side source of truth) permits is ACCEPTED as a header field by validateDefinition.
// If someone adds an input to meta-schema.json but forgets the Worker's INPUTS, this fails.
describe("publishValidation accepts every meta-schema input enum member (parity)", () => {
  const SCHEMA_INPUTS = (
    metaSchema as { $defs: { input: { enum: string[] } } }
  ).$defs.input.enum;
  const ctx = { identity: "input-probe", parentFormCode: "input-probe" };

  function defWithHeaderInput(input: string) {
    const field: Record<string, unknown> = { key: "probe_field", label: "Probe", input };
    if (input === "select") field.options = ["A"];
    // Carry a signature field so the required-content legal floor
    // (defaults_for_new_identities.required_signature_inputs_min = 1) is satisfied — this
    // isolates the test to INPUT-TYPE acceptance, not the unrelated signature floor.
    const sig = { key: "probe_signature", label: "Signature", input: "signature" };
    return {
      form_code: "input-probe-v1",
      parent_form_code: "input-probe",
      form_name: "Input Probe",
      variant_label: null,
      version: 1,
      archetype: "sectioned_assessment",
      source_pdf: "probe.pdf",
      sections: [{ type: "header", fields: [field, sig] }],
    };
  }

  it("has a non-empty enum (sanity)", () => {
    expect(SCHEMA_INPUTS.length).toBeGreaterThan(0);
    expect(SCHEMA_INPUTS).toContain("photo");
  });

  it.each(SCHEMA_INPUTS)("validateDefinition accepts a header field with input '%s'", (input) => {
    expect(validateDefinition(defWithHeaderInput(input), ctx)).toEqual({ ok: true });
  });

  it("rejects an input NOT in the meta-schema enum (negative control)", () => {
    expect(validateDefinition(defWithHeaderInput("bogus"), ctx).ok).toBe(false);
  });
});
