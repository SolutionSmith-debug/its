"""Live-API integration test for the Phase-5 Box primitives (Op Stds §30).

Default `pytest -q` SKIPS this file (pyproject addopts `-m 'not integration'`).
Run with `pytest -m integration`. Requires the Box OAuth credentials in Keychain
(ITS_BOX_CLIENT_ID / _SECRET / _REFRESH_TOKEN). NOT executed in CI.

Exercises the new Box WRITE paths: get_or_create_folder (find-or-create, with an
ITS-prefixed name per the operator naming rule) + upload_bytes (in-memory upload).
Cleans up via the boxsdk client directly (the public surface has no delete).
"""
from __future__ import annotations

import pytest

from shared import box_client

pytestmark = pytest.mark.integration

# A unique, ITS-prefixed folder under the Box user root for the round-trip.
TEST_FOLDER = "ITS _int_box_primitive_sandbox"


@pytest.fixture
def _client():
    try:
        return box_client.get_client()
    except box_client.BoxError as e:
        pytest.skip(f"Box credentials unavailable: {e!r}")


def test_get_or_create_folder_and_upload_bytes_round_trip(_client):
    # Create (or adopt) the ITS-prefixed test folder directly under root ("0").
    folder_id = box_client.get_or_create_folder("0", TEST_FOLDER)
    file_id = None
    try:
        assert folder_id and folder_id != "0"

        # Idempotent: a second call returns the same folder (no duplicate).
        assert box_client.get_or_create_folder("0", TEST_FOLDER) == folder_id

        # upload_bytes lands a real file with the given name.
        meta = box_client.upload_bytes(
            folder_id, "2026-06-05-jha.pdf", b"%PDF-1.4 integration test\n"
        )
        file_id = meta["id"]
        assert meta["name"] == "2026-06-05-jha.pdf"
        assert int(meta["size"]) > 0
    finally:
        if file_id is not None:
            _client.file(file_id).delete()
        _client.folder(folder_id).delete(recursive=True)
