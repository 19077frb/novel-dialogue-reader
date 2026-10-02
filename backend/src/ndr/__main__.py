"""`uv run --project backend python -m ndr` 入口。"""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    from .app import create_app

    while True:
        settings = get_settings()
        app = create_app(settings)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                log_level="info",
            )
        )
        restart = False

        def request_restart(current_server=server) -> None:
            nonlocal restart
            restart = True
            current_server.should_exit = True

        app.state.request_restart = request_restart
        server.run()
        if not restart:
            return
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
