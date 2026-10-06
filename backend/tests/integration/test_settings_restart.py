"""Real local restart on disposable libraries, with no models or user service involved."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from ndr.config import Settings
from ndr.portable import LibraryLock

STARTUP_TIMEOUT_SECONDS = 180


def _wait_ready(
    client,
    process,
    port,
    log_path,
    *,
    timeout=STARTUP_TIMEOUT_SECONDS,
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    """Wait for this process, never restart it or accept a merely open port."""
    started = monotonic()
    deadline = started + timeout
    last_health = "尚未收到健康响应"
    while monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            pytest.fail(
                f"隔离服务已退出，code={returncode}，port={port}\n"
                + log_path.read_text(encoding="utf-8")
            )
        try:
            response = client.get(f"http://127.0.0.1:{port}/api/health")
            last_health = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code == 200 and response.json()["database"]["state"] == "READY":
                return
        except httpx.TransportError as exc:
            last_health = f"{type(exc).__name__}: {exc}"
        sleep(0.1)
    pytest.fail(
        f"隔离服务启动超时：port={port}，等待={monotonic() - started:.1f}s，"
        f"上限={timeout}s，最近健康状态={last_health}\n" + log_path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("outcome", ["slow-ready", "never-ready", "exited"])
def test_startup_wait_is_bounded_and_requires_database_ready(tmp_path, outcome):
    from types import SimpleNamespace

    elapsed = [0.0]
    calls = []
    log_path = tmp_path / "wait.log"
    log_path.write_text("migration in progress", encoding="utf-8")

    def sleep(seconds):
        elapsed[0] += seconds

    def get(url):
        calls.append(url)
        state = "READY" if outcome == "slow-ready" and elapsed[0] >= 60 else "MIGRATING"
        return httpx.Response(200, json={"database": {"state": state}})

    kwargs = dict(monotonic=lambda: elapsed[0], sleep=sleep)
    client = SimpleNamespace(get=get)
    process = SimpleNamespace(poll=lambda: 7 if outcome == "exited" else None)
    if outcome == "slow-ready":
        _wait_ready(client, process, 1234, log_path, **kwargs)
        assert 60 <= elapsed[0] < STARTUP_TIMEOUT_SECONDS
        assert calls
    else:
        reason = "服务已退出.*code=7" if outcome == "exited" else "启动超时.*MIGRATING"
        with pytest.raises(pytest.fail.Exception, match=reason) as error:
            _wait_ready(client, process, 1234, log_path, **kwargs)
        assert "migration in progress" in str(error.value)
        if outcome == "exited":
            assert elapsed[0] == 0 and calls == []
        else:
            assert STARTUP_TIMEOUT_SECONDS <= elapsed[0] < STARTUP_TIMEOUT_SECONDS + 0.2


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.parametrize("entrypoint", ["portable", "module"])
def test_save_and_restart_changes_port_and_library_after_releasing_old_resources(
    tmp_path, entrypoint
):
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    library = tmp_path / "old-library"
    new_library = tmp_path / "new-library"
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>原创测试页面</html>", encoding="utf-8")
    old_port, new_port = _port(), _port()
    while new_port == old_port:
        new_port = _port()
    config_path = bootstrap / "application-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "data_dir": str(library),
                "static_dir": str(static),
                "port": old_port,
                "auto_migrate": True,
                "credential_backend": "session",
            }
        ),
        encoding="utf-8",
    )
    script = f"""
from pathlib import Path
import sys
from ndr import config
config.default_data_dir = lambda: Path({str(bootstrap)!r})
if {entrypoint!r} == 'portable':
    from ndr import portable
    original = portable.portable_settings
    def configured(**kwargs):
        result = original(**kwargs)
        result.static_dir = Path({str(static)!r})
        return result
    portable.portable_settings = configured
    sys.argv = ['test-launcher', '--no-browser']
    raise SystemExit(portable.main())
else:
    from ndr.__main__ import main
    main()
"""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("NDR_")}
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    with (
        (tmp_path / "server.log").open("wb") as log,
        httpx.Client(trust_env=False, timeout=1) as client,
    ):
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:

            def wait_ready(port):
                _wait_ready(client, process, port, tmp_path / "server.log")

            wait_ready(old_port)
            info = client.get(f"http://127.0.0.1:{old_port}/api/settings/application").json()[
                "data"
            ]
            assert info["restart_blocked_reason"] is None
            response = client.post(
                f"http://127.0.0.1:{old_port}/api/settings/application/save-and-restart",
                json={
                    "revision": info["revision"],
                    "values": {
                        "port": new_port,
                        "data_dir": str(new_library),
                        "llm_timeout_seconds": 180,
                    },
                },
            )
            assert response.status_code == 200, response.text
            wait_ready(new_port)
            current = client.get(f"http://127.0.0.1:{new_port}/api/settings/application").json()[
                "data"
            ]
            values = {field["key"]: field for field in current["fields"]}
            assert values["port"]["current_value"] == new_port
            assert values["data_dir"]["current_value"] == str(new_library)
            assert values["llm_timeout_seconds"]["current_value"] == 180
            assert values["port"]["locked_reason"] is None
            assert current["restart_required"] == []
            assert current["config_path"] == str(config_path)
            assert (library / "ndr.sqlite3").exists() and (new_library / "ndr.sqlite3").exists()
            with socket.socket() as released:
                released.bind(("127.0.0.1", old_port))
            if entrypoint == "portable":
                with LibraryLock(library):
                    pass
            assert Settings.model_fields  # No model calls or paid credentials were configured.
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
