"""`python -m ndr.evaluation`：评测命令。

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
uv run --project backend python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
    --config evaluation/configs/b2.json --output evaluation/reports/b2.json --allow-live
```

- `validate`：只校验清单与金标准（不加载模型，不调用网络）。
- `run`：产出预测并计算指标；`--config` 为 B0 规则基线时完全离线，
  为 LLM 配置时必须显式 `--allow-live` 并提供 `--profile-id`，否则报告记为 `NOT_RUN`。
- `loss`：离线证据账。重放长 Gap 压缩，列出丢掉的行文与是否删到金标准 ``must_keep``。

退出码：0 = 通过；1 = 校验/运行有错误；2 = 用法错误（argparse）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loss import loss_report
from .runner import RunOptions, build_report, validate_command, write_report


def _cmd_validate(args: argparse.Namespace) -> int:
    code, payload = validate_command(args.manifest)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"清单 {payload['manifest']}：作品 {payload['works']} 个、书 {payload['books']} 本、"
            f"划分 {payload['splits']}"
        )
        for item in payload["errors"]:
            print(f"  [error] {item['code']}: {item['message']}")
        for item in payload["warnings"]:
            print(f"  [warn ] {item['code']}: {item['message']}")
        print("校验通过。" if code == 0 else "校验失败。")
    return code


def _cmd_run(args: argparse.Namespace) -> int:
    options = RunOptions(
        manifest_path=args.manifest,
        config_path=args.config,
        output_path=args.output,
        predictions_path=args.predictions,
        splits=args.splits,
        allow_live=args.allow_live,
        profile_id=args.profile_id,
        min_sample=args.min_sample,
    )
    report = build_report(options)
    if args.output:
        write_report(report, args.output)
    overall = report.get("overall")
    print(
        json.dumps(
            {
                "quality_evidence": report["quality_evidence"],
                "books": len(report["books"]),
                "overall": None
                if overall is None
                else {
                    "accepted_accuracy": overall["grouping"]["accepted_accuracy"],
                    "coverage": overall["coverage"]["coverage"],
                    "pairwise_f1": overall["grouping"]["pairwise_f1"],
                    "sample": overall["sample"],
                },
                "targets_met": report["targets_met"],
                "blocks": report["blocks"],
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_loss(args: argparse.Namespace) -> int:
    payload = loss_report(
        args.manifest, context_policy=args.context_policy, splits=args.splits
    )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = payload["summary"]
    print(
        json.dumps(
            {
                "context_policy": payload["context_policy"],
                "gap_compression": payload["gap_compression"],
                "books": len(payload["books"]),
                **summary,
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ndr.evaluation", description="离线评测命令")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验清单与金标准（离线）")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--json", action="store_true", help="输出完整 JSON")
    validate.set_defaults(func=_cmd_validate)

    run = sub.add_parser("run", help="产出预测并计算指标")
    run.add_argument("--manifest", required=True)
    run.add_argument("--config", help="评测配置（B0/B1/B2）")
    run.add_argument("--output", help="报告输出路径")
    run.add_argument("--predictions", help="外部预测 JSON（{book_id: prediction}）")
    run.add_argument(
        "--split",
        action="append",
        dest="splits",
        help="只跑指定划分（可重复；默认全部）",
    )
    run.add_argument("--allow-live", action="store_true", help="显式允许真实模型调用")
    run.add_argument("--profile-id", help="真实运行使用的模型配置 ID")
    run.add_argument("--min-sample", type=int, default=30, help="宣布达标所需的最少可确定样本数")
    run.set_defaults(func=_cmd_run)

    loss = sub.add_parser("loss", help="离线证据账：压缩丢掉了哪些原文")
    loss.add_argument("--manifest", required=True)
    loss.add_argument("--context-policy", default="context-2", help="context-1 / context-2")
    loss.add_argument("--splits", nargs="*", help="只跑指定划分（默认全部）")
    loss.add_argument("--output", help="证据账输出路径（JSON）")
    loss.set_defaults(func=_cmd_loss)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"评测失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
