"""T19 集成测试：生产同源启动（后端直接提供前端构建产物，保持 /api 契约）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ndr.app import create_app
from ndr.config import Settings


def _build_static(root: Path) -> Path:
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        '<!DOCTYPE html><html lang="zh-CN"><body><div id="root">NDR-SPA</div></body></html>',
        encoding="utf-8",
    )
    (root / "assets" / "app.js").write_text("console.log('ndr-asset')", encoding="utf-8")
    return root


def _settings(tmp_path: Path, *, static_dir: Path | None) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        credential_backend="session",
        recover_on_startup=False,
        static_dir=static_dir,
    )


def test_same_origin_serves_spa_and_keeps_api_contract(tmp_path: Path) -> None:
    static_dir = _build_static(tmp_path / "dist")
    with TestClient(create_app(_settings(tmp_path, static_dir=static_dir))) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "NDR-SPA" in index.text
        assert index.headers["content-type"].startswith("text/html")

        # 前端路由（深链接）回退到 index.html，由 SPA 自己解析
        deep = client.get("/books/demo/read")
        assert deep.status_code == 200
        assert "NDR-SPA" in deep.text

        # 真实静态文件按原样返回
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "ndr-asset" in asset.text

        # /api 未知路径仍然是 JSON 契约 404，不被 SPA 回退吞掉
        missing = client.get("/api/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"

        # 既有接口不受影响
        assert client.get("/api/health").status_code == 200

        # 越界路径不读构建目录之外的文件
        (tmp_path / "secret.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
        for attempt in ("/../secret.txt", "/..%2Fsecret.txt", "/%2e%2e/secret.txt"):
            got = client.get(attempt)
            assert "SECRET-OUTSIDE" not in got.text, attempt


def test_root_is_not_served_without_static_dir(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, static_dir=None))) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200
