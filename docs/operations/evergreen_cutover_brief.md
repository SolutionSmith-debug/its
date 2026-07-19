---
type: operations
date: 2026-07-18
status: active
related_prs: []
workstream: null
tags: [cutover, delivery, aug7_delivery, evergreen, personnel_brief, external_facing]
---

# Evergreen Cutover Brief — What We Need to Go Live

**For:** Evergreen Renewables office staff, field leads, and approvers.
**From:** Solution Smith (ITS operator of record).
**Prepared:** 2026-07-18. **Go-live (cutover):** Monday **Aug 3, 2026**. **On-site delivery & training:** Friday **Aug 7, 2026**.

> **Draft for Seth's review before distribution.** This brief is synthesized from the ITS delivery
> program of record. A few preparation items were scheduled for early July; Seth will confirm their
> current status before this goes to Evergreen. Every date below is from the master delivery calendar.

---

## 1. The one-page summary

**What ITS is.** ITS ("Integrated Technical System") is a Claude-powered assistant that automates the
paperwork around your projects — compiling weekly safety and progress reports, generating purchase
orders and subcontracts, filing documents, and tracking approvals — while keeping a person in control
of everything that leaves the building.

**What changes, and when.**
- **Aug 3 (cutover):** ITS switches from our internal test tenant onto **your** Microsoft 365, Smartsheet,
  and Box. From this day it is running on live Evergreen accounts. Most of your team won't notice
  anything on Aug 3 — it's a behind-the-scenes flip.
- **Aug 7 (delivery):** We come on-site to install ITS on the office machine, demonstrate the live
  system, train your people, and hand over the manuals. This is when your team starts using it day-to-day.

**What does NOT change — important:**
- **Smartsheet, Box, and Outlook stay your systems of record.** ITS reads and writes them through the
  same accounts your team already uses; it does not replace them or move your data somewhere new. The
  cutover is a settings change in Smartsheet and Box, not a change to how your data is stored.
- **Nothing is emailed to a vendor, customer, or crew without a human approving it first.** This is a
  permanent, non-negotiable design rule: the part of ITS that *writes* a document and the part that
  *sends* it are separate programs, and the sending part will not act until an authorized person checks
  the approval box. There is no "auto-send."
- **You stay in control.** A single "pause" switch stops all outbound activity at any time, and every
  approval is re-verified against who actually checked the box — a checked box by itself is never trusted.

---

## 2. What we need from Evergreen — by owner, by date

These are the things ITS needs **from Evergreen people** to go live. The hard deadline to have external
dependencies resolved (or escalated) is **Thursday Jul 23**; anything still open by then puts the Aug 3
date at risk. Items are grouped by who owns them.

### A. Microsoft 365 administrator
| # | What we need | Needed by | Why |
|---|---|---|---|
| A1 | **M365 admin access** to register the ITS application and set up mail-send permissions | ASAP (before Sep 1, when the old method is deprecated) | ITS sends approved emails through your Microsoft 365. This is the setup that lets it. |
| A2 | Three mailboxes on **@evergreenrenewables.com** — **safety@**, **progress@**, **procurement@** | By cutover (Aug 3) | These are the "from" addresses on the weekly safety packet, the progress packet, and purchase orders / RFQs. |
| A3 | Confirmation of your **Cloudflare Workers plan** (paid vs. free) | Before production accounts go live | Determines a one-time technical setup path; the free tier needs an extra day of work, so we need to know early. |

### B. DNS / domain owner
| # | What we need | Needed by |
|---|---|---|
| B1 | The exact **production web address** for the field portal (e.g. `safety.evergreenrenewables.com`) and a DNS record pointing to it | Staging window (Jul 20–22) |

### C. Procurement & Subcontracting — **Teala Paradise**
| # | What we need | Needed by |
|---|---|---|
| C1 | The **real recipient and vendor contact lists** — who each project's reports go to, and vendor email addresses for purchase orders | By cutover (Aug 3); request already out — please confirm |

### D. Approver accounts — **the seven leaders below**
ITS uses **Smartsheet workspace membership as approval authority.** Being shared into a project workspace
*is* being granted the power to approve its outbound sends. For this to work at cutover, each of the seven
must be a **real Smartsheet user** whose Smartsheet login email **exactly matches** the address on file.

| Role | Person | Account email |
|---|---|---|
| CEO | Jacob Stephens | jacobs@evergreenrenewables.com |
| CFO | Ezra Jones | ezraj@evergreenrenewables.com |
| Head of Engineering | Jechiah Stephens | jechiahs@evergreenrenewables.com |
| Senior PM | Ben Finkhousen | benf@evergreenrenewables.com |
| Head of Permitting | Tiffany Montastirsky | tiffanym@evergreenrenewables.com |
| Procurement & Subcontracting | Teala Paradise | tealap@evergreenrenewables.com |
| Head of Field Operations | Sam Rigney | samr@evergreenrenewables.com |

**Two things that will silently break approvals if wrong — please double-check:**
1. **The email must match exactly.** (One address on the original contact sheet had a `renwables`
   misspelling. A mismatched email doesn't error — it just quietly means that person's approvals won't
   count. Confirm each of the seven addresses is spelled correctly and is an active Smartsheet account.)
2. **Shares must be to individual people, not to a group/team.** A group share resolves to "nobody" and
   silently blocks every send in that workspace. Each approver is shared as an individual user.

Approval authority applies across the send-bearing workspaces — **ITS — Safety Portal, ITS — Progress,
ITS — Purchase Orders** (and **ITS — Subcontracts** for that workstream). Sharing a person into a
workspace is how you give them approval power there; removing them revokes it.

### E. Box account owner
| # | What we need | Needed by |
|---|---|---|
| E1 | A dedicated **Box identity** for ITS (`its@evergreenrenewables.com`) with access to the project folders | Around Jul 31 (production-setup window) |

### F. Office / IT
| # | What we need | Needed by |
|---|---|---|
| F1 | **Office network details** for the machine ITS runs on (outbound internet only — ITS opens no inbound ports) | Before Aug 7 install |
| F2 | A reachable **stand-in vendor inbox** for the Aug 7 live-send demonstration | Before Aug 7 |

### G. Leadership / legal
| # | What we need | Needed by |
|---|---|---|
| G1 | **Confirm the purchasing entity** exactly as it should appear on purchase orders: "Evergreen Renewables LLC," Irvine STE 570, 888-303-6424, and the invoice cc-list | Before the first live PO send |
| G2 | **Review the standard 17-clause Terms & Conditions** that ride on purchase orders | Before the first live PO send |

---

## 3. What changes for each role after go-live

### Field crews & field PMs
- Submit **daily reports and photos from your phone** on the production portal. ITS files each report to
  the right Box project folder within about a minute.
- **Photos:** JPEG/PNG only, up to 8 per submission. ITS automatically re-processes each photo, which
  **strips location/EXIF metadata** — a privacy and safety feature — and only clean photos are filed.
- The training drill answers the common question directly: "Where did my report go?" → it shows you the
  exact Box path and the portal's filed view.

### Office & procurement staff
- Anyone with portal admin rights drafts **purchase orders (and, later, RFQs)** in the builder: pick the
  job (address, tax, and delivery details auto-fill) → pick the vendor → enter line items → choose terms
  → generate. The draft lands as a **pending-review row** in Smartsheet awaiting approval.
- **Drafting is not approving.** Building a PO does not send it; an authorized approver must approve the
  pending row before it goes out.

### Approvers (the seven)
- You approve pending-review rows in **Smartsheet**. Your authority comes from being a **member of the
  project workspace** — not from any separate password or config.
- Every approval is **re-verified**: ITS checks the cell history to confirm the approval was actually set
  by an authorized person. A checked box that wasn't set by a shared user is rejected and logged.
- **Recipients are resolved at the moment of sending** from the job record (the "to" is the job's contact,
  "cc" the job's listed contacts) — not from whatever is displayed on the review row. If a job has no
  contact, the send is **held**, never sent to a wrong or blank address.

### Managers & ownership
- An **Operator Dashboard** (reachable securely over Tailscale) shows read-only health panels — which
  automations are running, what's pending approval, any errors, and current settings — plus a small set
  of safe actions (pause everything, approve a row, respond to an alert, add/disable a portal user or
  trusted contact).
- **Weekly packets** compile automatically every **Friday at ~2:00 PM Pacific**.

---

## 4. Rehearsal & delivery calendar

The system is **already live on your tenants as of Aug 3** — so the Aug 7 visit is a demonstration of the
real system, not a staging act.

| Date | What happens |
|---|---|
| **Jul 20–22** | Production setup (app registration, mailboxes, DNS, Box, security rules) — behind the scenes |
| **Thu Jul 23** | **Deadline** to resolve/escalate anything still needed from Evergreen (Section 2) |
| **Jul 25–30** | Quiet unattended trial run — nothing changes |
| **Thu Jul 31** | Go / no-go decision; if go, production accounts and final deploy that afternoon |
| **Mon Aug 3** | **Cutover** — ITS flips onto live Evergreen tenants |
| **Tue Aug 4 / Wed Aug 5** | Soak + two dress rehearsals |
| **Fri Aug 7** | **On-site delivery** at the Evergreen office |
| **≈ Aug 14 (Day 7)** | Review checkpoint before the old test environment is retired |

### Delivery day (Aug 7) — the arc
1. **Install (morning):** power/placement → office network → boot & log in → secure remote access test →
   bring the system live → re-run the automated cutover verification on-site → confirm healthy. (We do
   not demo past any failed gate.)
2. **Demo (~40 min), on the real system:** a field submission → a purchase order built live → a live
   **F22 approval** (we narrate: "membership *is* authority") → the approved email lands in the stand-in
   vendor inbox from `procurement@evergreenrenewables.com` → a dashboard tour → manuals handoff.
3. **Training (~60 min), hands-on by your team:**
   - **Field PMs (~20 min each):** log in, submit a form + photo, find the filed report, re-download it.
   - **Owner/admin (~40 min):** flip the pause switch, approve a review row, respond to an alert,
     add/disable a trusted contact, add/disable/list portal users.
4. **Acceptance:** ownership signs the acceptance, which states in plain terms that **Seth Smith remains
   the operator of record after cutover**, with a named date for the future "successor operator" training
   milestone.
5. **Leave-behind:** printed manual set, a one-page **emergency card** (how to pause everything + Seth's
   phone/email), the signed acceptance, and an inventory of accounts.

---

## 5. Support & escalation after go-live

ITS is built to be maintainable, and support has three tiers:

- **Tier 1 — it fixes itself.** Most hiccups (a stalled automation, a brief outage) self-heal, with an
  external "dead-man" monitor as a backstop. **No one needs to do anything.**
- **Tier 2 — a trained operator with Claude Code.** For documented, low-risk issues — re-run an
  automation, toggle a documented setting, re-send an approval, clear a stuck lock. This role writes no
  code and touches no passwords or secrets. (Until a Successor-Operator is trained and cleared — a named
  post-delivery milestone — this role is Seth.)
- **Tier 3 — Seth (the developer).** Anything new, or anything touching the four "high-risk" areas —
  **the external-send gate, secrets/logins, core policy, or code/deploys** — always goes to Seth.

**The rule, in one line:** *when unsure, escalate.* After cutover, **Seth remains the operator of record**
and receives all alerts (through at least the Day-7 review). The emergency card in your leave-behind
package has the pause instructions and Seth's contact for anything urgent.

---

*This brief covers what Evergreen personnel need to know and do for cutover. It does not include internal
operator mechanics (the on-site technical checklist, the cutover verification script, and rollback
procedures live in the operator runbooks). Questions → Seth.*
