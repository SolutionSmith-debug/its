"""Build the "ITS –– Safety Portal" workspace + its two top-level folders.

D2 of the Phase-1 production-cutover builder gap-fill. The Safety Portal workspace
predates the migration-builder family: it was hand-created in the sandbox tenant on
2026-06-03/05 and has never had a builder, so the Phase-1 flip to Evergreen's
PRODUCTION Smartsheet plan had no reproducible way to stand it up. This script closes
that gap. It is the structural twin of `build_progress_reporting_workspace.py` and
`build_purchase_orders_workspace.py`.

Purpose
    Find-or-create, idempotently and create-only, exactly three objects:
      1. The "ITS –– Safety Portal" WORKSPACE.
      2. The "00_Safety Portal" FOLDER inside it — home of ITS_Active_Jobs,
         WSR_human_review and Orphaned Reports.
      3. The "00_Form Catalog" FOLDER inside it — home of ITS_Forms_Catalog.
    The sheets themselves are NOT built here; each already has its own builder
    (`build_its_active_jobs_sheet.py`, `build_wsr_human_review_sheet.py`,
    `build_orphaned_reports_sheet.py`, `build_its_forms_catalog_sheet.py`).
    Per-job folders + per-week sheets are RUNTIME find-or-create (`shared/job_sheet.py`,
    `safety_reports/week_folder.py`) and are never built here.

    Like Progress Reporting and Purchase Orders, this workspace sits OUTSIDE the §23
    audience-separation model and is governed by §46 — workspace membership = approval
    authority. The share list of THIS workspace is the approver set the F22 gate verifies
    before `weekly_send` dispatches a WSR. ITS OWNS it and writes it as a structured
    system-of-record (Op Stds v21 §51).

WHY THESE FOLDER NAMES (the decision this script encodes)
    Earlier briefs describe a single folder named "Safety Portal". That is STALE. Browsed
    live 2026-07-21 (`browse_workspace(6820552519247748)`), the workspace contains TWO
    canonical ITS folders:
        "00_Safety Portal"  id=2261538947000196   (ASCII underscore, no dash)
        "00_Form Catalog"   id=6765138574370692
    `sheet_ids.FOLDER_SAFETY_PORTAL` = 2261538947000196 — i.e. the constant the RUNTIME
    actually consumes points at "00_Safety Portal", and its trailing comment ("… / Safety
    Portal") is a stale relic of the pre-2026-06-05 location under ITS — Operations.
    ITS_Forms_Catalog does NOT live beside the other three; it lives in its own second
    folder. This builder therefore creates the names the LIVE tenant uses, because the
    entire point of the cutover is to make the production tenant match what the code
    consumes — creating a folder named "Safety Portal" would produce a structure no
    constant points at. `CANONICAL_FOLDER_NAMES` below is the single module-level knob:
    a future correction is a one-line edit.

KNOWN STALE DOWNSTREAM BUILDERS (out of scope for this script — do not be surprised)
    `build_its_active_jobs_sheet.py` and `build_its_forms_catalog_sheet.py` still target
    `WORKSPACE_OPERATIONS` with `FOLDER_NAME = "Safety Portal"`, which is stale against
    the 2026-06-05 move to `WORKSPACE_SAFETY_PORTAL`. Run as-is against a fresh production
    tenant they will find-or-create a THIRD, wrongly-named "Safety Portal" folder under
    ITS — Operations and build their sheets there — orphaned from every runtime constant.
    `build_wsr_human_review_sheet.py` and `build_orphaned_reports_sheet.py` are already
    correct (they target `FOLDER_SAFETY_PORTAL` directly). Fixing the two stale builders
    is a SEPARATE change; until it lands, the operator must not run them blind at cutover.

Invariants (blast-radius controls — this runs against a CUSTOMER PRODUCTION tenant)
    1. CREATE-ONLY. GET + create-POST only. No PUT, no DELETE, no update of anything,
       ever — including objects this script itself created.
    2. EXACT-NAME FIND, ADOPT-DON'T-TOUCH. Find is exact string equality on the canonical
       name. On find: print "[skip] … already present" and move on. Never rename,
       re-parent, re-share, or write into an adopted object.
    3. SCOPED CREATION. Folders are created only inside the workspace this script
       created-or-adopted by exact name. No enumerate-and-act across other workspaces.
    4. MINIMAL SET. Exactly the objects in CANONICAL_FOLDER_NAMES. No extras.
    5. IDEMPOTENT NO-OP. A second run prints the same ids and creates nothing.
    6. LIVE-WRITE CONFIRMATION. LIVE by default (family convention); `--dry-run` prints
       the full plan (every object, found-vs-would-create, target parent). A y/N prompt
       (the `seed_its_config.py` seam) precedes the FIRST live create.
    7. NO SECRETS IN OUTPUT. Names and ids only; the token is never printed.
    8. DUPLICATE-NAME AMBIGUITY IS LOUD. Smartsheet does not enforce unique workspace or
       folder names, and the shared `find_*_by_name_*` helpers return the FIRST match —
       which is demonstrably NOT always the live object (five sheets named "ITS_Errors"
       coexist in "02 — Logs"; only 8015637140950916 is live). Every find here counts ALL
       exact-name matches and prints a [WARN] naming the ambiguity and every matching id
       when count > 1. It still adopts the first (never creates a duplicate), but the
       operator must reconcile before flipping any id.
    9. §45 re-find-after-create. After each create, re-find by name and compare the WHOLE
       result set against `[new_id]` — not just its first element, because
       `GET /workspaces` documents NO ordering guarantee, so a concurrently-created
       same-named duplicate that happens to sort AFTER our new id would slip past a
       first-element check in silence. An EMPTY re-find is the benign
       create→read propagation lag (reported [info]; the id the POST returned is
       authoritative); any other differing set is a real race and is a [WARN].

    ADOPTION IS PLAN-BLIND — THE OWNERSHIP CHECK (why `accessLevel` is load-bearing)
    `GET /workspaces?includeAll=true` lists every workspace the token can SEE, including
    ones the identity is merely a MEMBER of. An operator whose production identity is also
    shared into the sandbox plan therefore finds the SANDBOX "ITS –– Safety Portal" here
    with count == 1 — so invariant 8's ambiguity [WARN] never fires — and this script would
    happily create the two canonical folders on the WRONG PLAN. The only distinguishing
    signals the listing carries are `accessLevel` and `permalink`, so both are printed for
    the adopted workspace, both ride the confirmation prompt, and an `accessLevel` that is
    anything other than "OWNER" HARD-STOPS the create-into path ([WARN]
    `adopted_workspace_not_owned`, nonzero exit, zero writes). An absent `accessLevel`
    (the API did not report one) is treated as UNKNOWN: it is called out [info] and the run
    proceeds, because refusing on a field the API may simply omit would brick the cutover.

    Name byte-exactness: WORKSPACE_NAME is written with explicit \\u escapes. "ITS ––
    Safety Portal" uses TWO U+2013 EN DASHes — NOT the U+2014 EM DASH used by every other
    ITS workspace. A silently-normalized dash makes the find miss and CREATES A DUPLICATE
    WORKSPACE in the customer's production plan. Do not "fix" the escapes to literals.

Failure modes
    - Keychain miss / bad token → `requests` HTTPError on the first GET; exit nonzero
      before any write.
    - Non-2xx on any GET/POST → raised, script aborts; nothing partially updated (each
      create is independent and idempotent, so a re-run resumes).
    - Duplicate-name adoption → surfaced as [WARN] with all ids (invariant 8), never silent.
    - Adopted workspace not OWNED by this identity (the sandbox-vs-production trap above)
      → [WARN] adopted_workspace_not_owned, no folders created, exit 1.
    - Operator declines the y/N prompt → zero writes, exit 0. A decline is a NO-OP, not a
      failure (family convention — D1/D3/D4 agree); the cutover checklist runs these four
      builders in sequence and must not treat "operator said no" as an error.

Cutover sequence (FLIP precedes SEED)
    1. THIS script — note the printed WORKSPACE + FOLDER ids.
    2. Flip WORKSPACE_SAFETY_PORTAL + FOLDER_SAFETY_PORTAL in shared/sheet_ids.py
       (FOLDER_OPERATIONS_SAFETY_PORTAL is a back-compat ALIAS of FOLDER_SAFETY_PORTAL —
       it takes the new value automatically; do NOT give it a second literal).
       Flip FOLDER_FORM_CATALOG too — "00_Form Catalog" now has its own constant; it is
       the target folder for the ITS_Forms_Catalog build step.
    3. build_its_active_jobs_sheet.py + build_wsr_human_review_sheet.py +
       build_orphaned_reports_sheet.py + build_its_forms_catalog_sheet.py
       (see KNOWN STALE DOWNSTREAM BUILDERS above BEFORE running the first and last).
    4. Flip SHEET_ACTIVE_JOBS + SHEET_WSR_HUMAN_REVIEW + SHEET_ORPHANED_REPORTS +
       SHEET_FORMS_CATALOG.
    5. OPERATOR (send-blocking): share every safety approver into "ITS –– Safety Portal"
       (§46 — an approver not shared here cannot approve a WSR_human_review row; an empty
       resolved set fails closed and blocks all safety sends).

Consumers
    Operator-run, one time, at Phase-1 cutover. No daemon imports this module.

§43: no successor-remediation runbook entry is needed. This is a one-time operator
migration with no Tier-2-recurring failure mode — a failed run is simply re-run, and the
only operator-facing outcome (a duplicate-name [WARN]) is a reconcile-before-flip decision
that belongs to the cutover checklist, not to a standing runbook.

No send capability, no AI: this module imports neither graph_client / resend_client nor
anthropic_client / anthropic.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_safety_portal_workspace.py --dry-run
    python3 scripts/migrations/build_safety_portal_workspace.py

Exit 0 on success, no-op, or an operator-declined confirmation; nonzero on any error and
on the not-owned hard stop.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, NamedTuple

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

# "ITS –– Safety Portal" — TWO U+2013 EN DASHes, NOT the U+2014 EM DASH the other ITS
# workspaces use. Written as escapes so no editor/normalizer can silently substitute a
# different dash: a mismatched name misses the find and DUPLICATES the workspace.
WORKSPACE_NAME = "ITS \u2013\u2013 Safety Portal"  # renders as: ITS –– Safety Portal

# The canonical folder names, in creation order. LIVE-VERIFIED 2026-07-21 against the
# sandbox tenant; both are pure ASCII (underscore, space — no dash of any kind). This is
# the single knob: correcting a name is a one-line edit here. See "WHY THESE FOLDER
# NAMES" in the module docstring for the justification over the stale "Safety Portal".
CANONICAL_FOLDER_NAMES: tuple[str, ...] = (
    "00_Safety Portal",  # ITS_Active_Jobs, WSR_human_review, Orphaned Reports
    "00_Form Catalog",   # ITS_Forms_Catalog
)

# Which sheet_ids.py constant each folder feeds, for the FLIP BLOCK. Both folders have a
# dedicated constant: FOLDER_FORM_CATALOG is added to shared/sheet_ids.py by the registry
# change that ships with this builder, so "00_Form Catalog" is a real FLIP line rather
# than a for-the-record note.
FOLDER_CONSTANTS: dict[str, str | None] = {
    "00_Safety Portal": "FOLDER_SAFETY_PORTAL",
    "00_Form Catalog": "FOLDER_FORM_CATALOG",
}

# The FLIP BLOCK placeholder for an id this run did not resolve (dry-run, declined
# confirmation, not-owned hard stop, or a partial run). Family-standard wording — D1 and
# D3 emit the same sentinel plus a `flip_block_incomplete` WARN.
UNRESOLVED = "<not created — re-run live>"

# U+2013 EN DASH — WORKSPACE_NAME uses TWO of them, deliberately NOT the U+2014 EM DASH
# every other ITS workspace uses. Derived via chr() from the codepoint (like D1's
# "—" escape) so the guard itself carries no literal dash a normalizer could
# silently swap. The canonical FOLDER names carry no dash of any kind.
EN_DASH = chr(0x2013)
# Every dash-like codepoint that must NOT appear where it is checked below, by codepoint
# (no literal dash in source — the same reason WORKSPACE_NAME is an escape).
_ALL_DASH_CODEPOINTS = (
    0x002D,  # HYPHEN-MINUS
    0x2010,  # HYPHEN
    0x2011,  # NON-BREAKING HYPHEN
    0x2012,  # FIGURE DASH
    0x2013,  # EN DASH
    0x2014,  # EM DASH
    0x2015,  # HORIZONTAL BAR
    0x2212,  # MINUS SIGN
)


def _assert_canonical_dashes() -> None:
    """Fail CLOSED at import if a canonical name's dash was normalized (rule-4 parity).

    Mirrors D1/D3's import-time codepoint check with an EXPLICIT raise (never a bare
    `assert`, which `python -O` / PYTHONOPTIMIZE strips): WORKSPACE_NAME must contain
    exactly TWO U+2013 EN DASHes as a spaced pair and no other dash-like codepoint, and
    the ASCII folder names must contain no dash at all. Raising here costs one run; NOT
    raising costs a duplicate workspace/folder on a customer's production plan, found
    only after ids are pasted into shared/sheet_ids.py.
    """
    if WORKSPACE_NAME.count(EN_DASH) != 2 or f" {EN_DASH}{EN_DASH} " not in WORKSPACE_NAME:
        raise ValueError(
            f"canonical_name_dash_corrupted: {WORKSPACE_NAME!r} must contain exactly two "
            "U+2013 EN DASHes as a spaced pair. A dash was normalized — restore the "
            "\\u2013\\u2013 escape before running against any tenant."
        )
    for cp in _ALL_DASH_CODEPOINTS:
        if cp != 0x2013 and chr(cp) in WORKSPACE_NAME:
            raise ValueError(
                f"canonical_name_dash_corrupted: {WORKSPACE_NAME!r} contains "
                f"U+{cp:04X}, not the canonical U+2013 EN DASH pair. Restore the "
                "\\u2013\\u2013 escape."
            )
    for fname in CANONICAL_FOLDER_NAMES:
        for cp in _ALL_DASH_CODEPOINTS:
            if chr(cp) in fname:
                raise ValueError(
                    f"canonical_name_dash_corrupted: folder {fname!r} contains "
                    f"U+{cp:04X} — the canonical folder names are pure ASCII with no dash "
                    "of any kind. A dash was introduced; restore the ASCII name."
                )


_assert_canonical_dashes()

# Constants whose resolved id came from an AMBIGUOUS (>1 exact-name match) find of a
# TERMINAL object (a folder — this script creates nothing inside its folders), mapped to
# the match COUNT. Consumed by the FLIP BLOCK so a known-ambiguous id is never rendered as
# a clean paste-ready integer (the absolute FLIP-BLOCK-leak rule). Mirrors D1/D3.
_AMBIGUOUS: dict[str, int] = {}


def _headers() -> dict[str, str]:
    """Auth headers. The token is never printed (invariant 7)."""
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _warn_if_ambiguous(kind: str, name: str, ids: list[int],
                       constant: str | None = None) -> None:
    """Rule 4 — never let a duplicate-name adoption of a TERMINAL object be silent.

    TERMINAL only — a folder, which this script creates nothing inside. The WORKSPACE
    is a PARENT and fails CLOSED instead (rule 2, `ensure_workspace`). `find_*_by_name_*`
    returns the FIRST match, which is not necessarily the live object (five sheets named
    "ITS_Errors" coexist in "02 — Logs"). We still adopt the first — creating another
    duplicate would be worse — but record it in `_AMBIGUOUS` so the FLIP BLOCK withholds
    the id, and the operator must reconcile before flipping.
    """
    if len(ids) > 1:
        if constant:
            _AMBIGUOUS[constant] = len(ids)
        print(f"[WARN] duplicate_name_ambiguity: {len(ids)} {kind}s named {name!r} exist "
              f"(ids={ids}). Adopting the FIRST ({ids[0]}) — this may NOT be the live one. "
              "Reconcile in Smartsheet before flipping the id in shared/sheet_ids.py. The "
              "FLIP BLOCK below will NOT print this id.")


def _find_workspaces() -> list[dict[str, Any]]:
    """Return the WHOLE object for every workspace named WORKSPACE_NAME. Read-only.

    Whole objects, not ids: the listing includes workspaces this identity is merely a
    MEMBER of, and `accessLevel` + `permalink` are the only fields that tell a production
    workspace apart from a sandbox one of the same name (see "ADOPTION IS PLAN-BLIND" in
    the module docstring). The count still drives invariant 8.
    """
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [ws for ws in r.json().get("data", []) if ws.get("name") == WORKSPACE_NAME]


def _find_workspace_ids() -> list[int]:
    """Ids only — the shape the §45 re-find-after-create comparison needs."""
    return [int(ws["id"]) for ws in _find_workspaces()]


def _report_refind(kind: str, name: str, new_id: int, found: list[int], constant: str) -> None:
    """Invariant 9 (§45) — compare the WHOLE re-find set against the id just created.

    Comparing `found[0]` instead of `found` would stay SILENT whenever a concurrently
    created same-named duplicate sorts AFTER our new id: neither `GET /workspaces` nor the
    workspace-folder listing documents any ordering guarantee, so "first == mine" proves
    nothing. Two distinct outcomes, deliberately reported differently:

      - EMPTY set  -> Smartsheet's create→read propagation lag, seen routinely right after
        a create. Benign: the id the POST returned is authoritative. [info], not [WARN] —
        crying wolf here trains the operator to ignore the real race below.
      - Any other differing set -> a genuine duplicate now shares the name. [WARN] with
        EVERY id, because the operator must reconcile before flipping `constant`.
    """
    if found == [new_id]:
        return
    if not found:
        print(f"[info] {kind}_refind_empty: created {new_id}, but a name lookup for {name!r} "
              "returned nothing yet — Smartsheet create→read propagation lag, not a race. "
              f"The id above is authoritative; a re-run will confirm it before you flip "
              f"{constant}.")
        return
    print(f"[WARN] {kind}_race_duplicate: created {new_id} but a name lookup returns "
          f"{found} — another {kind} named {name!r} exists (a concurrent create raced us). "
          f"Identify the live one and reconcile BEFORE flipping {constant}.")


def _find_folder_ids(workspace_id: int, name: str) -> list[int]:
    """Return the ids of ALL top-level folders named `name` in `workspace_id`.

    Direct REST rather than `smartsheet_client.find_folder_by_name_in_workspace` for one
    reason only: that helper returns the first match and cannot report a count, which
    invariant 8 requires. Same endpoint, same exact-match semantics, read-only.
    """
    r = requests.get(f"{BASE}/workspaces/{workspace_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [int(f["id"]) for f in r.json().get("folders", []) if f.get("name") == name]


class LiveWriteGate:
    """One y/N confirmation before the FIRST live create (invariant 6).

    Asked once per run, then cached — the operator confirms the plan, not each object.
    A decline is terminal for the run: nothing is written, and `main()` exits 0 — a
    decline is a no-op, not a failure.
    """

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self._answered: bool | None = None
        # Identity of the workspace being created INTO, filled in by `ensure_workspace`
        # once it is adopted. It rides the prompt so the operator can see accessLevel +
        # permalink — the only way to catch "this is the sandbox workspace" by eye.
        self.workspace_context: str | None = None

    @property
    def declined(self) -> bool:
        """True once the operator has answered anything other than 'y'.

        A decline is a NO-OP, not a failure: nothing is written and `main()` still
        returns 0 (family convention — D1/D3/D4 agree, and the cutover checklist runs the
        four builders in sequence). This flag only drives the closing "declined" notice
        and the FLIP BLOCK's unresolved-id reporting.
        """
        return self._answered is False

    def allow(self, what: str) -> bool:
        if self.dry_run:
            return False
        if self._answered is None and os.environ.get("STANDUP_NONINTERACTIVE") == "1":
            # Orchestrated run (standup.py): its master gate is the control and
            # stdin is CLOSED — auto-approve without touching stdin so any
            # unexpected second prompt still fails loudly (EOFError).
            print(f"\nFirst live create is: {what} "
                  "[auto-approved: STANDUP_NONINTERACTIVE]")
            self._answered = True
        if self._answered is None:
            context = self.workspace_context or (
                f"a NEW workspace named {WORKSPACE_NAME!r} (none exists yet)"
            )
            answer = input(f"\nFirst live create is: {what}\n"
                           f"Target: {context}\n"
                           f"Create the missing objects in workspace {WORKSPACE_NAME!r}? "
                           "[y/N] ").strip().lower()
            self._answered = answer == "y"
            if not self._answered:
                print("Aborted; no writes.")
        return self._answered


class WorkspaceOutcome(NamedTuple):
    """Result of `ensure_workspace`: the id, plus whether creation is BLOCKED.

    `blocked` covers BOTH fail-closed cases — the not-owned hard stop (rule 1) and the
    duplicate-parent hard stop (rule 2) — and is deliberately distinct from
    `workspace_id is None`: a not-owned run HAS a resolved id (the adopted workspace) but
    must not create anything inside it. `ambiguous_count` is set only for the
    duplicate-parent case, so `main()` renders the workspace FLIP line as the
    `<AMBIGUOUS …>` sentinel; for not-owned it stays None and the block is suppressed
    entirely. Either way the exit is nonzero and no clean workspace id is ever emitted.
    """

    workspace_id: int | None
    blocked: bool = False
    ambiguous_count: int | None = None


def ensure_workspace(gate: LiveWriteGate) -> WorkspaceOutcome:
    """Find-or-create the workspace. Adopting one this identity does not OWN hard-stops.

    On adoption the workspace's `accessLevel` and `permalink` are printed and pinned onto
    the gate, so the live-write prompt names the exact object folders would be created
    into — the sandbox-vs-production trap in the module docstring, which invariant 8's
    count-based [WARN] structurally cannot catch (the sandbox workspace is a single exact
    name match). `accessLevel != "OWNER"` returns `blocked=True`; an absent `accessLevel`
    is UNKNOWN — reported, not fatal.
    """
    existing = _find_workspaces()
    if existing:
        ids = [int(ws["id"]) for ws in existing]
        if len(ids) > 1:
            # PARENT ambiguity FAILS CLOSED (rule 2): this script creates the canonical
            # folders inside the workspace, so it must never adopt-first-and-guess which of
            # several same-named workspaces to write into. (Converged with D3's
            # _resolve_unique_parent; D1 does the same via DuplicateParentError.)
            print(f"[WARN] duplicate_parent_ambiguity: {len(ids)} workspaces are named "
                  f"{WORKSPACE_NAME!r} — FAILING CLOSED. This script creates FOLDERS inside "
                  f"the workspace and will not write into a container it cannot uniquely "
                  f"identify. Nothing is created. Reconcile the duplicates (identify the "
                  f"live one, delete or rename the rest) BEFORE flipping "
                  f"WORKSPACE_SAFETY_PORTAL.")
            for ws in existing:
                print(f"        candidate id={int(ws['id'])} "
                      f"accessLevel={ws.get('accessLevel')} permalink={ws.get('permalink')}")
            return WorkspaceOutcome(None, blocked=True, ambiguous_count=len(ids))
        adopted = existing[0]
        workspace_id = int(adopted["id"])
        access = adopted.get("accessLevel")
        permalink = adopted.get("permalink")
        # Print the discriminators unconditionally — even the OWNER path should SHOW which
        # plan this run is about to write into.
        print(f"[skip] workspace {WORKSPACE_NAME!r} already present "
              f"(workspace_id={workspace_id}, accessLevel={access}, permalink={permalink}).")
        gate.workspace_context = (f"EXISTING workspace {workspace_id} "
                                  f"(accessLevel={access}, permalink={permalink})")
        if access is None:
            # Absent accessLevel FAILS CLOSED (rule 1, converged with D1): the endpoint
            # populates the field in practice, so an absent value is anomalous, and writing
            # into a customer PRODUCTION tenant under UNKNOWN ownership must fail closed.
            # (This is the D1-vs-D2 divergence the convergence pass closes — D2 previously
            # proceeded here.)
            print(f"[WARN] adopted_workspace_not_owned: the listing reported NO accessLevel "
                  f"for {WORKSPACE_NAME!r} id={workspace_id}, so OWNER access could not be "
                  f"confirmed. permalink={permalink}\n"
                  "       REFUSING to create anything. Open the permalink and verify this is "
                  "the production plan; if the API genuinely omitted accessLevel, escalate "
                  "rather than override.")
            return WorkspaceOutcome(workspace_id, blocked=True)
        if access != "OWNER":
            print(f"[WARN] adopted_workspace_not_owned: accessLevel={access!r} (not 'OWNER') "
                  f"for {WORKSPACE_NAME!r} id={workspace_id}. This identity is a MEMBER of "
                  "this workspace, not its owner — it is very likely the SANDBOX workspace "
                  "shared into this account, and creating the canonical folders inside it "
                  "would build the cutover structure on the WRONG PLAN. Nothing was "
                  "created. Open the permalink above, confirm which plan owns it, and "
                  "re-run from an identity that OWNS the production workspace.")
            return WorkspaceOutcome(workspace_id, blocked=True)
        return WorkspaceOutcome(workspace_id)
    if gate.dry_run:
        print(f"[dry-run] Would create workspace {WORKSPACE_NAME!r} (no parent — top level).")
        return WorkspaceOutcome(None)
    if not gate.allow(f"workspace {WORKSPACE_NAME!r}"):
        return WorkspaceOutcome(None)

    r = requests.post(f"{BASE}/workspaces", headers=_headers(),
                      json={"name": WORKSPACE_NAME}, timeout=30)
    r.raise_for_status()
    new_id = int(r.json()["result"]["id"])
    _report_refind("workspace", WORKSPACE_NAME, new_id, _find_workspace_ids(),
                   "WORKSPACE_SAFETY_PORTAL")
    print(f"[ok] created workspace {WORKSPACE_NAME!r} (workspace_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    WORKSPACE_SAFETY_PORTAL = {new_id}")
    return WorkspaceOutcome(new_id)


def ensure_folder(workspace_id: int, name: str, gate: LiveWriteGate) -> int | None:
    """Find-or-create ONE canonical folder in the workspace (invariants 2-4).

    Scoped: created only inside `workspace_id`, which this run created or adopted by
    exact name. Adopted folders are never renamed, re-parented, or written into.
    """
    existing = _find_folder_ids(workspace_id, name)
    if existing:
        _warn_if_ambiguous("folder", name, existing, FOLDER_CONSTANTS.get(name))
        print(f"[skip] folder {name!r} already present (folder_id={existing[0]}).")
        return existing[0]
    if gate.dry_run:
        print(f"[dry-run] Would create folder {name!r} in workspace {workspace_id}.")
        return None
    if not gate.allow(f"folder {name!r} in workspace {workspace_id}"):
        return None

    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, name)
    const = FOLDER_CONSTANTS.get(name)
    # §45 re-find-after-create — same whole-set comparison as the workspace create above.
    _report_refind("folder", name, new_id, _find_folder_ids(workspace_id, name),
                   const or "the folder id")
    print(f"[ok] created folder {name!r} (folder_id={new_id}).")
    if const:
        print(f"[bootstrap] Update shared/sheet_ids.py:\n    {const} = {new_id}")
    return new_id


def _render(value: int | None) -> str:
    """An id, or the family-standard unresolved sentinel — never a bare `None`.

    Interpolating a raw `None` into a paste-ready block emits `= None`, which is valid
    Python and would silently flip a constant to nothing. The sentinel does not parse, so
    a half-run block cannot be pasted by accident.
    """
    return str(value) if value is not None else UNRESOLVED


def _render_folder(name: str, value: int | None) -> str:
    """Render one folder id — the `<AMBIGUOUS …>` sentinel when duplicate-name (rule 4).

    A folder id resolved from an AMBIGUOUS (>1 match) adopt-first must NEVER print as a
    clean integer (the absolute FLIP-BLOCK-leak rule); `_AMBIGUOUS`, keyed by the
    sheet_ids constant, is populated by `_warn_if_ambiguous`.
    """
    const = FOLDER_CONSTANTS.get(name)
    matches = _AMBIGUOUS.get(const) if const else None
    if matches:
        return f"<AMBIGUOUS — {matches} matches, RECONCILE BEFORE FLIPPING>"
    return _render(value)


def _print_flip_block(workspace_id: int | None, folder_ids: dict[str, int | None],
                      *, workspace_ambiguous_count: int | None = None) -> None:
    """Ready-to-paste shared/sheet_ids.py lines. Names + ids only (invariant 7).

    Any id that is unresolved OR untrustworthy (duplicate-name ambiguous) renders through
    a sentinel, never as a clean integer — the absolute FLIP-BLOCK-leak rule.
    `workspace_ambiguous_count` renders the workspace line as the `<AMBIGUOUS …>` sentinel
    (the fail-closed duplicate-parent case). The not-owned hard stop never reaches here —
    `main()` suppresses the whole block for it, so the adopted sandbox id never leaks.
    """
    print("\n=== FLIP BLOCK ===")
    print("Paste into shared/sheet_ids.py (FLIP precedes SEED — do this before the "
          "sheet builders):\n")
    sp = folder_ids.get("00_Safety Portal")
    fc = folder_ids.get("00_Form Catalog")
    if workspace_ambiguous_count is not None:
        ws = f"<AMBIGUOUS — {workspace_ambiguous_count} matches, RECONCILE BEFORE FLIPPING>"
    else:
        ws = _render(workspace_id)
    sp_rendered = _render_folder("00_Safety Portal", sp)
    fc_rendered = _render_folder("00_Form Catalog", fc)
    print(f"    WORKSPACE_SAFETY_PORTAL = {ws}")
    print(f"    FOLDER_SAFETY_PORTAL = {sp_rendered}")
    print("    # FOLDER_OPERATIONS_SAFETY_PORTAL is a back-compat ALIAS "
          "(= FOLDER_SAFETY_PORTAL).")
    print("    # It inherits the value above — do NOT emit a second literal for it.")
    print(f"    FOLDER_FORM_CATALOG = {fc_rendered}")
    print("    # '00_Form Catalog' — the target folder for build_its_forms_catalog_sheet.py.")
    incomplete = (workspace_id is None or workspace_ambiguous_count is not None
                  or sp is None or fc is None or bool(_AMBIGUOUS))
    if incomplete:
        print(f"\n[WARN] flip_block_incomplete: one or more ids are {UNRESOLVED} or "
              "<AMBIGUOUS> (dry-run, declined confirmation, a not-owned/duplicate-parent "
              "hard stop, duplicate-name ambiguity, or a partial run). Re-run live and "
              "paste the COMPLETE block — never flip a placeholder or an ambiguous id.")
    print("=== END FLIP BLOCK ===")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Build the ITS –– Safety Portal workspace + its two canonical "
                     "folders (D2 cutover gap-fill). Create-only, idempotent.")
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    print(f"[info] Workspace = {WORKSPACE_NAME!r}")
    print(f"[info] Folders   = {list(CANONICAL_FOLDER_NAMES)}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print("[info] Create-only: no PUT, no DELETE, no update of any existing object.\n")

    gate = LiveWriteGate(dry_run=args.dry_run)
    _AMBIGUOUS.clear()
    outcome = ensure_workspace(gate)
    workspace_id = outcome.workspace_id

    # Rule 2 hard stop — a duplicate-parent (>1 same-named workspace). Print the FLIP
    # BLOCK with the workspace rendered as the `<AMBIGUOUS …>` sentinel (never a clean id)
    # so the operator sees exactly what to reconcile, then exit nonzero.
    if outcome.blocked and outcome.ambiguous_count is not None:
        print(f"\n[abort] duplicate_parent_ambiguity: {outcome.ambiguous_count} workspaces "
              f"named {WORKSPACE_NAME!r}. Nothing was created.")
        _print_flip_block(None, {name: None for name in CANONICAL_FOLDER_NAMES},
                          workspace_ambiguous_count=outcome.ambiguous_count)
        return 1

    # Rule 1 hard stop — adopted workspace not OWNED (or ownership unconfirmable). The WARN
    # is already printed; SUPPRESS the FLIP BLOCK entirely (mirrors D1) so the adopted
    # sandbox id never reaches the operator's clipboard as a clean paste line.
    if outcome.blocked:
        print("\n[abort] adopted_workspace_not_owned — no folders were created and nothing "
              "was written. No FLIP BLOCK is emitted (a not-owned/sandbox id must never leak "
              "as a clean paste line). Exiting nonzero so the cutover checklist stops here.")
        return 1

    folder_ids: dict[str, int | None] = {name: None for name in CANONICAL_FOLDER_NAMES}
    if workspace_id is not None:
        for name in CANONICAL_FOLDER_NAMES:
            folder_ids[name] = ensure_folder(workspace_id, name, gate)
    elif args.dry_run:
        for name in CANONICAL_FOLDER_NAMES:
            print(f"[dry-run] Would then create folder {name!r} inside the new workspace.")
    else:
        print("[info] Workspace absent and not created — skipping folders. "
              "Nothing was written.")

    print("\nSummary:")
    print(f"  WORKSPACE_SAFETY_PORTAL: id={workspace_id}")
    for name in CANONICAL_FOLDER_NAMES:
        const = FOLDER_CONSTANTS.get(name) or "(no constant)"
        print(f"  {name!r} -> {const}: id={folder_ids[name]}")
    print("\nNot built here (each has its own builder): ITS_Active_Jobs, "
          "WSR_human_review, Orphaned Reports, ITS_Forms_Catalog.")
    print("[WARN] build_its_active_jobs_sheet.py + build_its_forms_catalog_sheet.py still "
          "target WORKSPACE_OPERATIONS / FOLDER_NAME='Safety Portal' — STALE vs the "
          "2026-06-05 move. Run them only after that is corrected, or they will build "
          "into a third, orphaned folder.")
    print("[WARN] §46 send-blocking: share every safety approver into this workspace, or "
          "the F22 approver set resolves empty and all safety sends fail closed.")

    _print_flip_block(workspace_id, folder_ids)
    if gate.declined:
        print("\n[info] Operator declined the live-write confirmation — nothing was created. "
              "Re-run to proceed.")
    # A decline is a no-op (exit 0, family convention). Both blocked cases already returned
    # nonzero above.
    return 0


if __name__ == "__main__":
    sys.exit(main())
