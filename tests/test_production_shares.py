"""Tests for the CL-11 approver-share mechanization (manifest + seeder + VC-10).

Prove-the-control-bites discipline: every guard is exercised with a synthetic
violation asserting the REFUSAL — the ``renwables`` typo manifest, an ambiguous
workspace name, a non-OWNER token, a declined y/N gate, an invalid-user 404, a
GROUP share posing as F22 authority — never just the green path.

NO live calls: requests / keychain / input are all mocked (the suite-wide
``_forbid_external_network`` conftest fixture enforces it).

Identity discipline: this file composes production approver emails from the
MANIFEST data at runtime — no ``@evergreenrenewables.com`` email literal may
appear in a ``.py`` file (the CI ``secrets`` job's production-identity guard).
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests  # type: ignore[import-untyped]

from shared import sheet_ids, smartsheet_client
from shared.smartsheet_client import SmartsheetPermissionError

REPO = Path(__file__).resolve().parents[1]

# sys.path-driven imports (scripts/ and scripts/migrations/ are not packages) —
# the same idiom as tests/test_verify_cutover.py and tests/test_standup_tools.py.
_SCRIPTS_DIR = REPO / "scripts"
_MIGRATIONS_DIR = REPO / "scripts" / "migrations"
for _p in (_SCRIPTS_DIR, _MIGRATIONS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import seed_production_shares as sps  # noqa: E402
import sheet_ids_regen as regen  # noqa: E402
import verify_cutover as vc  # noqa: E402

# The CL-11 roster, held as LOCAL PARTS only (identity discipline above).
CL11_LOCALPARTS = {"jacobs", "ezraj", "jechiahs", "benf", "tiffanym", "tealap", "samr"}

CONSTANT_TO_REGEN_NAME = {
    "WORKSPACE_SAFETY_PORTAL": regen.WS_SAFETY_PORTAL,
    "WORKSPACE_PROGRESS_REPORTING": regen.WS_PROGRESS,
    "WORKSPACE_PURCHASE_ORDERS": regen.WS_PURCHASE_ORDERS,
    "WORKSPACE_SUBCONTRACTS": regen.WS_SUBCONTRACTS,
}


def _manifest() -> dict[str, Any]:
    """The REAL checked-in manifest, freshly validated (deep-copied per call)."""
    return copy.deepcopy(sps.load_manifest())


def _write_manifest(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


# ---- manifest data (this IS the typo guard for the checked-in file) -------


def test_manifest_loads_and_carries_the_full_roster_on_all_four_workspaces():
    manifest = _manifest()
    assert manifest["production_domain"] == sps.EXPECTED_PRODUCTION_DOMAIN
    assert manifest["mirror_domain"] == "evergreenmirror.com"
    constants = [ws["constant"] for ws in manifest["workspaces"]]
    assert constants == list(CONSTANT_TO_REGEN_NAME)
    for ws in manifest["workspaces"]:
        assert len(ws["approvers"]) == 7, ws["constant"]
        localparts = {a["email"].split("@")[0] for a in ws["approvers"]}
        assert localparts == CL11_LOCALPARTS, ws["constant"]
        for approver in ws["approvers"]:
            assert approver["email"].endswith("@" + manifest["production_domain"])
            assert approver["person"].strip()
            assert approver["role"].strip()
            assert approver["access_level"] in sps.KNOWN_ACCESS_LEVELS


def test_manifest_workspace_names_are_byte_exact_builder_canon():
    """The Safety Portal name uses TWO EN DASHes (U+2013 U+2013), the others one
    EM dash — byte-exact against sheet_ids_regen's live-verified canon."""
    for ws in _manifest()["workspaces"]:
        assert ws["name"] == CONSTANT_TO_REGEN_NAME[ws["constant"]], ws["constant"]
    safety = _manifest()["workspaces"][0]["name"]
    assert "––" in safety and "—" not in safety


def test_manifest_constants_resolve_to_nonzero_ids_in_sheet_ids():
    for ws in _manifest()["workspaces"]:
        value = getattr(sheet_ids, ws["constant"])
        assert isinstance(value, int) and value > 0, ws["constant"]


def test_validator_rejects_the_renwables_typo(tmp_path):
    """The documented Ezra-typo caution, mechanized: a ``renwables`` domain in
    any approver email must be REFUSED by the schema validator."""
    manifest = _manifest()
    approver = manifest["workspaces"][0]["approvers"][1]  # ezraj@
    approver["email"] = approver["email"].replace("renewables", "renwables")
    with pytest.raises(sps.ManifestError, match="typo"):
        sps.load_manifest(_write_manifest(tmp_path, manifest))


def _mutate_wrong_domain(m: dict[str, Any]) -> None:
    m["production_domain"] = "example.com"


def _mutate_empty_approvers(m: dict[str, Any]) -> None:
    m["workspaces"][0]["approvers"] = []


def _mutate_unknown_access_level(m: dict[str, Any]) -> None:
    m["workspaces"][0]["approvers"][0]["access_level"] = "OWNER"


def _mutate_unknown_constant(m: dict[str, Any]) -> None:
    m["workspaces"][0]["constant"] = "WORKSPACE_DOES_NOT_EXIST"


def _mutate_uppercase_email(m: dict[str, Any]) -> None:
    a = m["workspaces"][0]["approvers"][0]
    a["email"] = a["email"].upper()


def _mutate_duplicate_email(m: dict[str, Any]) -> None:
    ws = m["workspaces"][0]
    ws["approvers"][1]["email"] = ws["approvers"][0]["email"]


def _mutate_missing_person(m: dict[str, Any]) -> None:
    m["workspaces"][0]["approvers"][0]["person"] = "  "


@pytest.mark.parametrize(
    "mutate",
    [
        _mutate_wrong_domain,
        _mutate_empty_approvers,
        _mutate_unknown_access_level,
        _mutate_unknown_constant,
        _mutate_uppercase_email,
        _mutate_duplicate_email,
        _mutate_missing_person,
    ],
)
def test_validator_refuses_schema_violations(tmp_path, mutate):
    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(sps.ManifestError):
        sps.load_manifest(_write_manifest(tmp_path, manifest))


# ---- seeder plumbing ------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _install_fake_get(
    monkeypatch,
    listing: list[dict],
    shares_by_id: dict[int, list[dict]],
    share_calls: list[int] | None = None,
):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/workspaces?includeAll=true"):
            return _Resp(body={"data": listing})
        match = re.search(r"/workspaces/(\d+)/shares", url)
        if match:
            workspace_id = int(match.group(1))
            if share_calls is not None:
                share_calls.append(workspace_id)
            return _Resp(body={"data": shares_by_id.get(workspace_id, [])})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(sps.requests, "get", fake_get)


def _boom_post(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise AssertionError(f"requests.post reached: {url}")

    monkeypatch.setattr(sps.requests, "post", fake_post)


def _owner_listing(manifest: dict[str, Any], access_level: str = "OWNER") -> list[dict]:
    return [
        {"name": ws["name"], "id": 111 * (i + 1), "accessLevel": access_level}
        for i, ws in enumerate(manifest["workspaces"])
    ]


def test_plan_mode_never_posts_and_exits_zero(monkeypatch, capsys):
    manifest = _manifest()
    _install_fake_get(monkeypatch, _owner_listing(manifest), {})
    _boom_post(monkeypatch)
    assert sps.main([]) == 0
    out = capsys.readouterr().out
    assert "PLAN ONLY — no writes performed" in out
    assert "to add" in out


def test_ambiguous_workspace_name_is_refused(monkeypatch, capsys):
    manifest = _manifest()
    listing = _owner_listing(manifest)
    listing.append(dict(listing[0], id=999))  # duplicate name, different id
    share_calls: list[int] = []
    _install_fake_get(monkeypatch, listing, {}, share_calls)
    _boom_post(monkeypatch)
    assert sps.main([]) == 1
    out = capsys.readouterr().out
    assert "[REFUSED] 2 live workspaces named" in out
    # The ambiguous workspace's share list was never fetched.
    assert listing[0]["id"] not in share_calls and 999 not in share_calls


def test_commit_declined_gate_makes_no_writes(monkeypatch, capsys):
    manifest = _manifest()
    _install_fake_get(monkeypatch, _owner_listing(manifest), {})
    _boom_post(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert sps.main(["--commit"]) == 1
    assert "[abort] declined — nothing written." in capsys.readouterr().out


def test_commit_eof_counts_as_decline(monkeypatch, capsys):
    manifest = _manifest()
    _install_fake_get(monkeypatch, _owner_listing(manifest), {})
    _boom_post(monkeypatch)

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert sps.main(["--commit"]) == 1
    assert "[abort] declined" in capsys.readouterr().out


def test_commit_refuses_non_owner_workspace(monkeypatch, capsys):
    manifest = _manifest()
    _install_fake_get(monkeypatch, _owner_listing(manifest, access_level="ADMIN"), {})
    _boom_post(monkeypatch)  # non-OWNER must never reach a POST
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert sps.main(["--commit"]) == 1
    out = capsys.readouterr().out
    assert "!= OWNER — refusing to write shares" in out


def test_commit_invalid_user_404_warns_named_and_continues(monkeypatch, capsys, tmp_path):
    manifest = _manifest()
    manifest["workspaces"] = [manifest["workspaces"][0]]
    ws = manifest["workspaces"][0]
    ws["approvers"] = ws["approvers"][:2]
    first_email = ws["approvers"][0]["email"]
    monkeypatch.setattr(sps, "MANIFEST_PATH", _write_manifest(tmp_path, manifest))

    _install_fake_get(
        monkeypatch, [{"name": ws["name"], "id": 42, "accessLevel": "OWNER"}], {}
    )
    posts: list[tuple[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append((url, json))
        if len(posts) == 1:
            return _Resp(404, text="Not Found: no such user")
        return _Resp(200)

    monkeypatch.setattr(sps.requests, "post", fake_post)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert sps.main(["--commit"]) == 1  # one add failed → nonzero, but BOTH attempted
    out = capsys.readouterr().out
    assert f"share add FAILED for {first_email}" in out
    assert "must exist as a real Smartsheet USER first" in out
    assert len(posts) == 2
    assert all("sendEmail=false" in url for url, _ in posts)


def test_commit_all_adds_succeed_with_expected_payload(monkeypatch, capsys, tmp_path):
    manifest = _manifest()
    manifest["workspaces"] = [manifest["workspaces"][0]]
    ws = manifest["workspaces"][0]
    ws["approvers"] = ws["approvers"][:2]
    monkeypatch.setattr(sps, "MANIFEST_PATH", _write_manifest(tmp_path, manifest))
    _install_fake_get(
        monkeypatch, [{"name": ws["name"], "id": 42, "accessLevel": "OWNER"}], {}
    )
    posts: list[tuple[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append((url, json))
        return _Resp(200)

    monkeypatch.setattr(sps.requests, "post", fake_post)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert sps.main(["--commit"]) == 0
    assert [body for _, body in posts] == [
        [{"email": a["email"], "accessLevel": a["access_level"]}] for a in ws["approvers"]
    ]


def test_mirror_residue_reported_with_manual_instruction_and_never_deleted(
    monkeypatch, capsys
):
    manifest = _manifest()
    listing = _owner_listing(manifest)
    residue_email = "seths@" + manifest["mirror_domain"]
    shares_by_id = {
        int(entry["id"]): [
            {"type": "USER", "email": residue_email, "accessLevel": "ADMIN"}
        ]
        for entry in listing
    }
    _install_fake_get(monkeypatch, listing, shares_by_id)
    _boom_post(monkeypatch)
    assert sps.main([]) == 0
    out = capsys.readouterr().out
    assert f"[RESIDUE] mirror-account USER share {residue_email}" in out
    assert "remove BY HAND" in out


def test_seeder_source_contains_no_delete_call():
    """ADD-only is structural: no DELETE verb exists anywhere in the module."""
    source = Path(sps.__file__).read_text(encoding="utf-8")
    assert "requests.delete" not in source
    assert ".delete(" not in source
    assert '"DELETE"' not in source and "'DELETE'" not in source


def test_group_share_flagged_non_counting_in_plan(monkeypatch, capsys):
    manifest = _manifest()
    listing = _owner_listing(manifest)
    shares_by_id = {
        int(entry["id"]): [
            {"type": "GROUP", "name": "Evergreen Approvers", "accessLevel": "EDITOR",
             "groupId": 9}
        ]
        for entry in listing
    }
    _install_fake_get(monkeypatch, listing, shares_by_id)
    _boom_post(monkeypatch)
    assert sps.main([]) == 0
    out = capsys.readouterr().out
    assert "[GROUP]  group share 'Evergreen Approvers' does NOT count toward F22" in out


# ---- shared.smartsheet_client.list_workspace_shares -----------------------


def _rest_response(data: list[dict] | None, status: int = 200) -> MagicMock:
    """Mock requests.Response for GET /workspaces/{id}/shares — the
    `_rest_shares_response` idiom from tests/test_smartsheet_client.py
    (`_translate_smartsheet_error` drives off raise_for_status)."""
    response = MagicMock()
    response.status_code = status
    response.json.return_value = {} if data is None else {"data": data}
    if status >= 400:
        error = requests.HTTPError(f"HTTP {status}")
        error.response = response
        response.raise_for_status.side_effect = error
        response.text = "error"
    else:
        response.raise_for_status.return_value = None
        response.text = ""
    return response


def test_list_workspace_shares_returns_raw_records_including_groups(mocker):
    mocker.patch(
        "shared.smartsheet_client.requests.get",
        return_value=_rest_response([
            {"type": "USER", "email": "alice@x.com", "accessLevel": "EDITOR"},
            {"type": "GROUP", "name": "Approvers", "accessLevel": "EDITOR", "groupId": 9},
            "not-a-dict",
        ]),
    )
    out = smartsheet_client.list_workspace_shares(42)
    assert out == (
        {"type": "USER", "email": "alice@x.com", "accessLevel": "EDITOR"},
        {"type": "GROUP", "name": "Approvers", "accessLevel": "EDITOR", "groupId": 9},
    )


def test_list_workspace_shares_translates_permission_error(mocker):
    mocker.patch(
        "shared.smartsheet_client.requests.get",
        return_value=_rest_response(None, status=403),
    )
    with pytest.raises(SmartsheetPermissionError):
        smartsheet_client.list_workspace_shares(42)


# ---- VC-10 approver-shares ------------------------------------------------


def _vc10_shares_by_workspace(manifest: dict[str, Any]) -> dict[int, tuple[dict, ...]]:
    return {
        getattr(sheet_ids, ws["constant"]): tuple(
            {"type": "USER", "email": a["email"], "accessLevel": a["access_level"]}
            for a in ws["approvers"]
        )
        for ws in manifest["workspaces"]
    }


def _install_vc10(monkeypatch, by_id: dict[int, tuple[dict, ...]]):
    monkeypatch.setattr(
        vc.smartsheet_client, "list_workspace_shares", lambda wid: by_id[wid]
    )


def test_vc10_registered_as_the_tenth_check():
    spec = vc.CHECKS[-1]
    assert (spec.check_id, spec.slug) == ("VC-10", "approver-shares")
    assert spec.fn is vc._check_approver_shares


def test_vc10_full_manifest_set_passes(monkeypatch):
    manifest = _manifest()
    _install_vc10(monkeypatch, _vc10_shares_by_workspace(manifest))
    outcome = vc._check_approver_shares(vc.Options())
    assert outcome.passed
    assert "4 manifest workspaces" in outcome.summary


def test_vc10_subset_semantics_extra_live_shares_still_pass(monkeypatch):
    """SUBSET not equality: the operator's own production account holding a
    share beyond the manifest must NOT fail the gate."""
    manifest = _manifest()
    by_id = _vc10_shares_by_workspace(manifest)
    extra = "operator@" + manifest["production_domain"]
    by_id = {
        wid: shares + ({"type": "USER", "email": extra, "accessLevel": "ADMIN"},)
        for wid, shares in by_id.items()
    }
    _install_vc10(monkeypatch, by_id)
    assert vc._check_approver_shares(vc.Options()).passed


def test_vc10_missing_approver_fails_naming_them(monkeypatch):
    manifest = _manifest()
    by_id = _vc10_shares_by_workspace(manifest)
    safety = manifest["workspaces"][0]
    safety_id = getattr(sheet_ids, safety["constant"])
    dropped = safety["approvers"][0]["email"]
    by_id[safety_id] = tuple(s for s in by_id[safety_id] if s["email"] != dropped)
    _install_vc10(monkeypatch, by_id)
    outcome = vc._check_approver_shares(vc.Options())
    assert not outcome.passed
    assert f"missing approver USER share {dropped}" in outcome.details
    assert safety["constant"] in outcome.details


def test_vc10_mirror_residue_fails_naming_it(monkeypatch):
    manifest = _manifest()
    by_id = _vc10_shares_by_workspace(manifest)
    residue = "daniels@" + manifest["mirror_domain"]
    first = next(iter(by_id))
    by_id[first] = by_id[first] + (
        {"type": "USER", "email": residue, "accessLevel": "EDITOR"},
    )
    _install_vc10(monkeypatch, by_id)
    outcome = vc._check_approver_shares(vc.Options())
    assert not outcome.passed
    assert f"mirror-account share residue {residue}" in outcome.details
    assert "UNSHARE by hand" in outcome.details


def test_vc10_group_share_flagged_as_non_counting(monkeypatch):
    manifest = _manifest()
    by_id = _vc10_shares_by_workspace(manifest)
    first = next(iter(by_id))
    by_id[first] = by_id[first] + (
        {"type": "GROUP", "name": "Evergreen Approvers", "accessLevel": "EDITOR",
         "groupId": 9},
    )
    _install_vc10(monkeypatch, by_id)
    outcome = vc._check_approver_shares(vc.Options())
    assert not outcome.passed
    assert "GROUP share 'Evergreen Approvers' does NOT count toward F22" in outcome.details


def test_vc10_allow_sandbox_waives_without_any_api_call(monkeypatch):
    def boom(wid):
        raise AssertionError("VC-10 must not hit the API under --allow-sandbox")

    monkeypatch.setattr(vc.smartsheet_client, "list_workspace_shares", boom)
    outcome = vc._check_approver_shares(vc.Options(allow_sandbox=True))
    assert outcome.passed
    assert "(sandbox mode — manifest diff waived)" in outcome.summary


def test_vc10_profile_does_not_exempt(monkeypatch):
    """A --profile run exempts only its named VC-03 rows — VC-10 still bites."""
    manifest = _manifest()
    by_id = _vc10_shares_by_workspace(manifest)
    safety = manifest["workspaces"][0]
    safety_id = getattr(sheet_ids, safety["constant"])
    dropped = safety["approvers"][0]["email"]
    by_id[safety_id] = tuple(s for s in by_id[safety_id] if s["email"] != dropped)
    _install_vc10(monkeypatch, by_id)
    opts = vc.Options(
        profile="phase1-hybrid", sandbox_exempt=vc.PROFILES["phase1-hybrid"]
    )
    outcome = vc._check_approver_shares(opts)
    assert not outcome.passed
    assert dropped in outcome.details


def test_vc10_unresolvable_constant_is_a_named_problem(monkeypatch, tmp_path):
    manifest = _manifest()
    manifest["workspaces"][0]["constant"] = "WORKSPACE_DEMO_MISSING"
    path = _write_manifest(tmp_path, manifest)
    monkeypatch.setattr(vc, "APPROVER_SHARES_MANIFEST", path)
    _install_vc10(monkeypatch, _vc10_shares_by_workspace({
        "workspaces": manifest["workspaces"][1:]
    }))
    outcome = vc._check_approver_shares(vc.Options())
    assert not outcome.passed
    assert "sheet_ids.WORKSPACE_DEMO_MISSING missing or 0" in outcome.details


def test_vc10_missing_manifest_file_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(vc, "APPROVER_SHARES_MANIFEST", tmp_path / "absent.json")
    outcome = vc._check_approver_shares(vc.Options())
    assert not outcome.passed
    assert "manifest unreadable" in outcome.summary
