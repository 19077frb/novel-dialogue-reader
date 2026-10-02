"""当前评测配置不接受旧条数配置或无效参数。"""

import json
from pathlib import Path

import pytest

from ndr.evaluation.configs import load_config

BASE = {"config_version": "2.0", "config_id": "test", "strategy": "llm"}


@pytest.mark.parametrize(
    "changes",
    [
        {"budget": {"max_rechecks": 3}},
        {"scene_state": False},
        {"prompt_version": "labeling-2"},
        {"model": "unused"},
        {"context_policy": "unknown"},
        {"config_version": "1.0"},
        {"budget": {"max_recheck_rounds": -1}},
        {"budget": {"max_recheck_rounds": None}},
        {"budget": {"max_format_retries": 6}},
        {"budget": {"max_input_tokens": 0}},
        {"inference_options": {"thinking_mode": "invalid"}},
        {"typo": 3},
        {"budget": 3},
    ],
)
def test_reject_obsolete_or_invalid_config(tmp_path: Path, changes: dict) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**BASE, **changes}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_recheck_rounds_default_is_explicit_zero(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    config = load_config(path)
    assert config.budget["max_recheck_rounds"] == 0
    assert "max_rechecks" not in config.as_dict()["budget"]
