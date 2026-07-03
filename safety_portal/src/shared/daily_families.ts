// SINGLE SOURCE for the daily-status parent-form families — imported by BOTH tsconfig scopes:
// the Worker status endpoint (worker/fieldops_daily_requirements.ts) and the SPA client
// (src/lib/fieldops_daily_form.ts re-exports it; src/forms/FormRenderer.tsx consumes it). This
// replaces the two hand-synced literals whose doc comments had already drifted post-M2 — a missed
// sync silently drops Filed-✓ indicators (multi-surface fan-out correctness, not a reuse
// extraction). Cross-scope imports are the established pattern (the Worker already bundles
// catalog.json from the repo root).
//
// D2 (SOP daily form) — the parent-form families the Daily tab's status endpoint reports. These are
// the families the daily-report definition either deep-links to (form_link sections: jha /
// visitor-sign-in / incident-report; the M2 expected-materials "Report a problem →" deep-link:
// material-incident) or IS (daily-report — drives the "already filed today" banner).
// A fixed module constant, never caller input: the endpoint reports exactly these five, so a forged
// query can't turn it into an arbitrary-submission probe.
//
// SPA side: a form_link REQUIREMENT whose form_code is outside this set still deep-links fine, but
// has NO live filed indicator — the renderer notes that instead of showing a lying blank.
// material-incident joined at M2 (Material receipts): the daily form's Expected-materials section
// shows a live Filed ✓ indicator for the incident form it deep-links to.
export const DAILY_STATUS_FAMILIES: readonly string[] = [
  "jha",
  "visitor-sign-in",
  "incident-report",
  "daily-report",
  "material-incident",
];
