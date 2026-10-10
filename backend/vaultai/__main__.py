"""python -m vaultai: serve the desktop app on the configured loopback address."""

from __future__ import annotations

import uvicorn

from vaultai.app import create_app
from vaultai.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level="info",
        proxy_headers=False, server_header=False,
    )


if __name__ == "__main__":
    main()
