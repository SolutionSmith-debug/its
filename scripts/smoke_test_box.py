#!/usr/bin/env python3
"""Smoke test for ITS Box OAuth integration.

OPERATIONAL — makes REAL Box API calls under the authenticated user's
account. Sandbox-only.

Re-run after:
  - Token rotation manually triggered
  - OAuth app config changes in Box (scopes, redirect URI, secret rotation)
  - Refresh token replaced via scripts/setup_box_oauth.py
  - Any non-trivial change to shared/box_client.py

USAGE
    python scripts/smoke_test_box.py                 # read-only smoke
    python scripts/smoke_test_box.py --write-test    # adds write/read/delete loop

The default (read-only) smoke checks:
  1. Keychain credentials are readable.
  2. get_client() + /users/me succeeds — prints authenticated user.
  3. list_folder("0") succeeds — prints first few item names.

With --write-test, additionally:
  4. Read ITS_Config row `system.box_smoke_folder_id` for the write target.
  5. Upload a tiny synthetic file to that folder.
  6. Read it back via download_file; verify byte-for-byte.
  7. Delete the synthetic file.
  Pattern matches scripts/smoke_test_review_queue.py "leaves no droppings"
  discipline — cleanup runs even on failure (try/finally).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from shared import box_client, keychain
from shared.box_client import (
    KC_CLIENT_ID,
    KC_CLIENT_SECRET,
    KC_REFRESH_TOKEN,
)


def _check_keychain_seeded() -> None:
    """Fail fast with a remediation message if any Keychain entry is missing."""
    missing: list[str] = []
    for service in (KC_CLIENT_ID, KC_CLIENT_SECRET, KC_REFRESH_TOKEN):
        try:
            keychain.get_secret(service)
        except keychain.KeychainError:
            missing.append(service)
    if missing:
        print(f"      ERROR: missing Keychain entries: {', '.join(missing)}")
        if KC_REFRESH_TOKEN in missing:
            print(
                "      Run scripts/setup_box_oauth.py to write "
                "ITS_BOX_REFRESH_TOKEN (it must NOT be seeded manually)."
            )
        else:
            print(
                "      Seed with: security add-generic-password -a \"$USER\" "
                "-s <NAME> -w \"<value>\" -U"
            )
        sys.exit(1)


def _smoke_read_only() -> None:
    print("\n[2/3] get_client() + /users/me ...")
    client = box_client.get_client()
    me = client.user().get()
    print(f"      Authenticated as: {me.name} <{me.login}>")

    print("\n[3/3] list_folder('0') (root) ...")
    items = box_client.list_folder("0", limit=10)
    if items:
        print(f"      OK: {len(items)} item(s); first names:")
        for it in items[:5]:
            print(f"         {it['type']:6}  {it['name']}")
    else:
        print("      OK: 0 items (empty root)")


def _smoke_write_test() -> None:
    """Upload + download + delete; ITS_Config picks the target folder."""
    from shared import smartsheet_client

    print("\n[4/7] Reading system.box_smoke_folder_id from ITS_Config ...")
    folder_id = smartsheet_client.get_setting(
        "system.box_smoke_folder_id", workstream="global",
    )
    if not folder_id:
        print(
            "      ERROR: ITS_Config row system.box_smoke_folder_id is empty.\n"
            "      Seed it with a Box folder ID before running --write-test."
        )
        sys.exit(1)
    print(f"      OK: target folder ID = {folder_id}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    smoke_name = f"its-smoke-test-{stamp}.txt"
    smoke_body = (
        f"ITS Box smoke test\n"
        f"Written at {datetime.now(UTC).isoformat()}\n"
        f"Safe to delete.\n"
    ).encode()

    file_id: str | None = None
    with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as tmp:
        tmp.write(smoke_body)
        tmp_path = Path(tmp.name)

    try:
        print(f"\n[5/7] Upload {smoke_name} to folder {folder_id} ...")
        uploaded = box_client.upload_file(folder_id, str(tmp_path), name=smoke_name)
        file_id = uploaded["id"]
        print(f"      OK: file_id={file_id}")

        print("\n[6/7] Download + byte-for-byte compare ...")
        round_trip = box_client.download_file(file_id)
        if round_trip != smoke_body:
            print(
                f"      ERROR: round-trip mismatch\n"
                f"      wrote {len(smoke_body)} bytes, read {len(round_trip)} bytes"
            )
            sys.exit(1)
        print("      OK: bytes match")
    finally:
        tmp_path.unlink(missing_ok=True)
        if file_id is not None:
            print(f"\n[7/7] Deleting smoke file {file_id} ...")
            client = box_client.get_client()
            client.file(file_id).delete()
            print("      OK: no droppings")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-test",
        action="store_true",
        help=(
            "Run the upload/download/delete loop too. Requires "
            "ITS_Config row system.box_smoke_folder_id."
        ),
    )
    args = parser.parse_args()

    print("ITS Box smoke test")
    print("=" * 60)

    print("\n[1/3] Checking Keychain credentials ...")
    _check_keychain_seeded()
    print("      OK: all three Box Keychain entries present")

    _smoke_read_only()
    if args.write_test:
        _smoke_write_test()

    print("\n" + "=" * 60)
    print("All checks passed. box_client.py is wired.")


if __name__ == "__main__":
    main()
