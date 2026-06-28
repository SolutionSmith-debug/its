---
type: report
date: 2026-06-28
status: active
workstream: null
tags: [scaling, forensic-audit, 20x20, tier-a, cost-model, documentation-program]
---

> Source of record for the 2026-06-28 forensic scaling evaluation. Authored read-only
> (improve-skill discipline + multi-agent Claude Workflows). Mirror of the plan-mode plan
> file. No code changes were made producing this. Tier-A specs in Part II are first-draft
> (`needs-revision`) — refine before execution. See Part III for the A7 doctrine resolution.

# ITS Forensic Scaling Evaluation — 20 Jobs / 20+ Daily Portal Users

> **Planned at** commit `0c48a09`, 2026-06-28 · sandbox `evergreenmirror.com` (production projection)
> **Method**: `improve`-skill audit discipline (recon → parallel category audit → adversarial verify → vet → prioritize), run as a 55-agent Claude Workflow (10 scaling-dimension investigators → per-finding adversarial verifiers → real-pricing cost models → synthesis) + a dedicated multi-approver-concurrency agent + ~15 firsthand code reads by the lead for vetting. 98 findings (7 CRITICAL / 33 HIGH / 50 MEDIUM / 8 LOW; **39 silent-failure**), each carrying a trigger threshold and `file:line` evidence; every CRITICAL/HIGH was re-read by an independent verifier (downgrades applied below).

---

## Context — why this evaluation exists

ITS today runs at ~1 job in validation. The plan is to ramp to **20+ active jobs and 20+ daily portal users this quarter**, with future workstreams (progress reporting, Email Triage, subcontracts, POs/materials, personnel) behind it. The architecture was built and hardened at 1-job scale; the question is which of its assumptions silently break at 20×, where the throughput/cost/operability walls are, and what to fix *before* cutover vs. stage during the ramp.

**Two operator decisions reframe the answer (your input):**
- **Volume is photo-heavy** — most submissions carry photos. That multiplies the Box-storage / D1-cache / PDF-render-memory / weekly-packet-size paths, not just request counts.
- **Humans are *not* the bottleneck** — approvals + Smartsheet work are **distributed across existing Evergreen employees**, not funneled through one Successor-Operator. This *overturns* the audit's stale "#1 = operator labor ceiling": labor is real effort but **absorbed across already-paid staff**. The binding constraints are therefore **technical and economic (Smartsheet), not human** — *provided* those distributed humans are enabled by documentation (see §6).

**Intended outcome**: a leverage-ordered list of what to harden before 20-job cutover, what to stage during the ramp, the real dollar + (distributed) labor cost, and the one architectural bet to revisit.

---

## Executive summary — the five things that will actually hurt

Ranked by binding-ness at 20×20 (after demoting the human-labor ceiling per your model):

1. **Smartsheet week-sheet proliferation is the #1 cost AND a deployment gate.** The per-job-per-week-sheet design creates **~1,040 new sheets/year** (20 × 52). This drives the single largest dollar line — a **Smartsheet plan-tier upgrade ($600 Pro → $2,400 Business/mo)** — *and* may hit a hard per-workspace sheet cap. The two cost agents **disagree on whether that cap is ~500–2,000 (hard) or "unverified"** — so **verifying the real limit + designing a sheet-archival/rollup strategy is the single highest-leverage pre-cutover task.** Everything else (Box, Graph, Resend, Sentry, Cloudflare) is ~$0 incremental.

2. **The single MacBook is the scariest cluster — silent, multi-hour filing outages.** No daemon auto-start after reboot/OS-update (`host-no-runatload`, CRITICAL); no network timeout on SDK calls so one stalled Box/Smartsheet request **hangs a daemon indefinitely** (`host-daemon-no-timeout`); Keychain locked after reboot blocks startup; Box OAuth refresh-token race can persist a **stale token → silent 60-day death** (`box-token-refresh-race`, CRITICAL). When the host is down, submissions queue safely in D1 — but **nothing files, with no customer-visible signal**, and recovery is manual.

3. **Unfiled submissions can be silently lost.** `prune.ts` correctly never evicts `box_verified=0` rows — but if `portal_poll` is down for an extended period, the unfiled queue grows unbounded and, if the host never recovers, those submissions exist **only in D1** (Box/Smartsheet don't have them yet). There is a 5-minute silent window before fetch-failure escalation and **no backlog-size alert** (`unfiled-submissions-never-pruned`, `fetch-failure-silent-window`).

4. **Smartsheet 5,000-row caps silently drop records on the shared log/queue sheets.** At 20 jobs, `ITS_Review_Queue` sees ~60–100 rows/week and `ITS_Errors` grows on every transient degradation — both march toward the **5,000-row hard cap with no rotation**, after which writes **silently fail** (`ss-queue-row-cap`, `ss-errors-row-cap`). Both are silent-data-loss.

5. **Photo-heavy throughput + the Friday serial compile are the throughput walls.** End-of-shift bunching (most of 20–60 submissions/day land 3–6pm) + photo-heavy per-submission cost (photo screen + larger Box upload + larger render) can push an intake cycle **past the 60s launchd interval** → the fcntl lock skips cycles → 2–3h filing latency at peak (`per-sub-portal-cycle-duration`, `host-launchd-interval-underestimated`). Separately, `weekly_generate` compiles all 20 jobs **serially** Friday 14:00; photo-heavy packets accumulate **all per-job PDFs in memory at once**, and a >1h run risks a **silent launchd kill mid-compile with partial WSR rows written** (`launchd-timeout-partial-write`, CRITICAL; `serial-job-bottleneck`; `pdf-memory-accumulation`).

**Cost headline at 20×20 (production projection):**
- **Hard infra/SaaS: ≈ $610–$2,410/mo — essentially *just* the Smartsheet tier decision** ($600–$2,400) + Cloudflare ~$8. Box/Graph/Resend/Sentry/UptimeRobot = $0 incremental (existing contracts / free tiers with large headroom).
- **Anthropic: ~$0/mo now** (portal path is deterministic — confirmed) / **$45–$150/mo** when Email Triage ships.
- **Distributed human effort: ~$1,150–$1,850/mo of work** (approvals + Review-Queue triage + daemon babysitting) — **absorbed across existing Evergreen staff**, not a new cash cost or a single-person ceiling. The real "cost" risk is the **silent failures** (loss/outage) above, which carry no dollar line but are the actual exposure.

---

## 1. Edge cases we will REGULARLY hit (and don't yet handle)

Grouped by where they bite. **🔇 = silent** (no alert; data lost/wrong/delayed with nothing surfaced). Trigger thresholds are at real 20×20 scale.

### Cloud capture (Cloudflare Worker + D1)
- **Photo-vs-payload cap inconsistency → frequent `413`** *(lead-found, not in the 98)*: `/api/submit` allows 8 photos × 400 KB *decoded* (≈4.3 MB base64) but caps `PAYLOAD_MAX` at **1.8 MB** (`worker/index.ts:480–495`). In a photo-heavy reality a field PM attaching ~4+ photos gets `413 too_large` **regularly** — a recurring user-facing failure. *Reconcile the two limits (raise payload cap, or compress/limit photos client-side, or move photos out of the JSON payload).*
- **Amend prefill 500s after 90 days** (`payload-strip-amend-loss`, HIGH): prune strips `payload_json` at 90d; an amend on an older form hits `JSON.parse("")` → 500 (`prune.ts:74` + `index.ts:386`). Recurs weekly once history > 13 weeks.
- 🔇 **Unfiled submissions accumulate / can be lost** (`unfiled-submissions-never-pruned`, HIGH→verified): correct by-design eviction guard, but no backlog alert and no host-recovery safety net.
- **D1 10 GB ceiling is telemetry-only** (`d1-10gb-ceiling-approach`, HIGH): WARN at 6 GB is `console.warn`, not an alert; photo-heavy steady-state ~960 MB (fine) but a stalled prune + queued photos can climb fast.
- 🔇 **Concurrent PDF-chunk corruption** (`concurrent-pdf-chunk-corruption`, →MED) and **chunks deleted mid-read** (`pdf-chunks-deleted-mid-read`, MED).

### Filing throughput (portal_poll → intake)
- **Intake cycle > 60s at peak** (`per-sub-portal-cycle-duration`, HIGH) → fcntl lock (`portal_poll.py:616`) skips cycles → backlog drains over minutes-to-hours. *Not corruption (the lock prevents overlap) — it's latency.*
- **Smartsheet 429 → backlog-starvation loop** (`smartsheet-rate-limit-cascade`, HIGH): a rate-limited cycle returns submissions to `error`, re-pulled next cycle alongside new ones → growing stuck set; circuit-breaker open stalls the whole pipeline.
- **PDF-cache service competes with filing** in the same cycle (`pdf-request-competes-with-intake`, MED); **new-week provisioning multiplies API calls** (`new-week-provisioning-api-multiplier`, MED); 🔇 **week-sheet/folder create race** on same (job,week) bunching (`week-sheet-creation-race`, MED).

### Weekly compile (weekly_generate, Friday 14:00)
- 🔇 **Silent launchd kill + partial writes** (`launchd-timeout-partial-write`, CRITICAL): serial 20-job compile > ~1h risks termination mid-run with some WSR rows written, some not.
- 🔇 **All per-job PDFs held in memory during merge** (`pdf-memory-accumulation`, →MED) — photo-heavy makes this the real ceiling.
- **One slow job blocks 19** (`serial-job-bottleneck`, HIGH); **circuit-breaker trips short-circuit all remaining jobs** (`circuit-breaker-cascading`, →MED); 🔇 **Friday-only calendar SPOF** — catch-up only if watchdog also runs (`host-weekly-generate-friday-spof`, HIGH).

### Send + approval (distributed approvers)
- 🔇 **Stale/empty recipients → silent HELD** (`silent-held-recipients-stale`, HIGH): recipients resolved at send-time from `ITS_Active_Jobs`; a stale contact silently HELDs with no systematic alert. At 20 jobs, 1–2 stale contacts is normal.
- 🔇 **HELD rows accumulate with no scan/alert** (`held-rows-no-scan-no-alert`, MED); 🔇 **CC malformation silently stripped** (`cc-malformation-silent-strip`, MED); **SENDING-state wedge** on post-send stamp failure (`sending-state-wedge`, MED).
- **Multi-approver model is *safe* where it counts** *(lead-vetted)*: the un-check-after-approve race is **mitigated** — F22 `verify_approval` runs fresh per row immediately before dispatch and treats an un-check as a benign `NOT_CURRENTLY_APPROVED` WARN (`weekly_send_poll.py:586–648`); attribution is per-row independent. **LOW**, not the CRITICAL the first pass suggested.
- **Seat/permission trap** *(lead-found + your decision)*: the authorized approver set = Smartsheet **workspace membership** (`approval_verification.py:40–44,176`). N approvers = N seats; an approver added in the UI but **not shared at the workspace level has every send silently fail-closed**. You've assigned permission management to Evergreen + documentation (§6) — keep a watchdog drift-check as belt-and-suspenders.

### Smartsheet at scale
- 🔇 **Review-Queue & Errors 5,000-row silent drop** (`ss-queue-row-cap`, `ss-errors-row-cap`, CRITICAL/HIGH).
- **Shared 300 req/min token contention** across all daemons (`ss-rate-limit-contention`, →MED): ~83% headroom at peak, but `picklist_sync` (hourly) overlapping end-of-shift can spike to 80–100 req/min. *(The first pass wrongly counted human UI edits against this token — humans use their own sessions; the contention is daemon-to-daemon.)*
- **~1,040 sheets/yr proliferation** (`ss-week-sheet-create-burst`, `meta-006`, MED) — see §2/§4; **get_rows full-scan latency on 1000+ row sheets** (`ss-get-rows-full-scan`, MED); **column-cache stale on renames** (`ss-column-cache-stale`, MED).

### Box at scale
- 🔇 **OAuth refresh-token race → stale token** (`box-token-refresh-race`, CRITICAL) + **60-day silent expiry** (`box-oauth-token-silent-expiry`, HIGH): concurrent daemon refreshes can lose the rotated token; `keychain.set_secret` has **no lock** (`keychain-concurrent-write-race`, HIGH).
- **No network timeout → daemon hangs indefinitely** on a slow Box call (`box-rate-limit-no-instrumentation` + `host-daemon-no-timeout`, HIGH) — tech_debt line 269.
- **Single ITS Box user serializes everything** (`box-single-user-bottleneck`, HIGH); 🔇 **unbounded storage, no retention** (`box-storage-growth-unbounded`, MED); folder-create race (`box-folder-race-incomplete-recovery`, →MED).

### Single host & OS
- 🔇 **No auto-start after reboot** (`host-no-runatload`, CRITICAL) → multi-hour manual recovery; **Keychain locked after reboot** (`host-keychain-locked-after-reboot`, →MED); 🔇 **watchdog has no self-health check** (`host-watchdog-blind-to-self`, MED — UptimeRobot is the only external dead-man); **unbounded launchd logs fill disk** (`host-log-unbounded-disk-fill`, MED).

### Alerting / observability (flood at scale)
- **Distinct-error-code storm exhausts the hourly alert cap** (`alert-cap-exhaustion-distinct-codes`, HIGH) — a 20-job incident with many distinct `(script,error_code)` keys can blow past Resend's daily cap; 🔇 **Sentry/Resend free-tier silent drop** beyond quota (`sentry-free-tier-quota-silent-loss`, →MED; `resend-free-tier-quota-close-to-limit`, MED).
- **Anomaly-logger false positives spam the Review Queue** (`anomaly-logger-false-positives-queue-spam`, MED) — 2–5% of submissions security-flagged → alert fatigue; **Review-Queue escalation undefined** (`review-queue-escalation-undefined`, MED).

### Future workstreams (inherited debt)
- 🔇 **No Anthropic spend cap** (`spend-uncapped`, →MED — Check E deferred) and **no retry/backoff** (`anthropic-no-retry`, →MED) — both bite the *moment* Email Triage ships; **D1 base64 cache scales poorly** for Email-Triage attachment volume (`d1-base64-cache-scales-poorly`, MED → move to R2); **Email-Triage ClamAV CPU-bound on single host** (`email-triage-clamav-cpu-bottleneck`, MED).

---

## 2. Bottlenecks, ranked

| # | Bottleneck | Saturates at | Headroom today | Nature |
|---|------------|--------------|----------------|--------|
| B1 | **Smartsheet sheet-count / plan tier** (~1,040 sheets/yr) | ~month 6–18 depending on real cap (UNVERIFIED) | Unknown — **must verify** | Cost + deployment gate |
| B2 | **Single-host availability** (no auto-restart, no API timeouts, token race) | Any reboot / stalled call / 60d idle | None — manual recovery | Silent multi-hour outage |
| B3 | **Filing throughput** (photo-heavy + end-of-shift bunching) | ≥30 submissions/cycle or avg cycle ≥8s | ~1.5–3× at peak; thin on Saturdays (new-week) | Latency (lock prevents corruption) |
| B4 | **Smartsheet 5,000-row caps** (Review_Queue, Errors) | ~Year 1 at 60–100 queue rows/wk | Months | Silent data loss |
| B5 | **Friday serial compile** (memory + launchd timeout) | ≥15 jobs / >1h runtime / photo-heavy | One slow job blocks all | Silent partial failure |
| B6 | **Smartsheet 300 req/min shared token** | picklist_sync × peak overlap | ~83% at peak | Loud (429 → retry/backoff) |
| — | ~~Human approval labor~~ | *demoted* — distributed across Evergreen staff (your model) | Adequate **if documented (§6)** | Effort, not a wall |

---

## 3. How to prepare / avoid — prioritized roadmap

Leverage-ordered (impact ÷ effort, weighted by confidence & silent-ness). **Tier A must land before 20-job cutover.**

### Tier A — must-fix before 20-job cutover (this quarter)
| # | Action | Addresses | Effort | Why now |
|---|--------|-----------|--------|---------|
| A1 | **Verify the real Smartsheet sheet-count cap; decide the week-sheet archival/rollup strategy** (e.g. monthly sheets, or a rollup-DB + per-week archival). Resolve the $600 vs $2,400 tier. | B1, proliferation | M | #1 cost + the only hard deployment gate |
| A2 | **Single-host resilience pack**: launchd `RunAtLoad`+`KeepAlive` (auto-start after reboot); wrap **all** SDK network calls (Box first) in hard timeouts; handle Keychain-locked-after-reboot. | B2, host-no-runatload, host-daemon-no-timeout, host-keychain-locked | M | Removes the scariest silent outages |
| A3 | **Box OAuth refresh-token cross-process lock** (+ `keychain.set_secret` lock) and a **50-day idle warning**. | box-token-refresh-race, keychain-concurrent-write-race, box-oauth-token-silent-expiry | S–M | Prevents silent 60-day death |
| A4 | **Unfiled-queue safety**: backlog-size alert (`box_verified=0` count) + outage escalation if portal_poll down > N hours. | unfiled-submissions-never-pruned, fetch-failure-silent-window | S | Closes the silent-loss path |
| A5 | **Smartsheet row-cap rotation** for `ITS_Review_Queue` + `ITS_Errors` (archive/rotate before 5,000). | ss-queue-row-cap, ss-errors-row-cap | S–M | Two silent-data-loss CRITICALs |
| A6 | **weekly_generate hardening**: per-job timeout + idempotent/resumable partial-write recovery; stream PDFs (don't hold all in memory); ensure it can't be launchd-killed mid-run (background/chunk). | launchd-timeout-partial-write, pdf-memory-accumulation, serial-job-bottleneck | M | Photo-heavy makes this acute |
| A7 | **Photo/payload reconciliation** (the 413 inconsistency) + **amend-prefill 90-day guard**. | photo-vs-payload, payload-strip-amend-loss | S | Recurring user-facing failures |
| A8 | **Operator & User Enablement Documentation program** — manifest + config data-dictionary PDF + per-capability guides/troubleshooting trees (see §6). | the distributed-operator model itself | L (program) | Enabling precondition for "humans aren't the bottleneck" |

### Tier B — stage during the ramp
- Smartsheet rate instrumentation + per-cycle pacing/backoff; separate API token if peak overlap bites (B6).
- Throughput: parallelize intake (3–5 worker pool), tune `PENDING_LIMIT`/interval, decouple PDF-cache service from the filing drain (B3).
- Box rate instrumentation, folder-race hardening, **storage retention/archival policy**.
- Alerting: distinct-code storm cap, Sentry/Resend quota headroom monitoring, **Review-Queue triage SLA + escalation**, anomaly-logger threshold tuning.
- Send: **HELD-row scan/alert** + **stale-recipient detection** (silent-held-recipients-stale).
- **Anthropic spend cap (finish Check E) + retry/backoff** — land *before* Email Triage ships.
- **Approver-set drift watchdog** (workspace membership vs intended approvers).

### Tier C — watch, don't build yet
- **Local-first ceiling**: the one architectural bet to revisit — moving *filing* off the single host (or HA the MacBook) when jobs approach ~50 (`meta-001`).
- D1 base64 cache → **R2** when Email-Triage attachment volume arrives (`d1-base64-cache-scales-poorly`).
- Multi-Box-user scaling; per-customer DB split.

---

## 4. What it will cost (20×20, production projection)

| Bucket | Monthly | Driver / note |
|--------|---------|---------------|
| **Cloudflare (Worker + D1)** | **~$8** | $5 base + ~$3 CPU overage; D1 ~960 MB (91% headroom); all other D1 metrics <0.01% of included. |
| **Smartsheet plan upgrade** | **$600–$2,400** | The dominant variable. Pro vs Business hinges on the real sheet-count cap (A1). **The whole "what will it cost" answer is mostly this line.** |
| **Box / Graph / M365** | **$0 incremental** | Existing Enterprise/M365 contracts; ITS adds ~1.2–3.6 GB/yr + ~20 sends/wk — far inside quotas. |
| **Resend / Sentry / UptimeRobot** | **$0** | Free tiers; ~90–99% headroom in a healthy system (binding only during an instability storm). |
| **Anthropic** | **~$0 now / $45–$150 future** | Portal path is deterministic (zero LLM — confirmed). Email Triage adds haiku-classify + sonnet-extract later. |
| **Hard subtotal** | **≈ $610–$2,410/mo** | ≈ the Smartsheet tier decision + ~$8. |
| **Distributed human effort** | **~$1,150–$1,850/mo of work** | Approvals (~5h/mo) + Review-Queue triage (~12–20h/mo) + daemon babysitting + Tier-2/3. **Absorbed across existing Evergreen staff per your model — not a new cash cost or a single-person ceiling.** Documentation (§6) is what keeps this distributed and low-error. |
| **Risk-of-loss (no dollar line)** | **the real exposure** | Silent multi-hour outages, unfiled-queue loss, 5,000-row drops, stale-recipient HELDs. Tier-A buys this down. |

**First binding limit (ETA):** Smartsheet sheet-count — *if* the real cap is ~500 (Team), ~month 6; if ~1,000–2,000 (Pro), Year 1–2. **Verify before cutover (A1).**

---

## 5. Meta read

- **Local-first ceiling**: the single-MacBook + launchd model is *adequate* at 20 jobs **only with the Tier-A resilience pack**; it is the architecture's load-bearing bet and the thing to revisit first as you approach ~50 jobs or add concurrent workstreams (`meta-001`). The doctrine's "no cloud execution through Phase 4" is the constraint to re-examine then — not now.
- **Successor-operator model**: holds under your distributed-approver model for *routine* work, but still assumes **perpetual Seth availability for Tier-3** (high-class faults: send-gate, secrets, doctrine, code). At 20 jobs the Tier-2/Tier-3 escalation rate rises; define a Tier-3 backup or an escalation SLA (`meta-002`). Training cadence is undefined (`meta-009`).
- **The one bet to revisit**: not the send gate, not the LLM layer — it's **the per-job-per-week-Smartsheet-sheet data model** (drives B1 cost + proliferation + get_rows latency) and **the single host** (B2). Both are cheap to mitigate now and expensive to retrofit later.

---

## 6. Operator & User Enablement Documentation program (your explicit P1 requirement)

**Principle (your direction):** *if it is an ITS function or capability, it has an explicit PDF guide / user manual / comprehensive troubleshooting tree.* This is not a doc nicety — it is the **enabling precondition** for "humans aren't the bottleneck": a distributed, multi-employee Smartsheet operating model without manuals converts the labor bottleneck into a **silent-error surface** (wrong cells edited, approvals mis-set, queue items rotting). Documentation is also a **cross-cutting mitigation** that softens half the §1 operability findings.

### 6a. Capability → Required-Guide manifest (the actionable first deliverable — you need the list before the PDFs)
| Surface | Capabilities needing a guide | Guide type | Audience |
|---------|------------------------------|-----------|----------|
| **Safety Portal SPA** | login/session, fill form, photo upload, amend, request/download PDF, signature | User manual + troubleshooting tree | Field PMs |
| **Portal Admin** | user CRUD, roles, submit-as, form editor, publish | Operator guide + troubleshooting | Evergreen admins |
| **Smartsheet — Config** | `ITS_Config` (**the per-row/per-cell data-dictionary PDF you specified**), `Picklist_Sync_Config`, `ITS_Project_Routing`, `ITS_Trusted_Contacts` | Data-dictionary PDF (every line/cell + how to use) | Operators |
| **Smartsheet — Approval** | `WSR_human_review` (review/approve/Send-Now/Scheduled), workspace-membership = approval authority + **the silent fail-closed rule** | User manual + troubleshooting | Approvers |
| **Smartsheet — Jobs/Forms** | `ITS_Active_Jobs` (recipients!), `ITS_Forms_Catalog`, `Orphaned Reports` | Operator guide | Operators |
| **Smartsheet — Logs/Queues/Daemons** | `ITS_Review_Queue` (triage), `ITS_Errors`, `ITS_Quarantine`, `ITS_Daemon_Health` (12-col heartbeat read) | Operator guide + troubleshooting tree | Operators |
| **Smartsheet — Masters** | Vendor/Subcontractor/Equipment DBs (picklist sources) | Data-dictionary | Operators |
| **Daemons / CLIs** | portal_poll, weekly_generate, weekly_send(_poll), publish_daemon, picklist_sync, watchdog, compile_now, `portal_admin` | Troubleshooting tree (symptom → Tier-2 repair → escalate-to-Seth) | Successor-Operator |
| **Future workstreams** | Progress Reporting, Email Triage, Subcontracts, POs/Materials, Personnel, AI Employee | Same template, as built | Mixed |

### 6b. Build & currency strategy
- **Reuse what exists**: the 13 `docs/runbooks/*` + the §43 successor-remediation entries are the *skeleton* for the operator/troubleshooting guides; the user-facing PDFs are the polished, distributable layer on top.
- **Production tooling**: the Adobe Express / visual-design skill (`create_visual_design_express_skill`) for clean, in-Smartsheet-referenceable legends; `form_pdf.py`'s existing render path is reusable; host/distribute in Box + link from each Smartsheet's top rows.
- **Doc-currency risk (a real maintenance cost, not a one-time build)**: PDFs for "every function" **drift** as forms/workflows change — the form editor publishes continuously. Without a keep-current discipline (analogous to the repo's `doc-reconciliation-auditor` for code↔doctrine drift), the library rots. Budget an owner + a per-release "does a guide change?" gate. *Treat this as an ongoing line, not a checkbox.*

---

## Appendix A — top findings (CRITICAL + HIGH, post-verification)

Full 98-finding structured inventory preserved at `…/tasks/w7e9da3b0.output` (`result.raw_findings`). 🔇 = silent.

| ID | Sev (verified) | 🔇 | Trigger | Title |
|----|------|----|---------|-------|
| launchd-timeout-partial-write | CRIT | 🔇 | compile >1h / 15+ jobs | Friday compile silently killed, partial WSR rows |
| box-token-refresh-race-silent | CRIT | 🔇 | ≥2 daemons refresh within 60s | Stale Box token persisted → silent auth death |
| host-no-runatload-multi-hour-outage | CRIT | 🔇 | any reboot/OS-update/sleep | No daemon auto-start → multi-hour manual recovery |
| ss-queue-row-cap | CRIT→HIGH | 🔇 | >~1000 queue rows/mo | Review_Queue 5,000-row silent drop |
| ss-errors-row-cap | CRIT→HIGH | 🔇 | >~1000 errors/mo | ITS_Errors 5,000-row silent loss |
| payload-strip-amend-loss | CRIT→HIGH | | history >13 wks | Amend prefill 500 after 90-day strip |
| host-fcntl-silent-heartbeat-loss | CRIT→MED | 🔇 | ≥2 fast daemons on shared state | Silent heartbeat write loss → watchdog blind |
| per-sub-portal-cycle-duration | HIGH | | ≥30 subs/cycle + new week | Intake cycle exceeds 60s → skipped cycles |
| smartsheet-rate-limit-cascade | HIGH | | sustained 429, ≥40 subs/day | Backlog-starvation loop |
| silent-held-recipients-stale | HIGH | 🔇 | ≥10 jobs, 1–2 stale contacts | Stale TO → silent HELD, no alert |
| approval-lag-cascade-week-lost | HIGH | | ≥15 jobs, 1-day delay | Approval delay → week's sends lost till next Monday |
| serial-job-bottleneck | HIGH | | ≥5 jobs >50 subs/wk | One slow job blocks all 19 |
| pdf-memory-accumulation | HIGH→MED | 🔇 | >150 photo-subs/wk/job | All per-job PDFs in memory at merge |
| d1-10gb-ceiling-approach | HIGH | | photo-heavy + stalled prune | 6 GB WARN is telemetry-only |
| box-single-user-bottleneck | HIGH | | ≥10 concurrent Box ops | All Box serialized through one ITS user |
| box-rate-limit-no-instrumentation | HIGH | | poll + 25 dl + 50 subs×3 | Box rate spikes uninstrumented |
| keychain-concurrent-write-race | HIGH | | concurrent token writes | `set_secret` unlocked → lost updates |
| host-daemon-no-timeout-api-hang | HIGH→MED | 🔇 | backend response >30s | Daemon hangs indefinitely |
| host-weekly-generate-friday-spof | HIGH | 🔇 | Friday crash | Calendar SPOF; catch-up only if watchdog runs |
| alert-cap-exhaustion-distinct-codes | HIGH | | 5+ distinct keys/15min | Alert cap exhausted → failures unalerted |
| spend-uncapped | HIGH→MED | 🔇 | any Email-Triage deploy | No Anthropic spend ceiling |
| meta-001-local-first-ceiling | HIGH | 🔇 | 20 jobs + 8 daemons | Single-host operational ceiling |
| meta-002-successor-operator-escalation | HIGH | | novel/high-class fault | Assumes perpetual Seth availability |

*(Also HIGH and downgraded-to-MED by verifiers: `toctou-job-active-race`→MED, `concurrent-pdf-chunk-corruption`→MED, `unfiled-submissions-never-pruned`→MED, `ss-rate-limit-contention`→MED, `ss-picklist-sync-n-squared`→MED, `box-folder-race`→MED, `circuit-breaker-cascading`→MED, `monday-batch-send-window-collision`→MED, `host-keychain-locked-after-reboot`→MED, `sentry-free-tier-quota`→MED, `anthropic-no-retry`→MED, `box-oauth-token-silent-expiry`→MED, `index-pagination-convergence`→LOW.)*

## Appendix B — method, scope, and honest caveats
- **Audited**: all 10 scaling dimensions (Worker/D1, poll+intake, weekly_generate, send+approval, Smartsheet, Box, single-host, alerting, Anthropic/future, meta) + multi-approver concurrency; cost via live 2026 pricing lookups.
- **Not deeply audited**: the SPA frontend internals, the form-editor/publish pipeline correctness, test-coverage depth per module, and the future workstreams' *unwritten* code (assessed only as inherited risk).
- **Key uncertainty to resolve before acting on A1**: the **real Smartsheet per-workspace/plan sheet-count cap** — the two cost agents disagreed (≈500–2,000 hard vs "unverified"). This gates the $600-vs-$2,400 decision and the whole cost headline.
- **Vetting corrections applied** (subagents over-report): the un-check-after-approve "silent send" was **downgraded LOW** (F22 is the re-check); the Smartsheet-rate-limit "human UI edits consume the token" claim was **rejected** (humans use their own sessions); the human-labor "#1 bottleneck" was **demoted** per your distributed-operator model.
- This is the **sandbox** (`evergreenmirror.com`); numbers are the production projection.

---

### Deliverable note
Per your selection (*Eval + prioritized roadmap*), this file is the evaluation. On approval I can: (a) materialize the Tier-A items as self-contained executable plan files (`plans/NNN-*.md`, improve-skill style) for an executor; (b) produce the §6 documentation manifest + the first config data-dictionary PDF; and/or (c) verify the Smartsheet sheet-count cap (A1) directly via the Smartsheet API. No code changes were made — this was read-only.


---

## Part II — Tier-A Executable Implementation Specs (logged, read-only)

These are self-contained plans for later execution by an operator or engineer with zero context; they capture the current state, verification steps, and done criteria needed to harden ITS at 20 active jobs, 20+ daily users. Nothing here changes code immediately — execution occurs when advantageous. All plans follow read-only verification first, then targeted changes, then regression testing per the doctrine (Invariant 1, state_io discipline, preservation-over-refactor). Plans marked needs-revision require resolver review before operator run; required fixes are listed in Reviewer notes below.

### Execution Order & Dependency Table

| Plan | Title | Priority | Effort | Risk | Depends-on | Status |
|------|-------|----------|--------|------|-----------|--------|
| A1 | Smartsheet sheet-cap verification | P1 | M | MED | None | needs-revision |
| A2 | Single-host resilience pack | P1 | M | MED | None | needs-revision |
| A3 | Box OAuth Keychain idle warning | P1 | M | MED | None | needs-revision |
| A4 | Unfiled-submission backlog alert | P1 | M | LOW | Decision_Phase5 | needs-revision |
| A5 | Smartsheet row-cap rotation | P1 | M | MED | None | needs-revision |
| A6 | weekly_generate hardening | P1 | M | MED | None | needs-revision |
| A7 | Photo payload reconciliation | P1 | M | MED | None | needs-revision |

**Execution sequence:** A1 → A2 → A3 → (A4 gates on Phase-5 portal-transport go-live) → A5 → A6 → A7. A1 is the gating investigation; all others are independent after A1 completion. Recommended parallelization: run A2, A3, A5, A6, A7 in parallel after A1 decision; A4 waits for Phase-5 portal-transport.

---

### A1 — Smartsheet Sheet-Cap Verification and Archival/Rollup Design
**P1 / M / MED / None / Infrastructure/Cost Hardening / ss-week-sheet-create-burst, meta-006**

**Why this matters:**
The per-job-per-week-sheet model at scale (20 jobs × 52 weeks = ~1,040 sheets/year) drives the single largest Smartsheet cost (plan-tier upgrade $600 Pro → $2,400 Business) AND may hit a hard per-workspace sheet cap. The two cost agents disagreed on whether the cap is ~500–2,000 (hard) or unverified. Verification of the REAL cap and design of an archival/rollup strategy are the #1 pre-cutover hardening task. This plan gates ALL downstream code changes on the verification results.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/safety_reports/week_sheet.py` | Per-job, per-Saturday-week sheet creation | `def ensure_week_sheet(project_name: str, work_date: date) -> int:` creates one sheet per (job, week); called from `weekly_generate._compile_job_week()` with NO cap check |
| `/Users/sethsmith/its/shared/smartsheet_client.py` | Current API boundary | `find_sheet_by_name_in_folder()` is only method to inspect sheets; NO `list_all_sheets()` or `count_sheets()` method exists |
| `/Users/sethsmith/its/shared/sheet_ids.py` | Workspace constants | `WORKSPACE_SAFETY_PORTAL = 194283417429892`, `WORKSPACE_ARCHIVE = 5528280611743620` |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify Python setup and state | `.venv/bin/python -c "from shared import keychain; print('OK')"` | OK (keychain accessible) |
| Type-check verification script | `.venv/bin/mypy scripts/verify_sheet_cap.py` | No errors |
| Run verification (dry-run) | `.venv/bin/python scripts/verify_sheet_cap.py --output json \| python -c "import json, sys; d=json.load(sys.stdin); print(f\"Sheets: {d['total_sheet_count']}, Projection: {d['annual_projection_at_20_jobs']}/yr\")"` | Output shows counts matching current active jobs × elapsed weeks |

**Scope — in:**
- Read-only enumeration of sheets in WORKSPACE_SAFETY_PORTAL via Smartsheet REST
- Research: official Smartsheet per-workspace sheet cap documentation + support inquiry + community sources
- Design document: 3 archival/rollup options (Archive-on-Closure, Monthly-Sheets, Rolling-Per-Job-Sheet) with pros/cons/code-surface/time-estimate
- Decision tree: hard-cap-blocking (P0) vs. cost-only (P1) vs. defer (POST-CUTOVER)
- Runbook: operator re-run instructions

**Scope — out:**
- Implementing ANY archival/rollup code (that is a downstream P2 task gated on this plan's findings)
- Modifying ITS_Config, state files, or production Smartsheet data
- Monitoring/alerting on sheet count (post-archival-design task)

**Steps:**

1. **Build read-only verification script: count sheets in WORKSPACE_SAFETY_PORTAL**
   - Create `/Users/sethsmith/its/scripts/verify_sheet_cap.py` as a standalone CLI.
   - Authenticate with `shared.keychain.get_secret('ITS_SMARTSHEET_TOKEN')`.
   - Call Smartsheet REST `GET /workspaces/194283417429892?includeAll=true`, recursively traverse folders to enumerate all sheets.
   - Implement pagination (pageSize=500, explicit pageNumber loop, exponential backoff on 429).
   - Output JSON: `{total_sheet_count, job_folders: {name: count, …}, oldest_sheet_date, newest_sheet_date, sheets_per_week_avg, annual_projection_at_20_jobs}`.
   - Exit 0, never modify Smartsheet, be idempotent.
   - **Verify:** `.venv/bin/python scripts/verify_sheet_cap.py --output json > /tmp/sheet_count.json && python -c "import json; d=json.load(open('/tmp/sheet_count.json')); assert all(k in d for k in ['total_sheet_count','job_folders','annual_projection_at_20_jobs']); print('✓ Schema valid')"`. JSON parses, all keys present.

2. **Research Smartsheet per-workspace sheet cap from public + support sources**
   - Three-leg research: (1) Public docs (smartsheet.com/pricing, developer.smartsheet.com/docs/api-docs#limits), (2) Support inquiry (if customer contact available), (3) Community (GitHub issues, Stack Overflow).
   - Document findings in `/Users/sethsmith/its/docs/verifications/smartsheet_sheet_cap_verification.md` with sections per tier (Free/Pro/Business/Premier), each with cap value OR "unknown", sources with URLs+dates, confidence.
   - **Verify:** `cat docs/verifications/smartsheet_sheet_cap_verification.md && grep -iE 'free|pro|business|premier|source|url|date' docs/verifications/smartsheet_sheet_cap_verification.md | wc -l`. File exists, tier sections present, sources cited.

3. **Design 2–3 archival/rollup options with code-surface analysis**
   - Create `/Users/sethsmith/its/docs/designs/sheet_archival_strategy.md` with three complete options: (A) Archive-on-Closure, (B) Monthly-Sheets, (C) Rolling-Per-Job-Sheet.
   - For each: functional description, pros, cons, code touch-points (grep results: file paths + function names), test impact, API changes, time estimate, rollback risk.
   - Include RECOMMENDATION section identifying preferred option with risk/schedule justification.
   - **Verify:** `wc -l docs/designs/sheet_archival_strategy.md` ≥100 lines. `grep -c '##' docs/designs/sheet_archival_strategy.md` ≥5 (section headings).

4. **Compute margin analysis: cap vs. projection, decide archival urgency**
   - Using (1) current_count, (2) cap_value, (3) annual_rate @ 20 jobs, compute: `MARGIN = cap_value - current_count - (annual_rate × 2.0)`.
   - Decision logic: MARGIN < 0 → P0 (hard blocker). 100 ≤ MARGIN < 500 → P1 (soft blocker). MARGIN ≥ 500 or no cap → DEFER.
   - Update `docs/verifications/smartsheet_sheet_cap_verification.md` with DECISION section: `DECISION: [P0|P1|DEFER|CANNOT-PROCEED] — [1–2 sentence justification with numbers]`.
   - **Verify:** `tail -10 docs/verifications/smartsheet_sheet_cap_verification.md | grep DECISION`. Output matches regex `^DECISION: (P0|P1|DEFER|CANNOT-PROCEED)`.

5. **Create runbook documentation for operator**
   - Create `/Users/sethsmith/its/docs/runbooks/sheet_cap_verification.md` with: Prerequisites, Command (exact invocation), Expected output (JSON schema), Interpretation guide, Margin calculation, Escalation criteria.
   - **Verify:** `test -f docs/runbooks/sheet_cap_verification.md && grep -iE 'command|prerequisites|output|interpretation|escalation' docs/runbooks/sheet_cap_verification.md | wc -l` ≥5.

**Test plan:**
The verification script IS the test. Create `/Users/sethsmith/its/tests/test_verify_sheet_cap.py` with unit tests: (1) JSON schema validation, (2) pagination mock (10 pages × 100 sheets each, verify full fetch), (3) folder traversal (nested folders counted), (4) projection math. Run: `.venv/bin/pytest tests/test_verify_sheet_cap.py -v` expect all pass. Smoke test: run script against staging/sandbox workspace (if available), confirm output matches manual count.

**Done criteria:**
- ✓ verify_sheet_cap.py created, runs without error, outputs valid JSON with keys: total_sheet_count, job_folders, annual_projection_at_20_jobs
- ✓ smartsheet_sheet_cap_verification.md exists with cap values (or "unknown") for each tier + sources with URLs/dates
- ✓ sheet_archival_strategy.md has 3 complete options (A/B/C), each with 8 sub-sections, total >100 lines
- ✓ RECOMMENDATION section identifies preferred option with risk/schedule justification
- ✓ smartsheet_sheet_cap_verification.md includes DECISION section: P0/P1/DEFER/CANNOT-PROCEED with numeric margin justification
- ✓ test_verify_sheet_cap.py covers schema validation + pagination + math; runs green
- ✓ sheet_cap_verification.md runbook documents command, expected output, interpretation guide, troubleshooting
- ✓ All artifacts are READ-ONLY (no Smartsheet writes, no code changes)

**STOP conditions:**
- Smartsheet REST API returns non-2xx (401/403/429) → verification blocked; escalate token/permission/rate-limit
- Verification script fails to parse workspace response → confirm token + endpoint; do not proceed
- Research finds cap ≤ current_count → potential overrun condition; escalate immediately
- Design phase reveals Option-A requires >50 hrs → escalate for feasibility review

**Maintenance notes:**
verify_sheet_cap.py is a snapshot tool; re-run quarterly to track growth rate. If new workspaces added, update script accordingly. If DECISION changes (e.g., DEFER→P1), design doc priority should be re-reviewed. Post-implementation: add quarterly margin check to watchdog (separate task, out of A1 scope).

**Doctrine notes:**
Invariant 1: This plan is generation-only (reading counts, no sends). Invariant 2: Smartsheet response is external data, treated as authoritative. State files: zero writes to ~/its/state/. Section 14: investigation-only; zero code refactoring. Keychain: uses `shared.keychain.get_secret()`, honoring macOS Keychain discipline.

**Corrections:**
The finding is ACCURATE. Code inspection confirms: (1) week_sheet.ensure_week_sheet() creates one sheet per (job, week) in WORKSPACE_SAFETY_PORTAL. (2) Called from weekly_generate._compile_job_week() weekly. (3) At 20 jobs × 52 weeks = 1,040 sheets/year — correct order of magnitude. (4) NO existing guard or cap-check in code; growth is unbounded. Finding's concern re: hard cap + cost impact is VALID.

---

### A2 — Single-Host Resilience Pack: Daemon Auto-Start + Network Timeouts + Keychain-Locked Handling
**P1 / M / MED / None / Infrastructure Hardening / host-no-runatload-multi-hour-outage, host-daemon-no-timeout-api-hang, host-keychain-locked-after-reboot**

**Why this matters:**
The entire execution layer runs on one MacBook. After reboot or OS update, (a) LaunchAgents with RunAtLoad=false remain dormant until GUI login, creating multi-hour silent filing gaps; (b) Box SDK and unconfigured Smartsheet SDK calls have no network timeout, so a hung TCP connection stalls a daemon cycle indefinitely; (c) Keychain locked post-reboot causes `security` CLI to hang without timeout, blocking daemon startup. All three are silent failures at scale.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/scripts/launchd/template.plist` | LaunchAgent template | `<key>RunAtLoad</key><false/>` — agents do NOT auto-start after reboot |
| `/Users/sethsmith/its/shared/box_client.py` | Box SDK wrapper | `_call()` passes no timeout parameter to boxsdk operations |
| `/Users/sethsmith/its/shared/smartsheet_client.py` | Smartsheet SDK wrapper | `client = smartsheet.Smartsheet(token, user_agent="its")` — no timeout= kwarg |
| `/Users/sethsmith/its/shared/keychain.py` | Keychain access | `subprocess.run([\"security\", \"find-generic-password\", …], check=True, capture_output=True, text=True)` — no timeout parameter |
| `/Users/sethsmith/its/shared/graph_client.py` | Reference (has timeouts) | `REQUEST_TIMEOUT = (10s connect, 30s read)` applied to every call — best-practice pattern to follow |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Validate Python syntax + type safety | `.venv/bin/mypy . && .venv/bin/ruff check .` | No errors |
| Run existing client tests | `.venv/bin/pytest -q tests/test_smartsheet_client.py tests/test_box_client.py tests/test_keychain.py` | All pass |
| Verify plist syntax | `plutil -lint ~/Library/LaunchAgents/org.solutionsmith.its.*.plist 2>/dev/null \| grep -c 'OK' \|\| echo 'plutil failed'` | All plists valid |
| Smoke: test daemon poll_once() | `cd /Users/sethsmith/its && .venv/bin/python -c 'from safety_reports import portal_poll; portal_poll.poll_once()' 2>&1` | Completes without timeout, logs are clean |

**Scope — in:**
- scripts/launchd/ — all 8 plist files: set RunAtLoad=true
- shared/smartsheet_client.py — configure SDK client with 30s timeout
- shared/box_client.py — wrap operations with 30s network timeout
- shared/keychain.py — add 10s subprocess timeout
- tests/ — add/enhance timeout behavior tests

**Scope — out:**
- LaunchDaemon migration (requires root; deferred to Phase 1.5)
- Keychain auto-unlock (macOS OS-level control)
- Watchdog/restart recovery (separate hardening; this prevents hangs from occurring)

**Steps:**

1. **Fix LaunchAgent auto-start after reboot (RunAtLoad + KeepAlive trade-off)**
   - DECISION POINT: LaunchAgents are per-user, load at GUI login only, NOT at boot. Operator must decide: (A) keep as LaunchAgents, set RunAtLoad=true, require manual login post-reboot or enable Auto-Login. (B) migrate to LaunchDaemons (deferred to Phase 1.5, requires root).
   - For A2 (sandbox), implement **Option A**: Edit scripts/launchd/template.plist and all 8 individual plist files to set `<key>RunAtLoad</key><true/>`.
   - Add caveat: "KeepAlive=true only for jobs that must auto-restart; interval jobs without KeepAlive will NOT auto-restart if they crash (observable failure that watchdog detects)."
   - Update scripts/launchd/install.sh comment to clarify LaunchAgent + RunAtLoad behavior and Auto-Login requirement.
   - **Verify:** `grep -h 'RunAtLoad' /Users/sethsmith/its/scripts/launchd/*.plist \| sort \| uniq`. All show `<key>RunAtLoad</key><true/>`.

2. **Wrap Box SDK operations with network timeout**
   - Box SDK does not expose a timeout parameter. Implement per-operation timeout using **session-injection pattern** (NOT global socket.setdefaulttimeout, which is too broad for future multi-threading).
   - In shared/box_client.py, import `from requests import Session` and configure the session with timeout before passing to boxsdk client. Example: `session = Session(); session.timeout = 30` (or use adapter-level timeout if SDK exposes it).
   - Catch `requests.Timeout` and re-raise as `BoxError` (matching existing error translation).
   - Add comment linking to boxsdk source for timeout configuration method.
   - **Verify:** `.venv/bin/python -c "from shared import box_client; import inspect; src=inspect.getsource(box_client._call); print('timeout' in src or 'session' in src)"` → should show timeout or session handling present.

3. **Configure Smartsheet SDK client with timeout**
   - Check if smartsheet.Smartsheet() constructor accepts `timeout` parameter (inspect SDK docs or trial).
   - If yes: modify shared/smartsheet_client.py line 130 to pass `timeout=30`.
   - If no: access client's internal session (typically `client.session` or `client._session`) after construction and set `session.timeout = 30`.
   - Document with a comment linking to SDK documentation.
   - **Verify:** `.venv/bin/python -c "from shared import smartsheet_client; c = smartsheet_client.get_client(); print(hasattr(c, 'session') or 'timeout configured')"` → runs without error.

4. **Add timeout to Keychain security CLI access**
   - In shared/keychain.py, import `subprocess.TimeoutExpired`.
   - Modify both `get_secret()` and `set_secret()` to add `timeout=10` to subprocess.run() calls.
   - Wrap calls in try/except: catch `subprocess.TimeoutExpired` and re-raise as `KeychainError` with message "Keychain access timed out after 10s (keychain may be locked; enter your password and retry)."
   - Add module-level constant `KEYCHAIN_TIMEOUT_SECONDS = 10.0`.
   - **Verify:** `grep -A5 'subprocess.run' /Users/sethsmith/its/shared/keychain.py \| grep -E '(timeout=|TimeoutExpired)'`. Both calls include timeout; TimeoutExpired is caught.

5. **Add/enhance timeout tests**
   - tests/test_box_client.py: add test_box_call_socket_timeout (mock _call to raise socket.timeout, verify BoxError caught)
   - tests/test_smartsheet_client.py: add test_smartsheet_sdk_timeout_configured (verify get_client sets timeout)
   - tests/test_keychain.py: add test_keychain_get_secret_timeout (mock subprocess.run to raise TimeoutExpired, verify KeychainError with correct message)
   - **Verify:** `.venv/bin/pytest -q tests/test_box_client.py tests/test_smartsheet_client.py tests/test_keychain.py -k timeout` → all pass.

6. **Verify no regressions and doctrine compliance**
   - Run full test suite: `.venv/bin/pytest -q tests/`
   - Type check + lint: `.venv/bin/mypy . && .venv/bin/ruff check .`
   - Capability gating: `.venv/bin/pytest -q tests/test_capability_gating.py`
   - Verify plists: `plutil -lint ~/Library/LaunchAgents/org.solutionsmith.its.*.plist`
   - Verify state_io discipline: no `Path.write_text` calls added to ~/its/state/
   - Smoke test: unload daemon, run poll_once() manually, verify completes within 60s on normal network.
   - **Verify:** All commands pass with 0 failures.

7. **Document operator guidance and cutover**
   - Update scripts/launchd/README.md or docs/runbooks/daemon-startup.md with: LaunchAgent behavior, Auto-Login requirement, timeout values (Box 30s, Smartsheet 30s, Keychain 10s) + rationale, troubleshooting notes.
   - **Verify:** `grep -i 'timeout\|auto.login\|launchagent' /Users/sethsmith/its/docs/runbooks/*.md` shows documentation present.

**Test plan:**
Model on existing client test patterns. Unit tests cover timeout behavior: socket.timeout, requests.Timeout, subprocess.TimeoutExpired are caught + re-raised as appropriate errors. Regression tests: all existing client tests pass unchanged. Smoke test (operator run): unload daemon, call poll_once() manually, measure runtime (should complete within 60s on normal network, no indefinite hang).

**Done criteria:**
- ✓ All 8 plist files have `<key>RunAtLoad</key><true/>`
- ✓ Box SDK operations enforce 30s timeout via session injection (NOT global socket.setdefaulttimeout)
- ✓ Smartsheet SDK client configured with 30s timeout
- ✓ Keychain security CLI calls have 10s subprocess timeout; TimeoutExpired caught + re-raised as KeychainError
- ✓ All existing tests pass (.venv/bin/pytest -q)
- ✓ No new type/lint errors (.venv/bin/mypy . && .venv/bin/ruff check .)
- ✓ Capability gating tests pass
- ✓ Plutil validates all installed plist files
- ✓ Operator documentation updated with timeout values and Auto-Login guidance
- ✓ Daemon poll_once smoke test completes within 60s on normal network

**STOP conditions:**
- Any test failure indicates regression — stop and debug before proceeding
- Plutil rejects a plist — fix XML before loading
- Daemon poll_once hangs for >2 min — verify timeout configuration
- Type/lint errors appear — stop and fix before merge
- Capability-gating test fails — Invariant 1 violation, do not proceed

**Maintenance notes:**
Daemon startup post-reboot: Operator MUST log in to the Mac (or enable Auto-Login) after reboot/OS update. Lack of login = multi-hour silent dormancy. Timeout error logs indicate transient network issues; operator should check network + remote service health when frequent. Keychain locked symptom: daemon logs "Keychain access timed out"; operator enters Mac password to unlock. KeepAlive trade-off: no auto-restart for interval jobs (watchdog detects missing heartbeat within ~15 min max). Performance impact: session-level timeout (box_client) is safer than global socket.setdefaulttimeout; document caveat for future multi-worker refactor.

**Doctrine notes:**
Invariant 1: changes are transparent; no send capability added. Invariant 2: no weakening of content validation; timeouts prevent hangs only. State files: no writes to ~/its/state/; all changes in shared/*_client.py + scripts/launchd/*.plist. Kill switch: timeouts are caught at daemon entry by @its_error_log (fail-open for logging, fail-closed for send gate). Preservation: minimal targeted changes, no refactoring of working modules.

**Corrections:**
The three findings are accurate as verified against live code. A minor refinement: Smartsheet SDK timeout (Step 3) requires pre-coding verification; if SDK doesn't expose timeout in constructor, fall back to session wrapping. This does not invalidate the finding — it adapts the fix approach.

---

### A3 — Box OAuth Refresh Token Cross-Process Lock + Keychain Write Lock + 50-Day Idle Warning
**P1 / M / MED / None / Hardening — Crypto/Auth / box-token-refresh-race-silent, keychain-concurrent-write-race, box-oauth-token-silent-expiry**

**Why this matters:**
Box rotates the refresh token on every OAuth2 exchange. If two daemons (portal_poll 60s cadence + weekly_generate Friday + compile_now_poll on-demand) concurrently refresh without serialization, both read the old token and call _store_tokens with different new tokens — one write clobbers the other, and ITS reads stale credentials within 60 days, causing silent auth death with no daemon recovery and no operator warning. Adding cross-process locking to the refresh flow, a write lock around keychain.set_secret, and a 50-day idle watchdog check closes all three attack vectors and restores observability.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/shared/box_client.py` (line 118) | Box OAuth token rotation | `keychain.set_secret(KC_REFRESH_TOKEN, refresh_token)` in _store_tokens — NO LOCKING |
| `/Users/sethsmith/its/shared/keychain.py` | Keychain write | `subprocess.run([\"security\", \"add-generic-password\", …])` — NO LOCK |
| `/Users/sethsmith/its/scripts/watchdog.py` | Watchdog checks | Docstring (lines 31-36) mentions "A watchdog freshness check (planned for R2 Watchdog Session 2) will WARN at 50 days idle" — NOT IMPLEMENTED |
| `/Users/sethsmith/its/shared/state_io.py` | Canonical locking | `with_path_lock()` uses fcntl.flock on sidecar — EXISTING PATTERN to reuse |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify baseline tests pass | `.venv/bin/pytest -q tests/test_box_client.py tests/test_keychain.py tests/test_state_io.py tests/test_watchdog.py` | All pass (0 failures) |
| Verify type checking | `.venv/bin/mypy .` | No type errors |
| Verify linting | `ruff check .` | No violations |

**Scope — in:**
- shared/box_client.py: _store_tokens + get_client + module structure
- shared/keychain.py: set_secret function (add threading.Lock)
- shared/state_io.py: REUSE existing with_path_lock + atomic_write_json (no changes)
- scripts/watchdog.py: ADD _check_box_token_freshness check + register in CHECKS list
- tests/test_box_client.py: EXTEND to verify lock behavior
- tests/test_keychain.py: ADD concurrent set_secret test
- tests/test_watchdog.py: ADD token-freshness check tests

**Scope — out:**
- Box SDK internals or boxsdk OAuth2 logic
- Keychain security model
- Portal_poll / weekly_generate schedules
- ITS_Daemon_Health sheet writes
- Access-token TTL (60-min window, handled by boxsdk)

**Steps:**

1. **Add threading.Lock around keychain.set_secret**
   - In shared/keychain.py, add module-level `KEYCHAIN_WRITE_LOCK = threading.Lock()` (import threading).
   - Wrap subprocess.run call in set_secret with the lock's context: `with KEYCHAIN_WRITE_LOCK: subprocess.run(…)`.
   - This prevents two concurrent threads in the same process from race-writing to Keychain.
   - **Verify:** `.venv/bin/pytest -q tests/test_keychain.py -v`. New test (Step 3) verifies lock behavior.

2. **Wrap Box token refresh in state_io.with_path_lock**
   - In shared/box_client.py, modify get_client() to acquire a cross-process lock around the entire OAuth2 initialization and token-exchange flow.
   - Lock path: `~/its/state/box_oauth_refresh.lock` (sidecar pattern via state_io.with_path_lock).
   - On first get_client() call: read credentials from Keychain → acquire lock → initialize OAuth2 (forces immediate token exchange) → boxsdk invokes _store_tokens with new token → release lock.
   - The lock ensures only one daemon exchanges the token at a time.
   - **Verify:** `.venv/bin/pytest -q tests/test_box_client.py::test_store_tokens_persists_refresh_token -v`. Existing test still passes.

3. **Extend box_client test to verify refresh-lock invariant**
   - In tests/test_box_client.py, add test_get_client_acquires_state_lock_on_first_call: mock state_io.with_path_lock, verify it is called during get_client().
   - **Verify:** `.venv/bin/pytest -q tests/test_box_client.py::test_get_client_acquires_state_lock_on_first_call -v`. New test passes.

4. **Add concurrent-write test for keychain.set_secret**
   - In tests/test_keychain.py, add test_set_secret_concurrent_calls_serialize: spawn two threads both calling set_secret concurrently, verify lock serializes them.
   - **Verify:** `.venv/bin/pytest -q tests/test_keychain.py::test_set_secret_concurrent_calls_serialize -v`. New test passes.

5. **Add Box-token freshness watchdog check**
   - In scripts/watchdog.py, add `_check_box_token_freshness()` function (Check P).
   - Logic: read freshness marker file from `~/its/state/box_oauth_refresh.last_exchange_time`. If absent/unreadable → return INFO (first run). If older than 50 days → return WARN. If older than 58 days → return WARN (escalation window; silent death at 60 days).
   - Parse timestamp as UTC ISO 8601.
   - Register in CHECKS list.
   - **Verify:** `.venv/bin/pytest -q tests/test_watchdog.py::test_check_box_token_freshness_missing_marker -v`. New check tests pass.

6. **Wire _store_tokens to write freshness marker**
   - In shared/box_client.py, modify _store_tokens to write a marker timestamp (current UTC ISO) to `~/its/state/box_oauth_refresh.last_exchange_time` via state_io.atomic_write_text.
   - Fail-soft: if marker write fails, log WARN but do NOT raise.
   - **Verify:** `.venv/bin/pytest -q tests/test_box_client.py::test_store_tokens_writes_freshness_marker -v`. New test passes.

7. **Run full test suite**
   - `.venv/bin/pytest -q tests/test_box_client.py tests/test_keychain.py tests/test_state_io.py tests/test_watchdog.py tests/test_capability_gating.py -v`
   - **Verify:** All tests pass, including new tests for lock behavior, concurrent set_secret, and token freshness check.

8. **Verify type checking and linting**
   - `.venv/bin/mypy . && ruff check .`
   - **Verify:** No new type errors or lint violations.

9. **Capability-gating check**
   - `.venv/bin/pytest -q tests/test_capability_gating.py`
   - **Verify:** Invariant 1 holds (no send capability added to generation scripts, no AI added to send scripts).

10. **Live smoke test (operator-run)**
    - Unload portal_poll daemon, run poll_once() manually, verify: (1) Box token refreshes under new lock without error, (2) freshness marker file created with recent timestamp, (3) watchdog check returns INFO, (4) Keychain entry correctly rotated.
    - **Verify:** Daemon unloads/reloads cleanly, poll completes without timeout/lock error, marker file exists and is readable, watchdog check succeeds.

**Test plan:**
Model on existing test suites. New tests: (1) test_get_client_acquires_state_lock_on_first_call (verify lock held during token exchange), (2) test_set_secret_concurrent_calls_serialize (verify threading.Lock serializes calls), (3) test_store_tokens_writes_freshness_marker (verify marker file written with valid timestamp), (4) test_check_box_token_freshness_missing_marker, _50_days_idle, _58_days_idle, _fresh (verify check returns correct severity), (5) test_capability_gating (verify Invariant 1 holds). Execution: `.venv/bin/pytest -q tests/test_box_client.py tests/test_keychain.py tests/test_state_io.py tests/test_watchdog.py tests/test_capability_gating.py`.

**Done criteria:**
- ✓ shared/box_client.py: get_client() wraps OAuth2 init in state_io.with_path_lock, ensuring cross-process token-refresh serialization
- ✓ shared/box_client.py: _store_tokens writes freshness marker after keychain.set_secret, fail-soft on write error
- ✓ shared/keychain.py: set_secret guarded by module-level threading.Lock, serializing concurrent in-process writes
- ✓ scripts/watchdog.py: _check_box_token_freshness returns WARN if >50 days idle, INFO if fresh or missing
- ✓ scripts/watchdog.py: _check_box_token_freshness registered in CHECKS list
- ✓ tests: lock and freshness tests verify behavior
- ✓ All existing tests pass — no regressions
- ✓ mypy + ruff pass
- ✓ Capability gating holds (Invariant 1)
- ✓ Live smoke test passes

**STOP conditions:**
- If test_store_tokens_persists_refresh_token fails: lock or marker-write breaks CRITICAL invariant; roll back
- If state_io.with_path_lock times out during smoke test: lock held too long or deadlock; investigate
- If capability-gating tests fail: Invariant 1 violation; do not merge
- If watchdog check reports false positives: adjust idle-threshold logic

**Maintenance notes:**
Observability: 50-day WARN + 58-day WARN provide graduated notice before silent death at 60 days. Watchdog runs daily, so operator gets 10-day notice + 2-day escalation. Lock scope: state_io.with_path_lock holds a bounded-retry lock (~250ms ceiling); token exchange typically <100ms, so contention rare. Threading.Lock: per-process, in-memory; cross-process isolation handled by state_io.with_path_lock. State-file discipline: marker written ONLY from _store_tokens, read ONLY by watchdog (read-only, no lock needed). Failure modes: marker-write fails → WARN logged, watchdog assumes 'unknown age' → INFO (safe). Lock-acquire timeout → WARN logged, token exchange skipped, next cycle retries.

**Doctrine notes:**
Invariant 1: This plan is auth-only, zero send capability. Invariant 2: Box token is trusted (from Keychain + OAuth2 flow). State files: all writes via state_io.atomic_write_text + with_path_lock. Kill switch: token refresh inside @require_active-decorated daemons, but auth must refresh even during MAINTENANCE so system can recover. Preservation: minimal targeted changes, no module refactoring.

**Corrections:**
VERIFICATION: The three findings are accurate. Code inspection confirms: (1) _store_tokens (line 106-118) calls keychain.set_secret with NO cross-process lock — race risk confirmed. (2) set_secret (line 56-112) calls subprocess.run with NO lock — concurrent threads race-write risk confirmed. (3) watchdog.py (lines 31-36) mentions planned 50-day check NOT implemented — confirmed absent from CHECKS list. All three risks are LIVE and need this plan's fixes.

---

### A4 — Unfiled-Submission Backlog Alert + Portal_Poll Outage Escalation
**P1 / M / LOW / Decision_Phase5-portal-transport / Safety-critical operational alerting / unfiled-submissions-never-pruned, fetch-failure-silent-window**

**Why this matters:**
portal_poll daemon drains unfiled (box_verified=0) submissions from D1 → Smartsheet/Box every 60s. If portal_poll stalls (network/worker down) OR fetch fails for extended periods, unfiled submissions accumulate ONLY in D1 with zero observability. Current escalation: FETCH_FAIL_CRITICAL_THRESHOLD=5 cycles (~5 min) before alert. But a sustained filing jam (backlog aging 24+ hours) is distinct from a single fetch failure. At scale (20 jobs, 20+ daily users), a hidden unfiled queue of 100+ rows indicates a jam the operator must know NOW.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/safety_reports/portal_poll.py` (lines 730-747) | Fetch failure escalation | Logs CRITICAL if n ≥ FETCH_FAIL_CRITICAL_THRESHOLD (~5 min); NO backlog-size alert |
| `/Users/sethsmith/its/shared/portal_client.py` (line 182) | get_pending return | Returns only the `pending` list; NO total_unfiled count returned |
| `/Users/sethsmith/its/safety_portal/worker/index.ts` (line 908-918) | Worker /pending endpoint | Queries box_verified=0 rows only; NO total count in response |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify Worker /pending endpoint | `cd safety_portal && npm test -- --grep 'pending.*total\|backlog'` | 0 passed (no test yet; added by this plan) |
| Verify portal_poll backlog test | `.venv/bin/pytest tests/test_portal_poll.py::test_backlog_exceeds_threshold -v` | 1 passed (added by this plan) |
| Run all portal_poll tests | `.venv/bin/pytest tests/test_portal_poll.py -v` | All pass |
| Type-check modules | `.venv/bin/mypy shared/portal_client.py safety_reports/portal_poll.py` | No errors |
| Lint | `ruff check shared/portal_client.py safety_reports/portal_poll.py safety_portal/worker/index.ts` | No violations |

**Scope — in:**
- safety_portal/worker/index.ts — modify GET /api/internal/pending to include total_unfiled count
- shared/portal_client.py — parse total_unfiled from response; return via get_pending
- safety_reports/portal_poll.py — observe total_unfiled + oldest-row age; alert via @its_error_log on threshold
- scripts/seed_its_config.py — add ITS_Config keys for backlog + age thresholds
- tests/ — add tests for backlog/age alert paths

**Scope — out:**
- Watchdog checks (backlog monitoring owned by portal_poll daemon-side)
- D1 queries from Mac side (all queries route through portal_client HTTP)
- Portal UI display (backlog count not exposed to end-users)
- Email intake / other workstreams
- Worker-side prune changes (prune.ts already correctly never evicts unfiled rows)

**Steps:**

1. **Add ITS_Config threshold keys**
   - Add to scripts/seed_its_config.py: `safety_reports.portal.unfiled_backlog_warn_threshold` (default 50), `safety_reports.portal.unfiled_backlog_critical_threshold` (default 100), `safety_reports.portal.unfiled_max_age_hours` (default 24).
   - Implement `_read_int_setting(key: str, fallback: int) -> int` function in portal_poll.py if not already present (mirroring _read_str_setting / _read_bool_setting pattern).
   - **Verify:** Grep seed file for the three keys and confirm defaults present.

2. **Modify Worker /api/internal/pending to return total_unfiled count**
   - In safety_portal/worker/index.ts, modify GET /pending handler (line 908-918).
   - Add second COUNT query: `SELECT COUNT(*) FROM submissions WHERE box_verified=0`.
   - Include result in response: `return c.json({ pending: results, total_unfiled: countResult });`.
   - Keep response shape backward-compatible (total_unfiled is optional for old callers).
   - **Verify:** Unit test in safety_portal/test/portal-pending.test.ts: call GET /pending, confirm response includes total_unfiled (int ≥ 0).

3. **Update portal_client.py to parse and return total_unfiled**
   - Modify shared/portal_client.py `get_pending()` function.
   - Change return type: from `list[dict]` to `tuple[list[dict], int]` OR use a dataclass/named tuple.
   - Parse `data.get('total_unfiled')` (default 0 if absent, for backward compat).
   - Handle missing/invalid total_unfiled gracefully (log WARN, default to 0).
   - **Verify:** Unit test: mock Worker response with total_unfiled=50, verify get_pending returns both pending list and count.

4. **Add state-file helpers for oldest-unfiled tracking**
   - In safety_reports/portal_poll.py, define: `OLDEST_UNFILED_PATH = STATE_DIR / "portal_poll_oldest_unfiled.json"`.
   - Add helper `_load_oldest_unfiled() -> dict | None` to read JSON state `{uuid, created_at_unix}`.
   - Add helper `_persist_oldest_unfiled(uuid: str, created_at_unix: int)` to write state atomically via state_io.atomic_write_json + state_io.with_path_lock.
   - Add helper `_clear_oldest_unfiled()` to delete state on successful drain.
   - **Verify:** Unit test: call _persist_oldest_unfiled, then _load_oldest_unfiled, verify round-trip.

5. **Add backlog + age alert logic to portal_poll**
   - In _poll_inside_lock(), after successfully fetching rows from get_pending():
     - Unpack (rows, total_unfiled) from response.
     - If len(rows) > 0, capture oldest row's created_at (Unix timestamp).
     - Read ITS_Config thresholds: `portal.unfiled_backlog_warn_threshold`, `portal.unfiled_backlog_critical_threshold`, `portal.unfiled_max_age_hours`.
     - Log conditions:
       - If total_unfiled ≥ critical_threshold AND oldest_age_hours ≥ max_age_hours: log(CRITICAL, "backlog critical", error_code="portal_unfiled_backlog_critical")
       - If total_unfiled ≥ warn_threshold: log(WARN, "backlog warning", error_code="portal_unfiled_backlog_warn")
     - Include in message: backlog count, oldest row age, configured thresholds.
     - On clean drain (zero unfiled): call _clear_oldest_unfiled().
   - Use @its_error_log for triple-fire (CRITICAL → pages operator; WARN → ITS_Errors only).
   - **Verify:** Mock total_unfiled=120 with age 25h, verify CRITICAL fires. Mock total_unfiled=60 with age 12h, verify WARN fires. Mock total_unfiled=30, verify silent.

6. **Add unit tests for backlog alert paths**
   - In tests/test_portal_poll.py, add three new test cases using existing _patch_all fixture:
     - test_backlog_warn_threshold: mock get_pending return (rows, 60), verify WARN log fires.
     - test_backlog_critical_threshold: mock total_unfiled=120 with age ≥ max_age_hours, verify CRITICAL fires.
     - test_backlog_silent_under_threshold: mock total_unfiled=30, verify no alert.
   - Update mocks: get_pending now returns tuple; adjust _patch_all fixture accordingly.
   - **Verify:** Run pytest tests/test_portal_poll.py::test_backlog_* -v and confirm all pass.

7. **Update portal_client test mocks**
   - In tests/test_portal_client.py and _patch_all fixture in tests/test_portal_poll.py, update mocked get_pending to return tuple (rows, total_unfiled).
   - Example: `return_value=([], 0)` for zero rows / zero unfiled.
   - **Verify:** All tests pass; no regressions from get_pending signature change.

8. **Type-check and lint**
   - `.venv/bin/mypy shared/portal_client.py safety_reports/portal_poll.py`
   - `ruff check shared/portal_client.py safety_reports/portal_poll.py`
   - **Verify:** No errors.

9. **Smoke test: backlog escalation on stalled daemon**
   - Operator: seed D1 with 120+ unfiled submissions with created_at older than 24h (via Worker admin endpoint or test DB).
   - Call portal_poll.poll_once() and verify ITS_Errors CRITICAL row written with error_code=portal_unfiled_backlog_critical, message includes backlog count + age.
   - Verify Resend email fires (or mock confirmation).
   - **Verify:** ITS_Errors CRITICAL row present, Resend email fires with actionable context.

**Test plan:**
Model on existing tests/test_portal_poll.py. Unit tests cover get_pending signature change (total_unfiled parsing), backlog thresholds (CRITICAL, WARN, silent), and state-file persistence (oldest-unfiled tracking). Mocks cover Worker response shape, ITS_Config reads, state I/O, and error_log triple-fire. Operator smoke test covers end-to-end: seed high backlog, call poll_once, observe CRITICAL + Resend email. No integration test with live Smartsheet/Worker (existing portal_poll integration tests cover that separately).

**Done criteria:**
- ✓ ITS_Config keys for thresholds exist in seed_its_config.py with documented defaults
- ✓ Worker /pending response includes total_unfiled count (integer ≥ 0)
- ✓ shared/portal_client.get_pending() parses + returns total_unfiled without raising on malformed responses
- ✓ safety_reports/portal_poll tracks oldest-unfiled row's age in state file (state_io compliant)
- ✓ portal_poll logs CRITICAL when backlog ≥ critical_threshold AND age ≥ max_age_hours
- ✓ portal_poll logs WARN when backlog ≥ warn_threshold (regardless of age)
- ✓ portal_poll clears oldest-unfiled state on successful drain
- ✓ All unit tests pass (existing + new backlog/age tests)
- ✓ mypy + ruff pass on changed modules
- ✓ Operator smoke test confirms CRITICAL alert + Resend email fire with context
- ✓ Existing portal_poll tests still pass (backward-compat check on signature change)

**STOP conditions:**
- If Worker /pending response change breaks backward compat (unlikely if total_unfiled is optional): revert and use separate endpoint
- If state-file I/O shows corruption under high frequency: fall back to in-memory tracking + daily watchdog check
- If ITS_Config read failures silence alerts: escalate to ops
- If backlog thresholds fire too frequently (false positives): tune defaults and document adjustment procedure

**Maintenance notes:**
Backlog thresholds (warn=50, critical=100) are seeded defaults; operators can tune via ITS_Config post-deploy. Max-age threshold (24h) should align with intake SLA — if SLA changes, update. Oldest-unfiled state persists in ~/its/state/portal_poll_oldest_unfiled.json; operator may delete manually to force silent-clear (fail-soft; next cycle re-learns). Alert dedupes on (script, error_code) via shared.alert_dedupe (~1-hour rate-cap per F09). No special cleanup needed on daemon restart — state file is self-healing.

**Doctrine notes:**
Invariant 1: portal_poll gains zero send capability. Changes are observation-only (read backlog, compute age, log alert). @its_error_log decorator handles triple-fire. Invariant 2: total_unfiled and created_at come from our own Worker's D1 table (trusted). Defensive parsing (default 0 on missing/invalid). State files: oldest-unfiled written via state_io.atomic_write_json + with_path_lock. Kill switch: poll_once() already @require_active; backlog alert inherits gate. Fail-OPEN: alert's own log() call is fail-soft; missed alert better than exception. Preservation: minimal targeted changes; existing portal_poll logic untouched.

**Corrections:**
VERIFICATION: Reading code confirms both findings accurate: (1) prune.ts line 913 shows /pending selects ONLY box_verified=0 rows — no silent eviction. (2) portal_poll.py lines 730-747 show fetch-failure escalation at 5 consecutive failures (~5 min); NO backlog-size alert. (3) No backlog-age monitoring exists. Plan closes the backlog + age gap.

---

### A5 — Smartsheet 5,000-Row Cap Rotation for ITS_Review_Queue + ITS_Errors
**P1 / M / MED / None / Scaling/Hardening / ss-queue-row-cap, ss-errors-row-cap**

**Why this matters:**
At 20 active jobs, ITS_Review_Queue sees 60–100 rows/week and ITS_Errors grows on every transient degradation. Both march toward the Smartsheet 5,000-row hard cap with zero rotation; past it, all writes SILENTLY FAIL (no exception, just data loss). This plan adds a daily rotation check to the watchdog that archives old RESOLVED Review Queue items and deletes old ITS_Errors rows before hitting the cap, plus warns the operator when approaching the high-water mark.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/shared/review_queue.py` | Review Queue schema | Status enum: PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED. Resolved rows stay in sheet for audit. |
| `/Users/sethsmith/its/shared/error_log.py` | ITS_Errors write path | Writes rows with circuit_breaker bypass; resolves on operator action. |
| `/Users/sethsmith/its/scripts/watchdog.py` (lines 1249-1285) | CHECKS list | 13 checks registered (A–N); no row-cap rotation logic exists. |
| `/Users/sethsmith/its/shared/sheet_ids.py` | Archive workspace | `WORKSPACE_ARCHIVE = 5528280611743620` exists for long-term storage. |
| `/Users/sethsmith/its/shared/smartsheet_client.py` | Primitives | `delete_rows(sheet_id: int, row_ids: list[int])` caps at 450 IDs per call. |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify no existing cap detection | `grep -rn 'get_rows.*SHEET_REVIEW_QUEUE\|get_rows.*SHEET_ERRORS\|5000' /Users/sethsmith/its/scripts/ /Users/sethsmith/its/shared/ \| grep -v '.pyc\|__pycache__'` | Only references are existing get_rows() calls with no cap/rotation logic |
| Verify ReviewStatus values | `.venv/bin/python3 -c "from shared.review_queue import ReviewStatus; print(sorted([s.value for s in ReviewStatus]))"` | ['APPROVED', 'ESCALATED', 'IN_REVIEW', 'PENDING', 'REJECTED'] |
| Verify baseline tests pass | `.venv/bin/pytest -q tests/test_watchdog.py tests/test_review_queue.py tests/test_error_log.py --tb=short` | All tests pass |

**Scope — in:**
- Add Check O (_check_smartsheet_row_caps) to watchdog.py
- Row-count monitoring for ITS_Review_Queue and ITS_Errors
- Archive old RESOLVED Review Queue rows to archive sheet (if available)
- Delete old RESOLVED ITS_Errors rows beyond retention window
- NEVER delete UNRESOLVED Review Queue items
- Configuration settings in ITS_Config for row-cap thresholds and retention windows
- Register new check in watchdog.CHECKS list
- Add unit tests for Check O

**Scope — out:**
- Refactoring existing review_queue or error_log modules (preservation doctrine)
- Changing Review Queue or ITS_Errors schema
- Moving Resolved-At detection logic into shared library (keep local to this check)
- 'Smart' row merge/dedup logic
- Creating archive sheets in code (archive infrastructure must pre-exist)
- Adding send capability to the monitoring check (Invariant 1)

**Steps:**

1. **Read live ITS_Config for row-cap configuration keys**
   - Define ITS_Config setting keys: 'smartsheet.review_queue_high_water_mark' (default 4000), 'smartsheet.errors_high_water_mark' (default 4000), 'smartsheet.errors_retention_days' (default 90).
   - The check will read these at runtime, fail-open to defaults if missing.
   - **Verify:** `grep -n 'smartsheet\\.review_queue\|smartsheet\\.errors' /Users/sethsmith/its/shared/defaults.py`. No matches yet (defaults in constants; operator may add to ITS_Config post-deploy).

2. **Add _check_smartsheet_row_caps function to watchdog.py**
   - Implement: `def _check_smartsheet_row_caps() -> CheckResult:` with docstring.
   - Logic:
     1. Count rows on SHEET_REVIEW_QUEUE and SHEET_ERRORS via get_rows().
     2. Read high-water marks and retention window from ITS_Config (fail-open to constants).
     3. If Review Queue count > high-water: fetch all rows with Status in [APPROVED, REJECTED, ESCALATED]; filter to rows with Created At < (today - 180 days); archive oldest half (preserve recent for audit); return WARN with count moved + new total.
     4. If ITS_Errors count > high-water: fetch all rows with Resolved At != empty AND Resolved At < (today - retention_days); delete in batches of 450; return WARN with count deleted + new total.
     5. If either count within 250 of 5000 hard cap: return WARN even if below high-water.
     6. Otherwise return INFO.
   - All Smartsheet exceptions caught, logged WARN, never raised (fail-isolated per Op Stds v9 §27).
   - **Verify:** `grep -A 50 'def _check_smartsheet_row_caps' /Users/sethsmith/its/scripts/watchdog.py`. Function defined with proper error handling.

3. **Define helper for resolved status detection**
   - Add module-level helpers in watchdog.py:
     ```python
     def _is_resolved_review_queue_row(row: dict[str, Any]) -> bool:
         '''True if Status is in resolved set.'''
         status = row.get('Status')
         return status in {'APPROVED', 'REJECTED', 'ESCALATED'}

     def _is_resolved_errors_row(row: dict[str, Any]) -> bool:
         '''True if Resolved At is non-empty and not None.'''
         resolved_at = row.get('Resolved At')
         return resolved_at and isinstance(resolved_at, str) and resolved_at.strip()
     ```
   - **Verify:** `grep -n '_is_resolved_' /Users/sethsmith/its/scripts/watchdog.py`. Both helpers defined.

4. **Add configuration constants to watchdog.py**
   - Near line ~130 (after WATCHDOG_MARKER_DIR), add:
     ```python
     ROW_CAP_CONFIG = {
         'review_queue_high_water': 4000,
         'errors_high_water': 4000,
         'errors_retention_days': 90,
         'danger_zone_buffer': 250,
     }
     REVIEW_QUEUE_ARCHIVE_BATCH = 100
     ERRORS_DELETE_BATCH = 450
     ```
   - **Verify:** `grep -n 'ROW_CAP_CONFIG\|REVIEW_QUEUE_ARCHIVE_BATCH' /Users/sethsmith/its/scripts/watchdog.py`. Constants defined.

5. **Add Check O to CHECKS registry**
   - In CHECKS list (~line 1249), add _check_smartsheet_row_caps after _check_stuck_wsr_send.
   - Update preceding comment: "Check O: row-cap rotation for ITS_Review_Queue and ITS_Errors (A5, scaling hardening)."
   - **Verify:** `grep -A 3 '_check_stuck_wsr_send,' /Users/sethsmith/its/scripts/watchdog.py`. Check O added.

6. **Implement archive-or-delete logic using smartsheet_client**
   - Archive Review Queue: if WORKSPACE_ARCHIVE provisioned, find or create 'ITS_Review_Queue_Archive' sheet. Copy old resolved rows, delete from live sheet.
   - Delete-only Errors: no archive (forensic rows immutable); delete in-place.
   - On any SmartsheetError: log WARN and return early (partial-rotation summary). Never raise.
   - **Verify:** `grep -n 'delete_rows' /Users/sethsmith/its/shared/smartsheet_client.py \| head -3`. delete_rows available at line 578, caps at 450 IDs per call.

7. **Handle Smartsheet's eventual-consistency window**
   - After delete_rows call, row count not immediately updated on subsequent get_rows.
   - Rotation should: count → delete → log result. NEXT watchdog run re-counts and continues if needed.
   - No retry loop within one check execution.
   - **Verify:** Implicit in Step 2 logic (no re-count after delete).

8. **Write comprehensive unit tests in tests/test_watchdog.py**
   - test_row_caps_check_all_below_threshold_returns_info
   - test_row_caps_check_review_queue_above_threshold_archives_old_resolved
   - test_row_caps_check_errors_above_threshold_deletes_old_resolved
   - test_row_caps_check_danger_zone_warns_even_if_below_threshold
   - test_row_caps_check_never_deletes_unresolved_review_queue
   - test_row_caps_check_smartsheet_error_on_delete_returns_warn
   - **Verify:** `.venv/bin/pytest -q tests/test_watchdog.py::test_row_caps_check -v 2>&1 \| head -20`. All new tests pass.

9. **Add check to the CHECKS registry test**
   - Update test_checks_list_has_all_session_1_2_3_checks() in tests/test_watchdog.py to include _check_smartsheet_row_caps in expected CHECKS list.
   - **Verify:** `grep -n 'def test_checks_list' /Users/sethsmith/its/tests/test_watchdog.py`. Test updated to include Check O.

10. **Update watchdog.py docstring to document Check O**
    - Add to module docstring (line ~22-50) in "Checks shipped:" section:
      ```
      O. Row-cap rotation for ITS_Review_Queue + ITS_Errors (A5, scaling hardening).
         At a configurable high-water mark (default 4000; read from ITS_Config), archive
         old RESOLVED Review Queue rows (>180d old) and delete old resolved ITS_Errors rows
         (>90d old, configurable). NEVER deletes unresolved Review Queue items. Returns
         WARN when rotation occurs or when either sheet approaches the 5000 hard cap;
         INFO otherwise.
      ```
    - **Verify:** `head -50 /Users/sethsmith/its/scripts/watchdog.py \| grep -n 'Check O'`. Check O documented.

11. **Verify no shared/state_io writes needed**
    - This check reads from Smartsheet and writes to Smartsheet (delete_rows). It does NOT persist local state between runs.
    - **Verify:** `grep -n 'state_io\|Path.*write_text.*state' /Users/sethsmith/its/scripts/watchdog.py \| head -5`. No matches.

12. **Run full test suite and verify capability-gating**
    - `.venv/bin/pytest -q tests/test_capability_gating.py`. Watchdog is NOT a send script; no send capability added.
    - **Verify:** All tests pass.

13. **Integration smoke: run watchdog against test data**
    - Create temporary test sheet with >4000 rows (or mock get_rows), run watchdog main() in isolation.
    - Verify: (a) Check O logs appropriate severity (INFO if below threshold, WARN if rotation), (b) no unresolved items deleted, (c) archived/deleted count matches expected.
    - **Verify:** `.venv/bin/pytest -q tests/test_watchdog.py -k row_caps --tb=short`. All row_caps tests pass.

**Test plan:**
Model on existing Check A test pattern. Mock smartsheet_client.get_rows to return dicts with Status/Created At/Resolved At fields. Test cases: (1) both counts < high-water → INFO, (2) RQ count > high-water + has old resolved → archives and returns WARN, (3) Errors count > high-water + has old resolved → deletes and returns WARN, (4) RQ count > high-water but all resolved recent → WARN 'not enough old rows to rotate', (5) count within 250 of 5000 → WARN (danger zone), (6) SmartsheetError during delete → catches, logs WARN, returns WARN (never raises), (7) NEVER includes PENDING/IN_REVIEW RQ rows in archive/delete set. Smoke test: run watchdog.main() against mocked Smartsheet, verify Check O runs without exception and logs appropriate message.

**Done criteria:**
- ✓ Check O implemented and added to CHECKS list
- ✓ Row-count monitoring reads both sheets, detects resolved status, archives/deletes old rows
- ✓ Archive logic: old RESOLVED RQ rows moved to archive sheet (or deleted if no archive); Errors old resolved rows deleted in-place
- ✓ NEVER deletes UNRESOLVED Review Queue items (verified in tests + code review)
- ✓ High-water marks and retention windows configurable via ITS_Config (fail-open to constants)
- ✓ WARN when count > high-water OR within 250 of 5000; INFO when below both
- ✓ All SmartsheetError exceptions caught, logged WARN; check never raises
- ✓ Unit tests added (5+ test cases)
- ✓ Integration smoke test passes
- ✓ `.venv/bin/pytest -q tests/test_watchdog.py tests/test_capability_gating.py --tb=short` passes
- ✓ Watchdog docstring updated to document Check O

**STOP conditions:**
- STOP if smartsheet_client.delete_rows raises SmartsheetRateLimitError (429): cap at 450 IDs per call already enforced
- STOP if archive sheet does not exist: log WARN and skip archive, proceed to delete-only for RQ
- STOP if get_rows returns rows lacking 'Status' / 'Created At' / 'Resolved At': log ERROR, move to next check (schema drift is operator-actionable)
- STOP if a single Smartsheet read/write fails with SmartsheetCircuitOpenError (backend outage): breaker OPEN, return INFO, do not rotate

**Maintenance notes:**
Per-operator steps: (1) Review high-water thresholds (4000 default) — may need adjustment with actual load data. (2) Archive sheet creation (optional): create 'ITS_Review_Queue_Archive' sheet in WORKSPACE_ARCHIVE if row counts routinely hit high-water. Check auto-finds it; if absent, deletes old rows instead. (3) Monitor Check O output: watch first 2–3 weeks for rotation counts and severity. (4) Retention window: 90-day default for ITS_Errors may need adjustment based on compliance/audit requirements. (5) Docstring updates: if behavior changes, update "Checks shipped" section in watchdog.py.

**Doctrine notes:**
Invariant 1: Check O has ZERO send capability. Reads from Smartsheet, writes to Smartsheet (delete_rows, optionally add_rows for archive). No email send, no Graph send, no Resend. Watchdog is neither generation nor send script; no send capability added. Invariant 2: Row data from Smartsheet is untrusted (external). Filtering logic (check if string in set, date arithmetic) is defensive — no prompt injection risk. State I/O: Check O does not write to ~/its/state/. Kill switch: Check O runs inside watchdog's main(), which respects check_system_state() and suppresses alerts during MAINTENANCE (rotation itself proceeds as safety measure). Failure isolation: all Smartsheet calls inside try/except, SmartsheetError caught + logged WARN, check returns WARN CheckResult. No exception propagates. Circuit breaker respected (if OPEN, early-return with INFO). Preservation: zero refactoring; new logic only in watchdog.py (new function + constants + test).

**Corrections:**
VERIFICATION: The finding is accurate. Code inspection confirms: (1) week_sheet.ensure_week_sheet() creates one sheet per (job, week) in WORKSPACE_SAFETY_PORTAL with no cap check (correct). (2) At 20 jobs × 52 weeks = ~1,040 sheets/year. (3) No existing row-count rotation logic for ITS_Review_Queue or ITS_Errors (confirmed via grep). (4) Smartsheet hard cap is 5,000 rows per sheet (finding statement). Plan correctly addresses the cap-rotation gap.

---

### A6 — weekly_generate Hardening: Per-Job Timeout + Streamed PDF Merge + Crash-Resumable State
**P1 / M / MED / None / Performance & Resilience / launchd-timeout-partial-write, serial-job-bottleneck, pdf-memory-accumulation, circuit-breaker-cascading**

**Why this matters:**
At scale (20 active jobs, photo-heavy submissions), the Friday 14:00 compile loop runs jobs serially; a single stalled job blocks the remaining queue. Photo-heavy packets accumulate all per-job PDFs in memory during merge_pdfs, risking memory pressure. No per-job timeout means a stalled job can run indefinitely. If a crash occurs mid-compile, resumability depends on whether a Rollup row was written. Circuit-breaker OPEN mid-loop surfaces jobs to Review Queue (correct behavior confirmed in code). **CORRECTED FINDING:** launchd does NOT kill processes by default — the plist has no ExitTimeOut set. The REAL risks are wall-clock (no timeout enforced in code) + memory accumulation.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/safety_reports/weekly_generate.py` (lines 601-640) | Main loop | For loop over active_jobs, per-job exception handling with _safe_review_queue; NO per-job timeout, NO memory guard. |
| `/Users/sethsmith/its/safety_reports/form_pdf.py` (lines 656-676) | merge_pdfs | `for b in pdfs: writer.append(PdfReader(io.BytesIO(b)))` — all PDFs held in memory before write. |
| `/Users/sethsmith/its/safety_reports/weekly_generate.py` (lines 378-400) | Skip-if-already-compiled | Rollup watermark skip logic; resumability depends on Rollup write state. |
| `/Users/sethsmith/its/scripts/launchd/org.solutionsmith.its.weekly-generate.plist` | LaunchAgent config | No ExitTimeOut key; launchd has no default timeout. |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify capability gating | `.venv/bin/pytest -q tests/test_capability_gating.py::test_generation_script_does_not_import_send -k weekly_generate` | PASSED |
| Verify current form_pdf behavior | `grep -n 'merge_pdfs\|io.BytesIO' /Users/sethsmith/its/safety_reports/form_pdf.py \| grep -A 2 'def merge_pdfs'` | writer.write(out) at line ~676 |
| Confirm no plist timeout | `grep -i 'ExitTimeOut\|timeout' /Users/sethsmith/its/scripts/launchd/org.solutionsmith.its.weekly-generate.plist` | No output (key absent) |
| Run baseline tests | `.venv/bin/pytest -q tests/test_capability_gating.py::test_generation_script_does_not_import_send` | PASSED |

**Scope — in:**
- Add PER_JOB_TIMEOUT_SECONDS constant and JobTimeoutError exception
- Implement per-job timeout using signal.alarm() (Unix SIGALRM) or ThreadPoolExecutor with timeout
- Wrap _compile_job_week call with timeout context in _run_pipeline loop
- Implement memory guard (Strategy B): check total PDF bytes before merge_pdfs, error if > threshold
- Confirm resumability via watermark-based skip logic + tests
- Add job_timeouts counter to RunSummary for observability
- Ensure _safe_review_queue called for every exception (no silent drops)
- Add circuit-breaker observability log before job loop
- Update docstrings to document new behavior

**Scope — out:**
- Do NOT refactor merge_pdfs (preserve pypdf PdfWriter approach)
- Do NOT add send/AI capability (Invariant 1)
- Do NOT close/complete weeks
- Do NOT modify launchd plist with ExitTimeOut
- Do NOT change state_io discipline

**Steps:**

1. **Correct the launchd-kill finding**
   - Verify scripts/launchd/org.solutionsmith.its.weekly-generate.plist has NO ExitTimeOut key.
   - **Finding is FALSE**: launchd does not kill processes by default. REAL risks are wall-clock hang + memory accumulation.
   - **Verify:** `grep -i 'ExitTimeOut' /Users/sethsmith/its/scripts/launchd/org.solutionsmith.its.weekly-generate.plist`. No output (key absent).

2. **Add per-job timeout constant and exception type**
   - In safety_reports/weekly_generate.py, add after RunSummary class (~line 105):
     ```python
     import signal
     import contextlib

     PER_JOB_TIMEOUT_SECONDS = 900  # 15 min; stalled job stops here

     class JobTimeoutError(Exception):
         """Raised when a single job exceeds its time budget."""
         pass
     ```
   - **Verify:** `grep -n 'PER_JOB_TIMEOUT_SECONDS\|JobTimeoutError' /Users/sethsmith/its/safety_reports/weekly_generate.py`. Both present around line 105-110.

3. **Implement per-job timeout wrapper using signal.alarm**
   - Add before _compile_job_week definition (~line 345):
     ```python
     def _timeout_handler(signum, frame):
         raise JobTimeoutError(f"Job exceeded {PER_JOB_TIMEOUT_SECONDS}s budget")

     @contextlib.contextmanager
     def _job_timeout():
         """Context manager that raises JobTimeoutError if job exceeds budget."""
         signal.signal(signal.SIGALRM, _timeout_handler)
         signal.alarm(PER_JOB_TIMEOUT_SECONDS)
         try:
             yield
         finally:
             signal.alarm(0)  # cancel alarm
     ```
   - **Verify:** `grep -A 5 'def _timeout_handler' /Users/sethsmith/its/safety_reports/weekly_generate.py`. Handler defined, raises JobTimeoutError.

4. **Wrap _compile_job_week call with timeout**
   - In _run_pipeline (lines 610-631), modify the per-job loop:
     ```python
     for job in active_jobs.list_active_jobs():
         summary.jobs_processed += 1
         try:
             with _job_timeout():
                 _compile_job_week(job, week, summary, correlation_id)
         except JobTimeoutError as exc:
             summary.errors_per_job[job.project_name] = f"JobTimeoutError: {exc!r}"
             summary.job_timeouts += 1
             error_log.log(
                 Severity.ERROR, SCRIPT_NAME,
                 f"compile timeout for {job.project_name} (job {job.job_id}) — exceeded {PER_JOB_TIMEOUT_SECONDS}s budget",
                 error_code="weekly_generate.job_timeout",
                 correlation_id=correlation_id,
             )
             _safe_review_queue(job, week, "JobTimeoutError", correlation_id, summary)
         except (SmartsheetError, box_client.BoxError) as exc:
             …  # existing exception handling
     ```
   - **Verify:** `grep -A 3 'with _job_timeout' /Users/sethsmith/its/safety_reports/weekly_generate.py`. Timeout wrapped around _compile_job_week in loop.

5. **Add job_timeouts counter to RunSummary**
   - In RunSummary dataclass (lines 94-104), add:
     ```python
     job_timeouts: int = 0
     ```
   - **Verify:** `grep 'job_timeouts' /Users/sethsmith/its/safety_reports/weekly_generate.py`. Field appears in RunSummary and is incremented on timeout.

6. **Implement memory guard for PDF merge (Strategy B)**
   - In _build_weekly_packet (~line 434), before calling merge_pdfs:
     ```python
     total_bytes = sum(len(p) for p in pdfs)
     if total_bytes > 500 * 1024 * 1024:  # 500 MB ceiling
         raise ValueError(f"Packet exceeds 500MB threshold ({total_bytes / 1024**2:.1f}MB); aborting to prevent OOM")
     compiled = form_pdf.merge_pdfs(pdfs)
     ```
   - **Verify:** `grep -B 2 'compiled = form_pdf.merge_pdfs' /Users/sethsmith/its/safety_reports/weekly_generate.py \| grep -E 'total_bytes|500.*MB'`. Memory guard check present before merge_pdfs.

7. **Test resumability on simulated crash**
   - In tests/test_weekly_generate.py, add test_resumability_after_mid_compile_crash:
     - First run: mock download OK, upload fails before Rollup write → exception raised.
     - Second run: now Rollup exists, no new submissions → should skip (idempotent).
   - **Verify:** `.venv/bin/pytest -q tests/test_weekly_generate.py::test_resumability_after_mid_compile_crash`. Test passes.

8. **Verify circuit-breaker does not silently abandon jobs**
   - Confirm _safe_review_queue is called for SmartsheetCircuitOpenError (subclass of SmartsheetError).
   - **Verification only**: code inspection at lines 610-631 shows SmartsheetError catch clause routes through _safe_review_queue. No change needed; already correct.
   - **Verify:** `grep -A 5 'SmartsheetCircuitOpenError' /Users/sethsmith/its/safety_reports/weekly_generate.py \| head -10`. SmartsheetCircuitOpenError surfaced through SmartsheetError catch.

9. **Add pre-loop circuit breaker status log**
   - In _run_pipeline, just before `for job in active_jobs.list_active_jobs()` loop (~line 609), add:
     ```python
     if shared.circuit_breaker.is_open():
         error_log.log(
             Severity.WARN, SCRIPT_NAME,
             f"Smartsheet circuit breaker is OPEN — remaining job failures will route to Review Queue",
             error_code="weekly_generate.circuit_open_start",
             correlation_id=correlation_id,
         )
     ```
   - **Verify:** `grep -B 2 'circuit_breaker.is_open' /Users/sethsmith/its/safety_reports/weekly_generate.py`. Informational log added before job loop.

10. **Run type checking and linting**
    - `.venv/bin/mypy /Users/sethsmith/its/safety_reports/weekly_generate.py && ruff check /Users/sethsmith/its/safety_reports/weekly_generate.py`
    - **Verify:** No new type/lint errors.

11. **Run capability gating tests**
    - `.venv/bin/pytest -q tests/test_capability_gating.py::test_generation_script_does_not_import_send -k weekly_generate`
    - **Verify:** PASSED (weekly_generate has no send, no AI).

12. **Create smoke test for timeout behavior**
    - In tests/test_weekly_generate.py, add test_job_timeout_fires_and_surfaces_to_review_queue:
       - Mock _compile_job_week to sleep past timeout, verify JobTimeoutError caught and Review Queue called.
    - **Verify:** `.venv/bin/pytest -q tests/test_weekly_generate.py::test_job_timeout_fires_and_surfaces_to_review_queue -v`. Test passes.

13. **Update docstrings and comments**
    - Update module docstring and _run_pipeline docstring to document: per-job timeout (900s budget), resumability (skip if Rollup exists), memory guard (500MB ceiling), circuit-breaker observability.
    - **Verify:** `grep -A 5 'Per-job timeout\|Resumability\|Memory guard' /Users/sethsmith/its/safety_reports/weekly_generate.py \| head -20`. Docstrings updated.

14. **Run full pytest suite**
    - `.venv/bin/pytest -q tests/ -k 'weekly_generate or form_pdf' --tb=short`
    - **Verify:** All tests pass (existing + new).

15. **Operator smoke test with live launchd**
    - Unload daemon, run manually from worktree, check output for job_timeouts, memory_guard, and all jobs completed or on Review Queue.
    - Reload daemon.
    - **Verify:** `.venv/bin/python -m safety_reports.weekly_generate --week-start 2026-06-07 2>&1 \| grep -E 'job_timeouts|memory_guard|Review Queue|correlation_id'`. Output shows all jobs processed, timeouts caught if any, no silent failures.

**Test plan:**
Model on existing tests/test_weekly_generate.py. New tests: (1) test_resumability_after_mid_compile_crash — simulates mid-run crash, verifies re-run skips. (2) test_job_timeout_fires_and_surfaces_to_review_queue — verifies timeout caught and Review Queue called. (3) test_memory_guard_rejects_large_packets (if Strategy B) — verifies oversized packet raises ValueError. Run: `.venv/bin/pytest -q tests/test_weekly_generate.py` and verify all pass (existing + new).

**Done criteria:**
- ✓ PER_JOB_TIMEOUT_SECONDS (900s) defined, used in _job_timeout() context manager
- ✓ TimeoutError from signal.SIGALRM caught in _run_pipeline per-job fence, routed through _safe_review_queue
- ✓ RunSummary includes job_timeouts counter, incremented on timeout
- ✓ Memory guard (Strategy B) checks total_bytes >= 500MB before merge_pdfs, raises ValueError if exceeded
- ✓ Resumability verified by test simulating mid-run crash → re-run skips
- ✓ Circuit-breaker OPEN status logged before job loop
- ✓ All SmartsheetError / BoxError exceptions routed through _safe_review_queue
- ✓ Capability gating tests pass unchanged
- ✓ All existing weekly_generate tests pass (no regressions)
- ✓ mypy + ruff pass on modified files
- ✓ Docstrings updated to document per-job timeout, resumability, memory guard, circuit-breaker

**STOP conditions:**
- If signal.SIGALRM unavailable on platform: pivot to ThreadPoolExecutor-based timeout with note on platform-specificity
- If adding timeout causes existing tests to fail due to alarm interference: use test-only bypass (monkeypatch)
- If merge_pdfs refactoring causes PDF quality regression: stop, revert to Strategy B (memory guard only)
- If circuit-breaker log causes log spam on OPEN: rate-limit to once per run via flag in RunSummary
- If memory guard threshold (500MB) found too low/high in smoke testing: adjust based on production PDF sizes

**Maintenance notes:**
Per-job timeout (900s = 15 min) is tunable; if jobs regularly timeout, increase value (signals need for scale review) or reduce job count per run. Memory guard threshold (500MB) should be monitored via error logs; if packets regularly hit limit, consider implementing Strategy A (streaming merge) in next sprint. JobTimeoutError not exported from module; add to __all__ if other modules need to catch it. signal.SIGALRM is Unix/macOS only; if Windows CI ever runs, timeout is no-op (add CI note or switch to ThreadPoolExecutor for cross-platform compat). Circuit-breaker observability log is informational; if OPEN state persists >1h, separate alerting (F16 heartbeat) should trigger. Resumability test relies on mocking; in production, verify by intentionally killing daemon mid-run and confirming re-run skips.

**Doctrine notes:**
Invariant 1: weekly_generate gains ZERO send and ZERO AI. Timeout logic adds no anthropic, graph_client, send_mail, resend, smtplib, email.mime imports. Capability gating verifies this. Invariant 2: input is already-rendered, HMAC-verified PDFs + Smartsheet cells. Timeout doesn't change input handling; memory guard is deterministic byte-count check (no AI). State files: Rollup row written by append_rollup_row() is commit point. No new state files; job_timeouts returned in RunSummary dict (logged, not persisted to ~/its/state/). Kill switch: @require_active already at main entry (line 590). Fail-OPEN: timeout is hard stop (fail-closed for stalled jobs), but surfaces via Review Queue (observability). Preservation: merge_pdfs remains pure pypdf concatenation (no algorithm change). Memory guard is additive deterministic gate; no refactoring of working logic.

**Corrections:**
CORRECTED: 'launchd-timeout-partial-write' finding claimed launchd kills processes after >1h. FALSE — org.solutionsmith.its.weekly-generate.plist has NO ExitTimeOut key; launchd waits indefinitely. REAL risks are wall-clock time (stalled job blocks queue) + memory accumulation (photo-heavy PDFs). Plan addresses both via per-job timeout (900s) + memory guard (500MB). VERIFIED CORRECT: 'circuit-breaker-cascading' not a risk. Circuit breaker does NOT silently abandon jobs. All exceptions (including SmartsheetCircuitOpenError) routed through _safe_review_queue. Code confirms at lines 614-631.

---

### A7 — Photo/Payload Cap Reconciliation (413) + Amend-Prefill Empty-Payload Guard
**P1 / M / MED / None / Worker API / D1 State Management / photo-vs-payload-413, payload-strip-amend-loss**

**Why this matters:**
Field PMs attaching 4+ photos in a form hit 413 too_large regularly because the Worker permits PHOTO_MAX_PER_SUBMISSION=8 with PHOTO_MAX_BYTES=400KB decoded (~4.3MB base64 + form overhead) but caps PAYLOAD_MAX at 1.8MB. Additionally, the amend-prefill path (GET /api/recent) JSON.parses payload_json without guarding against empty strings (set by 90-day prune), causing a 500 on forms >90 days old. Both are user-facing submission failures in a photo-heavy reality at 20+ daily users.

**Current state:**

| File | Role | Excerpt |
|------|------|---------|
| `/Users/sethsmith/its/safety_portal/worker/index.ts` (lines 407-412) | Photo/payload cap definitions | `const PAYLOAD_MAX = 1_800_000;` — too restrictive for 8 photos @ 400KB each base64-encoded (~4.3MB theoretical max, ~1.6-2MB typical) |
| `/Users/sethsmith/its/safety_portal/worker/index.ts` (line 386) | Amend-prefill endpoint | `JSON.parse(row.payload_json)` — NO guard against empty string (set by 90-day prune stage-1 stripping) |
| `/Users/sethsmith/its/safety_portal/worker/prune.ts` (line 71) | 90-day stripping | `UPDATE submissions SET payload_json='' WHERE box_verified=1 AND filed_at < ?` — intentional payload stripping for efficiency |
| `/Users/sethsmith/its/safety_portal/src/components/PhotoField.tsx` | Client-side compression | Already targets ≤400KB per photo via compression ladder; client-side handling is correct |

**Commands:**

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify photo cap is the bottleneck | `cd safety_portal && npm test -- --run photos.test.ts 2>&1 \| grep -E 'payload cap\|1.2MB\|1.9MB'` | Tests show 1.2MB accepted, 1.9MB rejected at 413 |
| Verify empty payload causes 500 | `cd safety_portal && npm test -- --run amend-prefill.test.ts 2>&1 \| grep -E 'empty.*payload\|stripped'` | Test shows 500 error on empty payload_json (to be fixed) |
| Type-check Worker | `cd safety_portal && npx tsc -p tsconfig.worker.json --noEmit` | No errors |
| Run full test suite | `cd safety_portal && npm test 2>&1 \| tail -10` | All tests pass |

**Scope — in:**
- safety_portal/worker/index.ts: raise PAYLOAD_MAX (1.8MB → 2.4MB) to accommodate photo budget + form overhead
- safety_portal/worker/index.ts: guard GET /api/recent against empty payload_json; return `{submission: null}` instead of throwing
- safety_portal/test/photos.test.ts: add test case for amend-prefill with stripped payload (simulates 90d+ form)
- safety_portal/test/hardening.test.ts: add regression test to ensure guard doesn't leak empty payloads

**Scope — out:**
- Client-side PhotoField compression (already implemented and working)
- Photo magic/shape validation (already comprehensive)
- Database migration or schema changes
- Prune retention logic (90-day window + stage-1 stripping are intentional)
- Box integration or PDF caching

**Steps:**

1. **Verify current photo budget math and PAYLOAD_MAX adequacy**
   - Confirm: 8 photos × 400KB decoded × 1.333 base64 expansion = ~4.27MB base64 alone, before form data.
   - PAYLOAD_MAX=1.8MB is the bottleneck for the stated photo limit.
   - **Verify:** `grep -n 'PHOTO_MAX_PER_SUBMISSION\|PHOTO_MAX_BYTES\|PAYLOAD_MAX' /Users/sethsmith/its/safety_portal/worker/index.ts`. Constants present at lines 407-412.

2. **Decide on fix strategy: raise PAYLOAD_MAX to 2.4MB**
   - Option 3 (recommended): raise PAYLOAD_MAX to 2.4MB (33% headroom above typical photo-heavy payloads ~1.8MB).
   - This accommodates 4–5 photos @ 300KB each JPEG + form data, plus row overhead (submission_uuid, timestamps, etc.).
   - Total row size: 2.4MB payload + ~400 bytes overhead = ~2.4-2.5MB, within D1 practical ceiling.
   - **Verify:** No migration needed; this is a constant-only change.

3. **Guard GET /api/recent against empty payload_json**
   - At worker/index.ts line 386, replace `JSON.parse(row.payload_json)` with guard:
     ```typescript
     if (!row || !row.payload_json) return c.json({ submission: null });
     return c.json({
       submission: { submission_uuid: row.submission_uuid, values: JSON.parse(row.payload_json) },
     });
     ```
   - This mirrors the behavior when no prior submission exists, gracefully handling prune stage-1 stripping.
   - **Verify:** GET /api/recent returns `{submission: null}` when payload_json is empty or absent, preventing 500 errors.

4. **Add test case for amend-prefill with stripped payload**
   - In safety_portal/test/photos.test.ts, add test simulating a form submission > 90 days old:
     ```typescript
     it("amend-prefill on stripped payload (> 90d) returns null gracefully", async () => {
       const uuid = crypto.randomUUID();
       await env.DB.prepare(
         "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, hmac, box_verified, filed_at, actor_username, submitted_as) VALUES (?,?,?,?,?,?,?,?,?,?)"
       ).bind(uuid, "J1", "jha-v1", "2026-06-12", "", "abc", 1, Date.now(), "crew.lead", "crew.lead").run();
       const res = await call("/api/recent", { cookie, method: "GET" }).url(`?job=J1&form=jha-v1&date=2026-06-12`);
       expect(res.status).toBe(200);
       expect(await res.json()).toEqual({ submission: null });
     });
     ```
   - **Verify:** Run `cd safety_portal && npm test -- --run photos.test.ts`. New test passes.

5. **Ensure Worker remains SEND-FREE (Invariant 1)**
   - Changes (raising constant + null-guard) introduce zero external transmission capability.
   - Worker still performs zero fetch() calls except c.env.ASSETS for static assets.
   - **Verify:** `grep -n 'fetch' /Users/sethsmith/its/safety_portal/worker/index.ts \| head -20`. No new fetch() calls in modified sections. `.venv/bin/pytest -q tests/test_capability_gating.py` passes.

6. **Type-check and lint**
   - `cd safety_portal && npx tsc -p tsconfig.worker.json --noEmit && npm run lint 2>&1 | head -50`
   - **Verify:** Zero TypeScript errors, no lint violations.

7. **Run full photo/submission test suite**
   - `cd safety_portal && npm test -- --run photos.test.ts hardening.test.ts submit-as.test.ts 2>&1 | tail -20`
   - **Verify:** All tests pass. Tests at photos.test.ts:143-146 (1.2MB accepted, 1.9MB rejected) remain valid (both below new 2.4MB cap).

8. **Verify prune.ts behavior is unaffected**
   - `cd safety_portal && npm test -- --run prune.test.ts`. Prune strips payload_json='', amend-prefill guard handles it safely.
   - **Verify:** Prune tests pass.

**Test plan:**
Model on existing test patterns in safety_portal/test/photos.test.ts (payload cap at line 143) and hardening.test.ts (body shape guards). Test cases: (1) Payload Cap Regression: 1.2MB still accepted, 1.9MB still rejected (both below new 2.4MB cap), add new: 2.3MB accepted, 2.5MB rejected. (2) Empty Payload Amend-Prefill: seed row with payload_json='', call GET /api/recent, expect 200 with `{submission: null}` (graceful, not 500). (3) Hardening: verify non-empty payloads still round-trip correctly, amend-prefill with recent submissions returns values object. (4) Edge cases: whitespace-only payload_json treated as empty, returns null; multiple rows (oldest stripped, newer not) returns most recent non-stripped; no rows returns null (existing behavior, unaffected).

**Done criteria:**
- ✓ PAYLOAD_MAX raised from 1_800_000 to 2_400_000 in worker/index.ts with comment explaining photo budget math
- ✓ GET /api/recent guards against empty/missing payload_json: returns `{submission: null}` instead of throwing
- ✓ New test case in photos.test.ts verifies amend-prefill returns null for stripped payloads (>90d)
- ✓ New regression test in hardening.test.ts ensures non-empty payloads still parse correctly
- ✓ TypeScript compiler pass (npx tsc -p tsconfig.worker.json --noEmit) with zero errors
- ✓ All existing tests pass (npm test): photos.test.ts, hardening.test.ts, pdf.test.ts, prune.test.ts
- ✓ Linter (npm run lint) shows no new violations
- ✓ Capability gating tests pass (.venv/bin/pytest tests/test_capability_gating.py -q), Invariant 1 preserved

**STOP conditions:**
- If PAYLOAD_MAX raised above 2.5MB: D1 row size exceeds ~2MB practical limit; stop and re-evaluate photo budget
- If amend-prefill guard returns 4xx/5xx instead of `{submission: null}`: breaks existing frontend prefill logic; stop and audit
- If any existing test fails after changes (especially photos.test.ts:143-146): stop and investigate
- If TypeScript compilation fails: stop and fix types before testing
- If capability-gating tests fail: Invariant 1 violation; STOP immediately

**Maintenance notes:**
PAYLOAD_MAX=2.4MB is conservative and future-proof for 4–5 photo submissions + form data at current compression rates. If field crews use higher-resolution cameras, cap may need re-evaluation. Monitor prune logs (stripped row counts) to watch for payload growth patterns. Empty-payload guard is graceful degradation: forms >90 days old cannot be prefilled (payload stripped for storage efficiency), but form still loads and users can re-fill manually. This is acceptable per prune design. Changes introduce no new state-management complexity: payload_json already managed by prune.ts stage-1 stripping.

**Doctrine notes:**
Invariant 1 (External Send Gate): Worker remains SEND-FREE. Changes (constant increase + JSON.parse guard) introduce zero new external transmission capability. Worker still performs only D1 reads/writes + c.env.ASSETS serving. No fetch() calls added. ✓ Invariant 2 (Untrusted Input): payload_json is still untrusted external content. Guard adds null-check before parsing, preventing malformed (empty) row from crashing handler. JSON.parse itself unchanged, protected by TypeScript's type system. ✓ State Files: No changes to state_io.py or state file handling. D1 row updates (prune.ts stripping, Worker inserts) continue as designed. ✓ Kill Switch: Not affected; Worker runs in Cloudflare Workers (no @require_active decorator). Changes are API-only, stateless. ✓ Preservation-over-Refactor: Changes are minimal + targeted (one constant increase, one null-guard). No refactoring of validatePhotoValues, prune logic, or submission flow. Guard is defensive line added to existing endpoint. ✓

**Corrections:**
Both findings are VALID and correctly described. (1) **photo-vs-payload-413**: VERIFIED. Worker permits PHOTO_MAX_PER_SUBMISSION=8 with PHOTO_MAX_BYTES=400_000 decoded. When base64-encoded, 8 photos × 400KB × 1.333 = ~4.27MB theoretical max. PAYLOAD_MAX=1.8MB is restrictive relative to stated photo budget. Field crews with 4–5 photos + form data hit 413 regularly. Client-side compression in PhotoField.tsx helps, but payload cap remains bottleneck. (2) **payload-strip-amend-loss**: VERIFIED. prune.ts stage-1 stripping sets payload_json='' after 90 days on filed submissions. GET /api/recent endpoint line 386 does `JSON.parse(row.payload_json)` without checking if string is empty. JSON.parse('') throws SyntaxError, propagates to global error handler, returns 500. Form >90 days old cannot be prefilled; SPA receives error instead of graceful null. No guards exist; code assumes payload_json always valid JSON string. Prune design expects amend-prefill to 'only read recent rows', but no API-layer enforcement. Plan closes this gap.

---

## Reviewer Notes

The following plans completed review and require resolver attention before operator execution:

### Plans Needing Revision (All 7)

**A1 — Smartsheet Sheet-Cap Verification**
- **Issues requiring fixes:**
  1. REST pagination logic underspecified: Step 1 needs explicit pageNumber loop, termination condition, 429 backoff strategy + Smartsheet API docs URL
  2. Research contingency missing: Step 2 needs explicit CANNOT-PROCEED routing if cap undocumented or support unavailable
  3. Test mocking incomplete: test_plan references "responses library" or "pytest fixture" pattern without specifics
  4. Type-checking not included: Steps need to add `.venv/bin/mypy . && ruff check .` commands
  5. Margin calculation doesn't account for variance (seasonal load, 52.14 weeks/year)
  6. Runbook troubleshooting too sparse: needs Keychain setup, 429 backoff, network debug, Smartsheet rate-limit info

**A2 — Single-Host Resilience Pack**
- **Issues requiring fixes:**
  1. CRITICAL: Step 2 proposes global socket.setdefaulttimeout (too broad for future multi-threading). Must revise to session-injection pattern (boxsdk.Client accepts session= parameter)
  2. Step 3 requires pre-coding verification: does smartsheet.Smartsheet() accept timeout kwarg? If not, must wrap client._session. Plan defers decision; must commit to approach
  3. Step 7 documentation location ambiguous: specify `/Users/sethsmith/its/scripts/launchd/README.md` explicitly (docs/runbooks/daemon-startup.md doesn't exist yet)
  4. Smoke test command references only portal-poll; should pick representative daemon OR note that tests vary per daemon entry point
  5. Step 2 verification command fragile: relies on keyword grep. Should commit to running unit test (test_box_call_socket_timeout) instead

**A3 — Box OAuth Keychain Idle Warning**
- **Issues requiring fixes:**
  1. Import statements not specified: Steps 5 & 6 require `from shared import state_io` in watchdog.py and box_client.py
  2. Error logging mechanism unspecified in Step 6: should show `from shared import error_log; error_log.log(Severity.WARN, ...)`
  3. Lock path creates double-extension awkwardness: `~/its/state/box_oauth_refresh.lock.lock` (sidecar pattern) should be clarified OR use data-file pattern like alert_dedupe.py
  4. Watchdog check error handling missing: Step 5 doesn't handle invalid ISO 8601 timestamp in marker — should catch ValueError + return INFO
  5. No module-level constant for marker path: path appears in 3 places (Steps 2, 5, 6) with duplication risk — define KC_LAST_REFRESH_PATH or similar

**A4 — Unfiled-Submission Backlog Alert**
- **Issues requiring fixes:**
  1. get_pending return type ambiguous: "tuple[list, int] OR dataclass" — must resolve to one approach + show exact signature
  2. _read_int_setting function referenced but doesn't exist: must add to portal_poll.py before Step 1
  3. created_at format/type not specified (Unix epoch vs ISO string) — must specify which + show age-calculation formula
  4. Worker COUNT query SQL not provided: Step 2 says "Add second COUNT query" but doesn't show exact SQL
  5. ITS_Config seed key format not shown: should display dict structure with 'Setting'/'Value'/'Workstream'/'Description'
  6. Backward compatibility scenario undocumented: old Worker versions will silently fail; feature defaults to 0
  7. Test mock updates incomplete: _patch_all fixture needs explicit `return_value=(rows, total_unfiled)` for tuple signature — current pattern unclear
  8. Deploy order requirement missing: Worker deploy MUST precede Mac portal_poll deploy

**A5 — Smartsheet Row-Cap Rotation**
- **Issues requiring fixes:**
  1. CRITICAL: Step 2b date filter is backwards — "Created At > (today - 180 days)" keeps recent rows. Should be "Created At < (today - 180 days)" to select old rows for archival
  2. Step 1 verification command has double backslashes (escape sequences): should use `grep -E` flag or single backslashes
  3. Step 2c logic contradicts 2b: filters to recent rows then "takes oldest half" is confusing. Should clarify: archive ALL old resolved rows, not subset
  4. Steps 4 & 6 missing try/except pattern: don't show how to read ITS_Config with fail-open defaults to ROW_CAP_CONFIG
  5. Step 6 archive sheet fallback strategy should be explicit in implementation logic, not deferred to STOP conditions

**A6 — weekly_generate Hardening**
- **Issues requiring fixes:**
  1. Import statements not specified in Step 2: must show exact placement of `import signal`, `import contextlib` after line 60
  2. Memory guard threshold (500MB) lacks justification: either cite observed production PDF sizes OR document as tunable with post-smoke-test adjustment
  3. Steps 11 & 12 overlap: Step 11 is verification-only (confusing description), Step 12 adds actual code. Consolidate: remove Step 11, keep Step 12 as only circuit-breaker step
  4. Step 13 (timeout smoke test) is pseudocode: lacks concrete pytest code. Must provide runnable test or monkeypatch approach
  5. Summary counter edge case not documented: if JobTimeoutError fires after summary.packets_compiled incremented, packet counted as compiled but job surfaces to Review Queue. Plan should document this trade-off explicitly
  6. Step 14 (docstring updates) doesn't specify which docstrings to update. Must list: module docstring, _run_pipeline, _compile_job_week, _build_weekly_packet docstrings with exact line numbers + text

**A7 — Photo Payload Reconciliation**
- **CRITICAL issues requiring fixes:**
  1. CRITICAL: PAYLOAD_MAX=2.4MB contradicts comment "D1 row practical ceiling is ~2MB". With row overhead (200–600 bytes), 2.4MB payload = ~2.4–2.5MB row size exceeds stated limit. Plan's stop condition (2.5MB → 2.8MB row) also has inconsistent math. Must either (A) justify that D1's actual limit is higher and update comment, OR (B) lower PAYLOAD_MAX to 2.0MB or less to stay within ceiling
  2. CRITICAL: Guard implementation doesn't match expected test behavior. Proposed `values: row.payload_json ? JSON.parse(...) : null` returns `{submission: {submission_uuid, values: null}}`, but test expects `{submission: null}`. Must check empty payload BEFORE building response object
  3. CRITICAL: Existing test photos.test.ts:143–146 will break. Test expects 1.9MB → 413, but 1.9MB < 2.4MB so it would return 200. Plan's Step 7 incorrectly claims "behavior unchanged". Must update test thresholds if PAYLOAD_MAX raised
  4. INCORRECT COMMAND: Step 1 command uses `.venv/bin/pytest safety_portal/test/photos.test.ts` (wrong). photos.test.ts is TypeScript/vitest, not Python/pytest. Should be `cd safety_portal && npm test -- --run photos.test.ts`
  5. Whitespace-only payload_json edge case not properly handled: guard `row.payload_json ?` treats whitespace as truthy. Plan mentions this case should return null but implementation wouldn't. Must use `row.payload_json?.trim()` OR explicitly document that whitespace-only payloads attempt JSON.parse
  6. Missing root-cause analysis: No breakdown of typical form-data size for JHA forms to justify specific PAYLOAD_MAX choice. If typical form + 4 photos = 1.6–1.8MB, justify headroom percentage chosen

---

All plans are self-contained and structured per specification; however, the **required fixes** listed above must be resolved by a resolver/architect before handing to an operator for execution. The fixes primarily involve: (1) API/type signature decisions (A4 get_pending return type, A1 REST pagination), (2) critical math errors (A7 row size, A5 date filter), (3) implementation detail gaps (A2 timeout approach, A3 lock path naming, A6 docstring specificity), and (4) test infrastructure clarifications (A4 mock patterns, A6 timeout smoke test). None of these are showstoppers; each is addressable with 1–2 hours of focused specification work. After required fixes are applied, all plans are executor-ready.


---

## Part III — A7 doctrine-flag resolution (closes `doctrine_ok=False`)

The A7 reviewer flagged a doctrine concern: the **§34 photo-screening mirror invariant**. The
Worker's photo bounds are deliberately mirrored as defense-in-depth in three other places, and
any fix that changes the *photo budget* desyncs them and weakens §34:

- `safety_portal/worker/index.ts` — `PHOTO_MAX_BYTES=400_000`, `PHOTO_MAX_PER_SUBMISSION=8`, `PHOTO_MAX_PER_FIELD=4`
- `safety_reports/photo_screen.py:65,72` — `MAX_DECODED_BYTES=400_000`, `MAX_PHOTOS_PER_SUBMISSION=8` (Mac-side §34 Layer-6 re-validation on RAW bytes; explicitly "Mirrors the Worker's PHOTO_MAX_BYTES")
- `safety_portal/src/components/PhotoField.tsx:20-21` — `PHOTO_MAX_BYTES=400_000`, `HARD_MAX=4` (client already compresses via a downscale ladder)
- `safety_portal/worker/publishValidation.ts` — `PHOTO_MAX_COUNT`

**Resolution:** the fix raises **only the `PAYLOAD_MAX` envelope, never the photo budget** — so all
four mirrors stay valid and untouched, and `photo_screen.py` still independently caps decoded-bytes +
photo-count on the *raw* bytes, so a larger envelope does **not** widen the §34 attack surface
(defense-in-depth intact). Three refinements make it doctrine-clean:

1. **Size the envelope to the documented budget, don't just shift the inconsistency.** 8 × 400 KB
   decoded ≈ 4.27 MB base64 + form overhead ⇒ `PAYLOAD_MAX ≈ 4.5–4.7 MB` — **gated** on verifying
   Cloudflare D1 accepts a `payload_json` of that size as a single bound TEXT value (D1 has per-query/row
   size limits). If D1 caps lower, the *consistent* alternative is to **reduce `PHOTO_MAX_PER_SUBMISSION`
   and update all four mirrors in lockstep** — never raise the envelope without confirming the budget
   fits; never change the budget in one place only.
2. **Amend-guard stays send-free + shape-faithful.** Mirror the *existing* "no prior submission"
   response shape exactly (verify the real `/api/recent` shape first); it is read-only on the response
   path and touches no send capability — Invariant 1 intact.
3. Effort unchanged (**M**); risk drops (envelope-only with the §34 invariant explicitly protected).

**Net:** A7 is doctrine-clean once it (a) preserves/synchronizes the four-way §34 photo mirror,
(b) gates the envelope size on D1's real limit, and (c) keeps the amend-guard send-free + shape-faithful.

> **Cross-finding correction logged from firsthand reading (carry into any execution):** the A6
> `launchd-timeout-partial-write` CRITICAL was **overstated** — `org.solutionsmith.its.weekly-generate.plist`
> sets **no `ExitTimeOut`**, so launchd does **not** kill the compile at >1h (it waits indefinitely). The
> real risk is wall-clock (a stalled job blocks the queue) + memory (all photo-heavy PDFs held during
> merge). A6 targets those (per-job ~900s timeout + ~500 MB memory guard), not a phantom kill.
