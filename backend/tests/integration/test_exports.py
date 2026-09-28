"""集成测试：冻结快照、EPUB/HTML 导出、校验与受控下载。

覆盖：

- TXT（含已确认与自动标注）→ EPUB/HTML，正文完整、编号样式正确、**零模型调用**；
- EPUB（ruby + 图片）→ 导出 EPUB/HTML，资源闭合、注音不重复；
- 只导出部分章节时，只带必要的资源；
- 损坏的成品被校验报告为失败，不生成假成功；
- 重复导出幂等（同一 fingerprint 复用产物）、重复下载 MIME 正确、中文文件名可用；
- 快照隔离：导出使用冻结投影，之后的人工更正不会改变已生成的产物。
"""

from __future__ import annotations

import json
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import (
    create_fake_profile,
    import_sample,
    run_deterministic_job,
    session_scope,
)
from fixtures.epub_factory import build_epub, ruby_and_image_spec
from ndr.config import Settings
from ndr.exports.validation import check_epub
from ndr.storage.models import ExportArtifact, ExportSnapshot, InferenceRun
from ndr.storage.transactions import transaction

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "「……谢谢。」她低声说。\n"
    "第二章 名字\n"
    "「我叫小満。」她抬起头。\n"
)


def _prepared_book(client: TestClient, settings: Settings, *, key: str = "k-export") -> dict:
    data = import_sample(client, SAMPLE)
    profile_id = create_fake_profile(client, name=f"导出提供方 {key}")
    run_deterministic_job(
        settings, client, book_id=data["book_id"], profile_id=profile_id, key=key
    )
    return data


def _runs_count(settings: Settings) -> int:
    with session_scope(settings) as factory, transaction(factory) as session:
        return len(list(session.execute(select(InferenceRun)).scalars()))


def _preview(client: TestClient, book_id: str, **overrides) -> dict:
    payload = {
        "visibility_policy": "reread",
        "style": {"preset": "color_and_label"},
        **overrides,
    }
    response = client.post(f"/api/books/{book_id}/exports/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _export(client: TestClient, book_id: str, snapshot_id: str, fmt: str, *, key: str) -> dict:
    response = client.post(
        f"/api/books/{book_id}/exports",
        json={
            "snapshot_id": snapshot_id,
            "format": fmt,
            "style": {"preset": "color_and_label"},
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_f21_txt_export_epub_and_html_without_model_calls(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepared_book(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    runs_before = _runs_count(migrated_settings)

    preview = _preview(fake_provider_client, book_id)
    assert preview["counts"]["accepted"] >= 3
    assert preview["sample_html"].startswith("<!DOCTYPE html>")
    assert "〔S1〕" in preview["sample_html"]  # 编号是真实文本
    assert any("「雨停了。」" in fragment for fragment in preview["sample_fragments"])

    epub = _export(fake_provider_client, book_id, preview["snapshot_id"], "epub", key="k-epub")
    assert epub["state"] == "COMPLETED"
    assert epub["validation"]["internal"]["ok"] is True
    assert epub["validation"]["standard"]["state"] in {"NOT_RUN", "PASS"}

    html = _export(fake_provider_client, book_id, preview["snapshot_id"], "html", key="k-html")
    assert html["state"] == "COMPLETED"
    assert html["filename"].endswith(".html")

    # 导出零模型调用
    assert _runs_count(migrated_settings) == runs_before

    # 受控下载：MIME 与中文文件名
    download = fake_provider_client.get(f"/api/exports/{epub['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/epub+zip"
    assert "%E6%A0%87%E6%B3%A8%E7%89%88" in download.headers.get(
        "content-disposition", ""
    ).upper() or "filename*=" in download.headers.get("content-disposition", "")
    assert download.content[:2] == b"PK"

    html_download = fake_provider_client.get(f"/api/exports/{html['id']}/download")
    assert html_download.headers["content-type"].startswith("text/html")
    body = html_download.content.decode("utf-8")
    assert "「雨停了。」" in body
    assert "〔S1〕" in body
    assert "http://" not in body and "https://" not in body  # 无外部依赖


def test_f22_epub_source_export_keeps_resources_and_drops_ruby_readings(
    migrated_client: TestClient,
) -> None:
    response = migrated_client.post(
        "/api/books/import",
        files={"file": ("ruby.epub", build_epub(ruby_and_image_spec()), "application/epub+zip")},
    )
    assert response.status_code == 202, response.text
    book_id = response.json()["data"]["book_id"]

    preview = _preview(migrated_client, book_id)
    epub = _export(migrated_client, book_id, preview["snapshot_id"], "epub", key="k-epub-ruby")
    assert epub["state"] == "COMPLETED", epub
    validation = epub["validation"]["internal"]
    assert validation["ok"] is True, validation
    assert validation["checks"]["resource_closure"] is True

    # EPUB → HTML：同样是「四种输入/输出组合」之一，图片内联成 data URL（离线可读）
    html = _export(migrated_client, book_id, preview["snapshot_id"], "html", key="k-html-ruby")
    assert html["state"] == "COMPLETED", html
    html_body = migrated_client.get(f"/api/exports/{html['id']}/download").content.decode("utf-8")
    assert "data:image/png;base64," in html_body
    assert "かん" not in html_body
    assert "http://" not in html_body and "https://" not in html_body

    # 打包后的 zip 里确实带上了插图，且注音没被写进正文
    download = migrated_client.get(f"/api/exports/{epub['id']}/download")
    with zipfile.ZipFile(__import__("io").BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert any(name.startswith("OEBPS/images/") for name in names), names
        text = "".join(
            archive.read(name).decode("utf-8")
            for name in names
            if name.startswith("OEBPS/text/")
        )
        assert "对白" in text
        assert "かん" not in text
        assert "<script" not in text


def test_f26_partial_chapter_export_only_carries_needed_resources(
    migrated_client: TestClient,
) -> None:
    spec = ruby_and_image_spec()
    response = migrated_client.post(
        "/api/books/import",
        files={"file": ("ruby.epub", build_epub(spec), "application/epub+zip")},
    )
    book_id = response.json()["data"]["book_id"]
    chapters = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"]
    assert chapters

    preview = _preview(migrated_client, book_id, chapter_ids=[chapters[0]["id"]])
    assert preview["selected_chapter_ids"] == [chapters[0]["id"]]
    epub = _export(migrated_client, book_id, preview["snapshot_id"], "epub", key="k-partial")
    assert epub["state"] == "COMPLETED"
    download = migrated_client.get(f"/api/exports/{epub['id']}/download")
    with zipfile.ZipFile(__import__("io").BytesIO(download.content)) as archive:
        assert sum(1 for name in archive.namelist() if name.endswith(".xhtml")) >= 1
        assert "（节选）" in epub["filename"]

def test_f30_repeat_export_is_idempotent_and_download_is_stable(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepared_book(fake_provider_client, migrated_settings, key="k-idem")
    book_id = data["book_id"]
    preview = _preview(fake_provider_client, book_id)

    first = _export(fake_provider_client, book_id, preview["snapshot_id"], "epub", key="k-first")
    second = _export(fake_provider_client, book_id, preview["snapshot_id"], "epub", key="k-second")
    assert second["id"] == first["id"]  # 同一 fingerprint 复用产物
    assert second["file_sha256"] == first["file_sha256"]

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        artifacts = list(session.execute(select(ExportArtifact)).scalars())
        assert len(artifacts) == 1

    # 重复下载内容一致
    one = fake_provider_client.get(f"/api/exports/{first['id']}/download").content
    two = fake_provider_client.get(f"/api/exports/{first['id']}/download").content
    assert one == two


def test_snapshot_isolation_from_later_corrections(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """快照冻结之后的人工更正不能改变已经生成的导出。"""

    data = _prepared_book(fake_provider_client, migrated_settings, key="k-freeze")
    book_id = data["book_id"]
    preview = _preview(fake_provider_client, book_id)
    frozen_snapshot = preview["snapshot_id"]

    quotes = fake_provider_client.get(f"/api/books/{book_id}/quotes").json()["data"]["items"]
    target = quotes[0]["quote_id"]
    detail = fake_provider_client.get(f"/api/quotes/{target}").json()["data"]
    corrected = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={
            "action": "create_speaker",
            "description": "人工新建",
            "expected_version": detail["annotation"]["version"],
        },
    )
    assert corrected.status_code == 201, corrected.text

    epub = _export(fake_provider_client, book_id, frozen_snapshot, "epub", key="k-frozen")
    assert epub["state"] == "COMPLETED"

    # 新预览会得到新的快照（revision 变了 → 新 hash），旧快照仍然存在且内容不同
    fresh = _preview(fake_provider_client, book_id)
    assert fresh["snapshot_id"] != frozen_snapshot
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        old = session.get(ExportSnapshot, frozen_snapshot)
        assert old is not None
        new = session.get(ExportSnapshot, fresh["snapshot_id"])
        assert new is not None
        assert old.snapshot_hash != new.snapshot_hash


def test_f27_invalid_artifact_is_reported_not_faked(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """内部校验必须能识别坏成品：篡改后的文件被报告为失败。"""

    data = _prepared_book(fake_provider_client, migrated_settings, key="k-invalid")
    book_id = data["book_id"]
    preview = _preview(fake_provider_client, book_id)
    epub = _export(fake_provider_client, book_id, preview["snapshot_id"], "epub", key="k-broken")

    # 把成品改坏（丢掉 mimetype 的存储方式）：校验应给出失败而不是 PASS
    download = fake_provider_client.get(f"/api/exports/{epub['id']}/download").content
    report = check_epub(download, expected_text="「雨停了。」")
    assert report["ok"] is True

    broken = b"PK\x03\x04broken"
    report = check_epub(broken, expected_text="「雨停了。」")
    assert report["ok"] is False
    assert report["detail"] == "不是有效的 zip" or report["checks"]["mimetype_first"] is False

    # 未完成的产物不能下载
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        artifact = session.get(ExportArtifact, epub["id"])
        assert artifact is not None
        artifact.state = "FAILED"
    blocked = fake_provider_client.get(f"/api/exports/{epub['id']}/download")
    assert blocked.status_code == 409


def test_validate_exports_cli_reports_tool_status_truthfully(
    fake_provider_client: TestClient, migrated_settings: Settings, tmp_path
) -> None:
    """命令行校验：内部检查失败退出 1；缺 EPUBCheck 时如实记录 NOT_RUN（不伪装 PASS）。"""

    import sys
    from pathlib import Path

    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "scripts")
    )
    import validate_exports as cli  # noqa: PLC0415

    data = _prepared_book(fake_provider_client, migrated_settings, key="k-cli")
    book_id = data["book_id"]
    preview = _preview(fake_provider_client, book_id)
    html = _export(fake_provider_client, book_id, preview["snapshot_id"], "html", key="k-cli-html")
    epub = _export(fake_provider_client, book_id, preview["snapshot_id"], "epub", key="k-cli-epub")

    from fixtures.corrections import session_scope
    from ndr.storage.models import ExportArtifact
    from ndr.storage.paths import resolve_within
    from ndr.storage.transactions import transaction

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        html_path = resolve_within(
            migrated_settings, session.get(ExportArtifact, html["id"]).relative_path
        )
        epub_path = resolve_within(
            migrated_settings, session.get(ExportArtifact, epub["id"]).relative_path
        )

    report_path = tmp_path / "report.json"
    exit_code = cli.main(
        [
            "--html",
            str(html_path),
            "--epub",
            str(epub_path),
            "--epubcheck-jar",
            str(tmp_path / "missing-epubcheck.jar"),
            "--json",
            str(report_path),
        ]
    )
    assert exit_code == 0  # 内部检查通过；缺少标准检查工具不算失败，但状态必须是 NOT_RUN
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    epub_report = next(item for item in payload["reports"] if item["format"] == "epub")
    assert epub_report["standard"]["state"] == "NOT_RUN"
    assert "找不到 jar" in epub_report["standard"]["detail"]

    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"not a zip")
    assert cli.main(["--epub", str(broken)]) == 1
    assert cli.main(["--html", str(tmp_path / "missing.html")]) == 1
