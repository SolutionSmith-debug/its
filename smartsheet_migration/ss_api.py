"""ITS Smartsheet REST API helper.

Reads SMARTSHEET_TOKEN from env. Provides thin wrappers over the endpoints we
need for the migration:

  - get_sheet(sheet_id) -> full sheet object (including columns with IDs)
  - get_columns(sheet_id) -> [(col_id, title, type), ...]
  - rename_folder(folder_id, new_name)
  - update_sheet_settings(sheet_id, **settings) -- e.g. ganttEnabled, dependenciesEnabled
  - add_rows(sheet_id, rows) -> response (rows = list of {cells: [{columnId, value}, ...]})
  - api(method, path, **kwargs) -- escape hatch

Run as a script with sub-commands:
  python3 ss_api.py whoami
  python3 ss_api.py columns <sheet_id>
  python3 ss_api.py rename-folder <folder_id> "<new name>"
  python3 ss_api.py get-sheet <sheet_id> [--rows N]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = "https://api.smartsheet.com/2.0"


def _token() -> str:
    tok = os.environ.get("SMARTSHEET_TOKEN")
    if not tok:
        raise RuntimeError("SMARTSHEET_TOKEN env var not set")
    return tok


def api(method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
    url = BASE + path
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query)
    data = None
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} on {method} {path}: {err_body}") from e


def get_sheet(sheet_id: int | str, include: str | None = None) -> dict:
    q = {"include": include} if include else None
    return api("GET", f"/sheets/{sheet_id}", query=q)


def get_columns(sheet_id: int | str) -> list[tuple[int, str, str]]:
    sheet = get_sheet(sheet_id)
    return [(c["id"], c["title"], c["type"]) for c in sheet["columns"]]


def rename_folder(folder_id: int | str, new_name: str) -> dict:
    return api("PUT", f"/folders/{folder_id}", body={"name": new_name})


def update_sheet_settings(sheet_id: int | str, **settings) -> dict:
    return api("PUT", f"/sheets/{sheet_id}", body=settings)


def add_rows(sheet_id: int | str, rows: list[dict]) -> dict:
    return api("POST", f"/sheets/{sheet_id}/rows", body=rows)


def whoami() -> dict:
    return api("GET", "/users/me")


# ---- CLI ----
def _cli() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "whoami":
        u = whoami()
        print(f"{u.get('email')} ({u.get('firstName')} {u.get('lastName')})  admin={u.get('admin')}")
        return 0
    if cmd == "columns":
        sid = sys.argv[2]
        cols = get_columns(sid)
        print(f"{'COLUMN ID':<22}{'TITLE':<40}TYPE")
        print("-" * 80)
        for cid, title, ctype in cols:
            print(f"{cid:<22}{title:<40}{ctype}")
        return 0
    if cmd == "rename-folder":
        fid, name = sys.argv[2], sys.argv[3]
        r = rename_folder(fid, name)
        print(json.dumps(r, indent=2))
        return 0
    if cmd == "get-sheet":
        sid = sys.argv[2]
        sheet = get_sheet(sid)
        n_rows = len(sheet.get("rows", []))
        n_cols = len(sheet.get("columns", []))
        print(f"Sheet: {sheet.get('name')}")
        print(f"  id: {sheet.get('id')}")
        print(f"  permalink: {sheet.get('permalink')}")
        print(f"  rows: {n_rows}, cols: {n_cols}")
        print(f"  ganttEnabled: {sheet.get('ganttEnabled')}  dependenciesEnabled: {sheet.get('dependenciesEnabled')}")
        if "--rows" in sys.argv:
            i = sys.argv.index("--rows")
            n = int(sys.argv[i+1])
            for r in sheet.get("rows", [])[:n]:
                cells = {c.get("columnId"): c.get("value") for c in r.get("cells", [])}
                print(f"    row {r.get('rowNumber')}: {cells}")
        return 0
    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
