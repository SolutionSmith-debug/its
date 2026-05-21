"""Smartsheet SDK wrapper for ITS.

Wraps `smartsheet-python-sdk` so callers work in column-title terms instead of
column IDs, and so SDK exceptions don't leak into business code. Mirrors the
shape of `shared.graph_client` (lazy singleton from Keychain, typed exception
hierarchy, thin operation helpers) but delegates HTTP retry / rate-limit
backoff to the SDK rather than re-implementing those with `requests`.

Token: ITS_SMARTSHEET_TOKEN in macOS Keychain.

Column-name cache:
    Title → column-ID is cached per-sheet at module level. On a title that
    isn't in the cache, we refetch the sheet's columns once before giving
    up — that recovers when a column was *added* after the cache was built.
    It does NOT recover from a *rename*: callers using the old title will
    keep raising KeyError because the refreshed map won't contain it either.
    That is deliberate — silently writing into the wrong column is far worse
    than fast-failing on a stale title. Long-lived processes that need to
    survive a rename must restart or call `invalidate_column_cache()`.

External Send Gate (Foundation Mission v6, Invariant 1):
    Smartsheet writes are not external sends. This module is freely
    importable by both generation and send scripts.

SDK 404 noise:
    The Smartsheet SDK's central request/response logger emits the full
    response body at ERROR on the `smartsheet.smartsheet` logger for every
    non-2xx response, before our `_translate` raises a typed exception. We
    suppress that emission for 404 only — see `_Suppress404JSON` at the
    bottom of this module — because 404 covers the *expected* "row not yet
    seeded" case via `get_setting`. Other status codes (401 / 403 / 429 /
    500) are NOT suppressed; an operator should see them on stderr.
"""
from __future__ import annotations

import logging
from typing import Any

import requests  # type: ignore[import-untyped]
import smartsheet  # type: ignore[import-untyped]
import smartsheet.exceptions as sdk_exc  # type: ignore[import-untyped]

from . import keychain, sheet_ids

SDK_LOGGER_NAME = "smartsheet.smartsheet"


class SmartsheetError(Exception):
    """Base exception for all Smartsheet failures."""


class SmartsheetAuthError(SmartsheetError):
    """Token rejected (HTTP 401)."""


class SmartsheetPermissionError(SmartsheetError):
    """Access denied for this sheet/resource (HTTP 403)."""


class SmartsheetNotFoundError(SmartsheetError):
    """Sheet, row, column, or config setting missing (HTTP 404)."""


class SmartsheetRateLimitError(SmartsheetError):
    """SDK retry budget exhausted (HTTP 429)."""


_client: smartsheet.Smartsheet | None = None
_column_maps: dict[int, dict[str, int]] = {}


# ---- Client + error translation -----------------------------------------


def get_client() -> smartsheet.Smartsheet:
    """Return a process-wide Smartsheet SDK client, building it on first use.

    The SDK is configured with `errors_as_exceptions=True` so non-2xx
    responses surface as `smartsheet.exceptions.ApiError`, which we translate
    in `_translate` below.
    """
    global _client
    if _client is None:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
        client = smartsheet.Smartsheet(token, user_agent="its")
        client.errors_as_exceptions(True)
        _client = client
    return _client


def _translate(exc: sdk_exc.SmartsheetException) -> SmartsheetError:
    """Map an SDK exception onto our typed hierarchy."""
    if isinstance(exc, sdk_exc.ApiError):
        result = exc.error.result
        status = result.status_code
        message = result.message or "Smartsheet API error"
        detail = f"HTTP {status} (code {result.code}): {message}"
        if status == 401:
            return SmartsheetAuthError(detail)
        if status == 403:
            return SmartsheetPermissionError(detail)
        if status == 404:
            return SmartsheetNotFoundError(detail)
        if status == 429:
            return SmartsheetRateLimitError(detail)
        return SmartsheetError(detail)
    if isinstance(exc, sdk_exc.HttpError):
        return SmartsheetError(f"HTTP {exc.status_code}: {exc.body!r}")
    return SmartsheetError(str(exc))


# ---- Column-map cache ----------------------------------------------------


def _fetch_column_map(sheet_id: int) -> dict[str, int]:
    try:
        sheet = get_client().Sheets.get_sheet(sheet_id, include="columns")
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e
    return {col.title: col.id for col in sheet.columns}


def _column_map(sheet_id: int) -> dict[str, int]:
    cached = _column_maps.get(sheet_id)
    if cached is None:
        cached = _fetch_column_map(sheet_id)
        _column_maps[sheet_id] = cached
    return cached


def invalidate_column_cache(sheet_id: int | None = None) -> None:
    """Drop cached column maps. Call after a known schema change.

    Without `sheet_id`, drops every entry.
    """
    if sheet_id is None:
        _column_maps.clear()
    else:
        _column_maps.pop(sheet_id, None)


def _resolve_cells(sheet_id: int, values: dict[str, Any]) -> list[Any]:
    """Build SDK Cell objects for a row from a {title: value} dict.

    On any title that isn't in the cached column map, refetches the map once
    before giving up — see module docstring for the rename-breaks-cache
    failure mode.
    """
    columns = _column_map(sheet_id)
    if any(title not in columns for title in values):
        invalidate_column_cache(sheet_id)
        columns = _column_map(sheet_id)

    cells = []
    for title, value in values.items():
        if title not in columns:
            raise KeyError(
                f"Column {title!r} not found in sheet {sheet_id}. "
                f"Available: {sorted(columns)}"
            )
        cells.append(
            smartsheet.models.Cell({"column_id": columns[title], "value": value})
        )
    return cells


# ---- Reads ---------------------------------------------------------------


def get_sheet(sheet_id: int):
    """Fetch the full sheet object (SDK model). Most callers want get_rows()."""
    try:
        return get_client().Sheets.get_sheet(sheet_id)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e


def get_rows(
    sheet_id: int,
    *,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return rows as `{_row_id: int, <title>: value, ...}` dicts.

    `filters` is an equality-AND match applied client-side. Use only on
    sheets small enough to fetch in one round-trip (config, time-off, etc.);
    big logs should use Reports or scoped row queries.
    """
    try:
        sheet = get_client().Sheets.get_sheet(sheet_id)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e

    title_by_id = {col.id: col.title for col in sheet.columns}
    out: list[dict[str, Any]] = []
    for row in sheet.rows:
        record: dict[str, Any] = {"_row_id": row.id}
        for cell in row.cells:
            title = title_by_id.get(cell.column_id)
            if title is not None:
                record[title] = cell.value
        if filters and not all(record.get(k) == v for k, v in filters.items()):
            continue
        out.append(record)
    return out


def get_settings_with_prefix(
    prefix: str,
    *,
    workstream: str | None = None,
) -> dict[str, str]:
    """Return all ITS_Config rows whose `Setting` starts with `prefix`.

    Mirrors `get_setting`'s row-shape assumptions but iterates instead of
    raising. Returns a `{setting_key: value_str}` dict. Rows whose `Value`
    cell is not a string are skipped (matches `get_setting`'s contract).

    `workstream` narrows results to one workstream when set; default
    returns all matching rows across workstreams. Used by
    `scripts/watchdog.py` Check F to enumerate `mail_intake.*` rows
    without knowing the workstream slugs up front.
    """
    filters: dict[str, Any] = {}
    if workstream is not None:
        filters["Workstream"] = workstream
    rows = get_rows(sheet_ids.SHEET_CONFIG, filters=filters)
    out: dict[str, str] = {}
    for row in rows:
        setting = row.get("Setting")
        value = row.get("Value")
        if (
            isinstance(setting, str)
            and setting.startswith(prefix)
            and isinstance(value, str)
        ):
            out[setting] = value
    return out


def get_setting(key: str, *, workstream: str) -> str | None:
    """Read one Setting from ITS_Config, scoped to a workstream.

    Workstream is required — `ITS_Config` rows are keyed on (Setting,
    Workstream), and silently defaulting hides config misses.

    Returns the cell value as a string, or `None` if the row exists but
    the Value cell is empty / non-string. Raises `SmartsheetNotFoundError`
    if no row matches at all — callers distinguish "row missing" from
    "row found but blank Value" by which path triggers.
    """
    rows = get_rows(
        sheet_ids.SHEET_CONFIG,
        filters={"Setting": key, "Workstream": workstream},
    )
    if not rows:
        raise SmartsheetNotFoundError(
            f"ITS_Config has no row for Setting={key!r} Workstream={workstream!r}"
        )
    value = rows[0].get("Value")
    return value if isinstance(value, str) else None


# ---- Writes --------------------------------------------------------------


def add_rows(sheet_id: int, rows: list[dict[str, Any]]) -> list[int]:
    """Append rows to a sheet. Returns the new row IDs in input order.

    Each entry in `rows` is a `{column_title: value}` dict. Rows are
    appended to the bottom — change at the call site if a different
    position is needed.
    """
    if not rows:
        return []
    sdk_rows = []
    for values in rows:
        row = smartsheet.models.Row()
        row.to_bottom = True
        row.cells = _resolve_cells(sheet_id, values)
        sdk_rows.append(row)
    try:
        result = get_client().Sheets.add_rows(sheet_id, sdk_rows)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e
    return [r.id for r in result.result]


def update_rows(sheet_id: int, updates: list[dict[str, Any]]) -> None:
    """Update existing rows. Each update is `{_row_id: int, <title>: value, ...}`.

    Cells whose column titles aren't supplied are left untouched.
    """
    if not updates:
        return
    sdk_rows = []
    for values in updates:
        row_id = values.get("_row_id")
        if row_id is None:
            raise ValueError("update_rows entry missing required '_row_id'")
        cells_payload = {k: v for k, v in values.items() if k != "_row_id"}
        row = smartsheet.models.Row()
        row.id = row_id
        row.cells = _resolve_cells(sheet_id, cells_payload)
        sdk_rows.append(row)
    try:
        get_client().Sheets.update_rows(sheet_id, sdk_rows)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e


def delete_rows(sheet_id: int, row_ids: list[int]) -> None:
    """Delete rows by ID. Smartsheet caps at 450 IDs per call."""
    if not row_ids:
        return
    try:
        get_client().Sheets.delete_rows(sheet_id, row_ids)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e


# ---- Column + sheet helpers (PICKLIST sync) -----------------------------


def list_columns_with_options(sheet_id: int) -> list[dict[str, Any]]:
    """Return one dict per column with `id`, `title`, `type`, and `options`.

    `options` is the picklist option list when the column is `PICKLIST` /
    `MULTI_PICKLIST`; an empty list otherwise. Used by
    `shared.picklist_sync` to read current downstream picklist state
    before computing a diff against the source master DB.

    Bypasses the column-title cache because picklist sync needs the
    `options` field (the cache only stores `{title: id}` for cell-write
    resolution). A direct `get_sheet` is the right shape here.

    `type` is returned as a plain string (e.g. `"PICKLIST"`), NOT as the
    SDK's `EnumeratedValue` wrapper. Callers feeding `type` back into a
    Column body for `update_column_options` need the string form — the
    SDK's deserializer can't set an EnumeratedValue field from another
    EnumeratedValue object and silently strips it, which produces a body
    without `type` and triggers errorCode 1090 on the API side.
    Surfaced live during the PR #48 re-smoke.
    """
    try:
        sheet = get_client().Sheets.get_sheet(sheet_id, include="columns")
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e
    out: list[dict[str, Any]] = []
    for col in sheet.columns:
        opts = getattr(col, "options", None) or []
        # col.type is an EnumeratedValue wrapper; .value is the ColumnType
        # enum member; .name is the picklist-friendly string ("PICKLIST",
        # "TEXT_NUMBER", etc.). Defensively fall back to str() if the
        # SDK shape ever changes.
        col_type = col.type
        type_str: str
        if hasattr(col_type, "value") and hasattr(col_type.value, "name"):
            type_str = col_type.value.name
        else:
            type_str = str(col_type)
        out.append({
            "id": col.id,
            "title": col.title,
            "type": type_str,
            "options": list(opts),
        })
    return out


def update_column_options(
    sheet_id: int, column_id: int, options: list[str], *, column_type: str
) -> None:
    """Replace a PICKLIST column's options list with `options`.

    Smartsheet's `PUT /sheets/{sheetId}/columns/{columnId}` accepts an
    `options` array; the server replaces the whole list. Pass an
    alphabetized list when stable order matters (R5 — Smartsheet does
    not guarantee API-side preservation otherwise).

    Body shape requirements discovered live (PR #47 → PR #48):
      - `id` must NOT appear in the body (errorCode 1032; the column ID
        lives in the URL path).
      - `type` IS required when changing `options` (errorCode 1090).
        Caller passes it explicitly because callers already have the
        column type from list_columns_with_options(); fetching it here
        would mean an extra round-trip per write.

    Expected `column_type` values: "PICKLIST" or "MULTI_PICKLIST". Other
    values are accepted but the API will reject any type that doesn't
    take an options array.

    Invalidates the column-title cache for the sheet because picklist
    edits don't change titles but the cache may be stale if titles were
    edited in the same UI session.
    """
    try:
        body = smartsheet.models.Column({
            "type": column_type,
            "options": list(options),
        })
        get_client().Sheets.update_column(sheet_id, column_id, body)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e
    invalidate_column_cache(sheet_id)


def _translate_smartsheet_error(response: requests.Response, *, context: str) -> None:
    """Raise a typed `SmartsheetError` for a non-2xx REST response.

    No-op on 2xx — callers continue. On 4xx/5xx, dispatch the status code
    onto the same typed-exception hierarchy used by `_translate` for SDK
    errors (401 → Auth, 403 → Permission, 404 → NotFound, 429 → RateLimit,
    everything else → base `SmartsheetError`).

    Internal helper for the REST-backed helpers below (`find_sheet_by_name_in_folder`,
    `find_folder_by_name_in_folder`, `create_folder_in_folder`,
    `create_sheet_in_folder_from_template`). Reached the §14 abstraction
    threshold at PR #54 (4 REST helpers sharing identical dispatch).

    `context` is prepended to the error message so operator-facing logs
    identify which REST operation failed without needing a stack trace —
    e.g. "creating folder in parent 12345: HTTP 500: ...".

    Internally drives off `response.raise_for_status()` rather than direct
    `response.ok` / `status_code` inspection so the existing
    `requests.HTTPError`-shaped mock fixtures in
    `tests/test_smartsheet_client.py` continue to exercise the dispatch
    without per-fixture `.ok` configuration.
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        resp = e.response if e.response is not None else response
        status = resp.status_code if resp is not None else 0
        body_text = ((resp.text or "")[:200]) if resp is not None else str(e)
        if status == 401:
            raise SmartsheetAuthError(f"{context}: HTTP 401: {body_text}") from e
        if status == 403:
            raise SmartsheetPermissionError(f"{context}: HTTP 403: {body_text}") from e
        if status == 404:
            raise SmartsheetNotFoundError(f"{context}: HTTP 404: {body_text}") from e
        if status == 429:
            raise SmartsheetRateLimitError(f"{context}: HTTP 429: {body_text}") from e
        raise SmartsheetError(f"{context}: HTTP {status}: {body_text}") from e


def find_sheet_by_name_in_folder(folder_id: int, name: str) -> int | None:
    """Return the sheet ID with title `name` inside `folder_id`, or None.

    Used by migrations + `picklist_sync` to check "does this sheet
    already exist?" before issuing a `create_sheet_in_folder` POST — the
    idempotency pattern from the PR α migration generalizes here.

    Implemented via direct REST (`GET /folders/{id}`) rather than the
    SDK's `Folders.get_folder()` for two reasons surfaced live during
    the PR #50 integration-test run on 2026-05-21:

    1. `Folders.get_folder()` is deprecated upstream (emits
       DeprecationWarning).
    2. The deprecated method returns stale folder data within a single
       SDK client session — a sheet created via the SDK's
       `create_sheet_in_folder()` does NOT appear in a subsequent
       `get_folder()` from the same client. Direct REST sees the sheet
       immediately. Confirmed live: REST returned the freshly-created
       sheet, SDK did not.

    Matches on exact title equality (Smartsheet folder listings are
    case-sensitive; titles are unique within a folder by convention but
    not enforced by the API, so a duplicate returns the first match).
    """
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    url = f"https://api.smartsheet.com/2.0/folders/{folder_id}"
    context = f"finding sheet {name!r} in folder {folder_id}"
    try:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
    except requests.RequestException as e:
        raise SmartsheetError(f"{context}: {e!r}") from e
    _translate_smartsheet_error(response, context=context)
    body = response.json()
    for sheet in body.get("sheets", []):
        if sheet.get("name") == name:
            return int(sheet["id"])
    return None


def find_folder_by_name_in_folder(parent_folder_id: int, name: str) -> int | None:
    """Return the sub-folder ID with title `name` inside `parent_folder_id`, or None.

    Sibling of `find_sheet_by_name_in_folder` for the folders[] response field.
    Used by `safety_reports.week_folder.ensure_current_week_folder` to check
    "does this week's folder already exist?" before issuing a folder-create
    POST — same find-or-create idempotency pattern.

    Implemented via direct REST (`GET /folders/{id}`) rather than the SDK's
    `Folders.get_folder()` for the same two reasons documented on
    `find_sheet_by_name_in_folder`:

    1. `Folders.get_folder()` is deprecated upstream (emits
       DeprecationWarning).
    2. The deprecated method returns stale folder data within a single
       SDK client session — a folder created via `create_folder_in_folder`
       does NOT appear in a subsequent `get_folder()` from the same
       client. Direct REST sees the folder immediately. Confirmed live
       during the PR #51 integration-test run.

    Matches on exact title equality (Smartsheet folder listings are
    case-sensitive; titles are unique within a folder by convention but
    not enforced by the API, so a duplicate returns the first match —
    callers that need duplicate-aware behavior must inspect the listing
    themselves).
    """
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    url = f"https://api.smartsheet.com/2.0/folders/{parent_folder_id}"
    context = f"finding folder {name!r} in folder {parent_folder_id}"
    try:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
    except requests.RequestException as e:
        raise SmartsheetError(f"{context}: {e!r}") from e
    _translate_smartsheet_error(response, context=context)
    body = response.json()
    for folder in body.get("folders", []):
        if folder.get("name") == name:
            return int(folder["id"])
    return None


def create_sheet_in_folder(
    folder_id: int,
    name: str,
    columns: list[dict[str, Any]],
) -> int:
    """Create a new sheet inside `folder_id` and return its sheet ID.

    `columns` is a list of `{title, type, primary?, options?, ...}` dicts
    matching the Smartsheet Column model. The first entry whose
    `primary=True` becomes the primary column (Smartsheet requires
    exactly one; TEXT_NUMBER per its constraints).

    Idempotency is the caller's job — use `find_sheet_by_name_in_folder`
    first if the create needs to be re-run-safe (PR α migration pattern).
    """
    column_models = [smartsheet.models.Column(c) for c in columns]
    sheet_model = smartsheet.models.Sheet({"name": name, "columns": column_models})
    try:
        result = get_client().Folders.create_sheet_in_folder(folder_id, sheet_model)
    except sdk_exc.SmartsheetException as e:
        raise _translate(e) from e
    return int(result.result.id)


def create_folder_in_folder(parent_folder_id: int, name: str) -> int:
    """Create a sub-folder inside `parent_folder_id` and return its folder ID.

    Implemented via direct REST (`POST /folders/{id}/folders`) for symmetry
    with `find_folder_by_name_in_folder` — both legs of the find-or-create
    idempotency pattern in `safety_reports.week_folder` share the REST
    transport so the same-session cache bug (PR #51) cannot bite a
    later refactor.

    Idempotency is the caller's job — use `find_folder_by_name_in_folder`
    first if the create needs to be re-run-safe.
    """
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    url = f"https://api.smartsheet.com/2.0/folders/{parent_folder_id}/folders"
    context = f"creating folder {name!r} in folder {parent_folder_id}"
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"name": name},
            timeout=30,
        )
    except requests.RequestException as e:
        raise SmartsheetError(f"{context}: {e!r}") from e
    _translate_smartsheet_error(response, context=context)
    body = response.json()
    return int(body["result"]["id"])


def create_sheet_in_folder_from_template(
    folder_id: int,
    name: str,
    template_sheet_id: int,
    *,
    include: list[str] | None = None,
) -> int:
    """Clone `template_sheet_id` into `folder_id` with name `name`.

    `include` controls which parts of the template are copied. Empty list
    (or None — the default) clones structure only: columns, formatting,
    column descriptions. Pass `["data"]` to also copy row contents, or
    `["data", "attachments", "discussions"]` for a fuller clone. Values
    match Smartsheet's `POST /sheets/{id}/copy?include=...` query param.

    Used by `safety_reports.week_folder.ensure_current_week_folder` to
    clone the Bradley 1 / Week of 2026-03-09 templates forward into
    each new week. Empty include is the right default — we want the
    template's schema (column titles, picklists, descriptions) but not
    the template week's residual rows.

    Implemented via direct REST (`POST /sheets/{id}/copy`) for symmetry
    with `find_folder_by_name_in_folder` and `create_folder_in_folder`
    — keeps the create-then-find loop in `ensure_current_week_folder`
    on a single transport.

    Body shape requirement discovered live during this PR's integration
    test: Copy Sheet expects `destinationType` + `destinationId` as
    flat top-level keys, NOT a nested `destination: {type, id}` object.
    The nested form returns HTTP 400 errorCode 1008 ("Unknown attribute
    'destination'"). Smartsheet's other endpoints (Move Sheet, etc.) use
    the same flat shape — pattern is consistent once you know it.
    """
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    url = f"https://api.smartsheet.com/2.0/sheets/{template_sheet_id}/copy"
    include_csv = ",".join(include) if include else ""
    if include_csv:
        url += f"?include={include_csv}"
    context = (
        f"copying sheet {template_sheet_id} into folder {folder_id} as {name!r}"
    )
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "destinationType": "folder",
                "destinationId": folder_id,
                "newName": name,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise SmartsheetError(f"{context}: {e!r}") from e
    _translate_smartsheet_error(response, context=context)
    body = response.json()
    return int(body["result"]["id"])


# ---- SDK 404 noise suppression ------------------------------------------


class _Suppress404JSON(logging.Filter):
    """Drop the SDK's ERROR-level emission of the raw 404 response body.

    Inspects `record.args` (unformatted; first positional is the status
    code passed to the SDK's `_log_request` ERROR call) so the filter
    survives format-string changes in future SDK versions. Non-tuple or
    empty args, or any non-ERROR record, passes through untouched. The
    set is parameterized so additional status codes can be silenced later
    without re-architecting the filter.
    """

    _QUIET_STATUS_CODES = frozenset({404})

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.ERROR:
            return True
        args = record.args
        if not isinstance(args, tuple) or not args:
            return True
        return args[0] not in self._QUIET_STATUS_CODES


logging.getLogger(SDK_LOGGER_NAME).addFilter(_Suppress404JSON())
