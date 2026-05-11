"""Box SDK wrapper.

Uses Box JWT auth (server-to-server) — credentials come from Keychain as a JSON blob.

Awaiting Box credentials (Phase 1 open question). Stubbed import-safe.
"""
from __future__ import annotations

from typing import Any

_client: Any | None = None


def get_client():
    global _client
    if _client is None:
        # TODO: uncomment once Box JWT config exists in Keychain.
        # import json
        # from boxsdk import JWTAuth, Client
        # config_json = keychain.get_secret("ITS_BOX_JWT_CONFIG")
        # auth = JWTAuth.from_settings_dictionary(json.loads(config_json))
        # _client = Client(auth)
        raise NotImplementedError(
            "Box client not yet wired. "
            "Add ITS_BOX_JWT_CONFIG to Keychain (JSON blob from Box developer console), "
            "then enable in shared/box_client.py."
        )
    return _client


def upload_file(folder_id: str, file_path: str, name: str):
    """Upload a file to a Box folder."""
    raise NotImplementedError


def canonical_job_path(customer: str, job_number: str, job_name: str, year: int) -> str:
    """Return the canonical Box folder path for a given job.

    Path pattern (per Safety Reports Mission v3 open question — confirm with owner):
        /Customer/Job Number — Job Name/YYYY/

    TODO: confirm exact path pattern and update this function. All workstreams should call this
    helper rather than constructing paths inline.
    """
    return f"/{customer}/{job_number} — {job_name}/{year}/"
