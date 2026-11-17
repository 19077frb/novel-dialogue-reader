"""Real CMD launchers on copied source and disposable Windows libraries."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import venv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows CMD/console contract")
ROOT = Path(__file__).resolve().parents[3]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def hidden_console():
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}


@pytest.fixture()
def launcher(tmp_path):
    if not shutil.which("node") or not shutil.which("npm"):
        pytest.skip("Source launchers require Node.js and npm")
    repo = tmp_path / "CMD 书库 & 空格!"
    repo.mkdir()
    for name in ("start.bat", "stop.bat"):
        shutil.copy2(ROOT / name, repo / name)
    shutil.copytree(ROOT / "backend/src", repo / "backend/src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "backend/migrations", repo / "backend/migrations", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "backend/alembic.ini", repo / "backend/alembic.ini")
    venv.EnvBuilder(with_pip=False).create(repo / "backend/.venv")
    frontend = repo / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "src").mkdir()
    (frontend / "node_modules").mkdir()
    (frontend / "src/main.js").write_text("// original test fixture", encoding="utf-8")
    (frontend / "package.json").write_text(json.dumps({
        "scripts": {"build": "node -e \"require('fs').writeFileSync('dist/index.html','<html>rebuilt fixture</html>')\""},
    }), encoding="utf-8")
    (frontend / "dist/index.html").write_text("<html>original fixture</html>", encoding="utf-8")
    stamp = time.time() - 10
    for path in (frontend / "package.json", frontend / "src/main.js"):
        os.utime(path, (stamp, stamp))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("NDR_")}
    environment.update({
        "NDR_NO_BROWSER": "1", "NDR_CREDENTIAL_BACKEND": "session",
        "NDR_DATA_DIR": str(repo / "isolated library"), "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join([str(repo / "backend/src"), str(Path(sys.prefix) / "Lib/site-packages")]),
    })
    yield repo, environment


def run_batch(repo, environment, name, *args):
    return subprocess.run(
        ["cmd.exe", "/d", "/c", name, *map(str, args)], cwd=repo, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        **hidden_console(),
    )


def wait_ready(client, process, port, log_path, *, previous=None):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert process.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
        try:
            response = client.get(f"http://127.0.0.1:{port}/api/health")
            payload = response.json()
            if (response.status_code == 200 and payload.get("database", {}).get("state") == "READY"
                    and (previous is None or payload["started_at"] != previous)):
                return payload
        except (httpx.TransportError, ValueError):
            pass
        time.sleep(.1)
    pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))


def assert_released(port):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def start(repo, environment, port, log):
    return subprocess.Popen(
        ["cmd.exe", "/d", "/c", "start.bat", str(port)], cwd=repo, env=environment,
        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, **hidden_console(),
    )


def stop_owned(repo, environment, process):
    if process.poll() is None:
        result = run_batch(repo, environment, "stop.bat")
        assert result.returncode == 0, result.stdout + result.stderr
        process.wait(timeout=35)


def test_cmd_start_repeat_restart_and_normal_stop(launcher):
    repo, environment = launcher
    environment.pop("NDR_DATA_DIR")
    (repo / "data").mkdir()
    (repo / "data/application-settings.json").write_text(json.dumps({
        "data_dir": str(repo / "isolated library"), "auto_migrate": True,
    }), encoding="utf-8")
    port = free_port()
    log_path = repo / "launch.log"
    with log_path.open("wb") as log, httpx.Client(timeout=1, trust_env=False) as client:
        process = start(repo, environment, port, log)
        try:
            original = wait_ready(client, process, port, log_path)
            record_path = repo / "data/run/serve-cmd.json"
            before = json.loads(record_path.read_text(encoding="utf-8"))
            repeated = run_batch(repo, environment, "start.bat", port)
            assert repeated.returncode == 0, repeated.stdout + repeated.stderr
            assert "already running" in repeated.stdout
            assert json.loads(record_path.read_text(encoding="utf-8")) == before
            assert (repo / "frontend/dist/index.html").read_text() == "<html>original fixture</html>"
            # A second port must be rejected before either build or migration.
            # Make each preparation step observable if it is reached.
            source = repo / "frontend/src/main.js"
            stamp = (repo / "frontend/dist/index.html").stat().st_mtime + 2
            os.utime(source, (stamp, stamp))
            migration = repo / "backend/migrations/env.py"
            original_migration = migration.read_text(encoding="utf-8")
            migration.write_text("raise RuntimeError('migration must not run while service is live')", encoding="utf-8")
            try:
                other_port = free_port()
                refused = run_batch(repo, environment, "start.bat", other_port)
                assert refused.returncode == 1
                assert "A recorded service is still running" in refused.stderr
                assert "Preparing the database" not in refused.stdout
                assert "migration must not run" not in refused.stderr
                assert (repo / "frontend/dist/index.html").read_text() == "<html>original fixture</html>"
                assert json.loads(record_path.read_text(encoding="utf-8")) == before
                assert_released(other_port)
            finally:
                migration.write_text(original_migration, encoding="utf-8")
            url = f"http://127.0.0.1:{port}"
            description = client.get(url + "/api/settings/application").json()["data"]
            assert Path(description["config_path"]).is_relative_to(repo)
            response = client.post(url + "/api/settings/application/save-and-restart", json={
                "revision": description["revision"], "values": {
                    "llm_timeout_seconds": 42, "data_dir": str(repo / "second isolated library"),
                },
            })
            assert response.status_code == 200, response.text
            wait_ready(client, process, port, log_path, previous=original["started_at"])
            assert json.loads(record_path.read_text(encoding="utf-8")) == before
            result = run_batch(repo, environment, "stop.bat")
            assert result.returncode == 0, result.stdout + result.stderr
            assert process.wait(timeout=35) == 0, log_path.read_text(encoding="utf-8", errors="replace")
            assert not record_path.exists()
            assert_released(port)
            assert (repo / "isolated library/ndr.sqlite3").exists()
            assert (repo / "second isolated library/ndr.sqlite3").exists()
            events = "".join((repo / library / "logs/tasks.log").read_text(encoding="utf-8")
                             for library in ("isolated library", "second isolated library"))
            assert sum(json.loads(line)["event"] == "application_stopped" for line in events.splitlines()) >= 2
        finally:
            stop_owned(repo, environment, process)


@pytest.mark.parametrize("identity_field", ["created", "image"])
def test_cmd_stop_rejects_changed_identity_and_legacy_record(launcher, identity_field):
    repo, environment = launcher
    port = free_port()
    log_path = repo / "launch.log"
    with log_path.open("wb") as log, httpx.Client(timeout=1, trust_env=False) as client:
        process = start(repo, environment, port, log)
        record_path = repo / "data/run/serve-cmd.json"
        original = None
        try:
            wait_ready(client, process, port, log_path)
            original = record_path.read_text(encoding="utf-8")
            changed = json.loads(original)
            changed[identity_field] = changed["created"] + 1 if identity_field == "created" else "C:/another/python.exe"
            record_path.write_text(json.dumps(changed), encoding="utf-8")
            refused = run_batch(repo, environment, "stop.bat")
            assert refused.returncode == 1
            assert "identity" in refused.stdout
            assert process.poll() is None
            assert client.get(f"http://127.0.0.1:{port}/api/health").status_code == 200
            record_path.unlink()
            (record_path.parent / "serve-pids.json").write_text(json.dumps([{"pid": changed["pid"], "name": "serve-backend"}]))
            legacy = run_batch(repo, environment, "stop.bat")
            assert legacy.returncode == 1
            assert "original launch window" in legacy.stdout
            assert process.poll() is None
        finally:
            if original is not None:
                record_path.write_text(original, encoding="utf-8")
            stop_owned(repo, environment, process)
        assert_released(port)


def test_cmd_rebuilds_newer_sources_before_starting(launcher):
    repo, environment = launcher
    source = repo / "frontend/src/main.js"
    stamp = (repo / "frontend/dist/index.html").stat().st_mtime + 2
    os.utime(source, (stamp, stamp))
    port = free_port()
    log_path = repo / "launch.log"
    with log_path.open("wb") as log, httpx.Client(timeout=1, trust_env=False) as client:
        process = start(repo, environment, port, log)
        try:
            wait_ready(client, process, port, log_path)
            assert "rebuilt fixture" in client.get(f"http://127.0.0.1:{port}/").text
        finally:
            stop_owned(repo, environment, process)


def test_cmd_rejects_invalid_port_before_migration(launcher):
    repo, environment = launcher
    result = run_batch(repo, environment, "start.bat", 70000)
    assert result.returncode == 1
    assert "Port must" in result.stdout
    assert not (repo / "isolated library/ndr.sqlite3").exists()


@pytest.mark.parametrize("reader", [True, False])
def test_cmd_does_not_adopt_or_stop_an_unowned_listener(launcher, reader):
    repo, environment = launcher

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - HTTP handler API
            body = json.dumps({
                "app": "novel-dialogue-reader" if reader else "another-application",
                "status": "ok", "database": {"state": "READY"},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_port
    try:
        result = run_batch(repo, environment, "start.bat", port)
        assert result.returncode == (0 if reader else 1), result.stdout + result.stderr
        assert not (repo / "data/run/serve-cmd.json").exists()
        stopped = run_batch(repo, environment, "stop.bat")
        assert stopped.returncode == 0
        with httpx.Client(timeout=1, trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{port}/api/health").status_code == 200
        if reader:
            assert not (repo / "isolated library/ndr.sqlite3").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("during_import", [False, True])
def test_cmd_ctrl_c_reaches_only_the_isolated_launch_console(launcher, during_import):
    repo, environment = launcher
    if during_import:
        entry = repo / "backend/src/ndr/__main__.py"
        entry.write_text(entry.read_text(encoding="utf-8").replace(
            "import sys", "import time\ntime.sleep(5)\nimport sys",
        ), encoding="utf-8")
    else:
        # Keep the supervisor's readiness observation pending while the actual
        # application becomes ready, then cancel in that real scheduling window.
        batch = repo / "start.bat"
        original = batch.read_text()
        pending = original.replace("if health(port):\\n    ready=True", "if False:\\n    ready=True")
        assert pending != original
        batch.write_text(pending)
    port = free_port()
    log_path = repo / "launch.log"
    with log_path.open("wb") as log, httpx.Client(timeout=1, trust_env=False) as client:
        process = start(repo, environment, port, log)
        try:
            if during_import:
                deadline = time.monotonic() + 30
                while not (repo / "data/run/serve-cmd.json").exists() and time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
                    time.sleep(.05)
                assert (repo / "data/run/serve-cmd.json").exists()
            else:
                wait_ready(client, process, port, log_path)
            # Keyboard Ctrl+C is a console event. This sender attaches only to the
            # hidden console this test created, never the test runner/user console.
            send = """
import ctypes, sys, time
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.FreeConsole()
assert kernel.AttachConsole(int(sys.argv[1]))
assert kernel.SetConsoleCtrlHandler(None, True)
assert kernel.GenerateConsoleCtrlEvent(0, 0)
time.sleep(.5)
"""
            result = subprocess.run(
                [sys.executable, "-c", send, str(process.pid)], capture_output=True,
                timeout=10, **hidden_console(),
            )
            assert result.returncode == 0, result.stderr
            # CMD can ask whether to terminate the surrounding batch after its
            # Python command handles Ctrl+C; let it finish its normal exit path.
            process.communicate(input=b"N\r\n", timeout=35)
            assert process.returncode == 0, log_path.read_text(encoding="utf-8", errors="replace")
            assert_released(port)
            assert not (repo / "data/run/serve-cmd.json").exists()
            if not during_import:
                events = (repo / "isolated library/logs/tasks.log").read_text(encoding="utf-8")
                assert any(json.loads(line)["event"] == "application_stopped" for line in events.splitlines())
        finally:
            stop_owned(repo, environment, process)


def test_cmd_startup_timeout_and_stop_timeout_keep_live_identity(launcher):
    repo, environment = launcher
    # Shorten only the copied fixture's clocks. The child deliberately stays alive
    # after BREAK, allowing both launchers' bounded-wait behavior to be observed.
    batch = repo / "start.bat"
    batch.write_text(batch.read_text().replace("time.monotonic()+60", "time.monotonic()+1")
                     .replace("time.monotonic()+30", "time.monotonic()+1"))
    batch = repo / "stop.bat"
    batch.write_text(batch.read_text().replace("k.WaitForSingleObject(h,30000)", "k.WaitForSingleObject(h,300)"))
    (repo / "backend/src/ndr/__main__.py").write_text("""
import signal, time
from pathlib import Path
signal.signal(signal.SIGBREAK, lambda *_: None)
while not Path('release-test-child').exists():
    time.sleep(.05)
""", encoding="utf-8")
    port = free_port()
    record_path = repo / "data/run/serve-cmd.json"
    try:
        log_path = repo / "launch.log"
        with log_path.open("wb") as log:
            process = start(repo, environment, port, log)
            assert process.wait(timeout=30) == 1
        assert "still shutting down" in log_path.read_text(encoding="utf-8", errors="replace")
        before = json.loads(record_path.read_text(encoding="utf-8"))
        result = run_batch(repo, environment, "stop.bat")
        assert result.returncode == 1
        assert "still shutting down" in result.stdout
        assert json.loads(record_path.read_text(encoding="utf-8")) == before
    finally:
        (repo / "release-test-child").touch()
        deadline = time.monotonic() + 5
        while record_path.exists() and time.monotonic() < deadline:
            run_batch(repo, environment, "stop.bat")
            time.sleep(.05)
        assert not record_path.exists()
    assert_released(port)
