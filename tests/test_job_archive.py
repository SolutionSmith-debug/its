"""Track 6 — `field_ops/job_archive.py`.

The behaviours under test are the ones that make a PARTIAL archive safe rather than a wedge:
per-container fencing, move-BEFORE-rename ordering, the ADMIN pre-flight refusing loudly, and the
resume probe keying off the recorded folder id instead of a re-creatable name.
"""
from __future__ import annotations

from typing import Any

import pytest

from field_ops import job_archive
from shared import sheet_ids, smartsheet_client


@pytest.fixture
def _seams(mocker):
    """Patch every external edge the module touches. Nothing here reaches a live API."""
    return {
        "find_ws": mocker.patch.object(
            smartsheet_client, "find_folder_by_name_in_workspace", return_value=None
        ),
        "find_folder": mocker.patch.object(
            smartsheet_client, "find_folder_by_name_in_folder", return_value=None
        ),
        "create_ws": mocker.patch.object(
            smartsheet_client, "create_folder_in_workspace", return_value=9000
        ),
        "move": mocker.patch.object(smartsheet_client, "move_folder_to_folder", return_value=None),
        "rename": mocker.patch.object(smartsheet_client, "rename_folder", return_value=None),
        "name": mocker.patch.object(smartsheet_client, "get_folder_name", return_value="Coker"),
        "access": mocker.patch.object(
            smartsheet_client, "get_workspace_access_level", return_value="ADMIN"
        ),
        "log": mocker.patch.object(job_archive.error_log, "log"),
    }


def _job(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "JOB-000017",
        "project_name": "Coker",
        "archive_folder_key": "Coker",
        "archive_direction": "archive",
        "archive_state": "requested",
        "archive_attempts": 0,
    }
    base.update(over)
    return base


# ---- the slot table ------------------------------------------------------


def test_slots_cover_six_containers_four_smartsheet_two_box():
    # SIX, not eleven: the Box safety root is SHARED by PO/RFQ/subcontracts, so its per-job folder
    # carries them along. A future reader "fixing" this by adding four Box slots would double-move.
    assert len(job_archive.SLOTS) == 6
    assert sum(1 for s in job_archive.SLOTS if s.system == "smartsheet") == 4
    assert sum(1 for s in job_archive.SLOTS if s.system == "box") == 2


def test_every_smartsheet_slot_names_exactly_one_source_parent():
    # Safety/Progress sit directly under a WORKSPACE; PO/Subcontracts under a Jobs FOLDER.
    # A slot with neither (or both) would resolve against the wrong tree.
    for slot in job_archive.SLOTS:
        if slot.system != "smartsheet":
            continue
        assert (slot.workspace is None) != (slot.parent_folder is None), slot.key


def test_slot_keys_are_unique():
    # The keys index the D1 container report; a duplicate would make one container's outcome
    # silently overwrite another's in the operator's view.
    keys = [s.key for s in job_archive.SLOTS]
    assert len(keys) == len(set(keys))


# ---- the ADMIN pre-flight ------------------------------------------------


def test_preflight_passes_when_the_identity_is_admin_everywhere(_seams):
    assert job_archive.verify_archive_capability() is True
    # All five workspaces probed: Archive + the four sources.
    assert _seams["access"].call_count == 5


def test_preflight_refuses_loudly_on_insufficient_access(_seams):
    # Without this, the shortfall surfaces as a 403 only AFTER the operator pressed Archive, and
    # only for whichever containers got that far — a half-archived job caused purely by sharing.
    _seams["access"].return_value = "EDITOR"

    assert job_archive.verify_archive_capability() is False

    warn = [c for c in _seams["log"].call_args_list
            if c.kwargs.get("error_code") == "archive_preflight_not_admin"]
    assert warn, "an insufficient access level must never be a silent skip"


def test_preflight_refuses_on_a_probe_error_rather_than_assuming_ok(_seams):
    _seams["access"].side_effect = smartsheet_client.SmartsheetError("boom")

    assert job_archive.verify_archive_capability() is False

    assert any(c.kwargs.get("error_code") == "archive_preflight_unreadable"
               for c in _seams["log"].call_args_list)


# ---- the archive destination --------------------------------------------


def test_ensure_archive_job_folder_reuses_an_existing_one(_seams):
    _seams["find_ws"].return_value = 4242
    assert job_archive.ensure_archive_job_folder("Coker") == 4242
    _seams["create_ws"].assert_not_called()


def test_ensure_archive_job_folder_creates_on_miss(_seams):
    _seams["find_ws"].side_effect = [None, 9000]  # pre-find miss, post-find confirms our create
    assert job_archive.ensure_archive_job_folder("Coker") == 9000
    _seams["create_ws"].assert_called_once_with(sheet_ids.WORKSPACE_ARCHIVE, "Coker")


def test_ensure_archive_job_folder_adopts_the_race_winner_and_warns(_seams):
    # Smartsheet does not enforce folder-name uniqueness, so two creators can both pass the find.
    _seams["find_ws"].side_effect = [None, 7777]  # someone else's folder won
    _seams["create_ws"].return_value = 9000

    assert job_archive.ensure_archive_job_folder("Coker") == 7777  # first match adopted

    assert any(c.kwargs.get("error_code") == "archive_job_folder_duplicate"
               for c in _seams["log"].call_args_list)


# ---- moving one container ------------------------------------------------


def test_container_move_happens_before_the_rename(_seams):
    """Ordering is load-bearing, not cosmetic.

    Renaming first would hide the folder from week_sheet / hours_log / job_sheet, which all
    find-or-CREATE by job name — so the next filing would grow a fresh empty folder beside it and
    the archive would go on to move the wrong tree.
    """
    calls: list[str] = []
    _seams["find_ws"].return_value = 555
    _seams["move"].side_effect = lambda *a, **k: calls.append("move")
    _seams["rename"].side_effect = lambda *a, **k: calls.append("rename")

    slot = next(s for s in job_archive.SLOTS if s.key == "smartsheet:safety")
    res = job_archive.archive_smartsheet_container(slot, "Coker", 9000)

    assert calls == ["move", "rename"]
    assert res.moved is True


def test_container_rename_is_skipped_when_the_label_is_already_right(_seams):
    # The resume path: a crash between move and rename leaves a moved-but-unrenamed folder, so the
    # probe reads the RECORDED id's current name rather than searching for a name in the source
    # (which the live creators re-grow the moment anything is filed).
    _seams["find_ws"].return_value = 555
    _seams["name"].return_value = "Safety"  # a prior cycle already renamed it

    slot = next(s for s in job_archive.SLOTS if s.key == "smartsheet:safety")
    job_archive.archive_smartsheet_container(slot, "Coker", 9000)

    _seams["rename"].assert_not_called()


def test_absent_container_counts_as_moved_with_a_note(_seams):
    # "Nothing to move" is success, not failure: a job that never produced anything in a workstream
    # must not hold its archive at 'partial' forever.
    _seams["find_ws"].return_value = None

    slot = next(s for s in job_archive.SLOTS if s.key == "smartsheet:safety")
    res = job_archive.archive_smartsheet_container(slot, "Coker", 9000)

    assert res.moved is True and res.note == "nothing to move"
    _seams["move"].assert_not_called()


# ---- archiving a whole job ----------------------------------------------


def test_one_container_failure_never_blocks_the_others(_seams):
    """The defining property of a resumable partial.

    The old path moved four sheets in a loop with a single outer fence; a failure part-way left no
    record of what HAD moved. Here each container is fenced and reported independently.
    """
    _seams["find_ws"].return_value = 555
    _seams["find_folder"].return_value = 556
    # Fail only the second smartsheet container.
    _seams["move"].side_effect = [None, smartsheet_client.SmartsheetError("boom"), None, None]

    results = job_archive.archive_job(_job())

    smartsheet = [r for r in results if r.key.startswith("smartsheet:")]
    assert len(smartsheet) == 4
    assert sum(1 for r in smartsheet if r.moved) == 3  # the other three still ran
    assert any(c.kwargs.get("error_code") == "archive_container_failed"
               for c in _seams["log"].call_args_list)


def test_a_container_failure_never_raises(_seams):
    _seams["find_ws"].return_value = 555
    _seams["move"].side_effect = RuntimeError("unexpected")
    job_archive.archive_job(_job())  # must not raise — the caller reports, it does not crash


def test_the_failure_warn_names_the_job_system_and_container(_seams):
    # The old WARN named only a sheet. With six containers across two systems an operator must be
    # able to tell WHICH folder in WHICH system is stuck straight from ITS_Errors.
    _seams["find_ws"].return_value = 555
    _seams["move"].side_effect = smartsheet_client.SmartsheetError("boom")

    job_archive.archive_job(_job(job_id="JOB-000042"))

    msg = next(c.args[2] for c in _seams["log"].call_args_list
               if c.kwargs.get("error_code") == "archive_container_failed")
    assert "JOB-000042" in msg
    assert "smartsheet" in msg
    assert "Safety" in msg


def test_an_empty_folder_key_refuses_loudly_instead_of_looking_clean(_seams):
    """The subtlest failure this module can have.

    An empty key makes every find-by-name match nothing, so a naive implementation would report
    six 'nothing to move' successes and mark the archive COMPLETE without touching a thing.
    """
    results = job_archive.archive_job(_job(archive_folder_key=""))

    assert all(r.moved is False for r in results)
    assert any(c.kwargs.get("error_code") == "archive_folder_key_missing"
               for c in _seams["log"].call_args_list)
    _seams["move"].assert_not_called()


def test_the_archive_destination_folder_is_resolved_once_per_job(_seams):
    # Four smartsheet containers, one destination — resolving per container would be four extra
    # round trips and four chances to lose the create race.
    _seams["find_ws"].side_effect = [None, 9000] + [555] * 8
    job_archive.archive_job(_job())
    assert _seams["create_ws"].call_count == 1


# ---- collapsing results to the operator-visible state --------------------


@pytest.mark.parametrize(
    "moved_flags, expected",
    [
        ([True] * 6, "complete"),
        ([True, True, False, True, True, True], "partial"),
        ([False] * 6, "failed"),
    ],
)
def test_state_from_results(moved_flags, expected):
    # 'partial' is deliberately distinct from 'failed': an operator seeing "4 of 6 moved" needs to
    # know something DID move, because the repair differs from "nothing happened".
    results = [
        job_archive.ContainerResult(f"k{i}", f"L{i}", moved=flag)
        for i, flag in enumerate(moved_flags)
    ]
    assert job_archive.state_from_results(results) == expected


def test_folder_key_delegates_to_the_one_naming_rule():
    # Not a second copy: the Worker mirrors the SAME rule in TS, with parity asserted in
    # tests/test_job_archive_guard.py.
    from safety_reports import safety_naming

    for raw in ("Coker", "  Bradley 1  ", "A/B", "Bradley Solar"):
        assert job_archive.folder_key_for(raw) == safety_naming.job_folder_name(raw)
