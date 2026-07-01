"""Send-free invariant for the Safety Portal Cloudflare Worker (Invariant 1, P6 DoD).

The Worker is the send-free TypeScript boundary: it queues submissions in D1 and serves reads,
but performs ZERO external transmission — no email, no outbound HTTP. The one legitimate `fetch(`
is `c.env.ASSETS.fetch(...)`, the STATIC-ASSET binding that serves the built SPA (a local
binding call, not network egress).

This test greps every `safety_portal/worker/**/*.ts` for a `fetch(` call and FAILS on any hit
that is not `ASSETS.fetch(`. It codifies the invariant that P6's read-only rollup route upholds:
an outbound `fetch(` (or a `.fetch(` on any other binding) added to the Worker — the classic way
a "read-only" route quietly becomes an exfiltration/send path — fails at CI time, before it ships.

Run with: pytest -q tests/test_worker_send_free.py
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "safety_portal" / "worker"

# `fetch(` as a call, NOT preceded by an identifier char (so `refetch(` / `prefetch(` don't match).
_FETCH_RE = re.compile(r"(?<![A-Za-z0-9_])fetch\s*\(")
# The ONE allowed form: the static-asset binding `ASSETS.fetch(` (SPA serving; a local binding).
_ALLOWED_RE = re.compile(r"ASSETS\.fetch\s*\(")


def test_worker_has_no_outbound_fetch() -> None:
    assert WORKER_DIR.is_dir(), f"missing worker dir: {WORKER_DIR}"
    violations: list[str] = []
    for path in sorted(WORKER_DIR.rglob("*.ts")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            # Strip the allowed ASSETS.fetch( form, then flag any remaining fetch( on the line.
            residual = _ALLOWED_RE.sub("", line)
            if _FETCH_RE.search(residual):
                violations.append(f"  {rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Send-free invariant violation (Invariant 1): the Safety Portal Worker must make NO "
        "outbound fetch — the only allowed `fetch(` is `c.env.ASSETS.fetch(...)` (static-asset "
        "serving). Found:\n" + "\n".join(violations)
        + "\n\nRoute any Mac-side control-plane call through the audited shared/portal_client.py "
        "on the daemon; the Worker itself never transmits."
    )
