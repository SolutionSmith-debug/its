"""Live-API integration test for shared/trusted_contacts.py.

Per Op Stds v11 §30 (SDK-vs-Live discipline): writes to a typed-column
sheet (PICKLIST status, TEXT_NUMBER scope JSON-lists, primary key on
Email) need at least one live round-trip to catch the same class of
body-shape drift that bit PRs #47/#48/#49.

This test:
  1. Adds a temporary ITS_Trusted_Contacts row for a sandbox sender.
  2. Invalidates the in-process cache.
  3. Calls `check_scope` with the sandbox sender + workstream.
  4. Asserts the verdict is `allowed`.
  5. Cleans up the row in `finally` so no orphan state.

Skipped automatically when:
  - ITS_SMARTSHEET_TOKEN unavailable.
  - SHEET_TRUSTED_CONTACTS is the placeholder 0 (sheet not yet built).

Run with: pytest -m integration tests/test_trusted_contacts_integration.py
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared import keychain, sheet_ids, smartsheet_client, trusted_contacts

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


@pytest.fixture(scope="module")
def _sheet_built() -> int:
    sid = sheet_ids.SHEET_TRUSTED_CONTACTS
    if not sid:
        pytest.skip(
            "SHEET_TRUSTED_CONTACTS=0 placeholder; run "
            "scripts/migrations/build_its_trusted_contacts_sheet.py first."
        )
    return sid


def _sandbox_email() -> str:
    """Per-run unique email so concurrent runs don't collide on the primary."""
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    return f"int-test-{suffix}@evergreenmirror.com"


def test_check_scope_allows_active_sandbox_contact(_token_available, _sheet_built):
    """Live round-trip: write row → cache invalidate → check_scope → assert + cleanup."""
    sheet_id = _sheet_built
    email = _sandbox_email()
    today = datetime.now(UTC).date().isoformat()

    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [{
            "Email": email,
            "Display Name": "Integration Test",
            "Role": "Operator",
            "Project Scope": '["*"]',
            "Workstream Scope": '["safety_reports"]',
            "Status": "ACTIVE",
            "Added By": "seths@evergreenmirror.com",
            "Added Date": today,
            "Last Verified": today,
            "Notes": "added by tests/test_trusted_contacts_integration.py",
        }],
    )
    try:
        trusted_contacts.invalidate_cache()
        verdict = trusted_contacts.check_scope(
            email, workstream="safety_reports", project="bradley_1",
        )
        assert verdict.allowed is True, (
            f"expected ALLOWED for active sandbox contact, got {verdict!r}"
        )
        assert verdict.contact is not None
        assert verdict.contact.email == email
        assert verdict.contact.status is trusted_contacts.ContactStatus.ACTIVE
    finally:
        smartsheet_client.delete_rows(sheet_id, [row_id])
        trusted_contacts.invalidate_cache()
