---
type: reference
date: 2026-06-12
status: active
related_prs: []
workstream: safety_reports
tags: [adr, safety-portal, photo, transport, d1, r2, doctrine]
---

# ADR-0001 — D1-inline base64 vs R2 for Safety-Portal photo transport

**Status:** Accepted (owner decision 2026-06-12) — R2 is the recorded upgrade path.

## Context

The Safety Portal lets a field crew attach **site photos** to a safety submission
(PR-1 #271 added the photo input + Worker bounds gate + SPA capture; PR-2 #272
added the Mac-side §34 screening, PDF embed, and Box originals). A photo travels:

1. **Browser → Worker** — the SPA base64-encodes each photo and posts it inside the
   submission JSON to the Cloudflare Worker.
2. **Worker → D1** — the Worker stores the submission (photos included, base64-inline)
   in its D1 queue, send-free, awaiting the Mac pull (`decision_phase5-portal-transport`).
3. **D1 → Mac** — `portal_poll.py` pulls the submission over `GET /api/internal/pending`,
   `intake.py` §34-screens each photo, embeds it in the per-submission PDF, and files
   the **original** photo bytes to Box. The Sat→Fri **weekly packet** then merges the
   per-submission PDFs (photos embedded) into one PDF (`weekly_generate.py`).

The open question: **how should photo bytes ride from the browser to the Mac** — inline
in the D1-queued submission JSON (base64), or out-of-band via Cloudflare **R2** object
storage with only a reference in D1?

Constraints that bound the choice:

- The Worker caps a photo at 400 KB decoded and **8 photos per submission**
  (`photo_screen.MAX_PHOTOS_PER_SUBMISSION` mirrors the Worker). A worst-case
  submission is therefore ~3.2 MB raw, ~4.3 MB base64.
- The Worker holds **no Box credentials** and is **send-free** (Invariant 1). It is a
  durable queue, not a document store — the mission's "the Worker never holds documents"
  stance (see the open mission v4→v5 doctrine flag).
- D1 row/value size and the Worker's submission-body bound (~1.8 MB referenced in
  `intake.py`) put a ceiling on how much base64 can ride inline before it must chunk.

## Decision

**Photos ride D1-inline as base64 today.** No R2 dependency is added in the photo
workstream (PR-1 → PR-3). The current per-submission photo budget (≤8 × 400 KB) fits
the inline path with acceptable headroom, and inline keeps the transport simple: one
queue, one pull, no second store to provision, authorize, lifecycle-expire, or audit.

**R2 is the recorded upgrade path**, to be adopted if/when field crews need materially
more photo payload than the inline budget allows — concretely, the **trigger** is:

> Field crews need **> 4 full-resolution photos per field** (or the per-submission photo
> budget is raised past what D1-inline base64 carries within the Worker body bound).

At that trigger, photos move to **R2**: the SPA (or Worker) uploads each photo to R2 and
the D1 submission carries only the R2 object key; `portal_poll.py`/`intake.py` fetch the
bytes from R2 at screen time. R2 keeps the Worker send-free (object storage, not external
mail) and removes the base64 inflation + D1-value pressure.

## Consequences

**Positive (today):**
- Simplest transport that satisfies the current budget — one durable D1 queue, no second
  storage plane to stand up or secure.
- The Worker stays a pure queue: photos are transient queue payload, deleted on
  mark-filed, never a Worker-held document.
- §34 screening + Box filing are unchanged by the transport choice — they operate on the
  decoded bytes regardless of how they arrived.

**Negative / deferred:**
- Base64 inflates payload ~33%; the inline path is bounded by the Worker body limit, so
  the photo budget cannot grow much without hitting it. **This is the upgrade trigger.**
- A large photo-bearing **weekly packet** can exceed Microsoft Graph's ~3 MB inline
  `sendMail` ceiling on the **send** side — handled in PR-3 by the
  `weekly_send` upload-session switch (inline ≤ 2.5 MB / Graph upload-session > 2.5 MB).
  That is a separate concern from the **ingest** transport decided here, but the two are
  linked: both are downstream pressure from photos increasing payload size. See
  `docs/tech_debt.md` (upload-session threshold; R2 upgrade path).
- Revisiting means provisioning R2, an object-key scheme, lifecycle/expiry, and an
  access path from the Mac — non-trivial, hence deferred until the trigger fires.

**Revisit when:** the > 4-full-res-photos-per-field trigger above is hit, or the Worker
body bound blocks a needed photo-budget increase. Cross-referenced from the PR-3
tech-debt entry "R2 upgrade path for portal photo transport".
