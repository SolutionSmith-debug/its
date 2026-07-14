"""Shared primitives for operator-dashboard data sources.

Every panel is a `DataSource`. `DataSource.fetch()` wraps the concrete
`_fetch()` so ANY failure — a missing file, an unreadable marker, a dead
Smartsheet read, a failed module import — degrades to an 'unavailable' panel
instead of crashing the request. That is the D1-1 fail-soft contract:
read-only, never crash.

All displayed text is routed through `clean()`, which redacts secret/PII
shapes (shared.redact) and bounds length; Jinja autoescape then neutralizes
any HTML at render time. Together they make an adversarial Smartsheet cell or
a raw local log line render inert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from shared.redact import redact

SEV_OK = "ok"
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_ERROR = "error"
SEV_UNAVAILABLE = "unavailable"

_SEV_RANK = {SEV_OK: 0, SEV_INFO: 1, SEV_WARN: 2, SEV_ERROR: 3}

MAX_CELL_CHARS = 800


def worst_sev(a: str, b: str) -> str:
    """Return the higher-severity of two row-level severities."""
    return a if _SEV_RANK.get(a, 1) >= _SEV_RANK.get(b, 1) else b


def clean(value: object, *, max_chars: int = MAX_CELL_CHARS) -> str:
    """Coerce any value to a redacted, length-bounded display string.

    Order matters: redact (mask secret/PII shapes) THEN truncate. Jinja
    autoescape handles HTML at render, so a `<script>`-shaped value renders
    inert; redact() additionally strips tokens/keys/emails so the raw local
    log (intentionally un-redacted on disk, §54) never leaks a secret onto
    this new egress surface.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = redact(text)
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


def fmt_timedelta(td: timedelta) -> str:
    """Compact human duration, e.g. '3d 4h', '12m 5s', '45s'."""
    secs = int(td.total_seconds())
    if secs < 0:
        return "future"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, s = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    if mins:
        return f"{mins}m {s}s"
    return f"{s}s"


@dataclass
class PanelResult:
    """The rendered state of one panel, handed to the template layer."""

    panel_id: str
    title: str
    available: bool = True
    unavailable_reason: str = ""
    summary: str = ""
    severity: str = SEV_INFO
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)


class DataSource:
    """One read-only panel. Subclasses set `panel_id`/`title` and `_fetch()`."""

    panel_id: str = ""
    title: str = ""

    def _fetch(self, detail: bool = False) -> PanelResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def fetch(self, detail: bool = False) -> PanelResult:
        # detail=True is the drill-down (`/view/{panel_id}`) full-page render: the
        # capped panels (errors / logs / audit) return MORE rows; the rest, which
        # already show everything, ignore it. Passed as a param (not shared state)
        # so a concurrent htmx panel-poll and a detail view never race.
        try:
            return self._fetch(detail)
        except Exception as exc:  # fail-soft: a panel never crashes the page
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                available=False,
                unavailable_reason=clean(f"{type(exc).__name__}: {exc}") or "unavailable",
                severity=SEV_UNAVAILABLE,
            )
