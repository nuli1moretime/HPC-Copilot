"""评估确定性诊断规则，并区分当前支持范围与未来规划范围。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.diagnostics import DiagnosticsEngine  # noqa: E402
from score_results import macro_f1  # noqa: E402


def load_legacy_cases(cases_dir: Path) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in data.get("cases", []):
            item = dict(case)
            item["source_file"] = path.name
            cases.append(item)
    return cases


def evaluate(cases: list[dict], engine: DiagnosticsEngine) -> dict:
    implemented_rules = {item["id"] for item in engine._patterns}
    details = []
    expected_labels: list[str] = []
    predicted_labels: list[str] = []
    in_scope_expected: list[str] = []
    in_scope_predicted: list[str] = []
    by_file: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})

    for case in cases:
        diagnosis = engine.diagnose(str(case.get("input", "")))
        expected_type = str(case.get("expected_type", "UNKNOWN"))
        actual_type = diagnosis.error_type.value
        expected_rule = case.get("rule_id")
        rule_match = expected_rule is None or diagnosis.matched_rule == expected_rule
        passed = expected_type == actual_type and rule_match
        in_scope = expected_rule is None or expected_rule in implemented_rules

        expected_labels.append(expected_type)
        predicted_labels.append(actual_type)
        if in_scope:
            in_scope_expected.append(expected_type)
            in_scope_predicted.append(actual_type)
        by_file[case["source_file"]]["total"] += 1
        by_file[case["source_file"]]["passed"] += int(passed)
        details.append({
            "id": case["id"],
            "source_file": case["source_file"],
            "expected_type": expected_type,
            "actual_type": actual_type,
            "expected_rule": expected_rule,
            "actual_rule": diagnosis.matched_rule or None,
            "in_current_scope": in_scope,
            "passed": passed,
        })

    normal = [item for item in details if item["expected_type"] == "UNKNOWN"]
    false_positives = [item for item in normal if item["actual_type"] != "UNKNOWN"]
    in_scope_details = [item for item in details if item["in_current_scope"]]
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "implemented_rule_ids": sorted(implemented_rules),
        "summary": {
            "all_cases": len(details),
            "all_passed": sum(item["passed"] for item in details),
            "all_accuracy": round(sum(item["passed"] for item in details) / len(details), 4),
            "current_scope_cases": len(in_scope_details),
            "current_scope_passed": sum(item["passed"] for item in in_scope_details),
            "current_scope_accuracy": round(
                sum(item["passed"] for item in in_scope_details) / len(in_scope_details), 4
            ),
            "all_macro_f1": macro_f1(expected_labels, predicted_labels),
            "current_scope_macro_f1": macro_f1(in_scope_expected, in_scope_predicted),
            "normal_cases": len(normal),
            "false_positives": len(false_positives),
            "false_positive_rate": round(len(false_positives) / len(normal), 4) if normal else None,
        },
        "by_file": dict(sorted(by_file.items())),
        "details": details,
    }


def render_markdown(result: dict) -> str:
    summary = result["summary"]
    lines = [
        "# 诊断规则开发集评测",
        "",
        "> 本报告来自公开开发集，用于回归测试，不是最终竞赛成绩。",
        "",
        "## 核心结果",
        "",
        "| 口径 | 通过 | 准确率 | Macro-F1 |",
        "|---|---:|---:|---:|",
        f"| 全部公开开发题 | {summary['all_passed']}/{summary['all_cases']} | {summary['all_accuracy']:.1%} | {summary['all_macro_f1']:.3f} |",
        f"| 当前{len(result['implemented_rule_ids'])}条规则的支持范围 | {summary['current_scope_passed']}/{summary['current_scope_cases']} | {summary['current_scope_accuracy']:.1%} | {summary['current_scope_macro_f1']:.3f} |",
        f"| 正常日志误报 | {summary['false_positives']}/{summary['normal_cases']} | {summary['false_positive_rate']:.1%} | — |",
        "",
        "## 按原测试文件",
        "",
        "| 文件 | 通过 | 准确率 |",
        "|---|---:|---:|",
    ]
    for file_name, stats in result["by_file"].items():
        lines.append(
            f"| `{file_name}` | {stats['passed']}/{stats['total']} | "
            f"{stats['passed'] / stats['total']:.1%} |"
        )
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- “当前支持范围”按诊断引擎中已经存在的规则编号确定，不包含规划但未实现的类别。",
        "- 该评测只验证错误分类和规则命中，不等同于完整智能体的问答质量或修复成功率。",
        "- 最终竞赛结论必须再加入冻结测试集、同模型对照组和真实平台脚本运行结果。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行诊断规则开发集评测")
    parser.add_argument(
        "--cases-dir", type=Path, default=PROJECT_ROOT / "benchmark" / "cases"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "benchmark_v2" / "results" / "rule_development_eval.json",
    )
    args = parser.parse_args()

    result = evaluate(load_legacy_cases(args.cases_dir), DiagnosticsEngine())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output = args.output.with_suffix(".md")
    markdown_output.write_text(render_markdown(result), encoding="utf-8")
    summary = result["summary"]
    print(
        f"全部题目：{summary['all_passed']}/{summary['all_cases']} "
        f"({summary['all_accuracy']:.1%})"
    )
    print(
        f"当前规则范围：{summary['current_scope_passed']}/{summary['current_scope_cases']} "
        f"({summary['current_scope_accuracy']:.1%})"
    )
    print(
        f"正常日志误报：{summary['false_positives']}/{summary['normal_cases']} "
        f"({summary['false_positive_rate']:.1%})"
    )
    print(f"结果已保存：{args.output}")
    print(f"可读报告已保存：{markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
