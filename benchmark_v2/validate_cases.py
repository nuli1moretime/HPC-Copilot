"""校验评测题目的格式、来源标记和重复项。"""

from __future__ import annotations

import argparse
from pathlib import Path

from core import load_yaml, validate_case_set


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 HPC Copilot Benchmark v2 题目")
    parser.add_argument("cases", type=Path)
    args = parser.parse_args()

    data = load_yaml(args.cases)
    errors = validate_case_set(data)
    if errors:
        print(f"校验失败，共 {len(errors)} 个问题：")
        for error in errors:
            print(f"- {error}")
        return 1

    categories: dict[str, int] = {}
    sources: dict[str, int] = {}
    for case in data["cases"]:
        categories[case["category"]] = categories.get(case["category"], 0) + 1
        source_type = case["source"]["type"]
        sources[source_type] = sources.get(source_type, 0) + 1

    print(f"校验通过：{data['suite_id']}，共 {len(data['cases'])} 题")
    print("题型：" + "，".join(f"{key}={value}" for key, value in sorted(categories.items())))
    print("来源：" + "，".join(f"{key}={value}" for key, value in sorted(sources.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
