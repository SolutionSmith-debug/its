"""One-shot migration: seed ITS_Trusted_Contacts from the legacy
`safety_reports.intake.allowed_senders` ITS_Config JSON list.

Cutover companion to the Phase 1.4 ITS_Trusted_Contacts cluster:
  1. `build_its_trusted_contacts_sheet.py` (one-time, builds the sheet).
  2. THIS script (one-time per cutover, populates from legacy allowlist).
  3. Operator manually deletes the ITS_Config row via Smartsheet UI after
     parity verification.

For each email in the legacy allowlist, this script creates one
ITS_Trusted_Contacts row with these defaults (operator edits after):

  Email            <email, case-normalized>
  Display Name     derived from local-part (`<local>@<domain>` →
                   `<local>` title-cased, `.`/`_` → space)
  Role             "Other"
  Project Scope    ["*"]  (wildcard preserves legacy allowlist semantics)
  Workstream Scope ["safety_reports"]
  Status           "ACTIVE"
  Added By         operator email from ITS_Config `system.operator_email`
                   if present, else "seths@solutionsmith.org"
  Added Date       today (ISO)
  Last Verified    today (ISO)
  Notes            "Migrated from ITS_Config
                   safety_reports.intake.allowed_senders on YYYY-MM-DD"

Idempotency: rows are matched by Email (case-insensitive); existing rows
are skipped, not overwritten.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/seed_its_trusted_contacts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

LEGACY_SETTING = "safety_reports.intake.allowed_senders"
LEGACY_WORKSTREAM = "safety_reports"
OPERATOR_EMAIL_SETTING = "system.operator_email"
DEFAULT_OPERATOR_EMAIL = "seths@solutionsmith.org"


def _read_legacy_allowlist() -> list[str]:
    """Read + parse the legacy JSON list from ITS_Config."""
    try:
        raw = smartsheet_client.get_setting(
            LEGACY_SETTING, workstream=LEGACY_WORKSTREAM,
        )
    except smartsheet_client.SmartsheetNotFoundError:
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(s) for s in parsed if isinstance(s, str)]


def _read_operator_email() -> str:
    """Resolve the operator email; fall back to a hardcoded default."""
    try:
        raw = smartsheet_client.get_setting(
            OPERATOR_EMAIL_SETTING, workstream="global",
        )
    except smartsheet_client.SmartsheetNotFoundError:
        return DEFAULT_OPERATOR_EMAIL
    return raw if isinstance(raw, str) and raw else DEFAULT_OPERATOR_EMAIL


def _derive_display_name(email: str) -> str:
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").title()


def _existing_emails(sheet_id: int) -> set[str]:
    rows = smartsheet_client.get_rows(sheet_id)
    out: set[str] = set()
    for r in rows:
        e = r.get("Email")
        if isinstance(e, str):
            out.add(e.strip().lower())
    return out


def seed_trusted_contacts(*, dry_run: bool) -> tuple[int, int, int]:
    """Seed all legacy allowlist entries into ITS_Trusted_Contacts.

    Returns (added, skipped, total). On dry-run, no writes happen but the
    summary still reflects what WOULD have been added vs skipped.
    """
    sheet_id = sheet_ids.SHEET_TRUSTED_CONTACTS
    if not sheet_id:
        raise RuntimeError(
            "SHEET_TRUSTED_CONTACTS=0 placeholder. Run "
            "scripts/migrations/build_its_trusted_contacts_sheet.py and "
            "update shared/sheet_ids.py before seeding."
        )

    legacy = _read_legacy_allowlist()
    print(f"[info] Legacy allowlist entries: {len(legacy)}")
    if not legacy:
        print("[info] Nothing to seed.")
        return 0, 0, 0

    existing = _existing_emails(sheet_id)
    operator_email = _read_operator_email()
    today = datetime.now(UTC).date().isoformat()

    rows_to_add: list[dict] = []
    skipped_count = 0
    for raw_email in legacy:
        # Skip domain-pattern entries (`@evergreenmirror.com`) — the sheet
        # is exact-match-per-email, not pattern. Domain-wildcard senders
        # need to be enumerated manually by the operator post-seed; flag
        # them in the output so they're easy to spot.
        if raw_email.startswith("@"):
            print(
                f"[skip] domain-pattern entry {raw_email!r} — sheet schema is "
                f"per-email; operator must enumerate concrete addresses."
            )
            skipped_count += 1
            continue

        email = raw_email.strip().lower()
        if not email:
            skipped_count += 1
            continue
        if email in existing:
            print(f"[skip] already present: {email}")
            skipped_count += 1
            continue

        rows_to_add.append({
            "Email": email,
            "Display Name": _derive_display_name(email),
            "Role": "Other",
            "Project Scope": '["*"]',
            "Workstream Scope": '["safety_reports"]',
            "Status": "ACTIVE",
            "Added By": operator_email,
            "Added Date": today,
            "Last Verified": today,
            "Notes": (
                f"Migrated from ITS_Config {LEGACY_SETTING} on {today}"
            ),
        })

    if not rows_to_add:
        print("[info] No new rows to add.")
        return 0, skipped_count, len(legacy)

    if dry_run:
        print(f"[dry-run] Would add {len(rows_to_add)} rows:")
        for r in rows_to_add:
            print(f"  + {r['Email']}  (Display Name={r['Display Name']!r})")
        return len(rows_to_add), skipped_count, len(legacy)

    new_row_ids = smartsheet_client.add_rows(sheet_id, rows_to_add)
    for r, rid in zip(rows_to_add, new_row_ids, strict=True):
        print(f"[ok] added row id={rid}: {r['Email']}")
    return len(rows_to_add), skipped_count, len(legacy)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed ITS_Trusted_Contacts from legacy allowed_senders.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be added without writing to Smartsheet.",
    )
    args = parser.parse_args()

    print(
        f"[info] Source: ITS_Config {LEGACY_SETTING!r} "
        f"(workstream={LEGACY_WORKSTREAM!r})"
    )
    print(f"[info] Target sheet id = {sheet_ids.SHEET_TRUSTED_CONTACTS}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    added, skipped, total = seed_trusted_contacts(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  Legacy entries scanned: {total}")
    print(f"  Rows {'planned' if args.dry_run else 'added'}: {added}")
    print(f"  Skipped (already-present + domain patterns + empty): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
