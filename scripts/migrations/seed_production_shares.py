"""Apply the CL-11/CL-37 production F22 approver workspace shares from the manifest.

Mechanizes cutover checklist item CL-11 (+ CL-37's subcontracts leg): F22 approval
authority IS each send-bearing workspace's individual USER share list
(`smartsheet_client.list_workspace_share_emails`; GROUP shares do NOT count — a
group-only share yields an EMPTY authorized set that silently fail-closes every
send). The approver identities live in the checked-in DATA file
`production_shares_manifest.json` (operator-reviewed; per-workspace narrowing and
access levels are data edits, never code changes) — this script only APPLIES it.

Guards (each one bites; tests/test_production_shares.py proves it):

- **Manifest schema validation.** Every approver email must end with exactly
  ``"@" + production_domain`` and the manifest's domain must equal the pinned
  ``EXPECTED_PRODUCTION_DOMAIN`` — the mechanical Ezra-typo guard (the original
  contact sheet carried a ``renwables`` typo; a non-matching account email
  fail-closes that approver SILENTLY at send time). Nonempty approver sets,
  known access levels, and workspace constants that really exist in
  ``shared.sheet_ids`` are refused otherwise.
- **PLAN mode is the default and NEVER writes.** It resolves each workspace live
  BY NAME (exact-name; ambiguity refused — the standup `_stage_restore_shares`
  pattern), then reports to-add / already-present / mirror-account residue /
  GROUP shares. Residue is reported LOUDLY with the manual removal instruction —
  **this tool NEVER deletes or unshares anything** (there is no DELETE call in
  this module; a test asserts that of the source).
- **``--commit`` is gated by a bare y/N ``input()`` prompt** (EOF = decline).
  The prompt IS the control — there is deliberately no bypass flag.
- **OWNER-access check per workspace before any write** — refusing to write
  shares into a workspace this token does not own (the standup pattern).
- **ADD-only** via ``POST /workspaces/{id}/shares?sendEmail=false``. A
  404/invalid-user response WARNs LOUDLY naming the account (the account must
  exist as a real Smartsheet user first — pending admin asks E1/E2) and never
  blocks the other adds. Transient 429/5xx ride ``_rest_retry`` and PROPAGATE
  on exhaustion — never the WARN path (a silently narrower F22 approver set is
  the failure mode this family refuses); re-runs are idempotent because
  already-present emails are never re-posted.

Auth: ``ITS_SMARTSHEET_TOKEN`` from macOS Keychain (the standup raw-REST pattern).

Exit codes: 0 = plan fully resolved / all adds applied; nonzero = an unresolved
workspace, a declined gate, a non-OWNER refusal with pending adds, or a failed add.

Run from ``~/its`` with the venv activated::

    python3 scripts/migrations/seed_production_shares.py            # PLAN (read-only)
    python3 scripts/migrations/seed_production_shares.py --commit   # gated ADD-only apply
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Family-lib sibling (this dir is sys.path[0] when run as a script; tests insert
# it explicitly). Transient 429/5xx retried; exhaustion PROPAGATES — an F22
# approver add must never be silently dropped behind a rate-limit blip (the
# same polarity as standup's share restore).
from _rest_retry import request_with_retry  # noqa: E402

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
MANIFEST_PATH = pathlib.Path(__file__).with_name("production_shares_manifest.json")

# The production domain PINNED IN CODE as a bare domain (no local part, so the CI
# production-identity guard does not match — identities themselves live ONLY in the
# JSON manifest, which is out of that scan's .py/.ts/.tsx scope by design). This is
# half of the mechanical Ezra-typo guard: the manifest must declare exactly this
# domain, and every approver email must end with "@" + it.
EXPECTED_PRODUCTION_DOMAIN = "evergreenrenewables.com"

# The mirror domain is pinned too (adversarial review 2026-07-23): the residue
# checks — the seeder's [RESIDUE] print AND VC-10's mirror-residue FAIL — key
# entirely off manifest["mirror_domain"], and a leftover mirror USER share on a
# production send-bearing workspace GRANTS that account live F22 authority. A
# manifest typo here would silently blind the one control that catches it.
# verify_cutover derives the same pin from SANDBOX_DOMAIN_MARKER (single source
# of the marker string); this constant must stay in lock-step.
EXPECTED_MIRROR_DOMAIN = "evergreenmirror.com"

# Workspace-share access levels Smartsheet accepts on POST /workspaces/{id}/shares.
# OWNER is deliberately absent — ownership is not grantable via a share add.
KNOWN_ACCESS_LEVELS = frozenset({"VIEWER", "COMMENTER", "EDITOR", "EDITOR_SHARE", "ADMIN"})


class ManifestError(ValueError):
    """The manifest failed schema validation — refuse before any live call."""


@dataclasses.dataclass(frozen=True)
class WorkspacePlan:
    """The read-only diff for one manifest workspace against the live tenant."""

    name: str
    constant: str
    live_id: int | None                     # None = name did not resolve uniquely
    access_level: str | None                # this token's access on the workspace
    to_add: tuple[tuple[str, str], ...]     # (email, access_level) pairs to POST
    already_present: tuple[str, ...]
    mirror_residue: tuple[str, ...]         # live USER shares on the mirror domain
    group_shares: tuple[str, ...]           # display names of live GROUP shares
    refusal: str | None                     # why this workspace is untouchable


def load_manifest(path: pathlib.Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load + schema-validate the manifest; raise ManifestError on any violation.

    The domain check is EXACT-suffix (``email.endswith("@" + production_domain)``)
    against the pinned ``EXPECTED_PRODUCTION_DOMAIN`` — this is the mechanical
    guard that refuses the documented ``renwables`` typo before it can silently
    fail-close an approver at send time.
    """
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot load manifest {path}: {exc}") from exc

    for key in ("production_domain", "mirror_domain", "workspaces"):
        if key not in manifest:
            raise ManifestError(f"manifest missing required key {key!r}")
    domain = str(manifest["production_domain"])
    if domain != EXPECTED_PRODUCTION_DOMAIN:
        raise ManifestError(
            f"production_domain {domain!r} != pinned {EXPECTED_PRODUCTION_DOMAIN!r} "
            "— refusing (the Ezra-typo guard pins the reviewed domain in code)"
        )
    mirror = str(manifest["mirror_domain"]).strip().lower()
    if mirror != EXPECTED_MIRROR_DOMAIN:
        raise ManifestError(
            f"mirror_domain {mirror!r} != pinned {EXPECTED_MIRROR_DOMAIN!r} — refusing "
            "(a typo here silently BLINDS both mirror-residue checks; the pin is the "
            "same guard pattern as EXPECTED_PRODUCTION_DOMAIN)"
        )
    workspaces = manifest["workspaces"]
    if not isinstance(workspaces, list) or not workspaces:
        raise ManifestError("workspaces must be a nonempty list")

    for ws in workspaces:
        constant = str(ws.get("constant") or "")
        name = str(ws.get("name") or "")
        if not constant or not hasattr(sheet_ids, constant):
            raise ManifestError(
                f"workspace constant {constant!r} does not exist in shared.sheet_ids"
            )
        if not name.strip():
            raise ManifestError(f"{constant}: workspace name must be nonempty")
        approvers = ws.get("approvers")
        if not isinstance(approvers, list) or not approvers:
            raise ManifestError(f"{constant}: approvers must be a nonempty list")
        seen: set[str] = set()
        for approver in approvers:
            email = str(approver.get("email") or "")
            if email != email.strip().lower():
                raise ManifestError(
                    f"{constant}: email {email!r} must be lowercase with no surrounding "
                    "whitespace (Smartsheet account emails compare lowercased)"
                )
            if email.count("@") != 1 or not email.endswith("@" + domain) or email.startswith("@"):
                raise ManifestError(
                    f"{constant}: email {email!r} is not a local-part @ {domain} address "
                    "— refusing (this is the mechanical Ezra-typo guard: a non-matching "
                    "account email would silently fail-close that approver at send time)"
                )
            if email in seen:
                raise ManifestError(f"{constant}: duplicate approver email {email!r}")
            seen.add(email)
            if not str(approver.get("person") or "").strip():
                raise ManifestError(f"{constant}: approver {email!r} missing person")
            if not str(approver.get("role") or "").strip():
                raise ManifestError(f"{constant}: approver {email!r} missing role")
            level = str(approver.get("access_level") or "")
            if level not in KNOWN_ACCESS_LEVELS:
                raise ManifestError(
                    f"{constant}: approver {email!r} access_level {level!r} not in "
                    f"{sorted(KNOWN_ACCESS_LEVELS)}"
                )
    return manifest


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _confirm(prompt: str) -> bool:
    """The commit gate: bare y/N ``input()``; EOF counts as a decline.

    The prompt IS the control (no bypass flag by design) — a piped or
    non-interactive run cannot approve a live share write.
    """
    try:
        return input(f"{prompt} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def _live_workspaces() -> list[dict[str, Any]]:
    r = request_with_retry("get", f"{BASE}/workspaces?includeAll=true",
                           headers=_headers(), timeout=30)
    data: list[dict[str, Any]] = r.json().get("data", [])
    return data


def _fetch_shares(workspace_id: int) -> list[dict[str, Any]]:
    r = request_with_retry(
        "get", f"{BASE}/workspaces/{workspace_id}/shares?includeAll=true",
        headers=_headers(), timeout=30,
    )
    data: list[dict[str, Any]] = r.json().get("data", [])
    return data


def build_plans(manifest: dict[str, Any]) -> list[WorkspacePlan]:
    """Resolve each manifest workspace live BY NAME and diff its share list.

    Exact-name resolution with ambiguity refused (0 or >1 live workspaces under
    the manifest name → a refusal, no share fetch) — the standup
    ``_stage_restore_shares`` pattern. Read-only: no write happens here.
    """
    mirror_suffix = "@" + str(manifest["mirror_domain"]).strip().lower()
    live_by_name: dict[str, list[dict[str, Any]]] = {}
    for ws in _live_workspaces():
        live_by_name.setdefault(str(ws.get("name")), []).append(ws)

    plans: list[WorkspacePlan] = []
    for ws in manifest["workspaces"]:
        name = str(ws["name"])
        constant = str(ws["constant"])
        targets = live_by_name.get(name, [])
        if len(targets) != 1:
            plans.append(WorkspacePlan(
                name=name, constant=constant, live_id=None, access_level=None,
                to_add=(), already_present=(), mirror_residue=(), group_shares=(),
                refusal=(
                    f"{len(targets)} live workspaces named {name!r} — refusing "
                    "(exact-unique-name resolution required; the standup pattern)"
                ),
            ))
            continue
        target = targets[0]
        live_id = int(target["id"])
        shares = _fetch_shares(live_id)
        user_emails = {
            str(s["email"]).strip().lower()
            for s in shares if isinstance(s, dict) and s.get("email")
        }
        groups = tuple(
            str(s.get("name") or f"groupId={s.get('groupId')}")
            for s in shares if isinstance(s, dict) and not s.get("email")
        )
        wanted = [(str(a["email"]), str(a["access_level"])) for a in ws["approvers"]]
        plans.append(WorkspacePlan(
            name=name,
            constant=constant,
            live_id=live_id,
            access_level=str(target.get("accessLevel")),
            to_add=tuple((e, level) for e, level in wanted if e not in user_emails),
            already_present=tuple(e for e, _ in wanted if e in user_emails),
            mirror_residue=tuple(sorted(e for e in user_emails if e.endswith(mirror_suffix))),
            group_shares=groups,
            refusal=None,
        ))
    return plans


def print_plan(plans: list[WorkspacePlan]) -> None:
    """Report every plan LOUDLY — residue and group shares are never silent."""
    for plan in plans:
        print(f"\n== {plan.name} ({plan.constant})")
        if plan.refusal:
            print(f"   [REFUSED] {plan.refusal}")
            continue
        constant_id = int(getattr(sheet_ids, plan.constant, 0) or 0)
        drift = ""
        if constant_id and constant_id != plan.live_id:
            drift = (f"  [WARN] sheet_ids.{plan.constant}={constant_id} != live id "
                     f"{plan.live_id} (stale constant? regen pending?)")
        print(f"   live id {plan.live_id}, this token's access {plan.access_level}{drift}")
        if plan.access_level != "OWNER":
            print(f"   [WARN] not OWNER — --commit will REFUSE to write into {plan.name!r}")
        for email in plan.already_present:
            print(f"   present : {email}")
        for email, level in plan.to_add:
            print(f"   to add  : {email} ({level})")
        if not plan.to_add:
            print("   to add  : (none — manifest already satisfied)")
        for email in plan.mirror_residue:
            print(
                f"   [RESIDUE] mirror-account USER share {email} — this tool NEVER "
                f"unshares; remove BY HAND in Smartsheet: workspace {plan.name!r} "
                f"> Share > remove {email} (CL-11 lists the mirror validation accounts)"
            )
        for group in plan.group_shares:
            print(
                f"   [GROUP]  group share {group!r} does NOT count toward F22 — "
                "group members hold NO approval authority; approvers need "
                "individual USER shares"
            )


def apply_plans(plans: list[WorkspacePlan]) -> tuple[int, int]:
    """ADD-only apply: POST each to-add share with sendEmail=false.

    Per-workspace OWNER refusal (never write into a workspace this token does
    not own); per-add WARN-loud-and-continue on any non-200 (a 404/invalid-user
    means the account does not exist as a Smartsheet user yet — pending admin
    asks E1/E2). Returns (added, failed) where failed counts BOTH failed POSTs
    and adds skipped by a refusal.
    """
    added = 0
    failed = 0
    for plan in plans:
        if plan.refusal:
            failed += len(plan.to_add)
            continue
        if not plan.to_add:
            continue
        if plan.access_level != "OWNER":
            print(
                f"  [WARN] shares skipped: workspace {plan.name!r} accessLevel="
                f"{plan.access_level} != OWNER — refusing to write shares into a "
                "workspace this token does not own."
            )
            failed += len(plan.to_add)
            continue
        for email, level in plan.to_add:
            # raise_for_status=False: a permanent 4xx (invalid user, dup) keeps
            # the loud-but-non-fatal WARN below; a transient 429/5xx retries
            # inside the helper and PROPAGATES on exhaustion — it must never
            # ride the WARN path and silently narrow an F22 approver set.
            r = request_with_retry(
                "post", f"{BASE}/workspaces/{plan.live_id}/shares?sendEmail=false",
                headers=_headers(),
                json=[{"email": email, "accessLevel": level}],
                timeout=30, raise_for_status=False,
            )
            if r.status_code == 200:
                added += 1
                print(f"  [ok] shared {plan.name!r} with {email} ({level})")
                continue
            failed += 1
            hint = ""
            if r.status_code == 404:
                hint = (
                    " — the account must exist as a real Smartsheet USER first "
                    "(pending admin asks E1/E2); create/verify it, then re-run"
                )
            print(
                f"  [WARN] share add FAILED for {email} ({level}) on {plan.name!r}: "
                f"HTTP {r.status_code} {r.text[:200]}{hint}"
            )
    return added, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CL-11 F22 approver-share applier — PLAN by default, gated ADD-only "
                    "--commit; NEVER deletes/unshares.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="apply the to-add share list (y/N gated; ADD-only, sendEmail=false)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(MANIFEST_PATH)
    except ManifestError as exc:
        print(f"[abort] manifest invalid: {exc}")
        return 2

    plans = build_plans(manifest)
    print_plan(plans)

    unresolved = [p for p in plans if p.refusal]
    total_to_add = sum(len(p.to_add) for p in plans)

    if not args.commit:
        print(
            f"\nPLAN ONLY — no writes performed. {total_to_add} share add(s) pending "
            f"across {len(plans)} workspace(s). Re-run with --commit to apply."
        )
        return 1 if unresolved else 0

    if total_to_add == 0:
        print("\nNothing to add — manifest already satisfied everywhere it resolved.")
        return 1 if unresolved else 0

    if not _confirm(
        f"\nApply {total_to_add} USER share add(s) across "
        f"{sum(1 for p in plans if p.to_add)} workspace(s) (sendEmail=false)?"
    ):
        print("[abort] declined — nothing written.")
        return 1

    added, failed = apply_plans(plans)
    print(f"\nseed_production_shares: {added} added, {failed} failed/refused.")
    return 0 if failed == 0 and not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
