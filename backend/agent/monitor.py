"""Agent 监听器：实时分析终端输出，自动触发诊断。

Agent 全程处于监听状态，当检测到终端输出中包含已知错误模式时，
自动触发诊断并通知前端（推送到对话面板）。
"""

import sys
from pathlib import Path
from typing import Optional

# 确保能导入 core 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.diagnostics import DiagnosticsEngine
from core.models import DiagnosisResult, ErrorType


class AgentMonitor:
    """流式接收终端输出，检测到错误时返回诊断结果。

    使用方式：
        monitor = AgentMonitor()
        # 每收到一段终端输出就 feed 进去
        diagnosis = monitor.feed(chunk)
        if diagnosis:
            # 检测到错误，推送给前端
            await notify_frontend(diagnosis)
    """

    def __init__(self, config_path: Optional[Path] = None):
        """初始化监听器。

        Args:
            config_path: 错误模式配置文件路径（默认使用 backend/config/）
        """
        if config_path is None:
            config_path = (
                Path(__file__).resolve().parent.parent / "config" / "error_patterns.yaml"
            )
        self._engine = DiagnosticsEngine(config_path)
        self._buffer = ""
        self._max_buffer = 4096  # 防止 buffer 无限增长
        self._last_diagnosis: Optional[DiagnosisResult] = None
        self._cooldown_lines = 0  # 冷却计数，避免重复告警
        self._cooldown_threshold = 5  # 触发后至少等 5 段输出再检测

    def feed(self, chunk: str) -> Optional[DiagnosisResult]:
        """喂入一段终端输出，如果检测到错误返回诊断结果。

        Args:
            chunk: 终端输出的一段文本

        Returns:
            检测到错误时返回 DiagnosisResult，否则返回 None
        """
        self._buffer += chunk

        # 防止 buffer 无限增长
        if len(self._buffer) > self._max_buffer:
            self._buffer = self._buffer[-self._max_buffer:]

        # 冷却期内不检测
        if self._cooldown_lines > 0:
            self._cooldown_lines -= 1
            return None

        # 执行诊断
        diagnosis = self._engine.diagnose(self._buffer)

        if diagnosis.error_type != ErrorType.UNKNOWN:
            # 避免同一个错误重复告警
            if (
                self._last_diagnosis
                and self._last_diagnosis.error_type == diagnosis.error_type
                and self._last_diagnosis.matched_rule == diagnosis.matched_rule
            ):
                return None

            # 触发告警，进入冷却期
            self._last_diagnosis = diagnosis
            self._cooldown_lines = self._cooldown_threshold
            self._buffer = ""  # 重置 buffer
            return diagnosis

        return None

    def reset(self) -> None:
        """重置监听器状态（用户切换会话时调用）。"""
        self._buffer = ""
        self._last_diagnosis = None
        self._cooldown_lines = 0

    def get_hint(self, error_type: ErrorType) -> str:
        """获取错误类型的解释提示（供 LLM 参考）。"""
        return self._engine.get_pattern_hint(error_type)

    @property
    def engine(self) -> DiagnosticsEngine:
        """暴露内部引擎（供需要直接调用 diagnose 的场景使用）。"""
        return self._engine
