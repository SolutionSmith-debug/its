"""Run the operator dashboard: `python -m operator_dashboard`.

Binds 127.0.0.1:8484 (localhost only). Expose over Tailscale with
`tailscale serve 8484` — never bind a public interface. Read routes are
loginless; every mutating ACT route is PIN-gated (`auth.py`, D1-2/D1-3 —
constant-time compare, fail-closed until `ITS_OPERATOR_PIN` is provisioned).
"""
from __future__ import annotations

import uvicorn

from operator_dashboard.app import create_app
from operator_dashboard.config import HOST, PORT


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
