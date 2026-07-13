import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, p, g, json } from "./helpers";
import { hmacHex } from "../worker/hmac";
import {
  poAttachmentCanonical,
  ATTACHMENT_MAX_BYTES,
  MAX_ATTACHMENTS_PER_PO,
  ATT_CHUNK_DECODED_MAX,
} from "../worker/po_attachments";
import { pruneOldData, DRAFT_CANCELED_RETENTION_DAYS } from "../worker/prune";
// The bundled tax config — derive generate totals from source, never pin editable
// content (HOUSE_REFLEXES §5; the po.test.ts pattern).
import taxConfig from "../../po_materials/config/tax.json";

// ─────────────────────────────────────────────────────────────────────────────
// PO document attachments (Feature B) — worker/po_attachments.ts + migration 0053
// + the po.ts delete-draft cascade + the prune.ts po_drafts-stage cascade.
//
// Coverage: the cap.po.manage gate on the browser surface; upload bounds (size,
// count, filename, MIME allowlist, magic-vs-MIME 422); mutation+audit atomicity
// (batch — a refused insert writes neither chunks nor audit); the po-att:v1 HMAC
// + sha256 signature; DRAFT-only upload/delete; cascade on delete-draft AND on
// the 90d prune (never-generated parents die whole; generated-then-canceled
// parents keep byte-free rows, chunks dropped); the internal Mac-ward surface
// (pending JOIN gating, claim-first, chunks read, result disposition +
// delete-on-disposition + idempotent replays); bearer-tier isolation.
// ─────────────────────────────────────────────────────────────────────────────

const PO_BEARER = "test-po-token";
const INTERNAL_BEARER = "test-internal-token";
const FIELDOPS_BEARER = "test-fieldops-token";
const ADMIN_BEARER = "test-admin-token";
const HMAC_SECRET = "test-hmac-payload-secret";

// ── Byte fixtures ────────────────────────────────────────────────────────────
const PDF_BYTES = new TextEncoder().encode("%PDF-1.4\nhello\n%%EOF");
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
const ZIP_BYTES = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0]);
const MIME_PDF = "application/pdf";
const MIME_PNG = "image/png";
const MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function b64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

async function seedVendor(vendorKey: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO po_vendors (vendor_key, vendor_name, contact_email, region, supply_categories, active, origin, sync_state, mirror_version, mirrored_version) " +
      "VALUES (?1,?2,?3,?4,?5,1,'smartsheet','synced',0,0)",
  )
    .bind(vendorKey, `Vendor ${vendorKey}`, "sales@vendor.example", "Midwest", '["racking"]')
    .run();
}

function draftBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    vendor_key: "VEN-000001",
    job_no: "2026.001",
    site_phase: 2,
    job_id: "JOB-000017",
    job_name: "Sunrise Solar",
    ship_to_name: "Evergreen Renewables LLC",
    ship_to_address: "100 Array Rd",
    ship_to_city: "Rockford",
    ship_to_state: "IL",
    ship_to_zip: "61101",
    sow_text: "Supply and deliver racking components.",
    payment_terms_text: "Net 30",
    terms_profile_id: "standard_17",
    terms_version: "v1",
    tax_mode: "auto",
    shipping_cents: 10_000,
    line_column_variant: "default",
    line_items: [
      { part_number: "RK-100", description: "Rail 100", qty: 10, unit: "ea", unit_cost_cents: 12_345 },
    ],
    ...over,
  };
}
// 10 × 12345 = 123450 (pure line math); tax derived from the bundled IL rate.
const SUBTOTAL_CENTS = 123_450;
const TAX_CENTS = Math.round((SUBTOTAL_CENTS * taxConfig.rates_bp.IL) / 10_000);
const EXPECTED = {
  subtotal_cents: SUBTOTAL_CENTS,
  tax_cents: TAX_CENTS,
  total_cents: SUBTOTAL_CENTS + TAX_CENTS + 10_000,
};

async function makeDraft(admin: string): Promise<number> {
  const created = await p(admin, "/api/po/drafts", draftBody());
  expect(created.status, await created.clone().text()).toBe(201);
  const { id } = await json<{ id: number }>(created);
  return id;
}

/** draft → queued → pending_review (generate + mark-filed): the serviceable parent state. */
async function makeFiled(admin: string): Promise<number> {
  const id = await makeDraft(admin);
  const gen = await p(admin, `/api/po/drafts/${id}/generate`, EXPECTED);
  expect(gen.status, await gen.clone().text()).toBe(200);
  const filed = await call("/api/po/internal/mark-filed", {
    method: "POST", bearer: PO_BEARER, body: JSON.stringify({ po_id: id, box_file_id: `bx-${id}` }),
  });
  expect(filed.status).toBe(200);
  return id;
}

function upload(
  admin: string, poId: number,
  over: { filename?: string; mime?: string; data_b64?: string } = {},
): Promise<Response> {
  return p(admin, `/api/po/drafts/${poId}/attachments`, {
    filename: over.filename ?? "spec sheet.pdf",
    mime: over.mime ?? MIME_PDF,
    data_b64: over.data_b64 ?? b64(PDF_BYTES),
  });
}

async function attRow(id: number): Promise<Record<string, unknown> | null> {
  return await env.DB.prepare("SELECT * FROM po_attachments WHERE id=?1").bind(id).first();
}
async function chunkCount(attId: number): Promise<number> {
  const r = await env.DB
    .prepare("SELECT COUNT(*) AS n FROM po_attachment_chunks WHERE attachment_id=?1")
    .bind(attId)
    .first<{ n: number }>();
  return r?.n ?? 0;
}
async function auditCount(action: string): Promise<number> {
  const r = await env.DB
    .prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action=?1")
    .bind(action)
    .first<{ n: number }>();
  return r?.n ?? 0;
}

let admin: string, submitter: string;
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM po_attachment_chunks"),
    env.DB.prepare("DELETE FROM po_attachments"),
    env.DB.prepare("DELETE FROM po_line_items"),
    env.DB.prepare("DELETE FROM purchase_orders"),
    env.DB.prepare("DELETE FROM po_vendors"),
    env.DB.prepare("UPDATE po_vendor_counter SET last_value=0 WHERE id=1"),
  ]);
  await provision("admin.att", "password123", "admin");
  await provision("submitter.att", "password123", "submitter");
  admin = await login("admin.att", "password123");
  submitter = await login("submitter.att", "password123");
  await seedVendor("VEN-000001");
});

// ── Capability gate (browser surface) ─────────────────────────────────────────
describe("cap.po.manage gate", () => {
  it("403s a submitter and 401s no-session on upload/list/delete", async () => {
    const id = await makeDraft(admin);
    expect((await upload(submitter, id)).status).toBe(403);
    expect((await g(submitter, `/api/po/pos/${id}/attachments`)).status).toBe(403);
    expect((await p(submitter, `/api/po/drafts/${id}/attachments/1/delete`, {})).status).toBe(403);
    expect((await call(`/api/po/pos/${id}/attachments`)).status).toBe(401);
    expect((await call(`/api/po/drafts/${id}/attachments`, { method: "POST", body: "{}" })).status).toBe(401);
  });
});

// ── Upload: happy path + signature ─────────────────────────────────────────────
describe("upload", () => {
  it("201s a valid PDF: row + gap-free chunks + audit land atomically; HMAC/sha256 verify", async () => {
    const id = await makeDraft(admin);
    const res = await upload(admin, id);
    expect(res.status, await res.clone().text()).toBe(201);
    const body = await json<{ id: number; size_bytes: number }>(res);
    expect(body.size_bytes).toBe(PDF_BYTES.length);

    const row = (await attRow(body.id))!;
    expect(row.status).toBe("pending");
    expect(row.filename).toBe("spec sheet.pdf");
    expect(row.declared_mime).toBe(MIME_PDF);
    expect(row.uploaded_by).toBe("admin.att");
    expect(await chunkCount(body.id)).toBe(1);
    expect(await auditCount("po_attachment_upload")).toBe(1);

    // The signature covers row identity + content digest — recompute both sides.
    const digest = await crypto.subtle.digest("SHA-256", PDF_BYTES as unknown as BufferSource);
    const sha = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    expect(row.sha256).toBe(sha);
    const expected = await hmacHex(
      HMAC_SECRET,
      poAttachmentCanonical(row.att_uuid as string, id, "spec sheet.pdf", MIME_PDF, PDF_BYTES.length, sha),
    );
    expect(row.hmac).toBe(expected);
  });

  it("accepts an OpenXML docx (PK magic under the docx MIME) and a PNG under image/png", async () => {
    const id = await makeDraft(admin);
    const docx = await upload(admin, id, { filename: "cutsheet.docx", mime: MIME_DOCX, data_b64: b64(ZIP_BYTES) });
    expect(docx.status, await docx.clone().text()).toBe(201);
    const png = await upload(admin, id, { filename: "drawing.png", mime: MIME_PNG, data_b64: b64(PNG_BYTES) });
    expect(png.status, await png.clone().text()).toBe(201);
  });

  it("chunks a multi-chunk payload gap-free (decoded concatenation == original)", async () => {
    const id = await makeDraft(admin);
    // 2.5 chunks of PDF-magic-led bytes.
    const big = new Uint8Array(Math.floor(ATT_CHUNK_DECODED_MAX * 2.5));
    big.set(PDF_BYTES, 0);
    const res = await upload(admin, id, { data_b64: b64(big) });
    expect(res.status, await res.clone().text()).toBe(201);
    const { id: attId } = await json<{ id: number }>(res);
    const { results } = await env.DB
      .prepare("SELECT chunk_index, chunk_total, chunk_b64 FROM po_attachment_chunks WHERE attachment_id=?1 ORDER BY chunk_index")
      .bind(attId)
      .all<{ chunk_index: number; chunk_total: number; chunk_b64: string }>();
    expect(results!.length).toBe(3);
    expect(results!.every((c) => c.chunk_total === 3)).toBe(true);
    expect(results!.map((c) => c.chunk_index)).toEqual([0, 1, 2]);
    // Each chunk decodes independently; concatenated decoded length matches.
    const totalDecoded = results!.reduce((n, c) => n + atob(c.chunk_b64).length, 0);
    expect(totalDecoded).toBe(big.length);
  });
});

// ── Upload bounds (Invariant 2 — the Worker is the real gate) ─────────────────
describe("upload bounds", () => {
  it("413s an over-cap file BEFORE decode (base64-length bound)", async () => {
    const id = await makeDraft(admin);
    // decoded len = len*3/4 > 10MB → refused pre-decode; all-'A' is valid base64.
    const oversize = "A".repeat((Math.ceil(ATTACHMENT_MAX_BYTES / 3) + 4) * 4);
    const res = await upload(admin, id, { data_b64: oversize });
    expect(res.status).toBe(413);
    expect((await json<{ error: string }>(res)).error).toBe("attachment_too_large");
  });

  it("409s the (cap+1)th attachment on one PO (count cap, in-WHERE at execution time)", async () => {
    const id = await makeDraft(admin);
    for (let i = 0; i < MAX_ATTACHMENTS_PER_PO; i++) {
      const r = await upload(admin, id, { filename: `spec-${i}.pdf` });
      expect(r.status, await r.clone().text()).toBe(201);
    }
    const over = await upload(admin, id, { filename: "one-too-many.pdf" });
    expect(over.status).toBe(409);
    expect((await json<{ error: string }>(over)).error).toBe("too_many_attachments");
    // The refused insert wrote NOTHING: no orphan chunks, exactly cap audit rows.
    expect(await auditCount("po_attachment_upload")).toBe(MAX_ATTACHMENTS_PER_PO);
  });

  it("bounds the filename: overlong, path separators, control chars, leading dot", async () => {
    const id = await makeDraft(admin);
    for (const filename of [
      `${"a".repeat(121)}.pdf`,
      "../traversal.pdf",
      "dir/name.pdf",
      "back\\slash.pdf",
      "ctrlbell.pdf",
      ".hidden.pdf",
      "",
    ]) {
      const res = await upload(admin, id, { filename });
      expect(res.status, `filename=${JSON.stringify(filename)}`).toBe(400);
    }
  });

  it("422s a MIME outside the allowlist (legacy OLE .doc, CAD, arbitrary)", async () => {
    const id = await makeDraft(admin);
    for (const [filename, mime] of [
      ["legacy.doc", "application/msword"],
      ["model.dwg", "image/vnd.dwg"],
      ["evil.exe", "application/octet-stream"],
    ] as const) {
      const res = await upload(admin, id, { filename, mime, data_b64: b64(ZIP_BYTES) });
      expect(res.status, mime).toBe(422);
      expect((await json<{ error: string }>(res)).error).toBe("mime_not_allowed");
    }
  });

  it("422s extension⇄MIME and magic⇄MIME mismatches (all three must agree)", async () => {
    const id = await makeDraft(admin);
    // Allowed MIME, wrong extension.
    const ext = await upload(admin, id, { filename: "spec.png", mime: MIME_PDF });
    expect(ext.status).toBe(422);
    expect((await json<{ error: string }>(ext)).error).toBe("extension_mime_mismatch");
    // Allowed MIME + matching extension, but the BYTES are PNG (declared PDF).
    const magic = await upload(admin, id, { filename: "spec.pdf", mime: MIME_PDF, data_b64: b64(PNG_BYTES) });
    expect(magic.status).toBe(422);
    expect((await json<{ error: string }>(magic)).error).toBe("magic_mime_mismatch");
    // A docx declared as PNG (zip magic under an image MIME) is a mismatch too.
    const zipAsPng = await upload(admin, id, { filename: "spec.png", mime: MIME_PNG, data_b64: b64(ZIP_BYTES) });
    expect(zipAsPng.status).toBe(422);
    // Nothing landed from any refusal above.
    expect(await auditCount("po_attachment_upload")).toBe(0);
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM po_attachments").first<{ n: number }>();
    expect(n?.n).toBe(0);
  });

  it("409s an upload onto a GENERATED (non-draft) PO — attachments are draft-time only", async () => {
    const id = await makeFiled(admin);
    const res = await upload(admin, id);
    expect(res.status).toBe(409);
    expect((await json<{ error: string }>(res)).error).toBe("not_draft");
  });
});

// ── List + browser delete ──────────────────────────────────────────────────────
describe("list + delete", () => {
  it("lists metadata only (no bytes, no hmac/sha on the wire)", async () => {
    const id = await makeDraft(admin);
    await upload(admin, id);
    const res = await g(admin, `/api/po/pos/${id}/attachments`);
    expect(res.status).toBe(200);
    const { attachments } = await json<{ attachments: Record<string, unknown>[] }>(res);
    expect(attachments.length).toBe(1);
    const a = attachments[0];
    expect(a.filename).toBe("spec sheet.pdf");
    expect(a.status).toBe("pending");
    expect(a.hmac).toBeUndefined();
    expect(a.sha256).toBeUndefined();
    expect(a.chunk_b64).toBeUndefined();
  });

  it("deletes a draft attachment (row + chunks, audit-gated); replay 404s", async () => {
    const id = await makeDraft(admin);
    const up = await json<{ id: number }>(await upload(admin, id));
    const del = await p(admin, `/api/po/drafts/${id}/attachments/${up.id}/delete`, {});
    expect(del.status, await del.clone().text()).toBe(200);
    expect(await attRow(up.id)).toBeNull();
    expect(await chunkCount(up.id)).toBe(0);
    expect(await auditCount("po_attachment_delete")).toBe(1);
    expect((await p(admin, `/api/po/drafts/${id}/attachments/${up.id}/delete`, {})).status).toBe(404);
  });

  it("409s a delete once the PO generated (attachments immutable browser-side)", async () => {
    const id = await makeDraft(admin);
    const up = await json<{ id: number }>(await upload(admin, id));
    const gen = await p(admin, `/api/po/drafts/${id}/generate`, EXPECTED);
    expect(gen.status).toBe(200);
    const del = await p(admin, `/api/po/drafts/${id}/attachments/${up.id}/delete`, {});
    expect(del.status).toBe(409);
    expect(await attRow(up.id)).not.toBeNull();
    expect(await chunkCount(up.id)).toBe(1); // bytes intact for the Mac pass
  });
});

// ── Cascade: delete-draft + prune ──────────────────────────────────────────────
describe("cascade", () => {
  it("POST /api/po/:id/delete cascades attachments + chunks with the draft", async () => {
    const id = await makeDraft(admin);
    const up = await json<{ id: number }>(await upload(admin, id));
    const del = await p(admin, `/api/po/${id}/delete`, {});
    expect(del.status, await del.clone().text()).toBe(200);
    expect(await attRow(up.id)).toBeNull();
    expect(await chunkCount(up.id)).toBe(0);
  });

  it("delete-draft on a NON-draft leaves attachments + chunks untouched (guard holds)", async () => {
    const id = await makeDraft(admin);
    const up = await json<{ id: number }>(await upload(admin, id));
    expect((await p(admin, `/api/po/drafts/${id}/generate`, EXPECTED)).status).toBe(200);
    expect((await p(admin, `/api/po/${id}/delete`, {})).status).toBe(409);
    expect(await attRow(up.id)).not.toBeNull();
    expect(await chunkCount(up.id)).toBe(1);
  });

  it("prune: an aged never-generated draft dies whole (rows + chunks); a fresh one survives", async () => {
    const oldId = await makeDraft(admin);
    const oldAtt = await json<{ id: number }>(await upload(admin, oldId));
    const freshId = await makeDraft(admin);
    const freshAtt = await json<{ id: number }>(await upload(admin, freshId));
    // Real now (the fresh draft's updated_at is a REAL unixepoch()); age only the old one.
    const nowSec = Math.floor(Date.now() / 1000);
    const aged = nowSec - (DRAFT_CANCELED_RETENTION_DAYS + 1) * 86_400;
    await env.DB.prepare("UPDATE purchase_orders SET updated_at=?1 WHERE id=?2").bind(aged, oldId).run();

    const result = await pruneOldData(env.DB, nowSec);
    expect(result.failedStages).toEqual([]);
    expect(result.poDrafts).toBe(1);
    expect(await attRow(oldAtt.id)).toBeNull();
    expect(await chunkCount(oldAtt.id)).toBe(0);
    expect(await attRow(freshAtt.id)).not.toBeNull();
    expect(await chunkCount(freshAtt.id)).toBe(1);
  });

  it("prune: a generated-then-canceled PO keeps its rows (numbering guard) but drops CHUNKS", async () => {
    const id = await makeDraft(admin);
    const up = await json<{ id: number }>(await upload(admin, id));
    expect((await p(admin, `/api/po/drafts/${id}/generate`, EXPECTED)).status).toBe(200);
    expect((await p(admin, `/api/po/${id}/cancel`, {})).status).toBe(200);
    const nowSec = Math.floor(Date.now() / 1000);
    const aged = nowSec - (DRAFT_CANCELED_RETENTION_DAYS + 1) * 86_400;
    await env.DB.prepare("UPDATE purchase_orders SET updated_at=?1 WHERE id=?2").bind(aged, id).run();

    const result = await pruneOldData(env.DB, nowSec);
    expect(result.failedStages).toEqual([]);
    // The allocated po_number keeps the parent + the byte-free manifest row…
    expect((await env.DB.prepare("SELECT id FROM purchase_orders WHERE id=?1").bind(id).first())).not.toBeNull();
    expect(await attRow(up.id)).not.toBeNull();
    // …but the bytes (the only unbounded growth) are gone.
    expect(await chunkCount(up.id)).toBe(0);
  });
});

// ── Internal Mac-ward surface ──────────────────────────────────────────────────
describe("internal surface", () => {
  it("pending serves NOTHING for drafts/queued; serves the row once the PO files", async () => {
    const id = await makeDraft(admin);
    await upload(admin, id);
    const whileDraft = await call("/api/po/internal/attachments/pending", { bearer: PO_BEARER });
    expect((await json<{ attachments: unknown[] }>(whileDraft)).attachments.length).toBe(0);

    expect((await p(admin, `/api/po/drafts/${id}/generate`, EXPECTED)).status).toBe(200);
    const whileQueued = await call("/api/po/internal/attachments/pending", { bearer: PO_BEARER });
    expect((await json<{ attachments: unknown[] }>(whileQueued)).attachments.length).toBe(0);

    const filed = await call("/api/po/internal/mark-filed", {
      method: "POST", bearer: PO_BEARER, body: JSON.stringify({ po_id: id, box_file_id: "bx-1" }),
    });
    expect(filed.status).toBe(200);
    const after = await call("/api/po/internal/attachments/pending", { bearer: PO_BEARER });
    const { attachments } = await json<{ attachments: Record<string, unknown>[] }>(after);
    expect(attachments.length).toBe(1);
    expect(attachments[0].po_id).toBe(id);
    expect(attachments[0].po_number).toBeTruthy();
    expect(attachments[0].job_name).toBe("Sunrise Solar");
    expect(attachments[0].hmac).toBeTruthy(); // the Mac's verify input
    expect(attachments[0].sha256).toBeTruthy();
  });

  it("claim flips pending→claimed once (found:false replay); claimed rows still re-serve", async () => {
    // The full flow: draft → upload → generate → mark-filed (attachments ride the draft).
    const id2 = await makeDraft(admin);
    const att = await json<{ id: number }>(await upload(admin, id2));
    expect((await p(admin, `/api/po/drafts/${id2}/generate`, EXPECTED)).status).toBe(200);
    await call("/api/po/internal/mark-filed", {
      method: "POST", bearer: PO_BEARER, body: JSON.stringify({ po_id: id2, box_file_id: "bx" }),
    });

    const claim1 = await call(`/api/po/internal/attachments/${att.id}/claim`, {
      method: "POST", bearer: PO_BEARER, body: "{}",
    });
    expect((await json<{ found: boolean }>(claim1)).found).toBe(true);
    expect(((await attRow(att.id))!).status).toBe("claimed");
    expect(await auditCount("po_attachment_claim")).toBe(1);

    const claim2 = await call(`/api/po/internal/attachments/${att.id}/claim`, {
      method: "POST", bearer: PO_BEARER, body: "{}",
    });
    expect((await json<{ found: boolean }>(claim2)).found).toBe(false);
    expect(await auditCount("po_attachment_claim")).toBe(1); // gated audit did not re-fire

    // A claimed row STILL re-serves on pending (crash recovery).
    const pending = await call("/api/po/internal/attachments/pending", { bearer: PO_BEARER });
    expect((await json<{ attachments: unknown[] }>(pending)).attachments.length).toBe(1);
  });

  it("chunks serve bytes Mac-ward for live rows only", async () => {
    const id = await makeDraft(admin);
    const att = await json<{ id: number }>(await upload(admin, id));
    const res = await call(`/api/po/internal/attachments/${att.id}/chunks`, { bearer: PO_BEARER });
    expect(res.status).toBe(200);
    const { chunks } = await json<{ chunks: { chunk_index: number; chunk_total: number; chunk_b64: string }[] }>(res);
    expect(chunks.length).toBe(1);
    expect(atob(chunks[0].chunk_b64).length).toBe(PDF_BYTES.length);
  });

  it("result 'filed' flips status + stores box_file_id + DELETES chunks atomically; replay found:false", async () => {
    const id = await makeDraft(admin);
    const att = await json<{ id: number }>(await upload(admin, id));
    const res = await call(`/api/po/internal/attachments/${att.id}/result`, {
      method: "POST", bearer: PO_BEARER,
      body: JSON.stringify({ status: "filed", box_file_id: "bx-att-1" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect((await json<{ found: boolean }>(res)).found).toBe(true);
    const row = (await attRow(att.id))!;
    expect(row.status).toBe("filed");
    expect(row.box_file_id).toBe("bx-att-1");
    expect(row.screened_at).not.toBeNull();
    expect(await chunkCount(att.id)).toBe(0); // delete-on-disposition
    expect(await auditCount("po_attachment_result")).toBe(1);

    const replay = await call(`/api/po/internal/attachments/${att.id}/result`, {
      method: "POST", bearer: PO_BEARER,
      body: JSON.stringify({ status: "filed", box_file_id: "bx-att-1" }),
    });
    expect((await json<{ found: boolean }>(replay)).found).toBe(false);
    expect(await auditCount("po_attachment_result")).toBe(1);

    // Disposed rows vanish from pending + chunks 404.
    expect((await call(`/api/po/internal/attachments/${att.id}/chunks`, { bearer: PO_BEARER })).status).toBe(404);
  });

  it("result contract: filed REQUIRES box_file_id; refused FORBIDS it; refused stores detail", async () => {
    const id = await makeDraft(admin);
    const att = await json<{ id: number }>(await upload(admin, id));
    expect(
      (await call(`/api/po/internal/attachments/${att.id}/result`, {
        method: "POST", bearer: PO_BEARER, body: JSON.stringify({ status: "filed" }),
      })).status,
    ).toBe(400);
    expect(
      (await call(`/api/po/internal/attachments/${att.id}/result`, {
        method: "POST", bearer: PO_BEARER, body: JSON.stringify({ status: "refused", box_file_id: "bx" }),
      })).status,
    ).toBe(400);
    const refused = await call(`/api/po/internal/attachments/${att.id}/result`, {
      method: "POST", bearer: PO_BEARER,
      body: JSON.stringify({ status: "refused", detail: "L2:pdf_active_content:JavaScript" }),
    });
    expect(refused.status).toBe(200);
    const row = (await attRow(att.id))!;
    expect(row.status).toBe("refused");
    expect(row.detail).toBe("L2:pdf_active_content:JavaScript");
    expect(await chunkCount(att.id)).toBe(0);
  });
});

// ── Cross-language HMAC vector ─────────────────────────────────────────────────
describe("po-att:v1 canonical", () => {
  it("matches the Python-side literal for the fixed vector (tests/test_po_attach_screen.py)", async () => {
    const hex = await hmacHex(
      HMAC_SECRET,
      poAttachmentCanonical(
        "0f4a8e1c-1111-2222-3333-444455556666", 7, "spec sheet.pdf",
        "application/pdf", 20, "aa".repeat(32),
      ),
    );
    expect(hex).toBe("9a1eaa8cccf781c9f565e703aba1e551d01925d6df770a2c8acd568bba786447");
  });
});

// ── Bearer tier isolation ──────────────────────────────────────────────────────
describe("bearer isolation", () => {
  it("rejects no-token, wrong token, and every sibling tier on every attachment-internal route", async () => {
    const routes: [string, string, unknown][] = [
      ["GET", "/api/po/internal/attachments/pending", undefined],
      ["POST", "/api/po/internal/attachments/1/claim", {}],
      ["GET", "/api/po/internal/attachments/1/chunks", undefined],
      ["POST", "/api/po/internal/attachments/1/result", { status: "refused" }],
    ];
    for (const [method, path, body] of routes) {
      const init = body === undefined ? { method } : { method, body: JSON.stringify(body) };
      expect((await call(path, init)).status, `${path} no token`).toBe(401);
      for (const bearer of ["wrong-token", INTERNAL_BEARER, FIELDOPS_BEARER, ADMIN_BEARER]) {
        expect((await call(path, { ...init, bearer })).status, `${path} bearer=${bearer}`).toBe(401);
      }
      expect((await call(path, { ...init, bearer: PO_BEARER })).status, `${path} po token`).not.toBe(401);
    }
  });
});
