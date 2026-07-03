// HMAC-SHA256 helper — EXTRACTED from worker/index.ts (Phase 5 transport) so the G1 item-photo
// route (fieldops_checklist.ts) signs with the same primitive without importing index.ts (a
// runtime import cycle — index.ts registers the fieldops modules). Byte-identical to the
// pre-extraction original; the SUBMISSION canonical string (canonicalPayload) stays in index.ts,
// the ITEM-PHOTO canonical string (itemPhotoCanonical) lives with its route in
// fieldops_checklist.ts — each protocol owns its own canonical builder, one shared MAC.

/** HMAC-SHA256(secret, message) → lowercase hex. */
export async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
