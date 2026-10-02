"""Exercise a released ZIP, never the developer's Python server or real library."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))


def get(url: str) -> bytes:
    with OPENER.open(url, timeout=3) as response:
        return response.read()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def check_exe_icon(exe: Path) -> None:
    """Check embedded icon bytes, not just that an unrelated ICO was committed."""
    import pefile

    source = Path(__file__).resolve().parents[2] / "assets/icons/app.ico"
    data = source.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, 7), "Invalid application ICO"
    expected = set()
    for index in range(count):
        entry = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        length, offset = entry[-2:]
        expected.add(data[offset:offset + length])
    with pefile.PE(str(exe)) as pe:
        embedded = set()
        for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id == 3:  # RT_ICON
                for image in resource.directory.entries:
                    for language in image.directory.entries:
                        record = language.data.struct
                        embedded.add(pe.get_data(record.OffsetToData, record.Size))
        assert expected <= embedded, "EXE does not embed all approved application icon frames"


def check(zip_path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("The Windows EXE smoke test requires Windows.")
    # Retain isolated logs under ignored dist for diagnosis. No recursive cleanup of user paths.
    destination = Path(tempfile.mkdtemp(prefix="免安装 验收 ", dir=zip_path.parent))
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe archive path: {name}")
            if path.suffix in {".sqlite3", ".sqlite", ".db", ".log"} or path.name == ".env":
                raise RuntimeError(f"Runtime data must not be published: {name}")
            if path.parts[:2] in {
                ("NovelDialogueReader", "data"), ("NovelDialogueReader", ".git")
            } or "internal" in path.parts:
                raise RuntimeError(f"Private files must not be published: {name}")
        archive.extractall(destination)
    exe = destination / "NovelDialogueReader" / "NovelDialogueReader.exe"
    assert exe.is_file(), "EXE missing"
    check_exe_icon(exe)
    env = {key: value for key, value in os.environ.items() if not key.startswith("NDR_")}
    env.update({"PATH": "", "NDR_CREDENTIAL_BACKEND": "session", "PYTHONUTF8": "1"})
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["LOCALAPPDATA"] = str(destination / "user profile")
    runtime_env = dict(env, NDR_CREDENTIAL_BACKEND="system")
    runtime = subprocess.run(
        [str(exe), "--check-runtime"], cwd=destination, env=runtime_env,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
    )
    facts = json.loads(runtime.stdout)
    assert facts["frozen"], facts
    assert facts["system_credentials_available"], "Windows credential backend missing from bundle"
    default_dir = destination / "user profile" / "NovelDialogueReader" / "data"
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    title = "免安装验收原创短文"

    def run_round(round_number: int) -> None:
        log_path = destination / f"round-{round_number}.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(exe), "--port", str(port), "--no-browser", "--run-seconds", "15"],
                cwd=destination, env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                deadline = time.monotonic() + 60
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
                    try:
                        health = json.loads(get(f"{url}/api/health"))
                        if health["database"]["state"] == "READY":
                            break
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen service did not become ready")
                    time.sleep(0.2)
                assert default_dir.joinpath("ndr.sqlite3").is_file(), "Wrong default data path"
                html = get(url).decode("utf-8")
                assert 'id="root"' in html
                assert get(f"{url}/books/example/read") == html.encode("utf-8")
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
                assert assets and all(get(url + asset) for asset in assets)
                duplicate = subprocess.run(
                    [str(exe), "--port", str(port), "--no-browser"], cwd=destination, env=env,
                    capture_output=True, timeout=10,
                )
                assert duplicate.returncode == 0, duplicate.stderr
                if round_number == 1:
                    boundary = uuid.uuid4().hex
                    text = f"{title}\n第一章 初遇\n「你好。」少女说。\n".encode()
                    body = (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                        'filename="smoke.txt"\r\nContent-Type: text/plain\r\n\r\n'
                    ).encode() + text + f"\r\n--{boundary}--\r\n".encode()
                    request = Request(f"{url}/api/books/import", data=body, headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                    })
                    with OPENER.open(request, timeout=5) as response:
                        assert response.status == 202
                        imported = json.load(response)["data"]
                    book_id = imported["book_id"]
                    assert get(f"{url}/api/books/{book_id}/chapters")
                else:
                    library = json.loads(get(f"{url}/api/books"))
                    assert len(library["data"]["items"]) == 1, "Book did not survive restart"
                process.wait(timeout=20)
                assert process.returncode == 0, log_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            finally:
                if process.poll() is None:
                    process.terminate()  # Only this isolated acceptance child; never real servers.
                    process.wait(timeout=10)

    run_round(1)
    run_round(2)
    # Stranger on the requested port must not cause a DB migration or open its website.
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", port))
        blocker.listen()
        other = destination / "blocked library"
        blocked = subprocess.run(
            [str(exe), "--port", str(port), "--data-dir", str(other), "--no-browser"],
            cwd=destination, env=env, capture_output=True, timeout=15,
        )
        assert blocked.returncode != 0
        assert not (other / "ndr.sqlite3").exists(), "Port conflict modified another library"
    assert not (destination / "NovelDialogueReader" / "data").exists()
    print(f"EXE smoke checks passed (isolated logs: {destination})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path)
    check(parser.parse_args().zip.resolve())
