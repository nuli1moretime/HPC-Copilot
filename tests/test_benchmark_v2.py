from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "benchmark_v2"))

from core import create_freeze_manifest, extract_script, load_yaml, score_response, validate_case_set  # noqa: E402
from render_report import build_report  # noqa: E402
from merge_platform_results import summarize_platform  # noqa: E402
from run_rule_eval import evaluate, load_legacy_cases, render_markdown as render_rule_markdown  # noqa: E402
from run_retrieval_eval import build_markdown as build_retrieval_report, metric_summary  # noqa: E402
from score_results import macro_f1, parse_diagnosis_type, score_all  # noqa: E402
from backend.core.diagnostics import DiagnosticsEngine  # noqa: E402


DEV_CASES = PROJECT_ROOT / "benchmark_v2" / "cases" / "development.yaml"
FINAL_CASES = PROJECT_ROOT / "benchmark_v2" / "cases" / "final" / "final_v1.yaml"


def test_development_cases_are_valid_and_cover_all_categories():
    data = load_yaml(DEV_CASES)
    assert validate_case_set(data) == []
    assert {case["category"] for case in data["cases"]} == {
        "platform_qa",
        "command_explanation",
        "script_generation",
        "diagnosis",
    }


def test_final_cases_are_frozen_valid_and_have_planned_coverage():
    data = load_yaml(FINAL_CASES)
    assert data["status"] == "frozen"
    assert validate_case_set(data) == []
    assert len(data["cases"]) == 30
    assert {case["category"] for case in data["cases"]} == {
        "platform_qa",
        "command_explanation",
        "script_generation",
        "diagnosis",
    }


def test_objective_scoring_rewards_required_facts_and_penalizes_forbidden_claims():
    case = {
        "category": "platform_qa",
        "expected": {
            "concept_groups": [["P107-A100"], ["qos_p107-a100"]],
            "forbidden_patterns": ["P107-V100"],
        },
    }
    good = score_response(case, "使用 P107-A100 和 qos_p107-a100。")
    bad = score_response(case, "使用 P107-A100 和 qos_p107-a100，也可以用 P107-V100。")
    assert good["score"] == 100.0
    assert bad["score"] == 75.0
    assert bad["forbidden_violations"] == ["P107-V100"]


def test_diagnosis_type_parser_and_macro_f1():
    assert parse_diagnosis_type("[DIAGNOSIS_TYPE: TIMEOUT]\n已超时") == "TIMEOUT"
    assert parse_diagnosis_type("没有结构化首行") == "UNPARSEABLE"
    assert parse_diagnosis_type(
        "[DIAGNOSIS_TYPE: 作业超时被取消]\n超过时间上限",
        {"TIMEOUT": ["超时", "时间上限"]},
    ) == "TIMEOUT"
    assert macro_f1(["A", "A", "B"], ["A", "B", "B"]) == 0.6667


def test_script_scoring_ignores_commands_only_mentioned_outside_script():
    response = "提交前运行 mkdir -p logs\n```bash\n#!/bin/bash\necho ok\n```"
    assert extract_script(response) == "#!/bin/bash\necho ok"
    case = {
        "category": "script_generation",
        "expected": {
            "concept_groups": [],
            "required_patterns": ["mkdir\\s+-p\\s+logs"],
            "forbidden_patterns": [],
        },
    }
    assert score_response(case, response)["score"] == 0.0


def test_score_all_keeps_three_systems_separate():
    case_data = {
        "suite_id": "tiny",
        "status": "development",
        "cases": [
            {
                "id": "q1",
                "category": "platform_qa",
                "expected": {"concept_groups": [["squeue"]], "forbidden_patterns": []},
            }
        ],
    }
    raw = {
        "model": "same-model",
        "temperature": 0.0,
        "systems": {
            "llm_only": {"cases": {"q1": {"response": "不知道", "elapsed_seconds": 1.0}}},
            "llm_rag": {"cases": {"q1": {"response": "使用 squeue", "elapsed_seconds": 1.2}}},
        },
    }
    scored = score_all(case_data, raw)
    assert scored["systems"]["llm_only"]["summary"]["overall_score"] == 0.0
    assert scored["systems"]["llm_rag"]["summary"]["overall_score"] == 100.0


def test_freeze_manifest_contains_case_and_aggregate_hash():
    manifest = create_freeze_manifest(PROJECT_ROOT, DEV_CASES)
    assert len(manifest["case_file_sha256"]) == 64
    assert len(manifest["aggregate_sha256"]) == 64
    assert "backend/main.py" in manifest["files"]
    assert "benchmark_v2/cases/final/final_v1.yaml" in manifest["files"]
    assert "backend/data/connection_config.json" not in manifest["files"]


def test_report_warns_when_suite_is_not_frozen():
    report = build_report(
        {
            "suite_id": "tiny",
            "suite_status": "development",
            "model": "same-model",
            "temperature": 0.0,
            "systems": {
                "llm_only": {
                    "summary": {
                        "overall_score": 50.0,
                        "category_scores": {"platform_qa": 50.0},
                        "diagnosis_macro_f1": None,
                        "forbidden_violation_count": 0,
                        "average_latency_seconds": 1.0,
                        "completed_cases": 1,
                        "total_cases": 1,
                    },
                    "cases": {},
                }
            },
        }
    )
    assert "开发集结果" in report
    assert "不能作为最终竞赛成绩" in report


def test_rule_eval_separates_current_scope_from_planned_rules():
    result = evaluate(
        load_legacy_cases(PROJECT_ROOT / "benchmark" / "cases"),
        DiagnosticsEngine(),
    )
    summary = result["summary"]
    assert summary["all_cases"] == 74
    assert summary["current_scope_cases"] == summary["all_cases"]
    assert summary["current_scope_accuracy"] == 1.0
    assert summary["false_positive_rate"] == 0.0
    report = render_rule_markdown(result)
    assert "74/74" in report
    assert "最终竞赛成绩" in report


def test_platform_summary_uses_first_attempt_and_final_state():
    summary = summarize_platform(
        {
            "runs": [
                {
                    "system": "hpc_copilot",
                    "first_submission_success": True,
                    "final_state": "COMPLETED",
                    "expected_output_found": True,
                },
                {
                    "system": "hpc_copilot",
                    "first_submission_success": False,
                    "final_state": "FAILED",
                    "expected_output_found": False,
                },
            ]
        }
    )["hpc_copilot"]
    assert summary["first_submission_success_rate"] == 0.5
    assert summary["completion_rate"] == 0.5
    assert summary["expected_output_rate"] == 0.5


def test_retrieval_metrics_count_only_hits_within_k():
    summary = metric_summary([1, 2, 0, 4], k=3)
    assert summary["hits"] == 2
    assert summary["hit_rate"] == 0.5
    assert summary["mrr"] == 0.375


def test_retrieval_report_separates_file_and_section_misses():
    report = build_retrieval_report(
        {
            "embedding_model": "embed-test",
            "production_top_k": 3,
            "search_top_k": 10,
            "reranker_enabled": False,
            "embedding_failures": 0,
            "elapsed_seconds": 1.0,
            "metrics": {
                key: {"hits": 1, "total": 2, "hit_rate": 0.5, "mrr": 0.5}
                for key in (
                    "strict_section_at_1",
                    "strict_section_at_3",
                    "file_at_1",
                    "file_at_3",
                )
            },
            "details": [
                {
                    "id": "a",
                    "question": "A?",
                    "strict_section_rank": 0,
                    "file_rank": 2,
                    "production_results": [],
                },
                {
                    "id": "b",
                    "question": "B?",
                    "strict_section_rank": 0,
                    "file_rank": 0,
                    "production_results": [],
                },
            ],
        }
    )
    assert "`a`" in report
    assert "`b`" in report
    assert "正确文件排名2" in report
