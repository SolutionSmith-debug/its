#!/usr/bin/env python3
"""Generate (and optionally upload) the manual-fallback blank-form archive — PR-L.

WHAT
    Renders every Safety Portal form definition to a BLANK, fillable-AcroForm PDF plus
    one manual-fallback cover sheet, writes them to a local out dir, and — only when an
    explicit upload flag is passed — uploads them to the Box "00_Form_Archive" folder
    (version-on-conflict, so re-running updates in place rather than duplicating).

WHY a separate script (not in form_pdf.py)
    `form_pdf.render_blank_fillable` / `render_cover_sheet` are pure bytes (no network,
    no send) so the renderer stays OUTSIDE the network-capability gate
    (tests/test_capability_gating.py walks shared/ + safety_reports/). All Box I/O lives
    here in scripts/ (deliberately NOT walked by that gate — operator-run entry points
    legitimately touch the network). Send-free: nothing here emails or touches the
    External Send Gate.

WHY render-only by default
    Per the build's HARD LIMITS, the live Box upload is the operator's ACTIVATION step.
    This script DEFAULTS to render-only (writes PDFs locally, no Box). Upload happens
    ONLY when `--upload` is passed.

MODES
    (default)            Render all forms + cover to --out-dir. NO Box. NO network.
    --upload            Render, then upload all PDFs to Box 00_Form_Archive
                        (version-on-conflict). Reads the Box root from ITS_Config.
    --out-dir DIR       Local output directory (default: ./form_archive_out).
    --root-folder-id ID Override the Box root folder id (defense fallback only; the
                        canonical value comes from ITS_Config — see _resolve_box_root).

REPRODUCIBILITY
    Deterministic + re-runnable. Regenerate + re-upload after any
    safety_portal/forms/*.json change (see docs/runbooks/safety_portal_forms.md).

NATURE
    Additive, send-free, reference-only. Blank forms only — no submissions, no auth,
    no D1, no Worker, no migration. NOT high-capability-class.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safety_reports import form_pdf, safety_naming  # noqa: E402
from shared.error_log import Severity, its_error_log, log  # noqa: E402

_SCRIPT = "scripts.generate_form_archive"
WORKSTREAM = "safety_reports"

# The archive folder name. "00_" sorts it ABOVE the per-job folders in Box, and no job
# is ever named "00_Form_Archive", so get_or_create_folder never collides with the live
# mirror tree.
ARCHIVE_FOLDER_NAME = "00_Form_Archive"

# Defense-only fallback for the Box root if ITS_Config is unreachable AND no CLI arg is
# given. The canonical source is ITS_Config (safety_reports.box.portal_root_folder_id);
# hitting this fallback is logged. Value is the seeded+live mirror root.
_DEFAULT_ROOT_FOLDER_ID = "388017263015"

_FORMS_DIR = Path(__file__).resolve().parents[1] / "safety_portal" / "forms"
_COVER_FILENAME = "00 — Manual Fallback Instructions.pdf"


def _form_definition_paths() -> list[Path]:
    """The SAME glob the TS registry uses (forms/*.json), minus the meta-schema.

    meta-schema.json is the JSON schema, not a form definition — excluded exactly as
    safety_portal/src/forms/registry.ts excludes it.
    """
    return sorted(p for p in _FORMS_DIR.glob("*.json") if p.name != "meta-schema.json")


def _blank_pdf_filename(form_name: str) -> str:
    """The archive filename for a form, named by its human Form Name.

    e.g. "Job Hazard Analysis" -> "Job Hazard Analysis (fillable).pdf". `/` would be a
    Box path separator, so it is replaced (mirrors safety_naming.job_folder_name's rule).
    """
    safe = form_name.replace("/", "-").strip() or "form"
    return f"{safe} (fillable).pdf"


def _render_all() -> list[tuple[str, bytes]]:
    """Render the cover sheet + every form's blank fillable PDF to (filename, bytes).

    Deterministic and pure (no network). The cover sheet sorts first by its "00 —"
    prefix. A single form that fails to render is logged and SKIPPED so the rest of the
    archive still generates — the archive is best-effort reference material, and a
    half-archive beats no archive; the skip is surfaced via ITS_Errors, never silent.
    """
    rendered: list[tuple[str, bytes]] = [(_COVER_FILENAME, form_pdf.render_cover_sheet())]
    import json
    for path in _form_definition_paths():
        try:
            definition = json.loads(path.read_text())
            pdf = form_pdf.render_blank_fillable(definition)
            rendered.append((_blank_pdf_filename(definition.get("form_name", path.stem)), pdf))
        except Exception as exc:  # noqa: BLE001 — one bad def must not abort the archive
            log(Severity.ERROR, _SCRIPT,
                f"failed to render blank form {path.name!r}: {exc!r}",
                error_code="form_archive_render_failed")
    return rendered


def _write_local(rendered: list[tuple[str, bytes]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in rendered:
        (out_dir / name).write_bytes(data)


def _resolve_box_root(cli_root: str | None) -> str:
    """Resolve the Box archive root folder id.

    Order: ITS_Config (safety_naming.CFG_BOX_PORTAL_ROOT) → CLI --root-folder-id →
    the seeded default. Hitting the CLI/default fallback is LOGGED (a WARN-level INFO)
    so an operator notices the config wasn't read, but it never FAILS — the archive is
    reference-only and should still upload to the known-good root.
    """
    from shared import smartsheet_client
    try:
        configured = smartsheet_client.get_setting(
            safety_naming.CFG_BOX_PORTAL_ROOT, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetError:
        configured = None
    if configured and configured.strip():
        return configured.strip()
    fallback = (cli_root or "").strip() or _DEFAULT_ROOT_FOLDER_ID
    log(Severity.WARN, _SCRIPT,
        f"ITS_Config {safety_naming.CFG_BOX_PORTAL_ROOT!r} empty/unreadable; "
        f"falling back to root folder id {fallback!r}",
        error_code="form_archive_root_fallback")
    return fallback


def _upload(rendered: list[tuple[str, bytes]], root_folder_id: str) -> int:
    """Upload all rendered PDFs to Box 00_Form_Archive (version-on-conflict).

    get_or_create_folder(root, "00_Form_Archive") → upload each PDF with
    upload_bytes_or_new_version (re-running updates in place; no duplicates). A per-file
    upload failure is logged (ERROR) and surfaced, never silent; the run continues so
    one transient failure doesn't block the rest. Returns the count uploaded OK.
    """
    from shared import box_client
    archive_id = box_client.get_or_create_folder(root_folder_id, ARCHIVE_FOLDER_NAME)
    ok = 0
    for name, data in rendered:
        try:
            box_client.upload_bytes_or_new_version(archive_id, name, data)
            ok += 1
        except box_client.BoxError as exc:
            log(Severity.ERROR, _SCRIPT,
                f"failed to upload {name!r} to Box folder {archive_id!r}: {exc!r}",
                error_code="form_archive_upload_failed")
    return ok


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate the manual-fallback blank-form archive (PR-L).")
    # Mutually-exclusive intent so the default is unambiguously render-only and an
    # upload requires the explicit flag (HARD LIMIT: live upload is operator-gated).
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--upload", action="store_true",
                   help="Render AND upload to Box 00_Form_Archive (version-on-conflict).")
    g.add_argument("--no-upload", action="store_true",
                   help="Render only; write PDFs locally, no Box (this is the DEFAULT).")
    ap.add_argument("--out-dir", default="form_archive_out",
                    help="Local output directory (default: ./form_archive_out).")
    ap.add_argument("--root-folder-id", default=None,
                    help="Override Box root folder id (defense fallback; ITS_Config wins).")
    return ap.parse_args(argv)


@its_error_log(script_name=_SCRIPT)
def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()

    rendered = _render_all()
    _write_local(rendered, out_dir)
    log(Severity.INFO, _SCRIPT,
        f"rendered {len(rendered)} PDFs to {out_dir}",
        error_code="form_archive_rendered")

    if not args.upload:
        # Render-only (default). No network, no Box.
        print(f"Rendered {len(rendered)} PDFs to {out_dir} (render-only; pass --upload to push to Box).")
        return 0

    root = _resolve_box_root(args.root_folder_id)
    ok = _upload(rendered, root)
    print(f"Uploaded {ok}/{len(rendered)} PDFs to Box {ARCHIVE_FOLDER_NAME!r} under root {root}.")
    # A partial upload is a non-fatal degraded state (each failure already logged ERROR);
    # return non-zero so the operator notices the archive isn't fully refreshed.
    return 0 if ok == len(rendered) else 1


if __name__ == "__main__":
    raise SystemExit(main())
