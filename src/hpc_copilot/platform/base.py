"""平台适配器基类。

定义统一接口，隔离 REST API、本地 CLI、Mock 的差异。
上层诊断逻辑只依赖这个接口，不关心底层实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JobInfo:
    """作业状态信息（从平台获取的结构化数据）。"""

    job_id: int
    name: str = ""
    user_name: str = ""
    partition: str = ""
    state: str = ""  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT...
    exit_code: Optional[int] = None
    state_reason: str = ""
    run_time: Optional[int] = None  # 秒
    time_limit: Optional[int] = None  # 分钟
    cpus: Optional[int] = None
    qos: str = ""
    working_directory: str = ""
    std_out: str = ""  # 输出文件路径
    std_err: str = ""  # 错误文件路径
    raw: dict = field(default_factory=dict)  # 原始 API 返回

    @property
    def is_terminal(self) -> bool:
        """作业是否已经结束（不再变化）。"""
        return self.state in (
            "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
            "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL",
        )

    @property
    def is_failed(self) -> bool:
        """作业是否异常结束。"""
        return self.state in (
            "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL",
        ) or (self.exit_code is not None and self.exit_code != 0)

    def to_log_text(self) -> str:
        """将作业信息转为可供诊断引擎分析的文本。"""
        lines = [
            f"Job {self.job_id} ({self.name}) {self.state}",
            f"Partition: {self.partition}",
            f"User: {self.user_name}",
        ]
        if self.state_reason:
            lines.append(f"State reason: {self.state_reason}")
        if self.exit_code is not None:
            lines.append(f"Exit code: {self.exit_code}")
        if self.run_time is not None:
            lines.append(f"Run time: {self.run_time}s")
        if self.time_limit is not None:
            lines.append(f"Time limit: {self.time_limit} min")
        if self.qos:
            lines.append(f"QoS: {self.qos}")
        if self.working_directory:
            lines.append(f"Working directory: {self.working_directory}")
        return "\n".join(lines)


class PlatformAdapter(ABC):
    """平台适配器抽象基类。"""

    @abstractmethod
    def get_job(self, job_id: int) -> Optional[JobInfo]:
        """获取单个作业的状态信息。"""
        ...

    @abstractmethod
    def get_jobs(self, user: Optional[str] = None) -> list[JobInfo]:
        """获取作业列表。user 为 None 时返回所有可见作业。"""
        ...

    @abstractmethod
    def get_partitions(self) -> list[str]:
        """获取可用分区列表。"""
        ...

    def is_available(self) -> bool:
        """检查平台连接是否可用。"""
        try:
            self.get_partitions()
            return True
        except Exception:
            return False
