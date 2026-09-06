"""冻结正式评测所使用的代码、知识库、题目和评测程序指纹。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import create_freeze_manifest, load_yaml, validate_case_set


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Benchmark v2 冻结清单")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "benchmark_v2" / "results" / "freeze_manifest.json",
    )
    args = parser.parse_args()
    case_file = args.cases.resolve()

    data = load_yaml(case_file)
    errors = validate_case_set(data)
    if errors:
        for error in errors:
            print(f"- {error}")
        return 1
    if data.get("status") != "frozen":
        print("拒绝冻结：正式题目文件的 status 必须是 frozen")
        return 2

    manifest = create_freeze_manifest(PROJECT_ROOT, case_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"冻结清单已生成：{args.output}")
    print(f"聚合指纹：{manifest['aggregate_sha256']}")
    print(f"工作区存在未提交修改：{'是' if manifest['git_dirty'] else '否'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
