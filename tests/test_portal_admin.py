"""Unit tests for safety_reports/portal_admin.py (operator user-provisioning CLI).

All HTTP (portal_client.admin_request), getpass, and creds are mocked — no network,
no real secrets. The Worker-side flow is the deploy-gated
tests/test_portal_admin_integration.py.
"""
from __future__ import annotations

import pytest

from safety_reports import portal_admin


def _admin(mocker, status, data=None):
    return mocker.patch.object(
        portal_admin.portal_client, "admin_request", return_value=(status, data or {})
    )


def _passwords(mocker, *values):
    mocker.patch.object(portal_admin.getpass, "getpass", side_effect=list(values))


# ---- add-user ------------------------------------------------------------


def test_add_user_created(mocker, capsys):
    _passwords(mocker, "pw123456", "pw123456")
    req = _admin(mocker, 201, {"username": "smith.seth"})
    portal_admin.cmd_add_user("https://w", "tok", "smith.seth")
    out = capsys.readouterr().out
    assert "created" in out and "smith.seth" in out
    assert req.call_args.args[2] == "POST"
    assert req.call_args.args[3] == "/api/internal/admin/users"
    assert req.call_args.kwargs["json_body"] == {"username": "smith.seth", "password": "pw123456"}


def test_add_user_conflict_exits_1(mocker):
    _passwords(mocker, "pw123456", "pw123456")
    _admin(mocker, 409, {"error": "exists"})
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_add_user("https://w", "tok", "smith.seth")
    assert e.value.code == 1


def test_add_user_invalid_exits_1(mocker):
    _passwords(mocker, "pw123456", "pw123456")
    _admin(mocker, 400, {"error": "invalid_username"})
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_add_user("https://w", "tok", "BadName")
    assert e.value.code == 1


# ---- password prompt -----------------------------------------------------


def test_prompt_password_mismatch_exits(mocker):
    _passwords(mocker, "a1234567", "b1234567")
    with pytest.raises(SystemExit):
        portal_admin._prompt_new_password()


def test_prompt_password_too_short_exits(mocker):
    _passwords(mocker, "short", "short")
    with pytest.raises(SystemExit):
        portal_admin._prompt_new_password()


def test_prompt_password_ok_returns_value(mocker):
    _passwords(mocker, "longenough", "longenough")
    assert portal_admin._prompt_new_password() == "longenough"


# ---- reset / disable / enable / list -------------------------------------


def test_reset_not_found_exits_1(mocker):
    _passwords(mocker, "pw123456", "pw123456")
    _admin(mocker, 404)
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_reset_password("https://w", "tok", "no.body")
    assert e.value.code == 1


def test_disable_user_ok_hits_disable_endpoint(mocker, capsys):
    req = _admin(mocker, 200, {"ok": True})
    portal_admin.cmd_set_disabled("https://w", "tok", "smith.seth", disable=True)
    assert "disabled" in capsys.readouterr().out
    assert req.call_args.args[3] == "/api/internal/admin/users/disable"


def test_enable_user_ok_hits_enable_endpoint(mocker, capsys):
    req = _admin(mocker, 200, {"ok": True})
    portal_admin.cmd_set_disabled("https://w", "tok", "smith.seth", disable=False)
    assert "enabled" in capsys.readouterr().out
    assert req.call_args.args[3] == "/api/internal/admin/users/enable"


def test_set_disabled_not_found_exits_1(mocker):
    _admin(mocker, 404)
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_set_disabled("https://w", "tok", "no.body", disable=True)
    assert e.value.code == 1


def test_list_users_renders_flags(mocker, capsys):
    _admin(mocker, 200, {"users": [
        {"username": "smith.seth", "disabled": 0},
        {"username": "doe.jane", "disabled": 1},
    ]})
    portal_admin.cmd_list_users("https://w", "tok")
    out = capsys.readouterr().out
    assert "smith.seth" in out and "active" in out
    assert "doe.jane" in out and "DISABLED" in out


def test_list_users_empty(mocker, capsys):
    _admin(mocker, 200, {"users": []})
    portal_admin.cmd_list_users("https://w", "tok")
    assert "(no users)" in capsys.readouterr().out


# ---- main() routing ------------------------------------------------------


def test_main_routes_add_user(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    add = mocker.patch.object(portal_admin, "cmd_add_user")
    portal_admin.main(["add-user", "smith.seth"])
    add.assert_called_once_with("https://w", "tok", "smith.seth")


def test_main_routes_disable_user(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    dis = mocker.patch.object(portal_admin, "cmd_set_disabled")
    portal_admin.main(["disable-user", "smith.seth"])
    dis.assert_called_once_with("https://w", "tok", "smith.seth", disable=True)


def test_main_requires_subcommand(mocker):
    with pytest.raises(SystemExit):
        portal_admin.main([])
