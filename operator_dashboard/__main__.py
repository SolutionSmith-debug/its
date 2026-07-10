"""Run the operator dashboard: `python -m operator_dashboard`.

Binds 127.0.0.1:8484 (localhost only). Expose over Tailscale with
`tailscale serve 8484` — never bind a public interface (D1-1 has no auth;
auth lands with the ACT surface in D1-2).
"""
from __future__ import annotations

import uvicorn

from operator_dashboard.app import create_app
from operator_dashboard.config import HOST, PORT


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
