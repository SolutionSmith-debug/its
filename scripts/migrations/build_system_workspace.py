"""Build the "ITS — System" workspace + its four operator-only folders.

D1 of the Phase-1 production-cutover gap-builder set. The "ITS — System" workspace
predates the builder family — it was hand-created during the 2026-05-17 sandbox
restructure and has never had a migration script, so a fresh tenant (the dedicated
ITS identity on Evergreen's PRODUCTION Smartsheet plan) has no reproducible way to
stand it up. This script closes that gap.

The workspace is OPERATOR-ONLY (not customer-facing, not an §46 approver surface):
it holds ITS's own machine state — ITS_Config, ITS_Errors, ITS_Quarantine,
ITS_Review_Queue, ITS_Daemon_Health. Its share list is Seth + the Successor-Operator,
never Evergreen staff.

Purpose
    Find-or-create, idempotently and create-only:
      1. The "ITS — System" WORKSPACE.
      2. Exactly four folders inside it — "01 — Config", "02 — Logs",
         "03 — Queues", "04 — Daemons". Nothing else. The sheets that live in
         them are built by their own scripts (or predate the family) and are NOT
         this script's business.

    Folder names use U+2014 EM DASH surrounded by single spaces, and the workspace
    name likewise. The bytes matter: an em-dash/en-dash or spacing mismatch makes
    the exact-name find MISS and this script would create a DUPLICATE alongside the
    real object. Every dash is therefore written as an explicit `\\u2014` escape
    (the D2 defence) and `_assert_canonical_dashes()` re-checks the codepoints at
    import: a silent normalization by an editor or formatter fails CLOSED at
    import time instead of failing OPEN as a duplicate on the customer's plan.

WHY accessLevel IS CHECKED (the wrong-plan adoption this script must refuse)
    `GET /workspaces?includeAll=true` returns every workspace the token is a MEMBER
    of — including workspaces owned by OTHER accounts and other PLANS. At cutover
    the dedicated ITS identity on the customer's PRODUCTION plan is very plausibly
    still shared into the SANDBOX "ITS — System" workspace (that share is how the
    sandbox was reachable in the first place). Exact-name find then returns exactly
    ONE match, so the invariant-8 duplicate WARN does NOT fire (count == 1), and a
    naive adopt silently creates the four folders inside the SANDBOX workspace — on
    the wrong plan — after which the operator pastes sandbox ids into production
    config. The listing already carries the discriminators: this script prints the
    adopted workspace's `accessLevel` + `permalink`, and treats any accessLevel
    other than OWNER as a HARD STOP on the create-into path (adopting for a pure
    read is fine; creating inside a workspace you do not own is not).

Invariants (blast-radius controls; this runs against a customer's PRODUCTION tenant
containing Evergreen's own live content)
    1. CREATE-ONLY. GET and create-POST only. No PUT, no DELETE, no update of any
       kind, on anything, ever — including objects this script itself created.
    2. EXACT-NAME FIND, ADOPT-DON'T-TOUCH. Find is an exact, case-sensitive string
       match on the canonical name. On a find the script prints "[skip] ... already
       present" and moves on: it never renames, re-parents, re-shares, or writes
       into an adopted object.
    3. SCOPED CREATION. Folders are created only inside the workspace this script
       created-or-adopted by exact name — and, when adopted, only when the token
       OWNS it (see "WHY accessLevel IS CHECKED"). No enumerate-and-act across
       other workspaces; nothing whose name is not in this module's canonical list
       is ever touched.
    4. MINIMAL SET. Exactly the five objects named above. No extras.
    5. IDEMPOTENT NO-OP. A second run prints the same ids and creates nothing.
    6. LIVE-WRITE CONFIRMATION. LIVE by default (family convention), --dry-run to
       preview the complete plan. A `seed_its_config.py`-style y/N prompt gates the
       FIRST live create; declining exits 0 having created nothing. --dry-run never
       prompts.
    7. NO SECRETS IN OUTPUT. Names and ids only — the bearer token is never printed.
    8. DUPLICATE-NAME AMBIGUITY IS LOUD. Smartsheet does not enforce unique
       workspace or folder names, and both the family's `_find_workspace_id` and
       `smartsheet_client.find_folder_by_name_in_workspace` return the FIRST match —
       silently. That is not theoretical: FIVE sheets named "ITS_Errors" exist in
       the live "02 — Logs" folder and only one is the live one. So every find here
       counts ALL exact-name matches and prints a [WARN] naming the ambiguity and
       every matching id when the count is > 1. It still adopts the first (never
       creating a sixth), but never silently.

Failure modes
    - A missing ITS_SMARTSHEET_TOKEN in Keychain, or a token scoped to the
      wrong plan → the Keychain read or the first GET raises; nothing is created.
    - HTTP non-2xx on the workspace GET/POST → `raise_for_status()` propagates and
      main() exits nonzero. Folder calls surface the typed `SmartsheetError`
      hierarchy from `shared.smartsheet_client`.
    - Duplicate-name ambiguity → [WARN], adopt-first, operator reconciles before
      flipping the id (invariant 8). Never auto-resolved.
    - Adopted-but-not-owned workspace (the shared-sandbox case) →
      [WARN] adopted_workspace_not_owned naming the accessLevel + permalink, no
      folders created, exit nonzero.
    - A dash codepoint silently normalized in this module's literals → ValueError
      at import, before any network call (fail closed, never a duplicate).
    - Partial run (create the workspace, then fail on folder 3) → safe: re-running
      adopts what exists and creates only the remainder (invariant 5).

Consumers
    Operator-run, one-time, during the Phase-1 production cutover. Downstream of it:
    `shared/sheet_ids.py` (the five constants in the FLIP BLOCK), and every module
    that reads ITS_Config / writes ITS_Errors / ITS_Quarantine / ITS_Review_Queue /
    ITS_Daemon_Health via those constants.

No §43 successor-remediation runbook entry is needed: this is a one-time operator
migration with no Tier-2-recurring failure mode. It runs under Seth's hand during
cutover, is idempotent, and has no daemon, no schedule, and no runtime consumer.

Cutover sequence (FLIP precedes SEED):
  1. THIS script — note the printed WORKSPACE + four FOLDER ids.
  2. Flip WORKSPACE_SYSTEM + FOLDER_SYSTEM_CONFIG/_LOGS/_QUEUES/_DAEMONS in
     shared/sheet_ids.py (paste the FLIP BLOCK this script prints).
  3. The System-sheet builders (they find-or-create their folder by the SAME name,
     so they are order-independent with this script), then flip their sheet ids.
  4. seed_its_config.py (and the other seeders) — SEED follows FLIP.
  5. OPERATOR: share "ITS — System" to the operator identities ONLY. This workspace
     is machine state, not a customer surface.

Convention: LIVE-write by default; pass --dry-run to preview.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_system_workspace.py --dry-run
    python3 scripts/migrations/build_system_workspace.py

Exit 0 on success, no-op, or an operator-declined confirmation; nonzero on any error,
including the adopted-but-not-owned refusal (which also fires under --dry-run — a
dry run's job is to surface the blocker before cutover day, and it still writes
nothing).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

# Canonical names. The workspace and all four folders use U+2014 EM DASH with a
# single space either side. Verified byte-exact against the live tenant. Written as
# explicit backslash-u escapes (the D2 defence in build_safety_portal_workspace.py) rather
# than raw literals: a raw dash is invisible to review and an editor/formatter that
# silently normalizes it does NOT fail closed — the exact-name find would MISS and
# this script would CREATE a duplicate workspace + four duplicate folders on the
# customer's production plan. `_assert_canonical_dashes()` below re-checks at import.
WORKSPACE_NAME = "ITS \u2014 System"  # renders as: ITS — System

# (folder name, shared/sheet_ids.py constant name) — the MINIMAL SET (invariant 4).
FOLDERS: tuple[tuple[str, str], ...] = (
    ("01 \u2014 Config", "FOLDER_SYSTEM_CONFIG"),    # renders as: 01 — Config
    ("02 \u2014 Logs", "FOLDER_SYSTEM_LOGS"),        # renders as: 02 — Logs
    ("03 \u2014 Queues", "FOLDER_SYSTEM_QUEUES"),    # renders as: 03 — Queues
    ("04 \u2014 Daemons", "FOLDER_SYSTEM_DAEMONS"),  # renders as: 04 — Daemons
)

EM_DASH = "\u2014"
# Every dash-like codepoint that is NOT the canonical one. A normalizer substitutes
# one of these; each would silently miss the find and duplicate the object.
_NON_CANONICAL_DASHES = (
    "\u002d",  # HYPHEN-MINUS
    "\u2010",  # HYPHEN
    "\u2011",  # NON-BREAKING HYPHEN
    "\u2012",  # FIGURE DASH
    "\u2013",  # EN DASH
    "\u2015",  # HORIZONTAL BAR
    "\u2212",  # MINUS SIGN
)


def _assert_canonical_dashes() -> None:
    """Fail CLOSED at import if any canonical name's dash was normalized (F2).

    Mirrors D2's escape discipline with a codepoint check: each canonical name must
    contain exactly ONE U+2014 EM DASH surrounded by single spaces and no other
    dash-like codepoint. Raising here costs one run; NOT raising costs a duplicate
    workspace and four duplicate folders on a customer's production plan, discovered
    only after ids are pasted into shared/sheet_ids.py.
    """
    for name in (WORKSPACE_NAME, *(n for n, _ in FOLDERS)):
        if name.count(EM_DASH) != 1 or f" {EM_DASH} " not in name:
            raise ValueError(
                f"canonical_name_dash_corrupted: {name!r} must contain exactly one "
                "U+2014 EM DASH surrounded by single spaces. A dash was normalized — "
                "restore the \\u2014 escape before running against any tenant."
            )
        for bad in _NON_CANONICAL_DASHES:
            if bad in name:
                raise ValueError(
                    f"canonical_name_dash_corrupted: {name!r} contains U+{ord(bad):04X}, "
                    "not the canonical U+2014 EM DASH. Restore the \\u2014 escape."
                )


_assert_canonical_dashes()


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---- confirmation seam (invariant 6) ------------------------------------
#
# A module-level function, deliberately NOT a --yes flag: the prompt IS the control,
# and a flag would let it be switched off from a shell history line. Tests
# monkeypatch `_confirm`.


def _confirm(prompt: str) -> bool:
    """Ask the operator to authorise live writes. True only on an explicit 'y'.

    STANDUP_NONINTERACTIVE=1 (set ONLY by the standup orchestrator, whose own
    master y/N gate is the documented control for orchestrated runs) auto-
    approves WITHOUT touching stdin — the orchestrator closes stdin, so any
    UNEXPECTED prompt raises EOFError and fails the stage loudly instead of
    being silently fed a 'y'. Standalone runs still prompt: the prompt IS the
    control, and an env var does not sit in shell history the way a --yes flag
    would."""
    if os.environ.get("STANDUP_NONINTERACTIVE") == "1":
        print(f"{prompt} [auto-approved: STANDUP_NONINTERACTIVE]")
        return True
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


class WorkspaceNotOwnedError(RuntimeError):
    """The exact-name workspace exists but the token does not OWN it (rule 1).

    Raised instead of returning an id, so no code path can reach folder creation:
    "adopt for reading" and "create inside" are different privileges, and the
    shared-sandbox-into-production case is indistinguishable from the real thing by
    name alone. Covers BOTH accessLevel != OWNER AND an absent accessLevel — writing
    into a customer PRODUCTION tenant under UNKNOWN ownership is exactly the posture
    that must fail closed.
    """


class DuplicateParentError(RuntimeError):
    """>1 exact-name workspace match — a PARENT this script creates folders inside (rule 2).

    Adopt-first-and-warn is sanctioned for TERMINAL objects only (a leaf). The
    workspace is a container this script then writes into, so a duplicate name FAILS
    CLOSED: it must never guess which of several same-named workspaces to build the
    four folders in. `count` rides to `main()` so the FLIP BLOCK renders the workspace
    line through the `<AMBIGUOUS …>` sentinel instead of a clean, paste-ready id.
    """

    def __init__(self, count: int) -> None:
        super().__init__(f"{count} workspaces named {WORKSPACE_NAME!r}")
        self.count = count


class LiveWriteGate:
    """Prompts ONCE before the first live create, then remembers the answer.

    Invariant 6. `allow()` returns False for the rest of the run if the operator
    declines, so a decline creates nothing at all (not just "nothing more").

    `target_note` carries the adopted workspace's accessLevel + permalink into the
    prompt text (F1): a y/N that names only the workspace NAME is a rubber stamp —
    the name is exactly what is ambiguous across the sandbox and production plans.
    """

    def __init__(self, *, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._answer: bool | None = None
        self.target_note: str = ""

    @property
    def declined(self) -> bool:
        return self._answer is False

    def allow(self, what: str) -> bool:
        """True if a live create may proceed. Never prompts under --dry-run."""
        if self._dry_run:
            return False
        if self._answer is None:
            print(f"\nAbout to make the FIRST live create in {WORKSPACE_NAME!r}: {what}")
            if self.target_note:
                print(f"  Target: {self.target_note}")
                print("  Confirm this is the PRODUCTION plan's workspace, not a sandbox "
                      "workspace shared into this identity — open the permalink if unsure.")
            self._answer = _confirm("Proceed with live creates?")
            if not self._answer:
                print("[skip] Operator declined; nothing was created.")
        return self._answer


# ---- duplicate-aware finders (invariant 8) ------------------------------


def _find_workspaces() -> list[dict[str, Any]]:
    """Return the FULL objects of ALL workspaces named WORKSPACE_NAME (exact match).

    The whole object, not just the id (F1): `GET /workspaces?includeAll=true` lists
    every workspace the token is a MEMBER of — across accounts and plans — and the
    only discriminators between "the production workspace" and "the sandbox
    workspace shared into this identity" are `accessLevel` and `permalink`, which
    an id-only finder throws away. Live-verified response shape: each entry carries
    `id`, `name`, `accessLevel`, `permalink`.
    """
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    data: list[dict[str, Any]] = r.json().get("data", [])
    return [ws for ws in data if ws.get("name") == WORKSPACE_NAME]


def _find_workspace_ids() -> list[int]:
    """Ids of ALL workspaces named WORKSPACE_NAME — the count invariant 8 needs.

    Duplicate-aware replacement for the family's first-match `_find_workspace_id`:
    the caller needs the COUNT, not just a winner.
    """
    return [int(ws["id"]) for ws in _find_workspaces()]


def _find_folder_ids(workspace_id: int, name: str) -> list[int]:
    """Return the ids of ALL top-level folders in `workspace_id` named `name`.

    `smartsheet_client.find_folder_by_name_in_workspace` hides duplicates (first
    match wins, silently), so enumerate the workspace listing ourselves.
    """
    r = requests.get(f"{BASE}/workspaces/{workspace_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [int(f["id"]) for f in r.json().get("folders", []) if f.get("name") == name]


# Constants whose resolved id came from an AMBIGUOUS (>1 exact-name match) find of a
# TERMINAL object (a folder — this script creates nothing inside its folders), mapped
# to the match COUNT. Populated by `_adopt_first`, consumed by the FLIP BLOCK so a
# known-ambiguous id is never rendered as a clean paste-ready integer (the absolute
# FLIP-BLOCK-leak rule). Mirrors D3's `_AMBIGUOUS`. Reset at the top of `main()`.
_AMBIGUOUS: dict[str, int] = {}


def _adopt_first(ids: list[int], kind: str, name: str, constant: str) -> int:
    """Adopt ids[0] of a TERMINAL object, WARNing loudly when ambiguous (rule 4).

    TERMINAL only — a folder, which this script creates nothing inside. Do NOT call
    this for the WORKSPACE: that is a PARENT the script creates folders inside, so a
    duplicate-name workspace fails CLOSED (`DuplicateParentError`), never adopt-first.
    An ambiguous terminal id is recorded in `_AMBIGUOUS` so the FLIP BLOCK renders it
    through the `<AMBIGUOUS …>` sentinel rather than as a clean integer.
    """
    if len(ids) > 1:
        _AMBIGUOUS[constant] = len(ids)
        print(f"[WARN] duplicate_name_ambiguity: {len(ids)} {kind}s are named {name!r} "
              f"(ids={', '.join(str(i) for i in ids)}). Adopting the FIRST ({ids[0]}) and "
              f"creating nothing — but the first match may NOT be the live one. Reconcile "
              f"(identify the live object, delete or rename the rest) BEFORE flipping "
              f"{constant}. The FLIP BLOCK below will NOT print this id.")
    return ids[0]


# ---- ensure steps (create-only, adopt-don't-touch) ----------------------


def ensure_workspace(gate: LiveWriteGate, *, dry_run: bool) -> int | None:
    """Find-or-create the workspace. Returns its id, or None if not created.

    Raises `WorkspaceNotOwnedError` when an existing exact-name workspace is not
    OWNED by this token (F1) — see "WHY accessLevel IS CHECKED" in the module
    docstring. Adoption for reading is fine; creating inside it is not, so the
    refusal happens here, before any folder step can be reached.
    """
    matches = _find_workspaces()
    if matches:
        ids = [int(ws["id"]) for ws in matches]
        if len(ids) > 1:
            # PARENT ambiguity FAILS CLOSED (rule 2): this script creates the four
            # folders inside the workspace, so it must never adopt-first-and-guess
            # which of several same-named workspaces to write into.
            print(f"[WARN] duplicate_parent_ambiguity: {len(ids)} workspaces are named "
                  f"{WORKSPACE_NAME!r} — FAILING CLOSED. This script creates FOLDERS inside "
                  f"the workspace and will not write into a container it cannot uniquely "
                  f"identify. Nothing is created. Reconcile the duplicates (identify the "
                  f"live one, delete or rename the rest) BEFORE flipping WORKSPACE_SYSTEM.")
            for ws in matches:
                print(f"        candidate id={int(ws['id'])} "
                      f"accessLevel={ws.get('accessLevel')} permalink={ws.get('permalink')}")
            raise DuplicateParentError(len(ids))
        adopted = matches[0]
        found = int(adopted["id"])
        access = adopted.get("accessLevel")
        permalink = str(adopted.get("permalink") or "<unknown>")
        print(f"[skip] workspace {WORKSPACE_NAME!r} already present (workspace_id={found}).")
        # Print the discriminators unconditionally: even on the OWNER path the
        # operator should SEE which plan this run is about to write into.
        print(f"[info] adopted workspace accessLevel={access} permalink={permalink}")
        if access is None:
            # Absent accessLevel FAILS CLOSED (rule 1): the endpoint populates the field
            # in practice, so an absent value is anomalous, and writing into a customer
            # PRODUCTION tenant under UNKNOWN ownership is the posture that must fail closed.
            print(f"[WARN] adopted_workspace_not_owned: the workspace named {WORKSPACE_NAME!r} "
                  f"that this token can see (id={found}) reported NO accessLevel, so OWNER "
                  f"access could not be confirmed. permalink={permalink}\n"
                  "       REFUSING to create anything. Open the permalink and verify this is "
                  "the production plan; if the API genuinely omitted accessLevel, escalate "
                  "rather than override.")
            raise WorkspaceNotOwnedError(
                f"workspace {WORKSPACE_NAME!r} (id={found}) accessLevel=<absent> — could not "
                "confirm OWNER access"
            )
        if access != "OWNER":
            print(f"[WARN] adopted_workspace_not_owned: the workspace named "
                  f"{WORKSPACE_NAME!r} that this token can see (id={found}) has "
                  f"accessLevel={access}, not OWNER — it belongs to another account "
                  f"and very likely another PLAN. permalink={permalink}\n"
                  "       This is the SANDBOX-shared-into-production case: the sandbox "
                  "workspace is visible to the production identity because it was shared "
                  "there, exact-name find returns exactly ONE match so the duplicate-name "
                  "WARN does not fire, and creating the four folders here would build them "
                  "on the WRONG PLAN — after which the printed ids would be pasted into "
                  "production config.\n"
                  "       REFUSING to create anything. Resolve first: run as an identity "
                  "that OWNS the production workspace, or un-share the sandbox workspace "
                  "from this identity, then re-run.")
            raise WorkspaceNotOwnedError(
                f"workspace {WORKSPACE_NAME!r} (id={found}) accessLevel={access} != OWNER"
            )
        gate.target_note = (
            f"workspace {WORKSPACE_NAME!r} id={found} accessLevel={access} "
            f"permalink={permalink}"
        )
        return found
    if dry_run:
        print(f"[dry-run] Would create workspace {WORKSPACE_NAME!r} (parent: account root).")
        return None
    if not gate.allow(f"create workspace {WORKSPACE_NAME!r}"):
        return None

    r = requests.post(f"{BASE}/workspaces", headers=_headers(),
                      json={"name": WORKSPACE_NAME}, timeout=30)
    r.raise_for_status()
    new_id = int(r.json()["result"]["id"])
    # §45 re-find-after-create: surface a concurrent-create duplicate. Compare the
    # WHOLE result set, not element 0 — a duplicate can sort either side of us.
    #
    # EMPTY is NOT a duplicate (F3): Smartsheet has a documented create->read
    # propagation window (this repo carries a live-verified 5x~2s readiness probe in
    # shared/job_sheet.py for exactly this), so a read-back that sees nothing yet is
    # lag, and telling the operator to "reconcile a duplicate" would send them
    # hunting for an object that does not exist. Only a DIFFERING non-empty set is a
    # real race.
    after = _find_workspace_ids()
    if not after:
        print(f"[info] workspace_readback_empty: created {new_id} but a name lookup returns "
              "nothing yet — Smartsheet's create->read propagation window, not a duplicate. "
              "The id above is authoritative.")
    elif after != [new_id]:
        print(f"[WARN] workspace_race_duplicate: created {new_id} but a name lookup returns "
              f"{after} — another {WORKSPACE_NAME!r} workspace exists; reconcile (delete the "
              "duplicate) before flipping WORKSPACE_SYSTEM.")
    print(f"[ok] created workspace {WORKSPACE_NAME!r} (workspace_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    WORKSPACE_SYSTEM = {new_id}")
    return new_id


def ensure_folder(workspace_id: int, name: str, constant: str,
                  gate: LiveWriteGate, *, dry_run: bool) -> int | None:
    """Find-or-create one top-level folder in the workspace. Returns its id or None.

    Scoped creation (invariant 3): `workspace_id` is always the workspace this run
    created-or-adopted by exact name.
    """
    existing = _find_folder_ids(workspace_id, name)
    if existing:
        found = _adopt_first(existing, "folder", name, constant)
        print(f"[skip] folder {name!r} already present (folder_id={found}).")
        return found
    if dry_run:
        print(f"[dry-run] Would create folder {name!r} (parent: workspace {workspace_id}).")
        return None
    if not gate.allow(f"create folder {name!r} in workspace {workspace_id}"):
        return None

    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, name)
    # §45 re-find-after-create — whole-set compare, and EMPTY means the create->read
    # propagation window, not a duplicate (same reasoning as ensure_workspace, F3).
    after = _find_folder_ids(workspace_id, name)
    if not after:
        print(f"[info] folder_readback_empty: created {new_id} but a name lookup in "
              f"workspace {workspace_id} returns nothing yet — Smartsheet's create->read "
              "propagation window, not a duplicate. The id above is authoritative.")
    elif after != [new_id]:
        print(f"[WARN] folder_race_duplicate: created {new_id} but a name lookup returns "
              f"{after} — another folder named {name!r} exists in workspace {workspace_id}; "
              f"reconcile before flipping {constant}.")
    print(f"[ok] created folder {name!r} (folder_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    {constant} = {new_id}")
    return new_id


def _print_flip_block(workspace_id: int | None, folder_ids: dict[str, int | None],
                      *, workspace_ambiguous_count: int | None = None) -> None:
    """Emit ready-to-paste shared/sheet_ids.py lines (alignment matches the module).

    A workspace or folder id that is unresolved OR untrustworthy (duplicate-name
    ambiguous) renders through a sentinel, never as a clean integer — the absolute
    FLIP-BLOCK-leak rule. `workspace_ambiguous_count` renders the workspace line as the
    `<AMBIGUOUS …>` sentinel (the fail-closed duplicate-parent case); ambiguous FOLDER
    ids are withheld the same way via `_AMBIGUOUS`.
    """
    print("\n=== FLIP BLOCK ===")
    print("Paste into shared/sheet_ids.py (replacing the existing lines):\n")
    if workspace_ambiguous_count is not None:
        ws = f"<AMBIGUOUS — {workspace_ambiguous_count} matches, RECONCILE BEFORE FLIPPING>"
    elif workspace_id is None:
        ws = "<unresolved>"
    else:
        ws = str(workspace_id)
    print(f"WORKSPACE_SYSTEM       = {ws}   # ITS — System (operator-only)")
    print()
    for name, constant in FOLDERS:
        value = folder_ids.get(constant)
        matches = _AMBIGUOUS.get(constant)
        if matches:
            rendered = f"<AMBIGUOUS — {matches} matches, RECONCILE BEFORE FLIPPING>"
        elif value is None:
            rendered = "<unresolved>"
        else:
            rendered = str(value)
        print(f"{constant:<21} = {rendered:<16} # {name}")
    incomplete = (workspace_id is None or workspace_ambiguous_count is not None
                  or any(v is None for v in folder_ids.values()) or bool(_AMBIGUOUS))
    if incomplete:
        print("\n[WARN] flip_block_incomplete: one or more ids are <unresolved> or "
              "<AMBIGUOUS> (dry-run, declined confirmation, duplicate-name ambiguity, or a "
              "partial run). Re-run live and paste the complete block — never flip a "
              "placeholder or an ambiguous id.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the ITS — System workspace + its four operator-only folders (D1)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    print(f"[info] Workspace = {WORKSPACE_NAME!r}")
    print(f"[info] Folders   = {', '.join(repr(n) for n, _ in FOLDERS)}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print("[info] Create-only: this script issues GET + create-POST only — no update, "
          "no delete, no re-share, on anything.\n")

    gate = LiveWriteGate(dry_run=args.dry_run)
    _AMBIGUOUS.clear()
    try:
        workspace_id = ensure_workspace(gate, dry_run=args.dry_run)
    except WorkspaceNotOwnedError as exc:
        # Rule 1 hard stop. The WARN naming the accessLevel + permalink is already
        # printed; nothing was created, no FLIP BLOCK is emitted (a not-owned/sandbox id
        # must never reach the operator's clipboard as a clean paste line), and the exit
        # is nonzero so the cutover checklist stops here rather than continuing.
        print(f"\n[abort] {exc}. Nothing was created. No FLIP BLOCK is emitted.")
        return 1
    except DuplicateParentError as exc:
        # Rule 2 hard stop. Unlike the not-owned case, the FLIP BLOCK IS printed — but
        # with the workspace rendered as the `<AMBIGUOUS …>` sentinel (never a clean id)
        # so the operator sees exactly what to reconcile before flipping WORKSPACE_SYSTEM.
        print(f"\n[abort] duplicate_parent_ambiguity: {exc.count} workspaces named "
              f"{WORKSPACE_NAME!r}. Nothing was created.")
        _print_flip_block(None, {c: None for _, c in FOLDERS},
                          workspace_ambiguous_count=exc.count)
        return 1

    folder_ids: dict[str, int | None] = {constant: None for _, constant in FOLDERS}
    if workspace_id is not None:
        for name, constant in FOLDERS:
            if gate.declined:
                break
            folder_ids[constant] = ensure_folder(
                workspace_id, name, constant, gate, dry_run=args.dry_run
            )
    elif args.dry_run:
        for name, _constant in FOLDERS:
            print(f"[dry-run] Would create folder {name!r} (parent: the new "
                  f"{WORKSPACE_NAME!r} workspace).")

    print("\nSummary:")
    print(f"  WORKSPACE_SYSTEM:      id={workspace_id}")
    for name, constant in FOLDERS:
        print(f"  {constant + ':':<22} id={folder_ids[constant]}  ({name})")

    _print_flip_block(workspace_id, folder_ids)

    if gate.declined:
        print("\nDeclined at the confirmation prompt — nothing was created. Re-run to proceed.")
    else:
        print("\nNext: flip the five ids above in shared/sheet_ids.py, then run the "
              "System-sheet builders, then the seeders (FLIP precedes SEED).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
