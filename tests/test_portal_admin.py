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
    # role defaults to 'submitter' and is always sent in the body.
    assert req.call_args.kwargs["json_body"] == {
        "username": "smith.seth", "password": "pw123456", "role": "submitter",
    }


def test_add_user_admin_role(mocker, capsys):
    _passwords(mocker, "pw123456", "pw123456")
    req = _admin(mocker, 201, {"username": "stephens.jacob", "role": "admin"})
    portal_admin.cmd_add_user("https://w", "tok", "stephens.jacob", role="admin")
    out = capsys.readouterr().out
    assert "created" in out and "role=admin" in out
    assert req.call_args.kwargs["json_body"] == {
        "username": "stephens.jacob", "password": "pw123456", "role": "admin",
    }


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


# ---- set-role ------------------------------------------------------------


def test_set_role_ok_hits_role_endpoint(mocker, capsys):
    req = _admin(mocker, 200, {"ok": True, "role": "admin"})
    portal_admin.cmd_set_role("https://w", "tok", "stephens.jacob", "admin")
    out = capsys.readouterr().out
    assert "role=admin" in out and "stephens.jacob" in out
    assert req.call_args.args[3] == "/api/internal/admin/users/role"
    assert req.call_args.kwargs["json_body"] == {"username": "stephens.jacob", "role": "admin"}


def test_set_role_not_found_exits_1(mocker):
    _admin(mocker, 404)
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_set_role("https://w", "tok", "no.body", "admin")
    assert e.value.code == 1


def test_list_users_renders_flags(mocker, capsys):
    _admin(mocker, 200, {"users": [
        {"username": "smith.seth", "role": "submitter", "disabled": 0},
        {"username": "stephens.jacob", "role": "admin", "disabled": 0},
        {"username": "doe.jane", "disabled": 1},
    ]})
    portal_admin.cmd_list_users("https://w", "tok")
    out = capsys.readouterr().out
    assert "smith.seth" in out and "active" in out
    assert "stephens.jacob" in out and "admin" in out
    # a row missing 'role' falls back to submitter (no crash)
    assert "doe.jane" in out and "DISABLED" in out and "submitter" in out


def test_list_users_empty(mocker, capsys):
    _admin(mocker, 200, {"users": []})
    portal_admin.cmd_list_users("https://w", "tok")
    assert "(no users)" in capsys.readouterr().out


# ---- purge-job -----------------------------------------------------------


def test_purge_job_ok_reports_counts(mocker, capsys):
    req = _admin(mocker, 200, {
        "ok": True, "found": True, "job_id": "JOB-000015",
        "job_deleted": 1, "submissions": 2, "pdfChunks": 2, "pdfRequests": 1,
    })
    portal_admin.cmd_purge_job("https://w", "tok", "JOB-000015")
    out = capsys.readouterr().out
    assert "purged JOB-000015" in out and "submissions=2" in out and "pdf_chunks=2" in out
    assert req.call_args.args[2] == "POST"
    assert req.call_args.args[3] == "/api/internal/admin/purge-job"
    assert req.call_args.kwargs["json_body"] == {"job_id": "JOB-000015"}


def test_purge_job_unknown_reports_nothing(mocker, capsys):
    _admin(mocker, 200, {"ok": True, "found": False, "job_deleted": 0})
    portal_admin.cmd_purge_job("https://w", "tok", "NOPE")
    assert "nothing purged" in capsys.readouterr().out


def test_purge_job_failure_exits_1(mocker):
    _admin(mocker, 500, {"error": "boom"})
    with pytest.raises(SystemExit) as e:
        portal_admin.cmd_purge_job("https://w", "tok", "JOB-000015")
    assert e.value.code == 1


# ---- main() routing ------------------------------------------------------


def test_main_routes_add_user(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    add = mocker.patch.object(portal_admin, "cmd_add_user")
    portal_admin.main(["add-user", "smith.seth"])
    add.assert_called_once_with("https://w", "tok", "smith.seth", "submitter")


def test_main_routes_add_user_with_role(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    add = mocker.patch.object(portal_admin, "cmd_add_user")
    portal_admin.main(["add-user", "stephens.jacob", "--role", "admin"])
    add.assert_called_once_with("https://w", "tok", "stephens.jacob", "admin")


def test_main_routes_set_role(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    sr = mocker.patch.object(portal_admin, "cmd_set_role")
    portal_admin.main(["set-role", "stephens.jacob", "admin"])
    sr.assert_called_once_with("https://w", "tok", "stephens.jacob", "admin")


def test_main_set_role_rejects_bad_role(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    with pytest.raises(SystemExit):  # argparse choices=() rejects before dispatch
        portal_admin.main(["set-role", "stephens.jacob", "superadmin"])


def test_main_routes_disable_user(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    dis = mocker.patch.object(portal_admin, "cmd_set_disabled")
    portal_admin.main(["disable-user", "smith.seth"])
    dis.assert_called_once_with("https://w", "tok", "smith.seth", disable=True)


def test_main_routes_purge_job(mocker):
    mocker.patch.object(portal_admin, "_resolve_creds", return_value=("https://w", "tok"))
    pj = mocker.patch.object(portal_admin, "cmd_purge_job")
    portal_admin.main(["purge-job", "JOB-000015"])
    pj.assert_called_once_with("https://w", "tok", "JOB-000015")


def test_main_requires_subcommand(mocker):
    with pytest.raises(SystemExit):
        portal_admin.main([])
