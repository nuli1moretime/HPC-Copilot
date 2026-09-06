"""使用题目中预先固定的规则，对三组原始回答进行客观评分。"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from core import load_yaml, score_response, validate_case_set


def parse_diagnosis_type(
    response: str, label_patterns: dict[str, list[str]] | None = None
) -> str:
    match = re.search(r"\[DIAGNOSIS_TYPE\s*:\s*([A-Z_]+)\]", response, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if label_patterns:
        first_line = response.splitlines()[0] if response else ""
        for text in (first_line, response):
            for label, patterns in label_patterns.items():
                if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns):
                    return label
    return "UNPARSEABLE"


def macro_f1(labels: list[str], predictions: list[str]) -> float | None:
    if not labels:
        return None
    classes = sorted(set(labels))
    scores: list[float] = []
    for label in classes:
        true_positive = sum(1 for y, p in zip(labels, predictions) if y == label and p == label)
        false_positive = sum(1 for y, p in zip(labels, predictions) if y != label and p == label)
        false_negative = sum(1 for y, p in zip(labels, predictions) if y == label and p != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 4)


def score_all(case_data: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    case_map = {case["id"]: case for case in case_data["cases"]}
    diagnosis_patterns: dict[str, list[str]] = defaultdict(list)
    for case in case_data["cases"]:
        if case.get("category") == "diagnosis":
            label = case["expected"]["error_type"]
            diagnosis_patterns[label].extend(case["expected"].get("label_patterns", []))
    scored_systems: dict[str, Any] = {}
    for system_name, system_data in raw.get("systems", {}).items():
        scored_cases: dict[str, Any] = {}
        category_scores: dict[str, list[float]] = defaultdict(list)
        category_totals: dict[str, int] = defaultdict(int)
        category_completed: dict[str, int] = defaultdict(int)
        diagnosis_labels: list[str] = []
        diagnosis_predictions: list[str] = []
        diagnosis_total = 0
        diagnosis_completed = 0
        violations = 0
        latencies: list[float] = []

        for case_id, case in case_map.items():
            result = system_data.get("cases", {}).get(case_id, {})
            response = str(result.get("response", ""))
            response_present = bool(response.strip()) and not result.get("error")
            score = score_response(case, response)
            predicted_type = None
            if case["category"] == "diagnosis":
                diagnosis_total += 1
                predicted_type = parse_diagnosis_type(response, dict(diagnosis_patterns))
                if response_present:
                    diagnosis_completed += 1
                    diagnosis_labels.append(case["expected"]["error_type"])
                    diagnosis_predictions.append(predicted_type)
            score["predicted_error_type"] = predicted_type
            score["response_empty"] = not response_present
            score["error"] = result.get("error")
            scored_cases[case_id] = score
            category_totals[case["category"]] += 1
            if response_present:
                category_completed[case["category"]] += 1
                category_scores[case["category"]].append(score["score"])
            violations += len(score["forbidden_violations"])
            if isinstance(result.get("elapsed_seconds"), (int, float)):
                latencies.append(float(result["elapsed_seconds"]))

        completed_scores = [item["score"] for item in scored_cases.values() if not item["response_empty"]]
        completed_count = len(completed_scores)
        run_complete = completed_count == len(case_map)
        scored_systems[system_name] = {
            "summary": {
                "overall_score": round(sum(completed_scores) / len(completed_scores), 2)
                if run_complete and completed_scores
                else None,
                "category_scores": {
                    category: round(sum(category_scores[category]) / len(category_scores[category]), 2)
                    if category_completed[category] == total and category_scores[category]
                    else None
                    for category, total in sorted(category_totals.items())
                },
                "diagnosis_macro_f1": macro_f1(diagnosis_labels, diagnosis_predictions)
                if diagnosis_completed == diagnosis_total
                else None,
                "forbidden_violation_count": violations,
                "average_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
                "completed_cases": completed_count,
                "total_cases": len(case_map),
                "run_complete": run_complete,
            },
            "cases": scored_cases,
        }

    return {
        "schema_version": 1,
        "suite_id": case_data["suite_id"],
        "suite_status": case_data["status"],
        "model": raw.get("model"),
        "temperature": raw.get("temperature"),
        "systems": scored_systems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="客观评分HPC Copilot对照实验")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    case_data = load_yaml(args.cases)
    errors = validate_case_set(case_data)
    if errors:
        for error in errors:
            print(f"- {error}")
        return 1
    raw = json.loads(args.results.read_text(encoding="utf-8"))
    if raw.get("suite_id") != case_data["suite_id"]:
        print("题目与结果的 suite_id 不一致")
        return 2

    scored = score_all(case_data, raw)
    output = args.output or args.results.with_name(args.results.stem.replace("_responses", "_scored") + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    for system_name, system_data in scored["systems"].items():
        summary = system_data["summary"]
        overall = f"{summary['overall_score']:.2f}" if summary["overall_score"] is not None else "未完成"
        print(
            f"{system_name}: 总分={overall}, "
            f"诊断Macro-F1={summary['diagnosis_macro_f1']}, "
            f"违规={summary['forbidden_violation_count']}"
        )
    print(f"评分结果已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
