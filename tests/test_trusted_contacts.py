"""Tests for shared/trusted_contacts.py — lookup + check_scope + cache.

All Smartsheet calls are mocked at the boundary; no live sheet hits.
Run with: pytest -q tests/test_trusted_contacts.py
"""
from __future__ import annotations

import time

import pytest

from shared import trusted_contacts
from shared.trusted_contacts import (
    CACHE_TTL_SECONDS,
    ContactStatus,
    TrustedContact,
    check_scope,
    invalidate_cache,
    lookup,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Each test starts with an empty cache."""
    invalidate_cache()
    yield
    invalidate_cache()


def _row(
    *,
    email: str,
    display_name: str = "Test User",
    role: str = "Operator",
    project_scope: str = '["*"]',
    workstream_scope: str = '["safety_reports"]',
    status: str = "ACTIVE",
    row_id: int = 1000,
) -> dict:
    return {
        "_row_id": row_id,
        "Email": email,
        "Display Name": display_name,
        "Role": role,
        "Project Scope": project_scope,
        "Workstream Scope": workstream_scope,
        "Status": status,
    }


def _patch_get_rows(mocker, rows: list[dict]):
    return mocker.patch(
        "shared.trusted_contacts.smartsheet_client.get_rows",
        return_value=rows,
    )


# ---- lookup() ------------------------------------------------------------


def test_lookup_hit_with_case_mismatched_email_returns_normalized_contact(mocker):
    _patch_get_rows(mocker, [_row(email="Seths@Evergreenmirror.COM")])
    contact = lookup("SETHS@evergreenmirror.com")
    assert contact is not None
    assert contact.email == "seths@evergreenmirror.com"
    assert contact.status is ContactStatus.ACTIVE


def test_lookup_miss_returns_none(mocker):
    _patch_get_rows(mocker, [_row(email="seths@evergreenmirror.com")])
    assert lookup("nobody@example.com") is None


# ---- check_scope() happy path + wildcards --------------------------------


def test_check_scope_happy_path_proceeds(mocker):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            project_scope='["bradley_1"]',
            workstream_scope='["safety_reports"]',
        )],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com",
        workstream="safety_reports",
        project="bradley_1",
    )
    assert verdict.allowed is True
    assert verdict.reason == "allowed"


def test_check_scope_workstream_wildcard_matches_any(mocker):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            workstream_scope='["*"]',
        )],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com",
        workstream="po_materials",
    )
    assert verdict.allowed is True


def test_check_scope_project_wildcard_matches_any(mocker):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            project_scope='["*"]',
        )],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com",
        workstream="safety_reports",
        project="huntley",
    )
    assert verdict.allowed is True


# ---- check_scope() denials ----------------------------------------------


def test_check_scope_workstream_not_in_scope_denies(mocker):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            workstream_scope='["po_materials"]',
        )],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com",
        workstream="safety_reports",
    )
    assert verdict.allowed is False
    assert verdict.reason == "workstream_out_of_scope"


def test_check_scope_project_not_in_scope_when_provided_denies(mocker):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            project_scope='["bradley_1"]',
        )],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com",
        workstream="safety_reports",
        project="huntley",
    )
    assert verdict.allowed is False
    assert verdict.reason == "project_out_of_scope"


def test_check_scope_status_disabled_denies(mocker):
    _patch_get_rows(
        mocker,
        [_row(email="seths@evergreenmirror.com", status="DISABLED")],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com", workstream="safety_reports",
    )
    assert verdict.allowed is False
    assert verdict.reason == "status_disabled"


def test_check_scope_status_pending_verification_denies(mocker):
    _patch_get_rows(
        mocker,
        [_row(email="seths@evergreenmirror.com", status="PENDING_VERIFICATION")],
    )
    verdict = check_scope(
        "seths@evergreenmirror.com", workstream="safety_reports",
    )
    assert verdict.allowed is False
    assert verdict.reason == "status_pending_verification"


def test_check_scope_unknown_sender_denies(mocker):
    _patch_get_rows(mocker, [])
    verdict = check_scope(
        "unknown@nowhere.example", workstream="safety_reports",
    )
    assert verdict.allowed is False
    assert verdict.contact is None
    assert verdict.reason == "unknown_sender"


# ---- cache behavior ------------------------------------------------------


def test_cache_hit_skips_second_smartsheet_call(mocker):
    get_rows = _patch_get_rows(
        mocker, [_row(email="seths@evergreenmirror.com")],
    )
    lookup("seths@evergreenmirror.com")
    lookup("seths@evergreenmirror.com")
    assert get_rows.call_count == 1


def test_cache_expires_and_refetches(mocker):
    """Force the cache to expire by manipulating its timestamp."""
    get_rows = _patch_get_rows(
        mocker, [_row(email="seths@evergreenmirror.com")],
    )
    lookup("seths@evergreenmirror.com")
    assert get_rows.call_count == 1
    # Push the cache expiry into the past so the next read refetches.
    assert trusted_contacts._cache is not None
    contacts, _expires = trusted_contacts._cache
    trusted_contacts._cache = (contacts, time.monotonic() - 1.0)
    lookup("seths@evergreenmirror.com")
    assert get_rows.call_count == 2


# ---- scope-column parse failures ----------------------------------------


def test_malformed_scope_json_treated_as_empty_and_denies(mocker, caplog):
    _patch_get_rows(
        mocker,
        [_row(
            email="seths@evergreenmirror.com",
            project_scope="not-json",
            workstream_scope="also-not-json",
        )],
    )
    import logging
    with caplog.at_level(logging.WARNING):
        verdict = check_scope(
            "seths@evergreenmirror.com", workstream="safety_reports",
        )
    assert verdict.allowed is False
    assert verdict.reason == "workstream_out_of_scope"
    # Both scope columns logged a warning during parse.
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warns) >= 1


def test_sheet_empty_returns_unknown_sender(mocker):
    _patch_get_rows(mocker, [])
    verdict = check_scope("seths@evergreenmirror.com", workstream="safety_reports")
    assert verdict.reason == "unknown_sender"


# ---- module hygiene ------------------------------------------------------


def test_contact_status_values_match_op_stds():
    expected = {"ACTIVE", "DISABLED", "PENDING_VERIFICATION"}
    assert {s.value for s in ContactStatus} == expected


def test_cache_ttl_is_60_seconds():
    # Pinned to the documented 60s TTL — change requires updating both the
    # constant AND the module docstring.
    assert CACHE_TTL_SECONDS == 60.0


def test_dataclass_is_hashable():
    # Frozen dataclasses are hashable by default; this lets contacts go
    # into sets if a caller wants dedup. Tuple scope helps here.
    c = TrustedContact(
        email="a@b.c", display_name="x", role="r",
        project_scope=("*",), workstream_scope=("*",),
        status=ContactStatus.ACTIVE, row_id=1,
    )
    assert hash(c) == hash(c)
