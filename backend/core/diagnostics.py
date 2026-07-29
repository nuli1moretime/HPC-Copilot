"""日志诊断引擎。

职责：加载 YAML 错误模式，对用户粘贴的日志做正则匹配，
产出确定性的 DiagnosisResult。不依赖大模型。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from .models import DiagnosisResult, ErrorType

# 默认配置文件路径
_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "error_patterns.yaml"


class DiagnosticsEngine:
    """基于正则规则的错误诊断引擎。"""

    def __init__(self, config_path: Optional[Path] = None):
        self._patterns: list[dict] = []
        self._load_config(config_path or _DEFAULT_CONFIG)

    def _load_config(self, path: Path) -> None:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._patterns = data.get("error_patterns", [])
        # 预编译正则
        for p in self._patterns:
            p["_compiled"] = re.compile(p["pattern"], re.IGNORECASE | re.MULTILINE)

    def diagnose(self, log_text: str) -> DiagnosisResult:
        """对一段日志文本进行诊断，返回最可能的错误类型。

        匹配逻辑：按配置顺序逐条匹配，返回第一个命中的结果。
        如果没有任何规则命中，返回 UNKNOWN。
        """
        if not log_text.strip():
            return DiagnosisResult(
                error_type=ErrorType.UNKNOWN,
                confidence=0.0,
                evidence=[],
                root_cause_brief="日志为空，无法判断",
            )

        for pattern in self._patterns:
            matches = pattern["_compiled"].findall(log_text)
            if matches:
                # 提取命中行作为证据
                evidence = self._extract_evidence_lines(log_text, pattern["_compiled"])
                return DiagnosisResult(
                    error_type=ErrorType(pattern["error_type"]),
                    confidence=pattern["confidence"],
                    evidence=evidence,
                    matched_rule=pattern["id"],
                    root_cause_brief=pattern["root_cause_brief"],
                    suggested_commands=pattern.get("suggested_commands", []),
                )

        # 没有命中任何规则
        return DiagnosisResult(
            error_type=ErrorType.UNKNOWN,
            confidence=0.0,
            evidence=[],
            root_cause_brief="未匹配到已知错误模式，可能需要人工查看或补充规则",
        )

    def _extract_evidence_lines(
        self, log_text: str, compiled: re.Pattern, max_lines: int = 3
    ) -> list[str]:
        """提取包含匹配内容的行，作为证据展示给用户。"""
        evidence = []
        for line in log_text.splitlines():
            if compiled.search(line):
                evidence.append(line.strip())
                if len(evidence) >= max_lines:
                    break
        return evidence

    def diagnose_job(
        self,
        state: str,
        exit_code: Optional[int] = None,
        state_reason: str = "",
        log_text: str = "",
    ) -> DiagnosisResult:
        """基于结构化作业状态数据诊断（优先于纯文本正则）。

        当从 REST API 或 sacct 拿到结构化信息时，用此方法更准确。
        """
        evidence = []
        if state:
            evidence.append(f"作业状态: {state}")
        if state_reason:
            evidence.append(f"原因: {state_reason}")
        if exit_code is not None:
            evidence.append(f"退出码: {exit_code}")

        # 1. 状态直接判定
        if state == "TIMEOUT":
            return self._build_result("timeout", evidence)
        if state == "OUT_OF_MEMORY":
            return self._build_result("out_of_memory", evidence)
        if state == "CANCELLED" and "TIME LIMIT" in state_reason.upper():
            return self._build_result("timeout", evidence)

        # 2. 退出码判定
        if state_reason == "NonZeroExitCode" or (exit_code is not None and exit_code != 0):
            if exit_code == 2:
                result = self._build_result("entrypoint_not_found", evidence)
                result.evidence.append("退出码 2 通常表示指定的脚本或文件不存在")
                return result
            if exit_code == 1:
                result = self._build_result("program_exit_nonzero", evidence)
                result.evidence.append("退出码 1 通常表示程序抛出了未捕获的异常")
                return result
            result = self._build_result("program_exit_nonzero", evidence)
            result.evidence.append(f"非零退出码 {exit_code} 表示程序异常终止")
            return result

        # 3. 回退到正则匹配
        if log_text.strip():
            return self.diagnose(log_text)

        return DiagnosisResult(
            error_type=ErrorType.UNKNOWN,
            confidence=0.0,
            evidence=evidence,
            root_cause_brief="结构化数据不足以判断，需要更多日志信息",
        )

    def _build_result(self, rule_id: str, evidence: list[str]) -> DiagnosisResult:
        """根据规则 ID 构建 DiagnosisResult。"""
        for p in self._patterns:
            if p["id"] == rule_id:
                return DiagnosisResult(
                    error_type=ErrorType(p["error_type"]),
                    confidence=p["confidence"],
                    evidence=evidence,
                    matched_rule=rule_id,
                    root_cause_brief=p["root_cause_brief"],
                    suggested_commands=p.get("suggested_commands", []),
                )
        return DiagnosisResult(
            error_type=ErrorType.UNKNOWN,
            confidence=0.0,
            evidence=evidence,
            root_cause_brief=f"未找到规则 {rule_id}",
        )

    def get_pattern_hint(self, error_type: ErrorType) -> str:
        """获取某类错误的解释提示（供大模型参考）。"""
        for p in self._patterns:
            if p["error_type"] == error_type.value:
                return p.get("explanation_hint", "")
        return ""
