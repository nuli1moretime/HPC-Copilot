"""
HPC Copilot 诊断引擎 Benchmark 评测脚本
用法：python benchmark/run_benchmark.py
"""

import sys
import os
from pathlib import Path
from dataclasses import dataclass

import yaml

# 将 backend 加入 path 以导入诊断引擎
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from core.diagnostics import DiagnosticsEngine  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass
class CaseResult:
    case_id: str
    source_file: str
    expected_type: str
    actual_type: str
    expected_rule: str | None
    actual_rule: str | None
    passed: bool
    note: str = ""


def load_cases() -> list[dict]:
    """加载所有测试案例"""
    all_cases = []
    for yaml_file in sorted(CASES_DIR.glob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and "cases" in data:
            for case in data["cases"]:
                case["_source_file"] = yaml_file.name
            all_cases.extend(data["cases"])
    return all_cases


def run_benchmark(verbose: bool = False) -> list[CaseResult]:
    """执行全部测试案例"""
    engine = DiagnosticsEngine()
    cases = load_cases()
    results = []

    print(f"{'=' * 70}")
    print(f"  HPC Copilot Diagnostics Benchmark")
    print(f"  Rules: {len(engine._patterns)} | Test cases: {len(cases)}")
    print(f"{'=' * 70}\n")

    for case in cases:
        input_text = case.get("input", "")
        expected_type = case.get("expected_type", "UNKNOWN")
        expected_rule = case.get("rule_id")
        source_file = case["_source_file"]

        # 运行诊断
        diagnosis = engine.diagnose(input_text)
        actual_type = diagnosis.error_type.value if hasattr(diagnosis.error_type, "value") else str(diagnosis.error_type)
        actual_rule = diagnosis.matched_rule if diagnosis.matched_rule else None

        # 判断是否通过
        type_match = actual_type == expected_type
        # 对于有明确 rule_id 的案例，还检查规则是否匹配
        rule_match = (expected_rule is None) or (actual_rule == expected_rule)
        passed = type_match and rule_match

        note = ""
        if not type_match:
            note = f"类型不匹配: 期望 {expected_type}, 实际 {actual_type}"
        elif not rule_match:
            note = f"规则不匹配: 期望 {expected_rule}, 实际 {actual_rule}"

        results.append(CaseResult(
            case_id=case["id"],
            source_file=source_file,
            expected_type=expected_type,
            actual_type=actual_type,
            expected_rule=expected_rule,
            actual_rule=actual_rule,
            passed=passed,
            note=note,
        ))

        # 输出单条结果
        status = "[PASS]" if passed else "[FAIL]"
        if verbose or not passed:
            print(f"  {status}  {case['id']:<28} [{source_file}]")
            if note:
                print(f"         -> {note}")

    return results


def print_summary(results: list[CaseResult]):
    """输出汇总报告"""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    # 按文件分组统计
    file_stats: dict[str, dict] = {}
    for r in results:
        if r.source_file not in file_stats:
            file_stats[r.source_file] = {"total": 0, "passed": 0}
        file_stats[r.source_file]["total"] += 1
        if r.passed:
            file_stats[r.source_file]["passed"] += 1

    # 按错误类型统计覆盖率
    error_types: dict[str, dict] = {}
    for r in results:
        if r.expected_type != "UNKNOWN":
            if r.expected_type not in error_types:
                error_types[r.expected_type] = {"total": 0, "passed": 0}
            error_types[r.expected_type]["total"] += 1
            if r.passed:
                error_types[r.expected_type]["passed"] += 1

    # 误报统计（正常输出被误判为错误）
    false_positives = [r for r in results if r.expected_type == "UNKNOWN" and r.actual_type != "UNKNOWN"]

    print(f"\n{'=' * 70}")
    print(f"  EVALUATION SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n  Total: {total} cases")
    print(f"  Passed: {passed} ({passed/total*100:.1f}%)")
    print(f"  Failed: {failed} ({failed/total*100:.1f}%)")

    print(f"\n{'-' * 70}")
    print(f"  By file:")
    print(f"{'-' * 70}")
    for fname, stats in file_stats.items():
        bar = "#" * int(stats["passed"] / stats["total"] * 20)
        print(f"  {fname:<25} {stats['passed']:>3}/{stats['total']:<3}  {bar}")

    print(f"\n{'-' * 70}")
    print(f"  Error type coverage:")
    print(f"{'-' * 70}")
    for etype, stats in sorted(error_types.items()):
        status = "[OK]" if stats["passed"] == stats["total"] else "[--]"
        print(f"  {status} {etype:<25} {stats['passed']}/{stats['total']}")

    # 尚未实现的规则（全部 FAIL 的类型）
    not_implemented = [et for et, s in error_types.items() if s["passed"] == 0]
    if not_implemented:
        print(f"\n{'-' * 70}")
        print(f"  Not yet implemented ({len(not_implemented)} types):")
        print(f"{'-' * 70}")
        for et in not_implemented:
            print(f"    - {et}")

    # 误报
    if false_positives:
        print(f"\n{'-' * 70}")
        print(f"  WARNING: False positives ({len(false_positives)}):")
        print(f"{'-' * 70}")
        for r in false_positives:
            print(f"    {r.case_id}: misclassified as {r.actual_type}")
    else:
        normal_total = sum(1 for r in results if r.expected_type == "UNKNOWN")
        if normal_total > 0:
            print(f"\n  False positive rate: 0% ({normal_total} normal outputs all clear)")

    print(f"\n{'=' * 70}\n")

    return failed == 0


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    results = run_benchmark(verbose=verbose)
    all_pass = print_summary(results)
    sys.exit(0 if all_pass else 1)
