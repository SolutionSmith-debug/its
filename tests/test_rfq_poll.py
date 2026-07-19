"""RED-suite unit tests for po_materials/rfq_poll.py + the R2 lane contracts
(ADR-0004 Lane 2, PR-C: verify → per-vendor price-free render → Box → RFQ_Log +
RFQ_Pending_Review → mark-filed once → status mirror).

Fully mocked at the module seams, the tests/test_estimate_poll.py house idiom (no
live Smartsheet / Box / Worker). Every daemon test here is a PROVE-THE-CONTROL-
BITES test: it asserts the CONTROL fires (rfq:v1 integrity refusal, per-vendor
fence, dark-ship gate, idempotent replay, receipt-last) and would fail if the
control were deleted.

Contract pins exercised (the PR-C shared contract):
  * rfq:v1 HMAC — signatures in these tests are computed IN-TEST from the pinned
    canonical math (recompute-from-fields: fixed header/line key order + compact
    JSON + "rfq:v1"\\n id\\n number\\n json), independent of shared.portal_hmac —
    a daemon verifying a drifted canonical fails the happy path here.
  * Tampered canonical (any signed field mutated after signing) → one-shot flag +
    security Review-Queue row + CRITICAL; NEVER rendered, NEVER filed, NO receipt.
  * Unknown vendor → per-vendor Review-Queue fence; the OTHER vendors still render
    + file + stage, and the receipt carries only the filed vendors.
  * ALL vendors unknown → receipt WITHHELD + one-shot flag (never a silent drain).
  * polling gate false → dark-ship no-op (zero Worker calls).
  * Idempotent replay (a crash after filing but before the receipt): a re-served
    rfq whose ledger + review rows already exist appends NOTHING new and still
    posts the receipt.
  * The review-row Workstream tag is the DISTINCT 'po_materials_rfq' lane value —
    hard-populated, registered, and ≠ po_send's 'po_materials' (cross-lane
    dispatch impossibility).
  * Renderer escaping (red-team #11): hostile reportlab markup in operator/vendor
    strings renders escaped — a deliberately BROKEN tag would crash the paraparser
    if escaping were removed. Price-free is source-pinned.

Run with: pytest -q tests/test_rfq_poll.py
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import inspect
import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from po_materials import rfq_generate, rfq_poll, rfq_review
from shared.error_log import Severity

SECRET = "rfq-test-secret"

HEADER_KEYS = (
    "rfq_number", "job_no", "job_name",
    "ship_to_name", "ship_to_address", "ship_to_city", "ship_to_state", "ship_to_zip",
    "delivery_contact_name", "delivery_contact_phone", "delivery_contact_email",
    "scope_text", "due_date",
)
LINE_KEYS = ("position", "part_number", "description", "qty", "unit", "line_note")

VENDOR_1 = {
    "Vendor Name": "Platt Electric Supply",
    "Vendor Key": "VEN-000001",
    "Address": "123 Supply Rd, Portland, OR 97201",
    "Contact Name": "Sam Seller",
    "Contact Email": "sam@platt.example",
    "Contact Phone": "503-555-0100",
}
VENDOR_2 = {**VENDOR_1, "Vendor Name": "Nassau Electric", "Vendor Key": "VEN-000007",
            "Contact Email": "quotes@nassau.example"}
PURCHASER = {
    "entity": "Evergreen Renewables LLC",
    "address_lines": ["500 Solar Way", "Rockford, IL 61101"],
    "phone": "815-555-0100",
}


# ---- row builders (rfq:v1 canonical computed IN-TEST — the golden math) ------------


def _golden_canonical_json(
    rfq: dict[str, Any], lines: list[dict[str, Any]], vendor_keys: list[str]
) -> str:
    obj: dict[str, Any] = {k: rfq.get(k) for k in HEADER_KEYS}
    obj["line_items"] = [{k: ln.get(k) for k in LINE_KEYS} for ln in lines]
    obj["vendor_keys"] = sorted(vendor_keys)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sign_rfq(
    secret: str, rfq_id: int, rfq: dict[str, Any],
    lines: list[dict[str, Any]], vendor_keys: list[str],
) -> str:
    canonical = "\n".join([
        "rfq:v1", str(rfq_id), str(rfq.get("rfq_number") or ""),
        _golden_canonical_json(rfq, lines, vendor_keys),
    ])
    return _hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _vendor_rows(keys: list[str], status: str = "pending") -> list[dict[str, Any]]:
    """The Worker-joined rfq_vendors rows the pending route serves."""
    return [
        {"vendor_key": k, "status": status, "box_pdf_file_id": None,
         "box_form_file_id": None, "review_row_id": None, "sent_at": None}
        for k in keys
    ]


def _rfq_row(**over: Any) -> dict[str, Any]:
    """A pending rfqs row signed EXACTLY as the Worker would (rfq:v1). Overrides
    applied AFTER signing = deliberate tampering in tests; overrides that should
    be signed go through `signed`; the vendor fan-out via `vendor_keys`."""
    rfq_id = int(over.pop("id", 7))
    signed: dict[str, Any] = over.pop("signed", {})
    vendor_keys: list[str] = over.pop("vendor_keys", ["VEN-000001"])
    lines: list[dict[str, Any]] = over.pop("lines", [
        {"position": 1, "part_number": "RK-100", "description": "Rail 100",
         "qty": 10, "unit": "EA", "line_note": "black"},
    ])
    row: dict[str, Any] = {
        "id": rfq_id,
        "rfq_uuid": f"u-rfq-{rfq_id}",
        "rfq_number": f"RFQ-2026.001-{rfq_id:03d}",
        "job_no": "2026.001",
        "job_name": "Sunrise Solar",
        "ship_to_name": "Sunrise Solar Laydown",
        "ship_to_address": "100 Array Rd",
        "ship_to_city": "Rockford",
        "ship_to_state": "IL",
        "ship_to_zip": "61101",
        "delivery_contact_name": "Dana Field",
        "delivery_contact_phone": "815-555-0101",
        "delivery_contact_email": "dana@evergreen.example",
        "scope_text": "Supply-only racking package.",
        "due_date": "2026-08-14",
        "status": "queued",
        "line_items": lines,
        "vendors": _vendor_rows(vendor_keys),
        **signed,
    }
    lines_for_sig = row["line_items"] if isinstance(row["line_items"], list) else []
    keys_for_sig = [str(v["vendor_key"]) for v in row["vendors"]]
    row["hmac"] = _sign_rfq(SECRET, rfq_id, row, lines_for_sig, keys_for_sig)
    row.update(over)  # post-signing overrides = tampering
    return row


# ---- fixture (the estimate_poll _patch idiom) --------------------------------------


@pytest.fixture
def _patch(mocker):
    r_log = mocker.patch("po_materials.rfq_poll.rfq_log")
    r_log.find_row.return_value = None
    r_log.append_row.return_value = 1
    r_log.update_status.return_value = True
    r_log.STATUS_FILED = "filed"
    r_log.STATUS_SENT = "sent"
    r_log.COL_STATUS = "Status"
    r_log.SETTLED_STATUSES = frozenset({"sent", "responded", "closed", "canceled"})

    r_review = mocker.patch("po_materials.rfq_poll.rfq_review")
    r_review.find_row_by_rfq_vendor.return_value = None
    r_review.add_rfq_review_row.return_value = 9001
    r_review.rfq_email_body_template.return_value = "seed body"
    r_review.notes_for_review_row.side_effect = (
        lambda rfq_id, rfq_number, vendor_key:
        f"rfq_id={rfq_id}; rfq_number={rfq_number}; vendor_key={vendor_key}"
    )
    r_review.sheet_id.return_value = 555
    r_review.WORKSTREAM_TAG = rfq_review.WORKSTREAM_TAG
    r_review.COL_WORKSTREAM = rfq_review.COL_WORKSTREAM
    r_review.COL_SEND_STATUS = rfq_review.COL_SEND_STATUS
    r_review.STATUS_SENT = rfq_review.STATUS_SENT
    r_review.row_rfq_id.side_effect = rfq_review.row_rfq_id
    r_review.row_rfq_number.side_effect = rfq_review.row_rfq_number
    r_review.row_vendor_key.side_effect = rfq_review.row_vendor_key

    upload = mocker.patch(
        "po_materials.rfq_poll.box_client.upload_bytes_or_new_version",
        return_value={"id": "f-rfq-1", "name": "x", "size": 9},
    )

    seams = {
        "gate": mocker.patch("po_materials.rfq_poll._polling_enabled", return_value=True),
        "resolve_cfg": mocker.patch("po_materials.rfq_poll.resolve_and_log", return_value={}),
        "creds": mocker.patch(
            "po_materials.rfq_poll._resolve_credentials",
            return_value=SimpleNamespace(
                base_url="https://portal.example", bearer="tok", secret=SECRET
            ),
        ),
        "purchaser": mocker.patch(
            "po_materials.rfq_poll.terms_lib.load_purchaser_config",
            return_value=PURCHASER,
        ),
        # Worker I/O (the pinned portal_client contract functions).
        "pending": mocker.patch(
            "po_materials.rfq_poll.portal_client.get_rfqs_pending", return_value=[]
        ),
        "mark_filed": mocker.patch(
            "po_materials.rfq_poll.portal_client.post_rfq_mark_filed", return_value=True
        ),
        "status_sync": mocker.patch(
            "po_materials.rfq_poll.portal_client.post_rfq_status_sync",
            return_value={"ok": True, "updated": 1},
        ),
        # SoR + render + Box seams.
        "vendor": mocker.patch(
            "po_materials.rfq_poll.vendors.get_vendor_by_key", return_value=VENDOR_1
        ),
        "render": mocker.patch(
            "po_materials.rfq_poll.rfq_generate.render_rfq_pdf",
            return_value=b"%PDF-rfq",
        ),
        "upload": upload,
        "box_folder": mocker.patch(
            "po_materials.rfq_poll._resolve_rfq_box_folder", return_value="folder-rfq"
        ),
        "attach": mocker.patch(
            "po_materials.rfq_poll.smartsheet_client.attach_pdf_to_row", return_value=1
        ),
        # Status-pass review-sheet read.
        "get_rows": mocker.patch(
            "po_materials.rfq_poll.smartsheet_client.get_rows", return_value=[]
        ),
        "rfq_log": r_log,
        "rfq_review": r_review,
        "review_q": mocker.patch("po_materials.rfq_poll.review_queue.add", return_value=1),
        "anomaly": mocker.patch(
            "po_materials.rfq_poll.anomaly_logger.check", return_value=None
        ),
        # Observability + flag-state seams.
        "log": mocker.patch("po_materials.rfq_poll.error_log.log", return_value=None),
        "hb": mocker.patch("po_materials.rfq_poll._write_heartbeat", return_value=None),
        "hb_row": mocker.patch("po_materials.rfq_poll._write_heartbeat_row", return_value=None),
        "marker": mocker.patch("po_materials.rfq_poll._write_watchdog_marker", return_value=None),
        "flags_load": mocker.patch("po_materials.rfq_poll._load_flags", return_value={}),
        "flags_persist": mocker.patch("po_materials.rfq_poll._persist_flags", return_value=None),
        "circuit": mocker.patch(
            "po_materials.rfq_poll.circuit_breaker.is_open", return_value=False
        ),
    }
    return seams


def _run(_patch) -> Any:
    """One cycle inside the (mocked-out) lock — the estimate_poll test idiom."""
    return rfq_poll._poll_inside_lock()


def _logged_codes(_patch) -> list[str]:
    return [kw.get("error_code") for _, kw in _patch["log"].call_args_list]


# ---- dark-ship gate ----------------------------------------------------------------


def test_polling_gate_false_is_total_noop(_patch):
    """Dark-ship: gate false → ZERO Worker calls (no pull, no receipt, no sync)."""
    _patch["gate"].return_value = False
    stats = rfq_poll.poll_once()
    assert stats.skipped_disabled is True
    _patch["pending"].assert_not_called()
    _patch["mark_filed"].assert_not_called()
    _patch["status_sync"].assert_not_called()
    _patch["hb"].assert_not_called()
    _patch["marker"].assert_not_called()


# ---- rfq:v1 integrity (tampered canonical) -----------------------------------------


def test_tampered_canonical_one_shot_flag_never_rendered(_patch):
    """PROVE-THE-CONTROL-BITES: a signed field mutated AFTER signing (here the
    scope text — the recompute-from-fields canonical covers it) → CRITICAL +
    security Review-Queue row + one-shot flag; NEVER rendered, NEVER uploaded,
    NO receipt (the row stays queued in D1 for forensics). Delete the verify and
    this test fails."""
    _patch["pending"].return_value = [_rfq_row(scope_text="tampered after signing")]

    stats = _run(_patch)

    assert stats.rejected == 1
    _patch["render"].assert_not_called()
    _patch["upload"].assert_not_called()
    _patch["mark_filed"].assert_not_called()
    _patch["review_q"].assert_called_once()
    rq = _patch["review_q"].call_args.kwargs
    assert rq["security_flag"] is True
    assert rq["severity"] == Severity.CRITICAL
    assert "rfq_hmac_failure" in _logged_codes(_patch)
    (persisted,), _ = _patch["flags_persist"].call_args
    assert persisted == {"7": "hmac"}


def test_tampered_vendor_list_is_rejected(_patch):
    """The vendor fan-out list is signature-covered: appending a vendors row after
    signing (recipient poisoning) fails the verify — nothing renders."""
    _patch["pending"].return_value = [
        _rfq_row(vendors=_vendor_rows(["VEN-000001", "VEN-666666"]))
    ]

    stats = _run(_patch)

    assert stats.rejected == 1
    _patch["render"].assert_not_called()
    _patch["mark_filed"].assert_not_called()


# ---- per-vendor fence ---------------------------------------------------------------


def test_unknown_vendor_fenced_other_vendors_still_filed(_patch):
    """A vendor missing from ITS_Vendors is fenced to the Review Queue while the
    OTHER vendors render + file + stage; the receipt carries ONLY the filed
    vendors. Remove the per-vendor fence and this test fails (the whole rfq
    aborts or the unknown vendor silently files)."""
    _patch["pending"].return_value = [
        _rfq_row(vendor_keys=["VEN-000001", "VEN-000007"])
    ]
    _patch["vendor"].side_effect = (
        lambda key: VENDOR_1 if key == "VEN-000001" else None
    )

    stats = _run(_patch)

    assert stats.vendors_fenced == 1
    assert stats.vendors_filed == 1
    assert stats.filed == 1
    _patch["render"].assert_called_once()  # only the known vendor rendered
    assert "rfq_vendor_unknown" in _logged_codes(_patch)
    _patch["review_q"].assert_called_once()  # the fence row
    _patch["mark_filed"].assert_called_once()
    receipt_vendors = _patch["mark_filed"].call_args.kwargs["vendor_results"]
    assert [v["vendor_key"] for v in receipt_vendors] == ["VEN-000001"]
    assert receipt_vendors[0]["box_pdf_file_id"] == "f-rfq-1"
    assert receipt_vendors[0]["review_row_id"] == "9001"  # string per the Worker shape


def test_all_vendors_unknown_withholds_receipt(_patch):
    """EVERY vendor fenced → the receipt is WITHHELD (a receipt with zero
    artifacts would silently drain the rfq) and the rfq is one-shot flagged."""
    _patch["pending"].return_value = [_rfq_row()]
    _patch["vendor"].return_value = None

    stats = _run(_patch)

    assert stats.filed == 0
    _patch["mark_filed"].assert_not_called()
    assert "rfq_all_vendors_fenced" in _logged_codes(_patch)
    (persisted,), _ = _patch["flags_persist"].call_args
    assert persisted == {"7": "vendors_fenced"}


# ---- idempotent replay --------------------------------------------------------------


def test_replay_after_lost_receipt_appends_nothing_and_reposts_receipt(_patch):
    """The mark-filed-crash contract: a re-served rfq whose RFQ_Log row AND
    review row already exist (the prior cycle filed them, then the receipt was
    lost) appends NO duplicate rows — and still posts the receipt with the
    EXISTING review row id. Remove either find-or-skip and this test fails."""
    _patch["pending"].return_value = [_rfq_row()]
    _patch["rfq_log"].find_row.return_value = {"_row_id": 42, "Status": "filed"}
    _patch["rfq_review"].find_row_by_rfq_vendor.return_value = {"_row_id": 9001}

    stats = _run(_patch)

    _patch["rfq_log"].append_row.assert_not_called()
    _patch["rfq_review"].add_rfq_review_row.assert_not_called()
    _patch["mark_filed"].assert_called_once()
    receipt_vendors = _patch["mark_filed"].call_args.kwargs["vendor_results"]
    assert receipt_vendors[0]["review_row_id"] == "9001"
    assert stats.filed == 1


def test_happy_path_files_ledger_review_and_receipts_last(_patch):
    """Clean rfq: render → Box → review row (tagged lane) → ledger row → receipt.
    The receipt is LAST and carries the collected artifacts."""
    _patch["pending"].return_value = [_rfq_row()]

    stats = _run(_patch)

    assert stats.filed == 1 and stats.vendors_filed == 1
    _patch["render"].assert_called_once()
    _patch["upload"].assert_called_once()
    upload_values = list(_patch["upload"].call_args.args)
    assert b"%PDF-rfq" in upload_values
    _patch["rfq_log"].append_row.assert_called_once()
    log_kwargs = _patch["rfq_log"].append_row.call_args.kwargs
    assert log_kwargs["vendor_key"] == "VEN-000001"
    assert log_kwargs["status"] == "filed"
    _patch["rfq_review"].add_rfq_review_row.assert_called_once()
    _patch["mark_filed"].assert_called_once()
    assert _patch["mark_filed"].call_args.kwargs["rfq_id"] == 7
    # No fence, no flag, no security row on the happy path.
    _patch["review_q"].assert_not_called()
    _patch["flags_persist"].assert_not_called()


# ---- bearer 401 (cycle-stop + catch-order; the PR-A downgrade bug class) ------------


def test_bearer_401_mid_cycle_stops_and_persists_earned_flag(_patch):
    """PROVE-THE-CONTROL-BITES: a 401 (PortalAuthError) raised mid-cycle — here on
    the mark-filed POST of a LATER row, after an EARLIER (tampered) row already
    earned a one-shot flag — STOPS the cycle (rfq_bearer_rejected CRITICAL; the
    status pass never runs) AND still persists the earned flag (the finally-persist,
    FIX 2). Everything stays queued in D1 for a safe re-attempt once the token is
    fixed.

    This RED-lights two ways: (a) if the flag-persist moved back out of the finally
    into a return-value dirty bool, the mid-loop raise would skip it → a duplicate
    CRITICAL/Review-Queue re-alert next cycle for the already-flagged row; (b) if
    _rfq_pass (or _process_pending_rfq) caught PortalTransportError BEFORE
    PortalAuthError, the 401 — a PortalTransportError SUBCLASS — would be swallowed
    as a per-row transient: no _BearerRejectedError, no cycle-stop, no
    rfq_bearer_rejected, and the status pass would run. The load-bearing catch order
    is PortalAuthError first."""
    tampered = _rfq_row(id=8, scope_text="tampered after signing")  # bad HMAC → flag "hmac"
    clean = _rfq_row(id=7)  # verifies + files, then the mark-filed receipt 401s
    _patch["pending"].return_value = [tampered, clean]
    _patch["mark_filed"].side_effect = rfq_poll.portal_client.PortalAuthError("401")

    stats = _run(_patch)

    # Cycle STOPPED on the 401.
    assert stats.bearer_rejected is True
    assert "rfq_bearer_rejected" in _logged_codes(_patch)
    # The whole cycle aborted — not just one row — so the status pass never ran.
    _patch["status_sync"].assert_not_called()
    # FIX 2: the flag the EARLIER (tampered) row earned this cycle still reached disk
    # despite the mid-loop bearer abort (finally-persist, not a return-value bool).
    _patch["flags_persist"].assert_called_once()
    (persisted,), _ = _patch["flags_persist"].call_args
    assert persisted == {"8": "hmac"}


# ---- status pass (forward-only SENT mirror) -----------------------------------------


def test_status_pass_syncs_sent_rows_then_stamps_ledger(_patch):
    """A SENT review row status-syncs per (rfq, vendor) and stamps the RFQ_Log
    mirror AFTER the POST (D1 first). A settled ledger row generates nothing."""
    sent_row = {
        "_row_id": 1,
        rfq_review.COL_WORKSTREAM: rfq_review.WORKSTREAM_TAG,
        rfq_review.COL_SEND_STATUS: rfq_review.STATUS_SENT,
        rfq_review.COL_JOB_ID: "VEN-000001",
        rfq_review.COL_NOTES: "rfq_id=7; rfq_number=RFQ-2026.001-007; vendor_key=VEN-000001",
    }
    _patch["get_rows"].return_value = [sent_row]
    _patch["rfq_log"].find_row.return_value = {"_row_id": 42, "Status": "filed"}

    stats = _run(_patch)

    _patch["status_sync"].assert_called_once()
    sync_kwargs = _patch["status_sync"].call_args.kwargs
    assert sync_kwargs == {"rfq_id": 7, "vendor_key": "VEN-000001", "status": "sent"}
    _patch["rfq_log"].update_status.assert_called_once_with(
        "RFQ-2026.001-007", "VEN-000001", "sent"
    )
    assert stats.status_synced == 1


def test_status_pass_ignores_foreign_workstream_tag(_patch):
    """P1b: a foreign-tagged row on the RFQ review sheet is never status-synced."""
    foreign = {
        "_row_id": 2,
        rfq_review.COL_WORKSTREAM: "po_materials",  # the PO lane's tag ≠ ours
        rfq_review.COL_SEND_STATUS: rfq_review.STATUS_SENT,
        rfq_review.COL_JOB_ID: "VEN-000001",
        rfq_review.COL_NOTES: "rfq_id=7; rfq_number=X; vendor_key=VEN-000001",
    }
    _patch["get_rows"].return_value = [foreign]

    _run(_patch)

    _patch["status_sync"].assert_not_called()
    assert "rfq_status_foreign_tag" in _logged_codes(_patch)


# ---- lane-tag contracts (cross-lane dispatch impossibility) -------------------------


def test_review_row_workstream_tag_is_distinct_lane_value(mocker):
    """The twin-shape pin: every review row is hard-populated with the DISTINCT
    'po_materials_rfq' lane tag (non-empty — red-team #8), which differs from
    po_send's SendConfig tag so the Stage-2b contamination guard HARD-HELDs an
    RFQ row on any other lane (cross-lane dispatch impossible)."""
    from po_materials import po_review

    add = mocker.patch("po_materials.rfq_review.wsr_review.add_wsr_row", return_value=1)
    mocker.patch("po_materials.rfq_review.sheet_id", return_value=123)
    rfq_review.add_rfq_review_row(
        job_project="2026.001 — Sunrise Solar",
        vendor_key="VEN-000001",
        rfq_date=date(2026, 7, 19),
        pdf_link="https://app.box.com/file/f-rfq-1",
        recipient_to="sam@platt.example",
        cc_display="",
        email_body="body",
        notes="rfq_id=7; rfq_number=N; vendor_key=VEN-000001",
    )
    tag = add.call_args.kwargs["workstream"]
    assert tag == "po_materials_rfq"
    assert tag.strip()  # non-empty — the fail-open-on-absent path can never apply
    assert tag != po_review.WORKSTREAM_TAG  # ≠ the PO lane's SendConfig tag


def test_lane_tag_registered_in_picklist_registry():
    """Registry parity (HOUSE_REFLEXES §4): the new PICKLIST value is registered
    in shared/picklist_validation in the SAME change that writes it."""
    from shared import picklist_validation

    assert "po_materials_rfq" in picklist_validation._RFQ_WORKSTREAM_VALUES
    assert picklist_validation._RFQ_WORKSTREAM_VALUES == {"po_materials_rfq"}
    # The ledger keeps the parent tag; the two vocabularies must not be conflated.
    assert "po_materials_rfq" not in picklist_validation._PO_WORKSTREAM_VALUES


def test_vendor_key_slot_mismatch_returns_none():
    """A review row whose 'Job ID' slot disagrees with the Notes vendor_key copy
    resolves NO vendor (a spliced/hand-edited row must never pick a recipient)."""
    row = {
        rfq_review.COL_JOB_ID: "VEN-000001",
        rfq_review.COL_NOTES: "rfq_id=7; rfq_number=N; vendor_key=VEN-000002",
    }
    assert rfq_review.row_vendor_key(row) is None


# ---- renderer: escaping RED + price-free + determinism ------------------------------

_HOSTILE_LINES = [
    {"position": 1, "part_number": "<b>PN-1</b>",
     "description": '<font color="#ff0000">RED</font> <i>unclosed <broken',
     "qty": 3, "unit": "<u>EA", "line_note": "<onDraw name='x'"},
]
_HOSTILE_RFQ = {
    "rfq_number": "RFQ-2026.001.0009",
    "job_no": "2026.001",
    "job_name": "Sunrise <script>Solar",
    "scope_text": "Line one <b>bold?</b>\n- bullet <i>broken",
    "due_date": "2026-08-14",
}


def test_render_survives_hostile_markup_escaped():
    """PROVE-THE-CONTROL-BITES (red-team #11): deliberately BROKEN reportlab
    markup in every untrusted string slot renders fine BECAUSE form_pdf's
    escaping neutralises it — strip the escaping (raw Paragraph(text)) and the
    paraparser raises on the malformed tags, failing this test."""
    pdf = rfq_generate.render_rfq_pdf(
        _HOSTILE_RFQ, _HOSTILE_LINES, VENDOR_1, PURCHASER,
        rfq_date=date(2026, 7, 19), due_date=date(2026, 8, 14),
    )
    assert pdf.startswith(b"%PDF") and len(pdf) > 500


def test_render_is_byte_deterministic():
    """invariant=1 contract: identical inputs → identical bytes (§47 idempotent
    version-on-conflict crash-retry filing depends on it)."""
    kwargs: dict[str, Any] = dict(rfq_date=date(2026, 7, 19), due_date=date(2026, 8, 14))
    a = rfq_generate.render_rfq_pdf(_HOSTILE_RFQ, _HOSTILE_LINES, VENDOR_1, PURCHASER, **kwargs)
    b = rfq_generate.render_rfq_pdf(_HOSTILE_RFQ, _HOSTILE_LINES, VENDOR_1, PURCHASER, **kwargs)
    assert a == b


def test_renderer_source_is_price_free():
    """NO money columns anywhere (the R2 contract): the renderer's source never
    touches a cents field, the money formatter, or a price/cost column — a money
    column can only be added by editing this pinned surface."""
    src = inspect.getsource(rfq_generate)
    assert "cents" not in src.lower()
    assert "_money" not in src
    assert "format_total" not in src
    for banned in ("Per Unit Cost", "Subtotal Amounts", "Price per Watt", "TOTAL"):
        assert banned not in src


def test_rfq_filename_and_title_are_vendor_scoped():
    from po_materials import rfq_naming

    assert rfq_naming.rfq_pdf_filename("RFQ-1", "Platt Electric Supply").endswith(
        "_RFQ_RFQ-1.pdf"
    )
    assert rfq_naming.rfq_pdf_filename("RFQ-1", None) == "RFQ RFQ-1.pdf"
    assert "Platt" in rfq_naming.rfq_pdf_title("RFQ-1", "Platt Electric Supply")
