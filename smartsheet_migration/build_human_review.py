"""[ARCHIVED 2026-05-17] — provisioning script. Sheets created, then moved.

This script was run on 2026-05-17 ~01:26 UTC and provisioned two sheets in
folder 210126402545540 (06 — Human Review of Forefront Portfolio — ITS Demo):
  - WPR_Pending_Review (id 3096105695793028)
  - ITS_Review_Queue   (id 7243317526876036)

Same-day evening session restructured the workspace topology:
  - WPR_Pending_Review moved → ITS — Human Review / 01 — Safety Reports
    (folder 2486957285631876, workspace 8561891980142468)
  - ITS_Review_Queue   moved → ITS — System / 03 — Queues
    (folder 7201663145535364, workspace 680592632244100)
  - 06 — Human Review folder (210126402545540) deleted

This script is no longer runnable as written. HR_FOLDER is invalid; sheets
already exist at their new homes. Preserved in-tree per Op Stds v8 §14
(preservation-over-refactor); git history is the diff reference.

For the current provisioning record, see shared/sheet_ids.py and
docs/session_logs/2026-05-17_smartsheet_workspace_restructure.md.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss_api import api

sys.exit("Archived script — do not re-run. See module docstring.")

HR_FOLDER = 210126402545540

# ---- WPR_Pending_Review ----
# 12 columns per Mission v4 — Customer, Job, Week, Draft Body, Recipients,
# Approved for Send, Approved By, Approved At, Sent At, Send Status, Late Send, Notes
WPR_SHEET = {
    "name": "WPR_Pending_Review",
    "columns": [
        {"title": "Customer", "primary": True, "type": "TEXT_NUMBER"},
        {"title": "Job", "type": "TEXT_NUMBER"},
        {"title": "Week", "type": "DATE"},
        {"title": "Draft Body", "type": "TEXT_NUMBER"},
        {"title": "Recipients", "type": "TEXT_NUMBER"},
        {"title": "Approved for Send", "type": "CHECKBOX"},
        {"title": "Approved By", "type": "CONTACT_LIST"},
        {"title": "Approved At", "type": "DATE"},
        {"title": "Sent At", "type": "DATE"},
        {"title": "Send Status", "type": "PICKLIST",
         "options": ["PENDING", "SENT", "FAILED", "HELD"]},
        {"title": "Late Send", "type": "CHECKBOX"},
        {"title": "Notes", "type": "TEXT_NUMBER"},
    ],
}

# ---- ITS_Review_Queue ----
# Best-inference from shared/review_queue.py stub:
#   workstream, summary, payload, sla_tier, reason, security_flag,
#   plus status enum + standard timestamps + assignee/resolver
# Defensible defaults — Seth can adjust before automation goes live.
RQ_SHEET = {
    "name": "ITS_Review_Queue",
    "columns": [
        {"title": "Item ID", "primary": True, "type": "TEXT_NUMBER"},
        {"title": "Created At", "type": "DATE"},
        {"title": "Workstream", "type": "PICKLIST",
         "options": ["safety_reports", "po_materials", "subcontracts",
                     "email_triage", "ai_employee", "other"]},
        {"title": "Summary", "type": "TEXT_NUMBER"},
        {"title": "Reason", "type": "TEXT_NUMBER"},
        {"title": "Payload", "type": "TEXT_NUMBER"},
        {"title": "SLA Tier", "type": "PICKLIST",
         "options": ["4h", "24h", "48h"]},
        {"title": "Status", "type": "PICKLIST",
         "options": ["PENDING", "IN_REVIEW", "APPROVED",
                     "REJECTED", "ESCALATED"]},
        {"title": "Security Flag", "type": "CHECKBOX"},
        {"title": "Assigned To", "type": "CONTACT_LIST"},
        {"title": "Resolved By", "type": "CONTACT_LIST"},
        {"title": "Resolved At", "type": "DATE"},
        {"title": "Notes", "type": "TEXT_NUMBER"},
    ],
}

def create(sheet_def):
    r = api("POST", f"/folders/{HR_FOLDER}/sheets", body=sheet_def)
    res = r["result"]
    print(f"✓ Created: {res['name']}  id={res['id']}")
    return res

if __name__ == "__main__":
    print("ARCHIVED — see docstring at top of file.", file=sys.stderr)
    print("Sheets already provisioned and moved. See shared/sheet_ids.py.", file=sys.stderr)
    sys.exit(1)
