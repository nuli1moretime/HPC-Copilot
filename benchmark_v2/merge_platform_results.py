"""将真实平台脚本运行记录合并到自动评分结果。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml


def summarize_platform(data: dict) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for run in data.get("runs", []):
        grouped[str(run.get("system", "unknown"))].append(run)
    summary = {}
    for system, runs in sorted(grouped.items()):
        total = len(runs)
        first_success = sum(bool(item.get("first_submission_success")) for item in runs)
        completed = sum(str(item.get("final_state", "")).upper() == "COMPLETED" for item in runs)
        expected_output = sum(bool(item.get("expected_output_found")) for item in runs)
        summary[system] = {
            "script_runs": total,
            "first_submission_success": first_success,
            "first_submission_success_rate": round(first_success / total, 4) if total else None,
            "completed": completed,
            "completion_rate": round(completed / total, 4) if total else None,
            "expected_output_found": expected_output,
            "expected_output_rate": round(expected_output / total, 4) if total else None,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="合并真实平台脚本运行结果")
    parser.add_argument("--scored", required=True, type=Path)
    parser.add_argument("--platform", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scored = json.loads(args.scored.read_text(encoding="utf-8"))
    platform = yaml.safe_load(args.platform.read_text(encoding="utf-8")) or {}
    if platform.get("suite_id") != scored.get("suite_id"):
        print("平台记录与自动评分的 suite_id 不一致")
        return 1
    scored["platform_execution"] = {
        "freeze_aggregate_sha256": platform.get("freeze_aggregate_sha256"),
        "summary": summarize_platform(platform),
        "runs": platform.get("runs", []),
    }
    output = args.output or args.scored.with_name(args.scored.stem + "_with_platform.json")
    output.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已合并真实平台记录：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
