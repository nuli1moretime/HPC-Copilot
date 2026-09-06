"""核心数据模型。

规则引擎产出 DiagnosisResult（确定性判断），
大模型基于 DiagnosisResult 生成 AgentResponse（初学者友好的解释和操作建议）。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ErrorType(str, Enum):
    """诊断引擎支持的错误类型。"""

    INVALID_QOS = "INVALID_QOS"
    INVALID_PARTITION = "INVALID_PARTITION"
    WORKDIR_NOT_FOUND = "WORKDIR_NOT_FOUND"
    ENTRYPOINT_NOT_FOUND = "ENTRYPOINT_NOT_FOUND"
    MODULE_NOT_FOUND = "MODULE_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    GPU_OOM = "GPU_OOM"
    DISK_QUOTA = "DISK_QUOTA"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NODE_FAIL = "NODE_FAIL"
    PREEMPTED = "PREEMPTED"
    CONDA_ENV = "CONDA_ENV"
    CUDA_DEVICE = "CUDA_DEVICE"
    INVALID_TIME = "INVALID_TIME"
    DEPENDENCY = "DEPENDENCY"
    ACCOUNT_ISSUE = "ACCOUNT_ISSUE"
    PROGRAM_EXIT_NONZERO = "PROGRAM_EXIT_NONZERO"
    UNKNOWN = "UNKNOWN"


class DiagnosisResult(BaseModel):
    """规则引擎的诊断输出——确定性判断，不依赖大模型。"""

    error_type: ErrorType
    confidence: float = Field(
        ge=0.0, le=1.0, description="规则命中时为 1.0，模糊匹配时酌情降低"
    )
    evidence: list[str] = Field(
        default_factory=list, description="日志中命中规则的具体行或片段"
    )
    matched_rule: str = Field(default="", description="命中的规则 ID")
    root_cause_brief: str = Field(default="", description="一句话根因（供大模型参考）")
    suggested_commands: list[str] = Field(
        default_factory=list, description="建议用户执行的命令（确定性部分）"
    )


class AgentResponse(BaseModel):
    """大模型生成的初学者友好回答。"""

    explanation: str = Field(description="用通俗语言解释发生了什么、为什么")
    next_steps: list[str] = Field(description="具体的下一步操作，每步一条命令或动作")
    warning: Optional[str] = Field(
        default=None, description="需要特别注意的事项（如有）"
    )


class DiagnosisReport(BaseModel):
    """一次完整诊断的最终输出：规则判断 + 智能体解释。"""

    raw_log: str = Field(description="用户粘贴的原始日志")
    diagnosis: DiagnosisResult
    agent_response: Optional[AgentResponse] = Field(
        default=None, description="大模型生成的解释（API 不可用时为 None）"
    )
