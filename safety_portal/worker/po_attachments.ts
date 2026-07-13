import type { FieldopsApp } from "./fieldops_gates";
import type { PoGates } from "./po";
import { auditStmtIfChanged } from "./audit";
import { hmacHex } from "./hmac";
import { b64DecodedLen, B64_RE } from "./photo_bounds";

// ─────────────────────────────────────────────────────────────────────────────
// PO document attachments (Feature B) — worker/po_attachments.ts
//
// The Worker half of the PO attachment pipeline: draft-scoped upload/list/delete
// under the SAME session + cap.po.manage gate as every other draft mutation in
// worker/po.ts, and an internal Mac-ward surface under the SAME requirePoToken
// bearer tier the po_poll daemon already holds. §34 Option-D generalized from
// photos to documents:
//
//   - The Worker BOUNDS-GATES ONLY (Invariant 2 shape/bounds: per-file 10MB, 5
//     per PO, filename bounds, declared-MIME allowlist AND a magic-byte sniff —
//     a mismatch is a 422). It performs NO content inspection — the §34 trust
//     boundary stays Mac-side (po_materials/po_attach_screen.py) before any Box
//     upload or Smartsheet attach.
//   - Bytes are queued in D1 (po_attachment_chunks, the filed_pdfs chunk shape)
//     SEND-FREE and flow ONLY Mac-ward over the PO bearer. There is NO route
//     serving bytes back to a browser — the list route projects filename/size/
//     status only (Option D, ratified for the photo pool 2026-07-03).
//   - Every upload is HMAC-signed under the NEW domain prefix "po-att:v1"
//     (same HMAC_PAYLOAD_SECRET, different domain — an attachment signature can
//     never replay as a submission/photo/PO/subcontract signature and vice
//     versa). The canonical string binds att_uuid + po_id + filename + mime +
//     size + sha256, so the Mac verifies both the row AND the reassembled bytes
//     (sha256 recompute) before a single byte is screened.
//   - Every mutation batches atomically with its audit row (W4).
//   - CASCADE: the #560 delete-draft route and the prune.ts stale-draft stage
//     delete chunks + rows with the parent (wired in po.ts / prune.ts).
//   - DELETE-ON-DISPOSITION: the internal result route deletes the chunks in
//     the same batch that applies filed/refused — D1 holds attachment bytes
//     only while status IN ('pending','claimed').
// ─────────────────────────────────────────────────────────────────────────────

const CAP_PO = "cap.po.manage";
export const PO_ATTACH_HMAC_DOMAIN = "po-att:v1";
const SYSTEM_ACTOR = "system:po_poll";

// ── Bounds (Invariant 2 — operator decisions locked 2026-07-13) ─────────────────
export const ATTACHMENT_MAX_BYTES = 10_000_000; // decoded bytes per file (10 MB)
export const MAX_ATTACHMENTS_PER_PO = 5;
export const MAX_ATTACHMENT_FILENAME = 120;
// Chunking mirrors filed_pdfs: ≤ 1MB decoded per chunk row (proven live row size);
// 10MB / 1MB = 10 chunks max.
export const ATT_CHUNK_DECODED_MAX = 1_000_000;
export const ATT_MAX_CHUNKS = Math.ceil(ATTACHMENT_MAX_BYTES / ATT_CHUNK_DECODED_MAX);
const ATT_PENDING_CAP = 25; // internal pending page size ceiling

// The parent statuses whose attachments the Mac services: the PO has FILED
// (mark-filed flipped it out of 'queued'), so the Box job folder + PO_Log row
// exist for the clean-file destination. Draft/queued/canceled attachments are
// never served — they ride the draft until the PO files (or die with it).
const SERVICEABLE_PO_STATUSES = "('pending_review','approved','sent','superseded')";

// ── Allowlist: declared MIME ⇄ extension ⇄ magic (all three must agree) ─────────
// Operator decision: PDF + JPEG/PNG images + Office OpenXML (.docx/.xlsx) ONLY.
// NO legacy OLE (.doc/.xls), NO CAD. The magic sniff here is the Worker's cheap
// gate; the real §34 structural inspection is Mac-side.
type Magic = "pdf" | "jpeg" | "png" | "zip";
const MIME_ALLOWLIST: Record<string, { exts: string[]; magic: Magic }> = {
  "application/pdf": { exts: [".pdf"], magic: "pdf" },
  "image/jpeg": { exts: [".jpg", ".jpeg"], magic: "jpeg" },
  "image/png": { exts: [".png"], magic: "png" },
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
    exts: [".docx"],
    magic: "zip",
  },
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
    exts: [".xlsx"],
    magic: "zip",
  },
};

function magicMatches(head: Uint8Array, magic: Magic): boolean {
  if (head.length < 8) return false;
  switch (magic) {
    case "pdf": // "%PDF-"
      return head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46 && head[4] === 0x2d;
    case "jpeg": // FF D8 FF
      return head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff;
    case "png": // 89 50 4E 47 0D 0A 1A 0A
      return (
        head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47 &&
        head[4] === 0x0d && head[5] === 0x0a && head[6] === 0x1a && head[7] === 0x0a
      );
    case "zip": // PK\x03\x04 (a local-file-header zip — OpenXML containers always start here)
      return head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04;
  }
}

/** Filename gate: bounded, no path separators / control chars / leading dot, and the
 *  extension must belong to the declared MIME (consistency, not just allowlist). */
function filenameProblem(filename: string, declaredMime: string): string | null {
  if (filename.length < 1 || filename.length > MAX_ATTACHMENT_FILENAME) return "invalid_filename";
  // No path separators, no control characters (incl. DEL) — the name lands in Box +
  // a Smartsheet attachment; traversal/terminal-escape characters never enter D1.
  // eslint-disable-next-line no-control-regex
  if (/[/\\\u0000-\u001f\u007f]/.test(filename)) return "invalid_filename";
  if (filename.startsWith(".")) return "invalid_filename";
  const entry = MIME_ALLOWLIST[declaredMime];
  if (!entry) return "mime_not_allowed";
  const lower = filename.toLowerCase();
  if (!entry.exts.some((e) => lower.endsWith(e) && lower.length > e.length)) {
    return "extension_mime_mismatch";
  }
  return null;
}

/** The po-att:v1 canonical string — ORDER + "\n" SEPARATOR are load-bearing; the Mac
 *  (shared/portal_hmac.po_attachment_canonical) recomputes it byte-for-byte before
 *  trusting a row. Binds identity (att_uuid, po_id), naming (filename, mime), and
 *  content (size_bytes, sha256) — a tampered row OR tampered bytes fail verify. */
export function poAttachmentCanonical(
  attUuid: string, poId: number, filename: string, declaredMime: string,
  sizeBytes: number, sha256: string,
): string {
  return [PO_ATTACH_HMAC_DOMAIN, attUuid, String(poId), filename, declaredMime, String(sizeBytes), sha256].join("\n");
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes: Uint8Array): string {
  // Chunked String.fromCharCode to stay under the argument-count limit.
  let bin = "";
  const STEP = 0x8000;
  for (let i = 0; i < bytes.length; i += STEP) {
    bin += String.fromCharCode(...bytes.subarray(i, i + STEP));
  }
  return btoa(bin);
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as unknown as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function parseIdParam(raw: string | undefined): number | null {
  const id = parseInt(raw ?? "", 10);
  return Number.isSafeInteger(id) && id > 0 && String(id) === (raw ?? "") ? id : null;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

const RESULT_STATUSES = new Set(["filed", "refused"]);

// ── Route registration ──────────────────────────────────────────────────────────
export function registerPoAttachmentRoutes(app: FieldopsApp, gates: PoGates): void {
  // ══ Browser surface (session + cap.po.manage — the po.ts draft-mutation gate) ══

  // POST /api/po/drafts/:id/attachments — draft-scoped upload. Body:
  // { filename, mime, data_b64 }. The whole file rides ONE request (base64 in JSON —
  // the photo wire); the Worker decodes once (magic + sha256 need the bytes anyway),
  // splits into ≤1MB-decoded chunks, and lands parent row + audit + ALL chunks in ONE
  // db.batch (W4). Guards are IN-WHERE (parent still 'draft'; count under cap at
  // execution time), so a lost race writes nothing.
  app.post("/api/po/drafts/:id/attachments", gates.requireSession, gates.requireCapability(CAP_PO), async (c) => {
    const poId = parseIdParam(c.req.param("id"));
    if (poId === null) return c.json({ error: "invalid_id" }, 400);
    let body: Record<string, unknown>;
    try {
      body = (await c.req.json()) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad_request" }, 400);
    }
    if (!isPlainObject(body)) return c.json({ error: "bad_request" }, 400);

    const filename = typeof body.filename === "string" ? body.filename.trim() : "";
    const declaredMime = typeof body.mime === "string" ? body.mime.trim() : "";
    const dataB64 = typeof body.data_b64 === "string" ? body.data_b64 : "";

    // Cheap gates first — allowlist + filename/extension consistency (422: the shape
    // is parseable, the CONTENT CLASS is refused), then base64 shape + size BEFORE
    // any decode materializes bytes.
    if (!Object.prototype.hasOwnProperty.call(MIME_ALLOWLIST, declaredMime)) {
      return c.json({ error: "mime_not_allowed" }, 422);
    }
    const nameProblem = filenameProblem(filename, declaredMime);
    if (nameProblem) return c.json({ error: nameProblem }, nameProblem === "extension_mime_mismatch" ? 422 : 400);
    if (dataB64.length === 0 || dataB64.length % 4 !== 0) {
      return c.json({ error: "invalid_data" }, 400);
    }
    // Size verdict BEFORE the charset regex or any decode: pure length arithmetic, so an
    // oversize body is refused as 413 (its true class) without materializing bytes.
    if (b64DecodedLen(dataB64) > ATTACHMENT_MAX_BYTES) {
      return c.json({ error: "attachment_too_large" }, 413);
    }
    if (!B64_RE.test(dataB64)) {
      return c.json({ error: "invalid_data" }, 400);
    }

    // Parent must exist and still be a DRAFT (re-checked in-WHERE at insert too).
    const parent = await c.env.DB
      .prepare("SELECT status FROM purchase_orders WHERE id = ?1")
      .bind(poId)
      .first<{ status: string }>();
    if (!parent) return c.json({ error: "not_found" }, 404);
    if (parent.status !== "draft") return c.json({ error: "not_draft" }, 409);

    // Decode ONCE (magic sniff + sha256 + chunking all need the bytes).
    let bytes: Uint8Array;
    try {
      bytes = b64ToBytes(dataB64);
    } catch {
      return c.json({ error: "invalid_data" }, 400);
    }
    if (bytes.length === 0 || bytes.length > ATTACHMENT_MAX_BYTES) {
      return c.json({ error: "attachment_too_large" }, 413);
    }
    // Magic sniff vs the DECLARED MIME (not just "any allowed magic") — a PNG named
    // .pdf/application/pdf is a mismatch even though PNG itself is allowlisted.
    if (!magicMatches(bytes.subarray(0, 8), MIME_ALLOWLIST[declaredMime].magic)) {
      return c.json({ error: "magic_mime_mismatch" }, 422);
    }

    if (!c.env.HMAC_PAYLOAD_SECRET) return c.json({ error: "hmac_secret_missing" }, 500);
    const sha256 = await sha256Hex(bytes);
    const attUuid = crypto.randomUUID();
    const hmac = await hmacHex(
      c.env.HMAC_PAYLOAD_SECRET,
      poAttachmentCanonical(attUuid, poId, filename, declaredMime, bytes.length, sha256),
    );

    // Chunk the DECODED bytes and re-encode per chunk (each chunk row decodes
    // independently; the Mac concatenates decoded chunks — the filed_pdfs shape).
    const chunkTotal = Math.ceil(bytes.length / ATT_CHUNK_DECODED_MAX);
    const chunkStmts = [];
    for (let i = 0; i < chunkTotal; i++) {
      const slice = bytes.subarray(i * ATT_CHUNK_DECODED_MAX, (i + 1) * ATT_CHUNK_DECODED_MAX);
      chunkStmts.push(
        c.env.DB
          .prepare(
            "INSERT INTO po_attachment_chunks (attachment_id, chunk_index, chunk_total, chunk_b64) " +
              "SELECT (SELECT id FROM po_attachments WHERE att_uuid = ?1), ?2, ?3, ?4 " +
              "WHERE EXISTS (SELECT 1 FROM po_attachments WHERE att_uuid = ?1)",
          )
          .bind(attUuid, i, chunkTotal, bytesToB64(slice)),
      );
    }

    const actor = c.get("session").username;
    // ONE batch (W4): parent-guarded attachment INSERT → changes()-gated audit →
    // chunk INSERTs (each guarded on the attachment row existing, so a refused
    // insert writes zero chunks). The count cap + draft guard are IN-WHERE — D1
    // serializes statements, so two racing uploads cannot both pass the cap.
    await c.env.DB.batch([
      c.env.DB
        .prepare(
          "INSERT INTO po_attachments (att_uuid, po_id, filename, declared_mime, size_bytes, sha256, status, hmac, uploaded_by) " +
            "SELECT ?1, ?2, ?3, ?4, ?5, ?6, 'pending', ?7, ?8 " +
            "WHERE (SELECT status FROM purchase_orders WHERE id = ?2) = 'draft' " +
            "AND (SELECT COUNT(*) FROM po_attachments WHERE po_id = ?2) < ?9",
        )
        .bind(attUuid, poId, filename, declaredMime, bytes.length, sha256, hmac, actor, MAX_ATTACHMENTS_PER_PO),
      auditStmtIfChanged(c, actor, "po_attachment_upload", String(poId), {
        po_id: poId, att_uuid: attUuid, filename, declared_mime: declaredMime, size_bytes: bytes.length,
      }),
      ...chunkStmts,
    ]);
    const row = await c.env.DB
      .prepare("SELECT id FROM po_attachments WHERE att_uuid = ?1")
      .bind(attUuid)
      .first<{ id: number }>();
    if (!row) {
      // The in-WHERE guards refused the insert — distinguish the two causes.
      const now = await c.env.DB
        .prepare("SELECT status FROM purchase_orders WHERE id = ?1")
        .bind(poId)
        .first<{ status: string }>();
      if (now && now.status !== "draft") return c.json({ error: "not_draft" }, 409);
      return c.json({ error: "too_many_attachments" }, 409);
    }
    return c.json({ ok: true, id: row.id, filename, size_bytes: bytes.length }, 201);
  });

  // GET /api/po/pos/:id/attachments — the builder list read: filename/size/status
  // metadata ONLY (never bytes, never the hmac/sha — Option D: bytes flow Mac-ward
  // over the bearer tier exclusively). Any parent status — a generated PO's
  // attachments stay listed read-only.
  app.get("/api/po/pos/:id/attachments", gates.requireSession, gates.requireCapability(CAP_PO), async (c) => {
    const poId = parseIdParam(c.req.param("id"));
    if (poId === null) return c.json({ error: "invalid_id" }, 400);
    const parent = await c.env.DB
      .prepare("SELECT id FROM purchase_orders WHERE id = ?1")
      .bind(poId)
      .first();
    if (!parent) return c.json({ error: "not_found" }, 404);
    const { results } = await c.env.DB
      .prepare(
        "SELECT id, filename, declared_mime, size_bytes, status, created_at " +
          "FROM po_attachments WHERE po_id = ?1 ORDER BY created_at ASC, id ASC",
      )
      .bind(poId)
      .all<Record<string, unknown>>();
    return c.json({ attachments: results ?? [] });
  });

  // POST /api/po/drafts/:id/attachments/:attId/delete — DRAFT-ONLY removal (the
  // builder's ✕). Chunks first, then the row, each subquery-scoped to the parent
  // STILL being a draft; audit gated on the row delete (W4). A generated PO's
  // attachments are immutable browser-side — their exit is the Mac disposition or
  // the parent's own lifecycle (cancel keeps rows; prune/delete-draft cascade).
  app.post(
    "/api/po/drafts/:id/attachments/:attId/delete",
    gates.requireSession,
    gates.requireCapability(CAP_PO),
    async (c) => {
      const poId = parseIdParam(c.req.param("id"));
      const attId = parseIdParam(c.req.param("attId"));
      if (poId === null || attId === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const guard =
        "attachment_id IN (SELECT a.id FROM po_attachments a JOIN purchase_orders p ON p.id = a.po_id " +
        "WHERE a.id = ?1 AND a.po_id = ?2 AND p.status = 'draft')";
      const res = await c.env.DB.batch([
        c.env.DB.prepare(`DELETE FROM po_attachment_chunks WHERE ${guard}`).bind(attId, poId),
        c.env.DB
          .prepare(
            "DELETE FROM po_attachments WHERE id = ?1 AND po_id = ?2 " +
              "AND (SELECT status FROM purchase_orders WHERE id = ?2) = 'draft'",
          )
          .bind(attId, poId),
        auditStmtIfChanged(c, actor, "po_attachment_delete", String(poId), { po_id: poId, attachment_id: attId }),
      ]);
      if ((res[1].meta.changes ?? 0) === 0) {
        const row = await c.env.DB.prepare("SELECT id FROM po_attachments WHERE id = ?1 AND po_id = ?2").bind(attId, poId).first();
        return row ? c.json({ error: "not_deletable" }, 409) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id: attId });
    },
  );

  // ══ Internal surface (requirePoToken — the Mac-side po_poll attachment pass) ════

  // GET /api/po/internal/attachments/pending — serviceable attachments, oldest-first:
  // status pending|claimed (claimed re-served for crash recovery — the pass is
  // idempotent) on a FILED parent (pending_review+ — the Box folder + PO_Log row the
  // clean file needs already exist). Serves row METADATA + the HMAC; bytes ride the
  // per-attachment chunks read below. Named field (portal_client._request contract).
  app.get("/api/po/internal/attachments/pending", gates.requirePoToken, async (c) => {
    const limit = Math.min(Math.max(parseInt(c.req.query("limit") || "25", 10) || 25, 1), ATT_PENDING_CAP);
    const { results } = await c.env.DB
      .prepare(
        "SELECT a.id, a.att_uuid, a.po_id, p.po_number, p.job_name, a.filename, a.declared_mime, " +
          "a.size_bytes, a.sha256, a.status, a.hmac, a.uploaded_by, a.created_at " +
          "FROM po_attachments a JOIN purchase_orders p ON p.id = a.po_id " +
          "WHERE a.status IN ('pending','claimed') AND p.po_number IS NOT NULL " +
          `AND p.status IN ${SERVICEABLE_PO_STATUSES} ` +
          "ORDER BY a.created_at ASC, a.id ASC LIMIT ?1",
      )
      .bind(limit)
      .all<Record<string, unknown>>();
    return c.json({ attachments: results ?? [] });
  });

  // POST /api/po/internal/attachments/:id/claim — claim-first marker (the photo-pool
  // semantics): pending→claimed, guarded in-WHERE + changes()-gated audit (W4).
  // Idempotent: found:false when already claimed/disposed — the daemon proceeds on a
  // row it already claimed (crash recovery), and a late replay is benign.
  app.post("/api/po/internal/attachments/:id/claim", gates.requirePoToken, async (c) => {
    const attId = parseIdParam(c.req.param("id"));
    if (attId === null) return c.json({ error: "invalid_id" }, 400);
    const res = await c.env.DB.batch([
      c.env.DB
        .prepare("UPDATE po_attachments SET status='claimed' WHERE id = ?1 AND status = 'pending'")
        .bind(attId),
      auditStmtIfChanged(c, SYSTEM_ACTOR, "po_attachment_claim", String(attId), { attachment_id: attId }),
    ]);
    return c.json({ ok: true, found: (res[0].meta.changes ?? 0) > 0 });
  });

  // GET /api/po/internal/attachments/:id/chunks — the Mac-ward byte read (the ONLY
  // route that ever serves attachment bytes, bearer-gated). Live rows only: a
  // filed/refused attachment's chunks were deleted at disposition.
  app.get("/api/po/internal/attachments/:id/chunks", gates.requirePoToken, async (c) => {
    const attId = parseIdParam(c.req.param("id"));
    if (attId === null) return c.json({ error: "invalid_id" }, 400);
    const row = await c.env.DB
      .prepare("SELECT status FROM po_attachments WHERE id = ?1")
      .bind(attId)
      .first<{ status: string }>();
    if (!row || (row.status !== "pending" && row.status !== "claimed")) {
      return c.json({ error: "not_found" }, 404);
    }
    const { results } = await c.env.DB
      .prepare(
        "SELECT chunk_index, chunk_total, chunk_b64 FROM po_attachment_chunks " +
          "WHERE attachment_id = ?1 ORDER BY chunk_index ASC",
      )
      .bind(attId)
      .all<Record<string, unknown>>();
    return c.json({ chunks: results ?? [] });
  });

  // POST /api/po/internal/attachments/:id/result — apply one screening disposition.
  // Body: { status: 'filed'|'refused', box_file_id? (filed ONLY — required; the Box
  // record must already exist), detail? (refused machine reason — audit only, never
  // bytes) }. ONE atomic batch (W4): the disposition UPDATE (guarded status IN
  // pending|claimed) → the changes()-gated audit → the chunk DELETE (delete-on-
  // disposition — the bytes leave D1; its subselect reads the POST-update status, so
  // a refused update deletes nothing it shouldn't). Idempotent: a re-post for an
  // already-disposed / unknown row is { ok:true, found:false } with no byte writes.
  app.post("/api/po/internal/attachments/:id/result", gates.requirePoToken, async (c) => {
    const attId = parseIdParam(c.req.param("id"));
    if (attId === null) return c.json({ error: "invalid_id" }, 400);
    let body: Record<string, unknown>;
    try {
      body = (await c.req.json()) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad_request" }, 400);
    }
    if (!isPlainObject(body)) return c.json({ error: "bad_request" }, 400);
    const status = typeof body.status === "string" && RESULT_STATUSES.has(body.status)
      ? (body.status as "filed" | "refused")
      : "";
    if (!status) return c.json({ error: "invalid_result", detail: "status" }, 400);
    const boxFileId =
      typeof body.box_file_id === "string" && body.box_file_id ? body.box_file_id.slice(0, 200) : null;
    const detail = typeof body.detail === "string" && body.detail ? body.detail.slice(0, 200) : null;
    // Tight contract (Invariant 2 — daemon input is untrusted too): filed MUST name
    // the Box record it just created; refused must NOT carry one.
    if (status === "filed" && !boxFileId) {
      return c.json({ error: "invalid_result", detail: "box_file_id_required" }, 400);
    }
    if (status === "refused" && boxFileId) {
      return c.json({ error: "invalid_result", detail: "box_file_id_forbidden" }, 400);
    }

    const row = await c.env.DB
      .prepare("SELECT id, po_id, status FROM po_attachments WHERE id = ?1")
      .bind(attId)
      .first<{ id: number; po_id: number; status: string }>();
    if (!row || (row.status !== "pending" && row.status !== "claimed")) {
      return c.json({ ok: true, found: false, status: row?.status ?? null });
    }

    const res = await c.env.DB.batch([
      c.env.DB
        .prepare(
          "UPDATE po_attachments SET status = ?1, box_file_id = ?2, detail = ?3, " +
            "screened_at = unixepoch() WHERE id = ?4 AND status IN ('pending','claimed')",
        )
        .bind(status, boxFileId, detail, attId),
      auditStmtIfChanged(c, SYSTEM_ACTOR, "po_attachment_result", String(attId), {
        attachment_id: attId, po_id: row.po_id, status, box_file_id: boxFileId, detail,
      }),
      // Delete-on-disposition: the subselect reads the status AFTER the UPDATE above
      // (same batch), so chunks are dropped exactly when the row is now disposed.
      c.env.DB
        .prepare(
          "DELETE FROM po_attachment_chunks WHERE attachment_id = ?1 " +
            "AND (SELECT status FROM po_attachments WHERE id = ?1) IN ('filed','refused')",
        )
        .bind(attId),
    ]);
    return c.json({ ok: true, found: (res[0].meta.changes ?? 0) > 0 });
  });
}
