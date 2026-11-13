"""`uv run --project backend python -m ndr` 入口。"""

from __future__ import annotations

import sys

import uvicorn

from .config import get_settings
from .diagnostics import diagnostic_scope, log_failure, start_diagnostics, stop_diagnostics
from .runtime_logging import configure_server_logging, startup_failure_message


def main() -> None:
    while True:
        try:
            settings = get_settings()
        except Exception as exc:
            print(startup_failure_message(exc, diagnostics_available=False,
                                          stage="configuration"), file=sys.stderr)
            raise SystemExit(1) from None
        with diagnostic_scope(settings.log_dir):
            handler = start_diagnostics(settings.log_dir)
            try:
                from .app import create_app

                configure_server_logging()
                app = create_app(settings)
                server = uvicorn.Server(
                    uvicorn.Config(
                        app, host=settings.host, port=settings.port,
                        log_level="info", log_config=None, access_log=False,
                    )
                )
                restart = False

                def request_restart(current_server=server) -> None:
                    nonlocal restart
                    restart = True
                    current_server.should_exit = True

                app.state.request_restart = request_restart
                server.run()
            except Exception as exc:
                log_failure("server_startup", None, exc)
                print(startup_failure_message(exc, diagnostics_available=handler is not None,
                                              stage="launch"), file=sys.stderr)
                raise SystemExit(1) from None
            finally:
                stop_diagnostics(handler)
        if not restart:
            return
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
