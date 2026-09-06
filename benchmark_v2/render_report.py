"""把结构化评分结果渲染成可审查的Markdown报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


CATEGORY_NAMES = {
    "platform_qa": "平台问答",
    "command_explanation": "命令解释",
    "script_generation": "脚本生成",
    "diagnosis": "报错诊断",
}


def fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def build_report(data: dict) -> str:
    status = data.get("suite_status", "unknown")
    lines = [
        f"# HPC Copilot Benchmark 报告：{data.get('suite_id', 'unknown')}",
        "",
        f"- 数据集状态：`{status}`",
        f"- 模型：`{data.get('model') or '未记录'}`",
        f"- 温度：`{data.get('temperature')}`",
        "",
    ]
    if status != "frozen":
        lines.extend([
            "> 这是开发集结果，只能用于调试和改进系统，不能作为最终竞赛成绩。",
            "",
        ])

    systems = data.get("systems", {})
    categories = sorted(
        {
            category
            for system in systems.values()
            for category in system.get("summary", {}).get("category_scores", {})
        }
    )
    lines.extend([
        "## 汇总",
        "",
        "| 系统 | 总分 | 诊断Macro-F1 | 禁止项命中 | 平均延迟/秒 | 完成题数 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for name, system in systems.items():
        summary = system["summary"]
        lines.append(
            f"| {name} | {fmt(summary['overall_score'])} | "
            f"{fmt(summary['diagnosis_macro_f1'])} | {summary['forbidden_violation_count']} | "
            f"{fmt(summary['average_latency_seconds'])} | "
            f"{summary['completed_cases']}/{summary['total_cases']} |"
        )

    incomplete = [name for name, system in systems.items() if not system["summary"].get("run_complete", False)]
    if incomplete:
        lines.extend([
            "",
            "> 以下系统运行不完整，缺失项显示为“—”，不能将其解释为0分："
            + "、".join(incomplete) + "。",
        ])

    if "hpc_copilot" in systems:
        lines.extend([
            "",
            "> `hpc_copilot` 在这份离线对照中只对诊断题额外加入确定性规则结果；"
            "其他题与 `llm_rag` 的输入相同，分数差异可能来自模型生成波动，不能解释为工具调用增益。"
            "真正的端到端增益需通过后续真实平台提交、状态跟踪和输出验证衡量。",
        ])

    lines.extend(["", "## 分题型得分", ""])
    header = "| 系统 | " + " | ".join(CATEGORY_NAMES.get(cat, cat) for cat in categories) + " |"
    separator = "|---|" + "---:|" * len(categories)
    lines.extend([header, separator])
    for name, system in systems.items():
        scores = system["summary"].get("category_scores", {})
        lines.append("| " + name + " | " + " | ".join(fmt(scores.get(cat)) for cat in categories) + " |")

    lines.extend(["", "## 未通过的客观检查", ""])
    any_failure = False
    for name, system in systems.items():
        failures = []
        for case_id, case_score in system.get("cases", {}).items():
            failed_checks = [item["name"] for item in case_score.get("checks", []) if not item["passed"]]
            if failed_checks or case_score.get("forbidden_violations") or case_score.get("response_empty"):
                failures.append(
                    f"- `{case_id}`：得分 {case_score['score']:.2f}；"
                    f"未通过 {', '.join(failed_checks) or '无'}；"
                    f"禁止项 {len(case_score.get('forbidden_violations', []))}"
                )
        if failures:
            any_failure = True
            lines.extend([f"### {name}", "", *failures, ""])
    if not any_failure:
        lines.extend(["所有客观检查均通过。", ""])

    lines.extend([
        "## 解释边界",
        "",
        "- 该报告的自动分数来自预先固定的关键词、正则和错误类别，不使用大模型裁判。",
        "- 脚本题的自动分数只代表结构和关键参数正确，不能替代真实平台提交结果。",
        "- 正式报告还必须合并每个脚本的提交状态、最终状态、退出码和关键输出。",
        "- 这是针对107算力平台的项目组自建评测，不应表述为第三方公开Benchmark。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成Benchmark Markdown报告")
    parser.add_argument("--scored", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.scored.read_text(encoding="utf-8"))
    output = args.output or args.scored.with_suffix(".md")
    output.write_text(build_report(data), encoding="utf-8")
    print(f"报告已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
