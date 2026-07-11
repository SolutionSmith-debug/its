"""Seed ITS_Subcontractors from the extracted subcontractor roster (SC S2).

One source — `subcontracts/data/subcontractor_roster.json` (the `subcontractors`
array: name, trades, state, notes), the 24 firms canonicalized from the
05_Subcontracts corpus subcontract preambles (ADR-0003). The roster is READ from
disk, never hardcoded here, so an operator edit to the roster never requires a code
change to reseed. Contact / email / phone / COI / license / MSA are intentionally
left BLANK (filled later per the parked-COI decision); the seed only carries the
name, trades, state, and notes the corpus preambles established.

This is the subcontractor analog of `seed_its_vendors.py`, with one deliberate
difference: rows are written through the §51 up-sync writer
`subcontracts.subcontractors.upsert_subcontractor` (a column-scoped find-or-create
on the immutable Sub Key) rather than a raw `smartsheet_client.add_rows`. Because
every NEW firm is minted a fresh Sub Key that cannot already be on the sheet, each
upsert resolves to an ADD; the upsert path is used purely so the seed obeys the same
column-scoped, Archived-non-clobber contract the portal up-sync obeys — it can never
clobber a system/operator column, and an operator-edited row is never overwritten
(a firm already present by name is deduped out before any upsert is attempted).

Sub Keys (SUB-######, decision D4) are allocated here sequentially past the highest
key already on the sheet — immutable once written; the D1 cache and sc_send join on
them.

Idempotency: firms match by NORMALIZED Subcontractor Name (lowercase, alphanumeric
collapse); near-duplicates where one normalized name contains the other (≥4 chars)
are SKIPPED LOUDLY for operator review rather than double-seeded. Existing sheet rows
are never modified. Re-running never duplicates: a firm seeded on a prior run matches
by name on the next run and is skipped, so it is never re-keyed and never re-upserted.

FLIP precedes SEED: refuses to run while SHEET_ITS_SUBCONTRACTORS is 0 (run
build_its_subcontractors_sheet.py first and flip the printed id).

Writes go through `upsert_subcontractor` → `smartsheet_client.add_rows/update_rows`,
which the picklist REGISTRY gates on State / Trades / Default Terms Profile / Active.
The roster's state and trade strings are set-subsets of the sheet's picklist options
(build_its_subcontractors_sheet.py STATE_OPTIONS / TRADE_OPTIONS); a blank state
(one unresolved corpus firm) is dropped by the upsert rather than written as '' — the
picklist analogue of a None cell.

    python3 scripts/migrations/seed_its_subcontractors.py --dry-run
    python3 scripts/migrations/seed_its_subcontractors.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared import sheet_ids, smartsheet_client  # noqa: E402
from subcontracts import subcontractors  # noqa: E402

SUB_KEY_RE = re.compile(r"^SUB-(\d{6})$")

ROSTER_PATH = REPO_ROOT / "subcontracts" / "data" / "subcontractor_roster.json"


def load_roster() -> list[dict[str, Any]]:
    """Read the seed roster's `subcontractors` array from the JSON file (never hardcoded)."""
    data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    roster = data.get("subcontractors")
    if not isinstance(roster, list):
        raise ValueError(f"{ROSTER_PATH}: missing or malformed 'subcontractors' array.")
    return roster


def _norm(name: str) -> str:
    """Normalize a subcontractor name for duplicate matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def is_duplicate_name(a: str, b: str) -> bool:
    """Whether two subcontractor names refer to the same firm.

    Exact normalized match, or containment either way when the shorter normalized
    form is ≥4 chars (catches suffix/label variants without letting 2–3-char
    fragments false-positive)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 4 and shorter in longer


def next_key_start(existing_keys: list[str]) -> int:
    """The next SUB- serial: max of well-formed existing keys + 1 (malformed ignored)."""
    highest = 0
    for key in existing_keys:
        m = SUB_KEY_RE.match((key or "").strip())
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def format_key(serial: int) -> str:
    return f"SUB-{serial:06d}"


def build_seed_payloads(
    existing_names: list[str],
    existing_keys: list[str],
    roster: list[dict[str, Any]],
    today: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compose the `upsert_subcontractor` payloads to seed + loud skip notes.

    Dedupes against the existing sheet AND within the batch via is_duplicate_name;
    allocates Sub Keys sequentially past the sheet's highest existing key. Each payload
    is the Worker `subcontractors/pending` shape `upsert_subcontractor` consumes (the
    roster→upsert adapter): name→sub_name, minted sub_key, trades passed through as the
    MULTI_PICKLIST list, state→state, notes→notes with provenance, active=1;
    contact / address / COI / license / MSA omitted (→ left blank by the upsert)."""
    skips: list[str] = []
    to_add: list[dict[str, Any]] = []
    seen_names: list[str] = list(existing_names)
    serial = next_key_start(existing_keys)

    for firm in roster:
        name = str(firm.get("name") or "").strip()
        if not name:
            skips.append("[skip] roster: blank subcontractor name — not seeded.")
            continue
        dup = next((s for s in seen_names if is_duplicate_name(name, s)), None)
        if dup is not None:
            skips.append(f"[skip] roster: {name!r} matches existing/seeded {dup!r} — review manually.")
            continue

        raw_trades = firm.get("trades")
        trades = (
            [str(t).strip() for t in raw_trades if str(t).strip()]
            if isinstance(raw_trades, (list, tuple)) else []
        )
        roster_notes = str(firm.get("notes") or "").strip()
        notes = (roster_notes + " " if roster_notes else "") + (
            f"Seeded from subcontractor roster on {today}; contact/COI/license pending."
        )
        payload: dict[str, Any] = {
            "sub_key": format_key(serial),
            "sub_name": name,
            "state": str(firm.get("state") or "").strip(),
            "trades": trades,
            "notes": notes,
            "active": 1,
        }
        serial += 1
        seen_names.append(name)
        to_add.append(payload)

    return to_add, skips


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed ITS_Subcontractors from the subcontractor roster (SC S2)."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not sheet_ids.SHEET_ITS_SUBCONTRACTORS:
        print("[error] SHEET_ITS_SUBCONTRACTORS is still 0 in shared/sheet_ids.py (FLIP precedes SEED).\n"
              "        Run build_its_subcontractors_sheet.py first and flip the printed id.",
              file=sys.stderr)
        return 2

    today = datetime.now(UTC).date().isoformat()
    roster = load_roster()
    print(f"[info] Target: ITS_Subcontractors ({sheet_ids.SHEET_ITS_SUBCONTRACTORS})")
    print(f"[info] Roster source: {ROSTER_PATH} ({len(roster)} firms)")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    existing = smartsheet_client.get_rows(sheet_ids.SHEET_ITS_SUBCONTRACTORS)
    existing_names = [str(r.get(subcontractors.COL_SUB_NAME) or "") for r in existing]
    existing_keys = [str(r.get(subcontractors.COL_SUB_KEY) or "") for r in existing]
    print(f"[info] Existing ITS_Subcontractors rows: {len(existing)}\n")

    to_add, skips = build_seed_payloads(existing_names, existing_keys, roster, today)
    for line in skips:
        print(line)
    for payload in to_add:
        trades = ",".join(payload.get("trades") or []) or "-"
        print(f"[plan] {payload['sub_key']}  {payload['sub_name']!r}"
              f"  state={payload.get('state') or '-'}  trades={trades}")

    if not to_add:
        print("\n[ok] Nothing to seed — all candidates already present.")
        return 0
    if args.dry_run:
        print(f"\n[dry-run] Would upsert {len(to_add)} rows.")
        return 0

    row_ids = [subcontractors.upsert_subcontractor(payload) for payload in to_add]
    print(f"\n[ok] upserted {len(row_ids)} subcontractor rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
