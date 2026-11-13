"""Offline acceptance and build cleanup, without running a packaged application."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("portable_acceptance", ROOT / "backend/scripts/test_portable.py")
assert spec and spec.loader
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows portable cleanup")


@pytest.fixture()
def simulated_acceptance(tmp_path, monkeypatch):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("NovelDialogueReader/NovelDialogueReader.exe", "offline fixture")
    children = []

    class Child:
        returncode = None

        def __init__(self, args, **kwargs):
            self.destination = kwargs["cwd"]
            self.stopped = False
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0
            self.stopped = True
            return 0

        def terminate(self):
            self.stopped = True

    def run(args, **kwargs):
        if "--check-runtime" in args:
            return SimpleNamespace(stdout=json.dumps({"frozen": True, "system_credentials_available": True}))
        return SimpleNamespace(returncode=1 if "--data-dir" in args else 0, stderr=b"")

    def get(url):
        if url.endswith("/api/health"):
            data_dir = children[-1].destination / "user profile/NovelDialogueReader/data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "ndr.sqlite3").touch()
            return b'{"database":{"state":"READY"}}'
        if url.endswith("/api/books"):
            return b'{"data":{"items":[{}]}}'
        return b'<html><div id="root"></div><script src="/assets/main.js"></script></html>'

    class ImportResponse(io.BytesIO):
        status = 202

    monkeypatch.setattr(acceptance, "check_exe_icon", lambda _: None)
    monkeypatch.setattr(acceptance.subprocess, "Popen", Child)
    monkeypatch.setattr(acceptance.subprocess, "run", run)
    monkeypatch.setattr(acceptance, "get", get)
    monkeypatch.setattr(acceptance.OPENER, "open", lambda *args, **kwargs: ImportResponse(b'{"data":{"book_id":"offline"}}'))
    return package, children


def test_success_removes_only_own_extraction_after_children_exit(simulated_acceptance, monkeypatch):
    package, children = simulated_acceptance
    unrelated = package.parent / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    real_remove = acceptance.remove_acceptance_directory

    def remove(destination, parent):
        assert len(children) == 2 and all(child.stopped for child in children)
        real_remove(destination, parent)

    monkeypatch.setattr(acceptance, "remove_acceptance_directory", remove)
    acceptance.check(package)
    assert list(package.parent.glob("免安装 验收 *")) == []
    assert package.is_file() and unrelated.read_text(encoding="utf-8") == "keep"


def test_failure_stops_child_and_retains_logs(simulated_acceptance, monkeypatch, capsys):
    package, children = simulated_acceptance
    monkeypatch.setattr(acceptance, "get", lambda _: (_ for _ in ()).throw(ValueError("offline failure")))
    with pytest.raises(ValueError, match="offline failure"):
        acceptance.check(package)
    assert len(children) == 1 and children[0].stopped
    destination, = package.parent.glob("免安装 验收 *")
    assert (destination / "round-1.log").is_file()
    assert str(destination) in capsys.readouterr().out


def test_termination_timeout_kills_and_waits_before_retaining(simulated_acceptance, monkeypatch):
    package, children = simulated_acceptance
    original_child = acceptance.subprocess.Popen

    class SlowChild(original_child):
        killed = False

        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("offline-child", timeout)
            return super().wait(timeout)

        def kill(self):
            self.killed = True

    monkeypatch.setattr(acceptance.subprocess, "Popen", SlowChild)
    monkeypatch.setattr(acceptance, "get", lambda _: (_ for _ in ()).throw(ValueError("offline failure")))
    with pytest.raises(ValueError, match="offline failure"):
        acceptance.check(package)
    assert children[0].killed and children[0].stopped
    assert len(list(package.parent.glob("免安装 验收 *"))) == 1


def test_cleanup_failure_preserves_diagnostics(simulated_acceptance, monkeypatch, capsys):
    package, _ = simulated_acceptance

    def reject(*args):
        raise PermissionError("locked fixture")

    monkeypatch.setattr(acceptance, "remove_acceptance_directory", reject)
    with pytest.raises(PermissionError, match="locked fixture"):
        acceptance.check(package)
    destination, = package.parent.glob("免安装 验收 *")
    assert (destination / "round-2.log").is_file()
    assert str(destination) in capsys.readouterr().out


def test_cleanup_refuses_outside_parent_and_keeps_files(tmp_path):
    destination = tmp_path / "免安装 验收 fixture"
    destination.mkdir()
    with pytest.raises(RuntimeError, match="Unsafe"):
        acceptance.remove_acceptance_directory(destination, tmp_path / "other")
    assert destination.is_dir()


def make_junction(link, target):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    driver = target.parent / "junction.ps1"
    driver.write_text("param([string]$Link, [string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null\n", encoding="utf-8")
    subprocess.run([powershell, "-NoProfile", "-File", str(driver), str(link), str(target)], check=True, capture_output=True)


@pytest.mark.parametrize("redirect", ["root", "child"])
def test_acceptance_refuses_junctions_without_deleting_target(tmp_path, redirect):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    destination = tmp_path / "免安装 验收 fixture"
    if redirect == "root":
        make_junction(destination, outside)
    else:
        destination.mkdir()
        make_junction(destination / "redirect", outside)
    with pytest.raises(RuntimeError, match="reparse point"):
        acceptance.remove_acceptance_directory(destination, tmp_path)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.fixture()
def build_cleanup(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    driver = tmp_path / "cleanup.ps1"
    driver.write_text("""param([string]$Source, [string]$Root, [string]$Build, [switch]$KeepBuildFiles)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$function = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Remove-BuildIntermediates'
}, $true)
Invoke-Expression $function.Extent.Text
$cleanup = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text -eq '-not $KeepBuildFiles'
}, $true)
if (-not $cleanup) { throw 'Debug retention switch is missing' }
$checksum = $ast.Extent.Text.IndexOf('Set-Content -LiteralPath "$zip.sha256"')
if ($checksum -lt 0 -or $cleanup.Extent.StartOffset -lt $checksum) {
    throw 'Cleanup must follow checksum generation'
}
$repoRoot = $Root
$buildRoot = $Build
Invoke-Expression $cleanup.Extent.Text
""", encoding="utf-8")

    def invoke(repo, build, *, keep=False):
        args = [powershell, "-NoProfile", "-File", str(driver), str(ROOT / "scripts/build-portable.ps1"), str(repo), str(build)]
        if keep:
            args.append("-KeepBuildFiles")
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")

    return invoke


def test_build_cleanup_keeps_zip_and_checksum(tmp_path, build_cleanup):
    build = tmp_path / "repo/dist" / ("portable-" + "a" * 32)
    for name in ("app", "work"):
        (build / name).mkdir(parents=True)
        (build / name / "large.bin").write_bytes(b"fixture")
    for name in ("package.zip", "package.zip.sha256"):
        (build / name).write_text("keep", encoding="utf-8")
    result = build_cleanup(tmp_path / "repo", build)
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in build.iterdir()) == ["package.zip", "package.zip.sha256"]


def test_build_debug_switch_retains_intermediates(tmp_path, build_cleanup):
    build = tmp_path / "repo/dist" / ("portable-" + "c" * 32)
    for name in ("app", "work"):
        (build / name).mkdir(parents=True)
    result = build_cleanup(tmp_path / "repo", build, keep=True)
    assert result.returncode == 0, result.stderr
    assert (build / "app").is_dir() and (build / "work").is_dir()


@pytest.mark.parametrize("redirect", ["outside", "root", "child"])
def test_build_cleanup_refuses_redirects_and_outside_root(tmp_path, build_cleanup, redirect):
    repo = tmp_path / "repo"
    build = repo / "dist" / ("portable-" + "b" * 32)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    if redirect == "root":
        build.parent.mkdir(parents=True)
        make_junction(build, outside)
    else:
        (build / "work").mkdir(parents=True)
        if redirect == "child":
            make_junction(build / "app", outside)
    result = build_cleanup(repo, outside if redirect == "outside" else build)
    assert result.returncode != 0
    assert "Refusing cleanup" in result.stderr
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    if redirect != "root":
        assert (build / "work").is_dir()
