// Shared Worker input bounds.
//
// MAX_ADDRESS is the free-text street-address length cap used by every route that accepts
// an address (PO ship-to, subcontract site/contractor, field-ops job create, and the
// ITS_Active_Jobs down-sync in index.ts). It was previously duplicated as a local `const
// MAX_ADDRESS = 512` in po.ts / subcontract.ts / fieldops_job_write.ts AND hardcoded as a
// bare `512` in index.ts's /api/internal/sync bound — four independent copies that agreed
// today but could silently drift if any one were bumped alone (SC-CFG-2). Hoisted here so
// all four import one source of truth.
export const MAX_ADDRESS = 512;
