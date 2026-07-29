"""SSH 执行器（备选方案）。

当算力中心没有开放 slurmrestd 时，通过 SSH 连接执行任意命令。
使用 paramiko 库，支持完整的终端交互体验。
"""

import asyncio
from typing import AsyncGenerator

from .base import BaseExecutor

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore


class SSHExecutor(BaseExecutor):
    """基于 SSH 的命令执行器，支持任意 shell 命令。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str = "",
        key_filename: str = "",
        port: int = 22,
    ):
        """初始化 SSH 执行器。

        Args:
            host: 集群地址
            username: 用户名
            password: 密码（与 key_filename 二选一）
            key_filename: SSH 私钥文件路径
            port: SSH 端口（默认 22）
        """
        if paramiko is None:
            raise ImportError(
                "SSH 模式需要安装 paramiko: pip install paramiko"
            )

        self.host = host
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self._client: paramiko.SSHClient | None = None
        self._channel = None  # paramiko.Channel

    async def connect(self) -> None:
        """建立 SSH 连接。"""
        loop = asyncio.get_event_loop()
        self._client = await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> "paramiko.SSHClient":
        """同步连接（在线程池中执行）。"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
        }
        if self.key_filename:
            connect_kwargs["key_filename"] = self.key_filename
        elif self.password:
            connect_kwargs["password"] = self.password

        client.connect(**connect_kwargs)
        return client

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        """通过 SSH 执行命令，流式返回输出。"""
        if self._client is None:
            await self.connect()

        loop = asyncio.get_event_loop()

        try:
            # 在线程池中执行 SSH 命令
            stdin, stdout, stderr = await loop.run_in_executor(
                None,
                lambda: self._client.exec_command(command, timeout=60),
            )

            # 流式读取 stdout
            while True:
                chunk = await loop.run_in_executor(
                    None, lambda: stdout.read(1024).decode("utf-8", errors="replace")
                )
                if not chunk:
                    break
                yield chunk

            # 读取 stderr
            err_output = await loop.run_in_executor(
                None, lambda: stderr.read().decode("utf-8", errors="replace")
            )
            if err_output:
                yield err_output

            # 获取退出码
            exit_code = await loop.run_in_executor(None, stdout.channel.recv_exit_status)
            if exit_code != 0:
                yield f"\n[进程退出码: {exit_code}]\n"

        except paramiko.AuthenticationException:
            yield "❌ SSH 认证失败，请检查用户名和密码/密钥。\n"
        except paramiko.SSHException as e:
            yield f"❌ SSH 错误: {e}\n"
        except Exception as e:
            yield f"❌ 执行出错: {type(e).__name__}: {e}\n"

    async def submit_job(self, script_content: str) -> dict:
        """通过 SSH 提交作业（写入临时文件后 sbatch）。"""
        import tempfile
        import os

        # 在远程创建临时脚本并提交
        remote_script = "/tmp/_hpc_copilot_job.sh"
        escaped_content = script_content.replace("'", "'\\''")

        commands = [
            f"echo '{escaped_content}' > {remote_script}",
            f"sbatch {remote_script}",
            f"rm -f {remote_script}",
        ]
        full_cmd = " && ".join(commands)

        output = ""
        async for chunk in self.execute_stream(full_cmd):
            output += chunk

        # 解析 job id
        job_id = "?"
        if "Submitted batch job" in output:
            parts = output.strip().split()
            job_id = parts[-1] if parts else "?"

        return {"job_id": job_id, "output": output}

    async def get_jobs(self) -> list[dict]:
        """通过 squeue 获取作业列表。"""
        output = ""
        async for chunk in self.execute_stream("squeue -u $USER -h -o '%i|%P|%j|%T|%M|%D'"):
            output += chunk

        jobs = []
        for line in output.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id": parts[0],
                    "partition": parts[1],
                    "name": parts[2],
                    "job_state": parts[3],
                    "run_time": parts[4],
                    "node_count": parts[5],
                })
        return jobs

    async def cancel_job(self, job_id: str) -> None:
        """通过 scancel 取消作业。"""
        async for _ in self.execute_stream(f"scancel {job_id}"):
            pass

    async def close(self) -> None:
        """关闭 SSH 连接。"""
        if self._client:
            self._client.close()
            self._client = None
