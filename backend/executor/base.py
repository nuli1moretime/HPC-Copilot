"""命令执行器抽象接口。

所有执行器（REST API / SSH）都实现这个接口，
上层 Agent 和 WebSocket 不需要关心底层是哪种连接方式。
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class BaseExecutor(ABC):
    """执行器基类：REST API 和 SSH 都实现这个接口。"""

    @abstractmethod
    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        """执行命令，流式返回输出。

        Args:
            command: 用户输入的命令字符串

        Yields:
            输出的文本片段（逐块返回，模拟终端流式效果）
        """
        ...
        # 让 mypy 知道这是一个 async generator
        yield ""  # pragma: no cover

    @abstractmethod
    async def submit_job(self, script_content: str) -> dict:
        """提交 Slurm 作业。

        Args:
            script_content: 作业脚本内容（sbatch 脚本）

        Returns:
            包含 job_id 等信息的字典
        """
        ...

    @abstractmethod
    async def get_jobs(self) -> list[dict]:
        """查询当前用户的作业列表。

        Returns:
            作业信息字典列表
        """
        ...

    @abstractmethod
    async def cancel_job(self, job_id: str) -> None:
        """取消指定作业。

        Args:
            job_id: 作业 ID
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源。"""
        ...
