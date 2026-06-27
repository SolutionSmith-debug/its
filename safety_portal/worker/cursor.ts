// Keyset-pagination cursor codec for the field-ops READ layer (P2.2). One opaque codec all
// tabs share. The cursor is base64url(JSON) of an ordering tuple (e.g. {n: name, i: id}).
//
// Invariants:
//   - decode is FAIL-SAFE: ANY malformed/absent input returns null (→ the route serves the
//     first page), never throws — a hostile/garbage cursor can never 500 a read route.
//   - the decoded values are ALWAYS bound as SQL parameters by the caller, NEVER interpolated
//     (Invariant 2 — adversarial input is untrusted data).
//   - keyset (WHERE (sort_key, pk) </> cursor), NEVER OFFSET — O(page), not O(table).

export function encodeCursor(tuple: Record<string, string | number>): string {
  return btoa(JSON.stringify(tuple)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeCursor(raw: string | undefined): Record<string, string | number> | null {
  if (!raw) return null;
  try {
    const o = JSON.parse(atob(raw.replace(/-/g, "+").replace(/_/g, "/")));
    if (!o || typeof o !== "object" || Array.isArray(o)) return null;
    // Reject any tuple whose values aren't primitives: an object/array value would flow through
    // to the caller's .bind() and either mis-coerce ([object Object] → wrong page) or throw a
    // TypeError (→ 500) — both break the FAIL-SAFE → first-page guarantee above.
    for (const v of Object.values(o)) {
      if (v !== null && typeof v !== "string" && typeof v !== "number") return null;
    }
    return o as Record<string, string | number>;
  } catch {
    return null;
  }
}
