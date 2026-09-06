"""Benchmark v2 的数据校验、指纹和客观评分基础函数。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ALLOWED_CATEGORIES = {"platform_qa", "command_explanation", "script_generation", "diagnosis"}
ALLOWED_SOURCE_TYPES = {"official_doc", "real_platform_log", "synthetic_variant"}
ALLOWED_STATUSES = {"development", "frozen"}


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return data


def validate_case_set(data: dict[str, Any]) -> list[str]:
    """返回全部格式问题；空列表表示通过。"""
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if not str(data.get("suite_id", "")).strip():
        errors.append("缺少 suite_id")
    if data.get("status") not in ALLOWED_STATUSES:
        errors.append("status 必须是 development 或 frozen")

    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases 必须是非空列表")
        return errors

    seen_ids: set[str] = set()
    seen_prompts: dict[str, str] = {}
    for index, case in enumerate(cases, start=1):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{where} 必须是对象")
            continue

        case_id = str(case.get("id", "")).strip()
        if not case_id:
            errors.append(f"{where} 缺少 id")
        elif case_id in seen_ids:
            errors.append(f"{where} id 重复: {case_id}")
        seen_ids.add(case_id)

        category = case.get("category")
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{case_id or where} category 无效: {category}")

        prompt = str(case.get("prompt", "")).strip()
        if not prompt:
            errors.append(f"{case_id or where} 缺少 prompt")
        else:
            normalized = re.sub(r"\s+", "", prompt).lower()
            previous = seen_prompts.get(normalized)
            if previous:
                errors.append(f"{case_id} 与 {previous} 的 prompt 完全重复")
            seen_prompts[normalized] = case_id

        source = case.get("source")
        if not isinstance(source, dict):
            errors.append(f"{case_id or where} 缺少 source")
        else:
            if source.get("type") not in ALLOWED_SOURCE_TYPES:
                errors.append(f"{case_id} source.type 无效: {source.get('type')}")
            if not str(source.get("reference", "")).strip():
                errors.append(f"{case_id} 缺少 source.reference")

        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id or where} 缺少 expected")
            continue
        if category == "diagnosis" and not str(expected.get("error_type", "")).strip():
            errors.append(f"{case_id} 诊断题缺少 expected.error_type")

        concept_groups = expected.get("concept_groups", [])
        if not isinstance(concept_groups, list):
            errors.append(f"{case_id} expected.concept_groups 必须是列表")
        else:
            for group_index, group in enumerate(concept_groups, start=1):
                if not isinstance(group, list) or not group or not all(isinstance(p, str) and p for p in group):
                    errors.append(f"{case_id} concept_groups[{group_index}] 必须是非空正则字符串列表")

    return errors


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def extract_script(response: str) -> str:
    """脚本题只评估首个shell代码块，避免说明文字造成假阳性。"""
    shell_block = re.search(
        r"```(?:bash|sh|shell)\s*\n(.*?)```", response, flags=re.IGNORECASE | re.DOTALL
    )
    if shell_block:
        return shell_block.group(1).strip()
    generic_block = re.search(r"```\s*\n(.*?)```", response, flags=re.DOTALL)
    return generic_block.group(1).strip() if generic_block else response


def score_response(case: dict[str, Any], response: str) -> dict[str, Any]:
    """按预先写入的客观规则评分，不调用大模型裁判。"""
    expected = case.get("expected", {})
    checks: list[dict[str, Any]] = []
    evaluation_text = extract_script(response) if case.get("category") == "script_generation" else response

    for index, alternatives in enumerate(expected.get("concept_groups", []), start=1):
        passed = _matches_any(evaluation_text, alternatives)
        checks.append({
            "name": f"concept_{index}",
            "passed": passed,
            "weight": 1.0,
            "patterns": alternatives,
        })

    if case.get("category") == "diagnosis":
        label_patterns = expected.get("label_patterns") or [re.escape(str(expected.get("error_type", "")))]
        checks.append({
            "name": "error_type",
            "passed": _matches_any(evaluation_text, label_patterns),
            "weight": 2.0,
            "patterns": label_patterns,
        })

    for index, pattern in enumerate(expected.get("required_patterns", []), start=1):
        checks.append({
            "name": f"required_{index}",
            "passed": bool(re.search(pattern, evaluation_text, flags=re.IGNORECASE | re.MULTILINE)),
            "weight": 1.0,
            "patterns": [pattern],
        })

    violations: list[str] = []
    for pattern in expected.get("forbidden_patterns", []):
        if re.search(pattern, evaluation_text, flags=re.IGNORECASE | re.MULTILINE):
            violations.append(pattern)

    earned = sum(item["weight"] for item in checks if item["passed"])
    possible = sum(item["weight"] for item in checks)
    raw_score = earned / possible * 100 if possible else 0.0
    penalty = min(100.0, len(violations) * 25.0)
    score = max(0.0, raw_score - penalty)
    return {
        "score": round(score, 2),
        "earned_weight": earned,
        "possible_weight": possible,
        "checks": checks,
        "forbidden_violations": violations,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_evaluation_files(project_root: Path) -> list[Path]:
    """收集会影响最终回答或评测结论的文件，排除凭证和生成结果。"""
    patterns = (
        "backend/**/*.py",
        "backend/config/*.yaml",
        "docs/knowledge/*.md",
        "frontend/src/**/*.ts",
        "frontend/src/**/*.tsx",
        "benchmark_v2/*.py",
        "benchmark_v2/**/*.yaml",
    )
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path for path in project_root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda item: item.as_posix())


def create_freeze_manifest(project_root: Path, case_file: Path) -> dict[str, Any]:
    files = tracked_evaluation_files(project_root)
    file_hashes = {
        str(path.relative_to(project_root)).replace("\\", "/"): sha256_file(path)
        for path in files
    }
    aggregate_payload = json.dumps(file_hashes, sort_keys=True, ensure_ascii=False).encode("utf-8")
    aggregate_hash = hashlib.sha256(aggregate_payload).hexdigest()

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=project_root, capture_output=True, text=True, check=False
        )
        return completed.stdout.strip()

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_file": str(case_file.relative_to(project_root)).replace("\\", "/"),
        "case_file_sha256": sha256_file(case_file),
        "git_head": git("rev-parse", "HEAD") or None,
        "git_dirty": bool(git("status", "--porcelain")),
        "aggregate_sha256": aggregate_hash,
        "files": file_hashes,
    }
