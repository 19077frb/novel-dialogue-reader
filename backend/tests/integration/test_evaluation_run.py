"""集成测试：报告生成、离线基线与「真实运行必须显式允许」。

覆盖：

- B0 规则基线 → 真实指标（覆盖率低、样本不足、`targets_met=null`、`quality_evidence=false`）；
- LLM 配置未加 `--allow-live` → `NOT_RUN` 且写明原因（不假装跑过）；
- `--allow-live` 但没有 `--profile-id` → `NOT_RUN`；
- `--allow-live` + 离线 FakeProvider 配置 → 真实地把正文导入、建任务、跑引擎、读投影
  （链路验证），但 `quality_evidence=false`（提供方是测试用假提供方）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import CredentialMode
from ndr.evaluation.runner import RunOptions, build_report, write_report
from ndr.llm.adapters.fake import FakeProviderAdapter as FakeProvider
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.migrate import run_migrations
from ndr.storage.models import BookCharacter, Job, ModelProfile
from ndr.storage.transactions import transaction

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "evaluation" / "manifests" / "dev.json"
CONFIG_B0 = REPO_ROOT / "evaluation" / "configs" / "b0.json"
CONFIG_B2 = REPO_ROOT / "evaluation" / "configs" / "b2.json"


@pytest.fixture(autouse=True)
def fake_roster(monkeypatch):
    """人物阶段给有效候选，对白阶段仍然全部 UNKNOWN；不伪造模型效果。"""
    original = FakeProvider._roster_output

    def roster(self):
        mode = self.labeling_mode
        self.labeling_mode = "deterministic"
        try:
            return original(self)
        finally:
            self.labeling_mode = mode

    monkeypatch.setattr(FakeProvider, "_roster_output", roster)


def _fake_profile_settings(tmp_path: Path) -> tuple[Settings, str]:
    settings = Settings(
        data_dir=tmp_path / "evaluation-data",
        credential_backend="session",
        allow_fake_provider=True,
        fake_provider_labels="unknown",
    )
    run_migrations(settings)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            profile = ModelProfile(
                name="评测用 FakeProvider",
                protocol="fake-provider",
                base_url="http://127.0.0.1:1",
                model="fake-model",
                params_json="{}",
                credential_mode=CredentialMode.NONE,
            )
            session.add(profile)
            session.flush()
            profile_id = profile.id
    finally:
        engine.dispose()
    return settings, profile_id


def test_offline_rule_baseline_report_has_real_metrics(tmp_path: Path) -> None:
    # 用默认 min_sample=30：本例只有 4 条可确定样本 → 样本不足，不能宣布达标
    report = build_report(RunOptions(manifest_path=MANIFEST, config_path=CONFIG_B0))
    book = report["books"][0]
    assert book["state"] == "OFFLINE_BASELINE"
    assert book["provider"] == "b0-rule-1"
    metrics = book["metrics"]
    assert metrics["extraction"]["recall"] == 1.0  # 候选覆盖全部金标准对白
    assert metrics["coverage"]["coverage"] == 0.4  # 只有 2/5 得到确定归属
    assert metrics["grouping"]["accepted_accuracy"] == 1.0
    assert metrics["sample"]["sample_sufficient"] is False  # 只有 4 条可确定样本
    assert report["targets_met"] is None  # 样本不足 → 不宣布达标
    assert report["quality_evidence"] is False
    assert any("quality_evidence=false" in item for item in report["blocks"])
    assert report["config_fingerprint"]
    assert report["usage_total"]["calls"] == 0  # 规则基线不调用模型

    output = write_report(report, tmp_path / "report.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["versions"]["engine"] == "attribution-engine-1"
    assert payload["versions"]["gold_schema"] == "1.0"


def test_llm_config_without_allow_live_is_not_run(tmp_path: Path) -> None:
    report = build_report(RunOptions(manifest_path=MANIFEST, config_path=CONFIG_B2, min_sample=3))
    book = report["books"][0]
    assert book["state"] == "NOT_RUN"
    assert "--allow-live" in book["reason"]
    assert report["overall"] is None
    assert report["targets_met"] is None
    assert report["quality_evidence"] is False


def test_allow_live_without_profile_is_not_run(tmp_path: Path) -> None:
    settings, _profile_id = _fake_profile_settings(tmp_path)
    report = build_report(
        RunOptions(
            manifest_path=MANIFEST,
            config_path=CONFIG_B2,
            allow_live=True,
            profile_id=None,
            min_sample=3,
            settings=settings,
        )
    )
    assert report["books"][0]["state"] == "NOT_RUN"
    assert "--profile-id" in report["books"][0]["reason"]


@pytest.mark.parametrize("mode", ["initial", "reread"])
def test_allow_live_with_offline_provider_verifies_pipeline(tmp_path: Path, mode: str) -> None:
    """显式允许后跑通真实链路（导入 → 任务 → 引擎 → 投影），但如实标注不是真实效果。"""

    settings, profile_id = _fake_profile_settings(tmp_path)
    config = json.loads(CONFIG_B2.read_text(encoding="utf-8"))
    config["reading_mode"] = mode
    config["inference_options"]["thinking_mode"] = "disabled"
    config_path = tmp_path / "mode.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    report = build_report(
        RunOptions(
            manifest_path=MANIFEST,
            config_path=config_path,
            allow_live=True,
            profile_id=profile_id,
            min_sample=3,
            settings=settings,
        )
    )
    book = report["books"][0]
    assert book["state"] == "COMPLETED", book
    assert book["provider"] == "fake-provider"
    # 假提供方对全部对白都给 UNKNOWN → 覆盖率 0、被标记为全拒答（这正是它的真实行为）
    assert book["metrics"]["coverage"]["coverage"] == 0.0
    assert book["metrics"]["degenerate"]["all_refusal"] is True
    assert report["quality_evidence"] is False  # 测试提供方不算效果证据
    assert any("真实模型效果" in item for item in report["blocks"])
    assert report["usage_total"]["calls"] == 2  # 人物及对白各一次
    assert book["model_config"]["model"] == "fake-model"
    assert book["model_config"]["params"]["thinking"]["type"] == "disabled"
    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as session:
            characters = list(session.scalars(select(BookCharacter)))
            assert characters and all(not c.user_confirmed for c in characters)
            jobs = list(session.scalars(select(Job)))
            dialogue = next(j for j in jobs if j.kind.value == "INFERENCE")
            assert json.loads(dialogue.range_json)["chapter_id"]
            assert json.loads(dialogue.budget_json)["max_recheck_rounds"] == 0
            assert json.loads(dialogue.budget_json)["max_input_tokens"] == 200000
            assert json.loads(dialogue.budget_json)["max_output_tokens"] == 20000
            assert json.loads(dialogue.budget_json)["max_format_retries"] == 1
            assert json.loads(dialogue.range_json)["reading_mode"] == mode
            for job in jobs:
                if job.profile_snapshot_json:
                    assert (
                        json.loads(job.profile_snapshot_json)["params"]["thinking"]["type"]
                        == "disabled"
                    )
    finally:
        engine.dispose()


def test_full_window_rounds_include_all_dialogues_and_usage(tmp_path, monkeypatch):
    requests = []
    original = FakeProvider.generate_labels

    async def record(self, payload):
        requests.append(dict(payload))
        return await original(self, payload)

    monkeypatch.setattr(FakeProvider, "generate_labels", record)
    settings, profile_id = _fake_profile_settings(tmp_path)
    report = build_report(
        RunOptions(
            manifest_path=MANIFEST,
            config_path=REPO_ROOT / "evaluation/configs/b4.json",
            allow_live=True,
            profile_id=profile_id,
            settings=settings,
        )
    )
    assert report["books"][0]["state"] == "COMPLETED", report
    dialogues = [r for r in requests if r.get("task") != "roster"]
    assert len(dialogues) == 2
    assert set(dialogues[0]["target_quote_ids"]) == set(dialogues[1]["target_quote_ids"])
    assert len(dialogues[1]["target_quote_ids"]) == 5
    assert report["usage_total"]["calls"] == 3


def test_failed_evaluation_preserves_cost_and_excludes_metrics(tmp_path, monkeypatch):
    from ndr.evaluation import runner

    monkeypatch.setattr(
        runner,
        "run_live_predictions",
        lambda **kwargs: (
            {
                "provider": "fake-provider",
                "usage": {
                    "calls": 2,
                    "input_tokens": 12,
                    "output_tokens": 8,
                    "total_tokens": 20,
                    "unknown_runs": 1,
                },
            },
            "LIVE_FAILED",
            "校验失败",
        ),
    )
    report = build_report(RunOptions(manifest_path=MANIFEST, config_path=CONFIG_B2))
    assert report["books"][0]["state"] == "LIVE_FAILED"
    assert report["overall"] is None
    assert report["usage_total"]["total_tokens"] == 20
    assert report["usage_unknown_books"] == 1


def test_input_budget_reaches_roster_scheduler(tmp_path):
    settings, profile_id = _fake_profile_settings(tmp_path)
    config = json.loads(CONFIG_B2.read_text(encoding="utf-8"))
    config["budget"]["max_input_tokens"] = 1
    path = tmp_path / "tiny-budget.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    report = build_report(
        RunOptions(
            manifest_path=MANIFEST,
            config_path=path,
            allow_live=True,
            profile_id=profile_id,
            settings=settings,
        )
    )
    assert report["books"][0]["state"] == "LIVE_FAILED"
    assert report["usage_total"]["calls"] == 0


def test_chapters_follow_reading_order_and_isolate_existing_results(tmp_path):
    from ndr.evaluation.live import run_live_predictions

    settings, profile_id = _fake_profile_settings(tmp_path)
    text = tmp_path / "chapters.txt"
    text.write_text("第一章 开始\n「你好。」\n第二章 继续\n「再见。」\n", encoding="utf-8")
    prediction, state, reason = run_live_predictions(
        settings=settings,
        profile_id=profile_id,
        book_path=text,
        config_id="chapters",
        allow_live=True,
    )
    assert state == "COMPLETED", reason
    assert prediction["usage"]["calls"] == 4
    with pytest.raises(ValueError, match="隔离书库"):
        run_live_predictions(
            settings=settings,
            profile_id=profile_id,
            book_path=text,
            config_id="chapters",
            allow_live=True,
        )
