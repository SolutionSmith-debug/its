"""Operator-facing briefs for the system map's Smartsheet nodes.

One entry per `system_map` node id whose surface is a Smartsheet sheet (or a
dynamic sheet family). Each brief answers, in plain language a NON-TECHNICAL
Successor-Operator can act on: what the sheet is, who/what writes and reads
it, and what the operator does with it day-to-day. Rendered in the `/system`
detail rail beneath the blurb.

Kept as a companion module (not inline `MapNode` fields) so `system_map.py`
stays a scannable topology registry. Same import-light contract: pure data.

Writing discipline (HOUSE_REFLEXES §5): a brief states what a sheet MEANS and
how it behaves — never a live value, count, or gate state. ITS_Config is the
single source of live state; the rail's badges show it.

Parity: `tests/test_system_map.py` asserts every sheet-kind node with a
`sheet_id` has a brief here, and every brief key is a real node id.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SheetBrief:
    what: str          # 2-4 plain-English sentences (paragraphs split on \n\n)
    columns: str = ""  # "Key columns: …" line, omitted when not useful


SHEET_BRIEFS: dict[str, SheetBrief] = {
    # ── machine plane ────────────────────────────────────────────────────
    "sheet_config": SheetBrief(
        what=(
            "The system's settings panel: every on/off switch, polling interval, and "
            "tunable value ITS reads at runtime lives here as one row per setting. "
            "Daemons read it constantly; it is written by you — directly or through "
            "this dashboard's config editor — and by the sanctioned config-actuation "
            "path.\n\n"
            "Day-to-day you edit values here to pause a daemon, flip a feature on, or "
            "tune a threshold — but always read a row's full Description first: some "
            "rows carry activation preconditions, and honoring them is doctrine."
        ),
        columns=(
            "Setting + Workstream (together identify a row) · Value (the live state) · "
            "Description (read before flipping) · Modified/Modified By (audit trail)"
        ),
    ),
    "sheet_errors": SheetBrief(
        what=(
            "The system's error journal: every warning, error, and critical alarm any "
            "ITS script raises writes one row here via the error-log decorator. The "
            "watchdog and this dashboard read it — an open CRITICAL (blank Resolved At) "
            "is what the \"am I on fire\" checks count.\n\n"
            "Day-to-day you watch for CRITICAL rows, fix or acknowledge the underlying "
            "issue, then mark rows resolved with the dashboard's mark-resolved verb "
            "(it stamps Resolved At; a Script/Error-code filter is required). Open "
            "CRITICALs are never auto-deleted."
        ),
        columns=(
            "Error (code) · Severity · Script · Message · Correlation_ID (links the row "
            "to its alert email / Sentry event) · Resolved At (blank = open)"
        ),
    ),
    "sheet_review_queue": SheetBrief(
        what=(
            "The \"needs a human look\" inbox: anything ITS wasn't confident enough to "
            "handle on its own — low-confidence extractions, refused documents, "
            "security-flagged items — lands here rather than failing silently.\n\n"
            "Day-to-day you review PENDING rows, act on the underlying item, and "
            "resolve them to APPROVED/REJECTED (the dashboard's review-resolve verb, "
            "filter required). Treat any row with Security Flag checked as top "
            "priority — that is the adversarial-input tripwire."
        ),
        columns=(
            "Item ID · Workstream · Summary · Reason · Severity · SLA Tier · "
            "Status (PENDING → APPROVED/REJECTED) · Security Flag · Resolution Notes"
        ),
    ),
    "sheet_daemon_health": SheetBrief(
        what=(
            "The heartbeat board for all background daemons: one row per daemon, "
            "updated in place every cycle, showing when it last ran and how the run "
            "went. Every polling daemon writes its own row; you and the watchdog "
            "read it.\n\n"
            "Day-to-day this is watch-only — a stale Last Heartbeat or a bad Last "
            "Cycle Status means a daemon is stuck (the watchdog usually alerts "
            "first). The Enabled checkbox is display metadata only; the real on/off "
            "switch is the daemon's polling_enabled row in ITS_Config. This "
            "dashboard's heartbeats panel reads the same daemons' local heartbeat "
            "files directly, so it stays truthful even when Smartsheet is down."
        ),
        columns=(
            "Daemon Name · Last Heartbeat · Last Cycle Status · Items Processed · "
            "Total Cycles (lifetime, not daily) · Last Error Summary"
        ),
    ),
    "sheet_quarantine": SheetBrief(
        what=(
            "The holding pen for suspicious inbound email: any message from a sender "
            "not on the trusted list, or failing forgery checks, is logged here "
            "instead of being processed. Nothing automated ever acts on a "
            "quarantined message.\n\n"
            "Day-to-day you skim new rows, decide whether each sender is legitimate, "
            "add good senders to the trusted-contacts allowlist, and mark rows "
            "Reviewed. This surface becomes load-bearing when the Email Triage "
            "workstream processes inbound mail."
        ),
        columns=(
            "Sender · Subject · Summary (first ~200 chars, deliberately never "
            "AI-processed) · Reviewed · Added to Allowlist · Notes (quarantine reason)"
        ),
    ),
    "sheet_project_routing": SheetBrief(
        what=(
            "The project-to-Box-folder map: it tells ITS which Box folder belongs to "
            "each project, so document filing can be re-routed without a developer "
            "editing code.\n\n"
            "Day-to-day you only touch it when onboarding or retiring a project — "
            "add a row with the project name and its Box folder ID, or untick "
            "Active to retire one."
        ),
        columns="Project Name (exact-match key) · Box Folder ID · Active",
    ),
    "sheet_time_off": SheetBrief(
        what=(
            "The PTO calendar ITS consults when deciding who should review or "
            "receive something: one row per person per time-off span (start and end "
            "dates, both inclusive). The reviewer-chain logic reads it and "
            "automatically skips anyone who's out, promoting the next person in the "
            "chain; watchdog Check D scans two weeks ahead for windows where a "
            "chain would have nobody available.\n\n"
            "Day-to-day: add a row when someone will be out (retroactive entries "
            "work too); nothing else to do."
        ),
        columns="person email · start date · end date (inclusive)",
    ),
    "sheet_picklist_sync_config": SheetBrief(
        what=(
            "The wiring diagram for dropdown-list syncing: each row maps a source "
            "column (e.g. a master database sheet) to a target sheet's dropdown so "
            "option lists stay consistent across sheets. The hourly picklist-sync "
            "job reads it; the Sunday audit checks it for drift.\n\n"
            "Day-to-day you rarely touch it — add or disable a mapping row only "
            "when a new dropdown needs to track a master list. Removing an option "
            "that live cells still use is automatically blocked and routed to the "
            "Review Queue."
        ),
        columns=(
            "mapping_id · source_sheet_id/source_column · "
            "target_sheet_id/target_column · enabled"
        ),
    ),
    "registry_sheets": SheetBrief(
        what=(
            "The master-database references behind dropdown syncing — Equipment "
            "Master is the live canonical source picklist-sync propagates from. "
            "The Documentation Index (the card catalog of operator guides, with "
            "Box links to published PDFs) also lives in this supporting tier.\n\n"
            "Day-to-day: edit Equipment Master when equipment options change and "
            "the hourly sync propagates them. The old Vendor DB / Subcontractor DB "
            "stubs are superseded by ITS_Vendors and ITS_Subcontractors — never "
            "edit the old stubs."
        ),
    ),
    # ── safety band ──────────────────────────────────────────────────────
    "sheet_active_jobs": SheetBrief(
        what=(
            "The master list of active jobs the Safety Portal shows field crews, "
            "including each job's address (auto-fills Work Location on forms) and "
            "who receives its weekly safety report. The portal-sync daemon reads it "
            "into the portal dropdown; the weekly safety send resolves TO + CC "
            "recipients from it at the moment of sending.\n\n"
            "Day-to-day you keep it current: add new jobs, set Active/Inactive, and "
            "keep the safety-report contact and CC emails correct — a wrong email "
            "here changes who gets the weekly report; a blank one HOLDS the send "
            "rather than mis-sending."
        ),
        columns=(
            "Project Name · Job ID (stable key) · Address · Active · safety-reports "
            "contact + CC 1–5 · Portal Job Key (system bridge — don't edit)"
        ),
    ),
    "sheet_forms_catalog": SheetBrief(
        what=(
            "The menu of safety forms the portal offers, and which jobs each form "
            "appears on.\n\n"
            "Day-to-day you rarely touch it — set a form Inactive to pull it from "
            "the portal, or set Available For Jobs to limit a form to specific jobs "
            "(blank = all jobs). Never edit Form Code: it is the stable key the "
            "code matches on."
        ),
        columns=(
            "Form Name · Form Code (stable key — never edit) · Active · "
            "Display Order · Available For Jobs"
        ),
    ),
    "sheet_week_sheets": SheetBrief(
        what=(
            "A dynamic family, not one fixed sheet: ITS find-or-creates one sheet "
            "per job per week under the job's folder, and every filed submission "
            "lands as a row there. The Friday compile gathers a week's rows into "
            "the weekly packet; the rollup row carries the Compile Now checkbox "
            "for an on-demand compile.\n\n"
            "Day-to-day these are watch-only — tick Compile Now on a week's rollup "
            "row when you need the packet before Friday."
        ),
    ),
    "sheet_wsr": SheetBrief(
        what=(
            "The approval desk for Weekly Safety Reports — the human gate on the "
            "only path where safety reports leave the building. The Friday compile "
            "writes one row per job per week with the draft email body and the "
            "compiled PDF; the send daemon dispatches only rows a human approved.\n\n"
            "Day-to-day this is your main approval surface: review the attached "
            "PDF, edit Email Body if needed (it IS what gets sent), then tick "
            "Approve for Scheduled Send (goes out Monday morning) or Send Now. "
            "Nothing sends without that tick, and the system verifies the approver "
            "is authorized (a workspace member)."
        ),
        columns=(
            "Job/Project · Week Of · Compiled PDF · Email Body (the send source of "
            "truth) · Approve for Scheduled Send / Send Now · Approved By/At · "
            "Send Status · Sent At"
        ),
    ),
    "sheet_orphaned_reports": SheetBrief(
        what=(
            "The lost-and-found for portal submissions naming a job ITS doesn't "
            "recognize, or one that's no longer active — they can't be filed to a "
            "job's week sheet, so they land here instead of drowning the Review "
            "Queue.\n\n"
            "Day-to-day you check Pending rows, re-home the report to the right "
            "job (or discard it), and set Status accordingly."
        ),
        columns=(
            "Submission UUID · Job ID (the unresolved key) · Reason (job_not_found / "
            "job_inactive) · Box Link (the rendered PDF) · Status"
        ),
    ),
    # ── progress band ────────────────────────────────────────────────────
    "sheet_active_jobs_progress": SheetBrief(
        what=(
            "The progress workspace's own copy of the active-jobs list, carrying "
            "the progress-report recipient emails instead of the safety ones. The "
            "fieldops-sync daemon writes it automatically as a mirror of the "
            "portal's job list.\n\n"
            "Day-to-day the only cells you own are the recipient columns: keep the "
            "Progress Reports Contact + CC emails correct. Leave Portal Job Key "
            "and Job ID alone — they are system-managed join keys."
        ),
        columns=(
            "Project Name · Job ID · Progress Reports Contact + CC 1–5 · "
            "Portal Job Key (system join key — don't edit)"
        ),
    ),
    "sheet_wpr": SheetBrief(
        what=(
            "The approval desk for Weekly Progress Reports — the exact progress-side "
            "twin of the safety WSR sheet: same columns, same approval checkboxes, "
            "same rules.\n\n"
            "Day-to-day: review the compiled PDF, edit Email Body if needed, tick "
            "Approve for Scheduled Send or Send Now."
        ),
    ),
    # ── field-ops band ───────────────────────────────────────────────────
    "sheet_trackers": SheetBrief(
        what=(
            "A dynamic family of per-job standing trackers — one Hours Log (plus "
            "equipment, materials, and incidents siblings) per job, written "
            "one-way-up from the portal's field capture by fieldops-sync. "
            "Append-only: rows are corrected by superseding entries, never "
            "deleted.\n\n"
            "Day-to-day these are watch-only office views; corrections happen in "
            "the portal, and the sheets follow."
        ),
    ),
    # ── procurement band ─────────────────────────────────────────────────
    "sheet_its_vendors": SheetBrief(
        what=(
            "The company's master vendor list — the single source of truth for "
            "vendor names, contact emails, and terms. The portal's vendor picker "
            "syncs from it, and the PO/RFQ send steps look up the vendor's Contact "
            "Email here at the moment of sending.\n\n"
            "Day-to-day you keep vendor contact emails current — a blank email "
            "doesn't mis-send, it HOLDS the PO/RFQ until you fill it in. "
            "Deactivate vendors via Active rather than deleting rows, and never "
            "edit Vendor Key."
        ),
        columns=(
            "Vendor Key (permanent ID — don't touch) · Contact Email (what sends "
            "resolve) · Supply Categories · Default Terms Profile · Active"
        ),
    ),
    "sheet_po_log": SheetBrief(
        what=(
            "The purchase-order ledger: one row per PO showing its number, vendor, "
            "total, and lifecycle stage. The po-poll daemon writes it automatically "
            "as a mirror of the portal's authoritative database.\n\n"
            "Day-to-day it's read-only — a convenient office view of all POs "
            "without opening the portal. Don't edit rows here (the portal is the "
            "master), and never do math on Total (it's a display string)."
        ),
    ),
    "sheet_po_pending_review": SheetBrief(
        what=(
            "The approval desk for outgoing purchase orders — same layout and "
            "rules as the weekly-report approval sheets: review, optionally edit "
            "Email Body, tick Approve for Scheduled Send or Send Now; only then "
            "does po-send email the vendor.\n\n"
            "Note three columns are borrowed slots: \"Job ID\" holds the Vendor "
            "Key, \"Week Of\" holds the PO date, and \"Compiled PDF\" is the PO "
            "PDF link."
        ),
    ),
    "sheet_estimate_log": SheetBrief(
        what=(
            "The ledger of vendor quotes/estimates uploaded through the portal: "
            "one row per document showing whether it was imported, refused, or "
            "needs review. The estimate-poll daemon writes it automatically.\n\n"
            "Day-to-day it's watch-only — the import history at a glance. Refused "
            "or needs-review items get their real handling in the portal's "
            "disposition screen or the Review Queue, not here."
        ),
    ),
    "sheet_rfq_log": SheetBrief(
        what=(
            "The ledger of outbound Requests-for-Quote: one row per RFQ per "
            "vendor (an RFQ sent to three vendors is three rows), tracking its "
            "lifecycle from queued through responded.\n\n"
            "Day-to-day it's watch-only — a quick answer to \"which vendors have "
            "we asked, and who has responded.\" When a vendor's quote comes back "
            "through the portal, the row flips to responded on its own."
        ),
    ),
    "sheet_rfq_pending_review": SheetBrief(
        what=(
            "The approval desk for outgoing RFQs — one row per RFQ per vendor, "
            "same layout and rules as the PO approval sheet. On approval, rfq-send "
            "emails the vendor the price-free RFQ PDF plus a fillable quote "
            "form.\n\n"
            "Same borrowed slots as the PO sheet (\"Job ID\" = Vendor Key). Its "
            "distinct Workstream tag is a safety wall so the PO and subcontract "
            "senders can never dispatch an RFQ row — never change it."
        ),
    ),
    # ── subcontracts band ────────────────────────────────────────────────
    "sheet_its_subcontractors": SheetBrief(
        what=(
            "The master subcontractor list — the single source of truth for "
            "subcontractor identity, contact emails, trades, and home state "
            "(which drives each contract's governing law). The subcontract send "
            "step resolves the recipient's Contact Email here at send time.\n\n"
            "Day-to-day: keep contact emails and State accurate (a blank email "
            "HOLDS the send, never drops it), deactivate via Active rather than "
            "deleting, and never edit Sub Key."
        ),
        columns=(
            "Sub Key (permanent ID) · Contact Email · Trades · State (governing "
            "law derives from it) · Active"
        ),
    ),
    "sheet_subcontract_log": SheetBrief(
        what=(
            "The subcontract ledger: one row per subcontract, tracking it from "
            "pending review through executed. The subcontract-poll daemon writes "
            "it automatically as a mirror of the portal's database.\n\n"
            "Day-to-day it's read-only — an office view of every subcontract's "
            "status. Don't edit rows, and never do math on the Total display "
            "string."
        ),
    ),
    "sheet_subcontract_pending_review": SheetBrief(
        what=(
            "The approval desk for outgoing subcontract packages — same layout "
            "and rules as the other send-approval sheets. On approval, "
            "subcontract-send emails the package (contract + Exhibit A + "
            "schedule of values, one editable ZIP) to the subcontractor.\n\n"
            "Borrowed slots: \"Job ID\" = Sub Key, \"Week Of\" = subcontract "
            "date, \"Compiled PDF\" = the package link."
        ),
    ),
}
