"""Create the two PM-facing sheets inside 06 — Human Review.

WPR_Pending_Review: schema from Safety Reports Mission v4 (Q9 — gated send architecture).
ITS_Review_Queue:   best-inference schema from shared/review_queue.py stub + Op Stds v5.

Both live inside folder 210126402545540 (06 — Human Review).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss_api import api

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
    wpr = create(WPR_SHEET)
    rq = create(RQ_SHEET)
    print()
    print("06 — Human Review now contains:")
    print(f"  - WPR_Pending_Review  ({wpr['id']})")
    print(f"  - ITS_Review_Queue    ({rq['id']})")
